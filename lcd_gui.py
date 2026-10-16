"""Kraken LCD visual cropper and native Thermaltake panel controller.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import io
import json
import colorsys
import queue
import socket
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageColor, ImageDraw, ImageTk

from lcd_media import (
    LCD_ASPECT,
    Crop,
    MediaError,
    MediaInfo,
    export_media,
    extract_preview,
    format_duration,
    human_size,
    maximum_crop,
    probe_media,
    render_lcd_jpeg,
    rotated_dimensions,
    stream_media,
)
from lcd_metrics import DEFAULT_LHM_URL, MetricsMonitor, OverlayStyle, PerformanceOverlayRenderer
from thermaltake_lcd import (
    BUSY_MESSAGE,
    STATE_BUSY,
    STATE_MISSING,
    STATE_READY,
    LcdBusyError,
    LcdError,
    ThermaltakeLcd,
    make_test_frame,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    RootBase = TkinterDnD.Tk
    HAS_DROP = True
except ImportError:
    DND_FILES = None
    RootBase = tk.Tk
    HAS_DROP = False

try:
    import pystray

    HAS_TRAY = True
except ImportError:
    pystray = None
    HAS_TRAY = False


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "lcd_gui_config.json"
LOG_PATH = APP_DIR / "kraken_lcd_studio.log"
LOG_LIMIT_BYTES = 1_000_000
STARTUP_NAME = "Kraken LCD Studio"
INSTANCE_PORT = 47839
VERSION = (APP_DIR / "VERSION").read_text(encoding="utf-8").strip() if (APP_DIR / "VERSION").exists() else "dev"
MEDIA_TYPES = [
    ("Supported media", "*.mp4 *.webm *.mkv *.mov *.avi *.gif *.webp *.png *.jpg *.jpeg *.bmp *.tif *.tiff"),
    ("Video", "*.mp4 *.webm *.mkv *.mov *.avi"),
    ("Images / animation", "*.png *.jpg *.jpeg *.gif *.webp *.bmp *.tif *.tiff"),
    ("All files", "*.*"),
]

BG = "#0b1016"
PANEL = "#121a23"
PANEL_2 = "#17222d"
TEXT = "#e8f2f8"
MUTED = "#8ba0af"
ACCENT = "#20e39a"
WARN = "#ffbc57"
ERROR = "#ff657a"
DEFAULT_OVERLAY_VISUALS = {
    "cycle_seconds": 4,
    "accent": "#20e39a",
    "text": "#ffffff",
    "font_family": "Segoe UI",
    "font_scale": 100,
    "panel_opacity": 0,
    "panel_background": False,
    "cards": ["cpu", "gpu", "memory", "clock"],
    "lhm_url": DEFAULT_LHM_URL,
}


def startup_command() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == "python.exe" and executable.with_name("pythonw.exe").is_file():
        executable = executable.with_name("pythonw.exe")
    return f'"{executable}" "{Path(__file__).resolve()}" --background'


def set_startup_enabled(enabled: bool) -> None:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, STARTUP_NAME)
            except FileNotFoundError:
                pass


def is_startup_enabled() -> bool:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, STARTUP_NAME)
        return True
    except FileNotFoundError:
        return False


def append_log_file(text: str) -> None:
    """Keep a durable log; a hidden pythonw launch has no console to report failures."""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_LIMIT_BYTES:
            LOG_PATH.replace(LOG_PATH.with_name("kraken_lcd_studio.old.log"))
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else f"{text}\n")
    except OSError:
        pass


def acquire_single_instance(show_existing: bool = True) -> socket.socket | None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", INSTANCE_PORT))
        listener.listen(2)
        listener.settimeout(1.0)
        return listener
    except OSError:
        listener.close()
        try:
            if show_existing:
                with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=1) as client:
                    client.sendall(b"SHOW\n")
        except OSError:
            pass
        return None


def tray_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), "#0b1016")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 13, 59, 51), radius=8, fill="#20e39a")
    draw.rectangle((11, 20, 53, 44), fill="#075985")
    draw.line((16, 38, 25, 26, 33, 34, 48, 22), fill="#ffffff", width=4)
    return image


def resolve_media_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_file():
        return path
    matches = list(APP_DIR.rglob(path.name)) if path.name else []
    return matches[0] if len(matches) == 1 else path


class LiveColorPicker(tk.Toplevel):
    PALETTE_WIDTH = 250
    PALETTE_HEIGHT = 170
    HUE_WIDTH = 28

    def __init__(self, master, title: str, initial: str, on_preview, on_accept):
        super().__init__(master)
        self.title(title)
        self.configure(bg=PANEL)
        self.resizable(False, False)
        self.transient(master)
        self.original = initial
        self.on_preview = on_preview
        self.on_accept = on_accept
        self.palette_photo = None
        self.hue_photo = None

        red, green, blue = ImageColor.getrgb(initial)
        self.hue, self.saturation, self.value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        self.hex_var = tk.StringVar(value=initial.upper())

        body = ttk.Frame(self, style="Panel.TFrame", padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="DRAG TO PREVIEW LIVE", style="PanelMuted.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.palette = tk.Canvas(
            body,
            width=self.PALETTE_WIDTH,
            height=self.PALETTE_HEIGHT,
            highlightthickness=1,
            highlightbackground="#526575",
            cursor="crosshair",
        )
        self.palette.grid(row=1, column=0, padx=(0, 8))
        self.hue_bar = tk.Canvas(
            body,
            width=self.HUE_WIDTH,
            height=self.PALETTE_HEIGHT,
            highlightthickness=1,
            highlightbackground="#526575",
            cursor="sb_v_double_arrow",
        )
        self.hue_bar.grid(row=1, column=1)
        self.palette.bind("<Button-1>", self.pick_saturation_value)
        self.palette.bind("<B1-Motion>", self.pick_saturation_value)
        self.hue_bar.bind("<Button-1>", self.pick_hue)
        self.hue_bar.bind("<B1-Motion>", self.pick_hue)

        self.swatch = tk.Label(body, width=5, bg=initial, relief="flat")
        self.swatch.grid(row=2, column=0, sticky="w", pady=(10, 0))
        entry = ttk.Entry(body, textvariable=self.hex_var, width=10)
        entry.grid(row=2, column=0, sticky="e", pady=(10, 0))
        entry.bind("<Return>", self.apply_hex_entry)

        buttons = ttk.Frame(body, style="Panel.TFrame")
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(buttons, text="OK", style="Accent.TButton", command=self.accept).pack(side="right", padx=(0, 8))

        self.render_hue_bar()
        self.render_palette()
        self.draw_cursors()
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.grab_set()
        self.focus_force()

    def color(self) -> str:
        red, green, blue = colorsys.hsv_to_rgb(self.hue, self.saturation, self.value)
        return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"

    def render_hue_bar(self) -> None:
        strip = Image.new("RGB", (1, self.PALETTE_HEIGHT))
        pixels = strip.load()
        for y in range(self.PALETTE_HEIGHT):
            red, green, blue = colorsys.hsv_to_rgb(y / max(1, self.PALETTE_HEIGHT - 1), 1, 1)
            pixels[0, y] = (round(red * 255), round(green * 255), round(blue * 255))
        strip = strip.resize((self.HUE_WIDTH, self.PALETTE_HEIGHT), Image.Resampling.NEAREST)
        self.hue_photo = ImageTk.PhotoImage(strip)
        self.hue_bar.create_image(0, 0, image=self.hue_photo, anchor="nw", tags="gradient")

    def render_palette(self) -> None:
        image = Image.new("RGB", (self.PALETTE_WIDTH, self.PALETTE_HEIGHT))
        pixels = image.load()
        for y in range(self.PALETTE_HEIGHT):
            value = 1 - y / max(1, self.PALETTE_HEIGHT - 1)
            for x in range(self.PALETTE_WIDTH):
                saturation = x / max(1, self.PALETTE_WIDTH - 1)
                red, green, blue = colorsys.hsv_to_rgb(self.hue, saturation, value)
                pixels[x, y] = (round(red * 255), round(green * 255), round(blue * 255))
        self.palette_photo = ImageTk.PhotoImage(image)
        self.palette.delete("gradient")
        self.palette.create_image(0, 0, image=self.palette_photo, anchor="nw", tags="gradient")
        self.palette.tag_lower("gradient")

    def draw_cursors(self) -> None:
        self.palette.delete("cursor")
        x = self.saturation * (self.PALETTE_WIDTH - 1)
        y = (1 - self.value) * (self.PALETTE_HEIGHT - 1)
        self.palette.create_oval(x - 6, y - 6, x + 6, y + 6, outline="#ffffff", width=2, tags="cursor")
        self.palette.create_oval(x - 8, y - 8, x + 8, y + 8, outline="#000000", width=1, tags="cursor")
        self.hue_bar.delete("cursor")
        hue_y = self.hue * (self.PALETTE_HEIGHT - 1)
        self.hue_bar.create_rectangle(0, hue_y - 2, self.HUE_WIDTH, hue_y + 2, outline="#ffffff", width=2, tags="cursor")

    def publish(self) -> None:
        color = self.color()
        self.hex_var.set(color)
        self.swatch.configure(bg=color)
        self.draw_cursors()
        self.on_preview(color)

    def pick_saturation_value(self, event) -> None:
        self.saturation = max(0, min(1, event.x / max(1, self.PALETTE_WIDTH - 1)))
        self.value = 1 - max(0, min(1, event.y / max(1, self.PALETTE_HEIGHT - 1)))
        self.publish()

    def pick_hue(self, event) -> None:
        self.hue = max(0, min(1, event.y / max(1, self.PALETTE_HEIGHT - 1)))
        self.render_palette()
        self.publish()

    def apply_hex_entry(self, _event=None) -> None:
        try:
            red, green, blue = ImageColor.getrgb(self.hex_var.get().strip())
        except ValueError:
            return
        self.hue, self.saturation, self.value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        self.render_palette()
        self.publish()

    def accept(self) -> None:
        color = self.color()
        self.grab_release()
        self.destroy()
        self.on_accept(color)

    def cancel(self) -> None:
        self.on_preview(self.original)
        self.grab_release()
        self.destroy()


class CropCanvas(tk.Canvas):
    def __init__(self, master, on_change, **kwargs):
        super().__init__(
            master,
            bg="#06090d",
            highlightthickness=1,
            highlightbackground="#263746",
            cursor="crosshair",
            **kwargs,
        )
        self.on_change = on_change
        self.source: Image.Image | None = None
        self.crop = Crop(0, 0, 1, 1 / LCD_ASPECT)
        self.crop_scale = 1.0
        self.photo = None
        self.image_box = (0.0, 0.0, 1.0, 1.0)
        self.drag_offset = (0.0, 0.0)
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)

    def set_image(self, image: Image.Image, reset: bool = True) -> None:
        self.source = image.convert("RGB")
        if reset:
            self.crop_scale = 1.0
            self.crop = maximum_crop(self.source.width, self.source.height)
        self.redraw()
        self.on_change(self.crop)

    def set_crop_scale(self, value: float) -> None:
        if not self.source:
            return
        center_x = self.crop.x + self.crop.width / 2
        center_y = self.crop.y + self.crop.height / 2
        self.crop_scale = min(max(float(value), 0.1), 1.0)
        sized = maximum_crop(self.source.width, self.source.height, self.crop_scale)
        self.crop = Crop(
            center_x - sized.width / 2,
            center_y - sized.height / 2,
            sized.width,
            sized.height,
        ).clamped()
        self.redraw_overlay()
        self.on_change(self.crop)

    def redraw(self) -> None:
        self.delete("all")
        if not self.source:
            self.create_text(
                max(self.winfo_width(), 400) / 2,
                max(self.winfo_height(), 260) / 2,
                text="DROP MEDIA HERE\n\nor click Browse",
                fill=MUTED,
                font=("Segoe UI Semibold", 18),
                justify="center",
            )
            return
        canvas_w = max(self.winfo_width() - 24, 40)
        canvas_h = max(self.winfo_height() - 24, 40)
        scale = min(canvas_w / self.source.width, canvas_h / self.source.height)
        display_w = max(1, round(self.source.width * scale))
        display_h = max(1, round(self.source.height * scale))
        x = (self.winfo_width() - display_w) / 2
        y = (self.winfo_height() - display_h) / 2
        resized = self.source.resize((display_w, display_h), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(resized)
        self.create_image(x, y, image=self.photo, anchor="nw", tags="source")
        self.image_box = (x, y, display_w, display_h)
        self.redraw_overlay()

    def _crop_canvas_box(self) -> tuple[float, float, float, float]:
        x, y, width, height = self.image_box
        return (
            x + self.crop.x * width,
            y + self.crop.y * height,
            x + (self.crop.x + self.crop.width) * width,
            y + (self.crop.y + self.crop.height) * height,
        )

    def redraw_overlay(self) -> None:
        self.delete("shade")
        self.delete("crop")
        if not self.source:
            return
        ix, iy, iw, ih = self.image_box
        x1, y1, x2, y2 = self._crop_canvas_box()
        shade = "#020305"
        stipple = "gray50"
        self.create_rectangle(ix, iy, ix + iw, y1, fill=shade, stipple=stipple, width=0, tags="shade")
        self.create_rectangle(ix, y2, ix + iw, iy + ih, fill=shade, stipple=stipple, width=0, tags="shade")
        self.create_rectangle(ix, y1, x1, y2, fill=shade, stipple=stipple, width=0, tags="shade")
        self.create_rectangle(x2, y1, ix + iw, y2, fill=shade, stipple=stipple, width=0, tags="shade")
        self.create_rectangle(x1, y1, x2, y2, outline=ACCENT, width=3, tags="crop")
        for third in (1 / 3, 2 / 3):
            self.create_line(x1 + (x2 - x1) * third, y1, x1 + (x2 - x1) * third, y2, fill="#8affd0", dash=(3, 5), tags="crop")
            self.create_line(x1, y1 + (y2 - y1) * third, x2, y1 + (y2 - y1) * third, fill="#8affd0", dash=(3, 5), tags="crop")
        self.create_text(x1 + 10, y1 + 10, text="480 × 128", anchor="nw", fill=ACCENT, font=("Consolas", 10, "bold"), tags="crop")

    def _press(self, event) -> None:
        if not self.source:
            return
        x1, y1, x2, y2 = self._crop_canvas_box()
        if not (x1 <= event.x <= x2 and y1 <= event.y <= y2):
            ix, iy, iw, ih = self.image_box
            center_x = (event.x - ix) / iw
            center_y = (event.y - iy) / ih
            self.crop = Crop(
                center_x - self.crop.width / 2,
                center_y - self.crop.height / 2,
                self.crop.width,
                self.crop.height,
            ).clamped()
            self.redraw_overlay()
            self.on_change(self.crop)
            x1, y1, x2, y2 = self._crop_canvas_box()
        self.drag_offset = (event.x - x1, event.y - y1)

    def _drag(self, event) -> None:
        if not self.source:
            return
        ix, iy, iw, ih = self.image_box
        new_x = (event.x - self.drag_offset[0] - ix) / iw
        new_y = (event.y - self.drag_offset[1] - iy) / ih
        self.crop = Crop(new_x, new_y, self.crop.width, self.crop.height).clamped()
        self.redraw_overlay()
        self.on_change(self.crop)

    def cropped_image(self) -> Image.Image | None:
        if not self.source:
            return None
        left = round(self.crop.x * self.source.width)
        top = round(self.crop.y * self.source.height)
        right = round((self.crop.x + self.crop.width) * self.source.width)
        bottom = round((self.crop.y + self.crop.height) * self.source.height)
        return self.source.crop((left, top, right, bottom)).resize((480, 128), Image.Resampling.LANCZOS)


class KrakenLcdApp(RootBase):
    def __init__(self, background: bool = False, instance_listener: socket.socket | None = None):
        super().__init__()
        self.title(f"Kraken LCD Studio {VERSION}")
        self.geometry("1380x900")
        self.minsize(1120, 760)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.request_close)

        self.config_data = self.load_config()
        overlay_config = self.config_data.get("overlay", {})
        self.background = background
        self.instance_listener = instance_listener
        self.tray_icon = None
        self.tray_notice_shown = False
        self.profiles: dict[str, dict] = dict(self.config_data.get("profiles", {}))
        self.resume_on_startup = bool(
            self.config_data.get("resume_on_startup", bool(self.config_data.get("last_media")))
        )
        self.pending_setup: dict | None = None
        self.pending_auto_play = False
        self.media_info: MediaInfo | None = None
        self.preview_image: Image.Image | None = None
        self.preview_photo = None
        self.worker: threading.Thread | None = None
        self.operation_kind: str | None = None
        self.active_overlay_renderer: PerformanceOverlayRenderer | None = None
        self.overlay_restart_pending = False
        self.stop_event = threading.Event()
        self.ui_events: queue.Queue[tuple] = queue.Queue()
        self.preview_generation = 0
        self.closing = False
        self.device_state: str | None = None
        self.last_error_notice: str | None = None

        self.path_var = tk.StringVar(value="")
        self.device_var = tk.StringVar(value="Checking USB panel…")
        self.meta_var = tk.StringVar(value="Choose an image, animation, or video to begin.")
        self.status_var = tk.StringVar(value="Ready")
        self.fps_var = tk.DoubleVar(value=float(self.config_data.get("fps", 8)))
        self.quality_var = tk.IntVar(value=int(self.config_data.get("quality", 85)))
        self.speed_var = tk.StringVar(value=str(self.config_data.get("speed", "1.0")))
        self.rotation_var = tk.StringVar(value=str(self.config_data.get("rotation", "0")))
        self.loop_var = tk.BooleanVar(value=bool(self.config_data.get("loop", True)))
        self.overlay_enabled_var = tk.BooleanVar(value=bool(overlay_config.get("enabled", False)))
        self.overlay_cycle_var = tk.StringVar(value=str(overlay_config.get("cycle_seconds", "4")))
        self.overlay_accent_var = tk.StringVar(value=str(overlay_config.get("accent", "#20e39a")))
        self.overlay_text_var = tk.StringVar(value=str(overlay_config.get("text", "#ffffff")))
        self.overlay_font_var = tk.StringVar(value=str(overlay_config.get("font_family", "Segoe UI")))
        self.overlay_font_scale_var = tk.IntVar(value=int(overlay_config.get("font_scale", 100)))
        self.overlay_opacity_var = tk.IntVar(value=int(overlay_config.get("panel_opacity", 78)))
        self.overlay_background_var = tk.BooleanVar(value=bool(overlay_config.get("panel_background", True)))
        cards = set(overlay_config.get("cards", ("cpu", "gpu", "memory", "clock")))
        self.overlay_cpu_var = tk.BooleanVar(value="cpu" in cards)
        self.overlay_gpu_var = tk.BooleanVar(value="gpu" in cards)
        self.overlay_memory_var = tk.BooleanVar(value="memory" in cards)
        self.overlay_clock_var = tk.BooleanVar(value="clock" in cards)
        self.overlay_preview_card_var = tk.StringVar(value="CPU")
        self.lhm_url_var = tk.StringVar(value=str(overlay_config.get("lhm_url", DEFAULT_LHM_URL)))
        self.profile_var = tk.StringVar(value=str(self.config_data.get("active_profile", "")))
        self.startup_var = tk.BooleanVar(value=is_startup_enabled())
        self.crop_size_var = tk.DoubleVar(value=100)
        self.time_var = tk.DoubleVar(value=0)

        self.configure_styles()
        self.build_ui()
        self.log(f"Kraken LCD Studio {VERSION} started{' in the background' if background else ''}.")
        self.start_instance_listener()
        self.start_tray()
        self.after(50, self.process_ui_events)
        self.after(150, self.poll_device)

        if background and HAS_TRAY:
            self.withdraw()

        last_setup = self.config_data.get("last_setup") or self.legacy_setup()
        last_path = last_setup.get("media_path", "") if last_setup else ""
        if last_path:
            auto_play = background and self.resume_on_startup
            self.after(300, lambda: self.apply_setup(last_setup, auto_play=auto_play))

    def configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL_2, bordercolor="#2b3c49", font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 24), foreground=TEXT)
        style.configure("Accent.TButton", background=ACCENT, foreground="#03110c", padding=(18, 11), font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", "#55f1b5"), ("disabled", "#315348")])
        style.configure("Blue.TButton", background="#075985", foreground="#ffffff", padding=(14, 9), font=("Segoe UI Semibold", 10))
        style.map(
            "Blue.TButton",
            background=[("disabled", "#334155"), ("pressed", "#0c4a6e"), ("active", "#0369a1")],
            foreground=[("disabled", "#d5e2ea"), ("pressed", "#ffffff"), ("active", "#ffffff")],
        )
        style.configure("TButton", background="#263746", foreground=TEXT, padding=(12, 8))
        style.map("TButton", background=[("active", "#344c5f")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#17222d", foreground=MUTED, padding=(16, 9))
        style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", ACCENT)])
        style.configure("TScale", background=PANEL, troughcolor="#263746")
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TCombobox", fieldbackground=PANEL_2, background=PANEL_2, foreground=TEXT)

    def build_ui(self) -> None:
        header = ttk.Frame(self, padding=(24, 18, 24, 12))
        header.pack(fill="x")
        ttk.Label(header, text="KRAKEN LCD STUDIO", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{VERSION}  •  native 480×128 USB control", style="Muted.TLabel").pack(side="left", padx=18, pady=(8, 0))
        self.device_label = tk.Label(header, textvariable=self.device_var, bg="#18251f", fg=ACCENT, padx=14, pady=8, font=("Segoe UI Semibold", 9))
        self.device_label.pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=24, pady=(0, 14))
        left = ttk.Frame(body)
        right = ttk.Frame(body, style="Panel.TFrame", width=360)
        body.add(left, weight=4)
        body.add(right, weight=2)

        file_bar = ttk.Frame(left)
        file_bar.pack(fill="x", pady=(0, 10))
        self.path_entry = ttk.Entry(file_bar, textvariable=self.path_var)
        self.path_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(file_bar, text="Browse media", style="Blue.TButton", command=self.browse_media).pack(side="left", padx=(8, 0))
        ttk.Label(left, textvariable=self.meta_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))

        self.crop_canvas = CropCanvas(left, self.crop_changed, height=455)
        self.crop_canvas.pack(fill="both", expand=True)
        if HAS_DROP:
            self.crop_canvas.drop_target_register(DND_FILES)
            self.crop_canvas.dnd_bind("<<Drop>>", self.handle_drop)

        crop_tools = ttk.Frame(left, padding=(0, 8, 0, 0))
        crop_tools.pack(fill="x")
        ttk.Label(crop_tools, text="Crop size", style="Muted.TLabel").pack(side="left")
        crop_scale = ttk.Scale(crop_tools, from_=25, to=100, variable=self.crop_size_var, command=lambda value: self.crop_canvas.set_crop_scale(float(value) / 100))
        crop_scale.pack(side="left", fill="x", expand=True, padx=10)
        ttk.Button(crop_tools, text="Center", command=self.center_crop).pack(side="left")

        time_row = ttk.Frame(left, padding=(0, 4, 0, 0))
        time_row.pack(fill="x")
        ttk.Label(time_row, text="Preview time", style="Muted.TLabel").pack(side="left")
        self.time_scale = ttk.Scale(time_row, from_=0, to=1, variable=self.time_var)
        self.time_scale.pack(side="left", fill="x", expand=True, padx=10)
        self.time_scale.bind("<ButtonRelease-1>", lambda _event: self.refresh_preview(reset_crop=False))
        self.time_label = ttk.Label(time_row, text="0:00.0", width=8)
        self.time_label.pack(side="left")

        output = ttk.Frame(left, style="Panel.TFrame", padding=10)
        output.pack(fill="x", pady=(10, 0))
        ttk.Label(output, text="EXACT LCD OUTPUT", style="PanelMuted.TLabel").pack(anchor="w")
        self.output_canvas = tk.Canvas(output, height=154, bg="#05070a", highlightthickness=0)
        self.output_canvas.pack(fill="x", pady=(6, 0))
        self.output_canvas.bind("<Configure>", lambda _event: self.update_output_preview())

        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True, padx=14, pady=14)
        output_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=16)
        overlay_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=16)
        activity_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=12)
        notebook.add(output_tab, text="OUTPUT")
        notebook.add(overlay_tab, text="OVERLAY")
        notebook.add(activity_tab, text="ACTIVITY")

        self.section(output_tab, "SAVED PROFILES")
        self.profile_combo = ttk.Combobox(
            output_tab,
            textvariable=self.profile_var,
            values=sorted(self.profiles),
            state="readonly",
        )
        self.profile_combo.pack(fill="x", pady=(0, 6))
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_selected_profile())
        profile_row = ttk.Frame(output_tab, style="Panel.TFrame")
        profile_row.pack(fill="x", pady=(0, 14))
        ttk.Button(profile_row, text="Save…", command=self.save_profile).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(profile_row, text="Load", command=self.load_selected_profile).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(profile_row, text="Delete", command=self.delete_profile).pack(side="left", fill="x", expand=True, padx=(3, 0))

        self.section(output_tab, "FRAMING")
        self.combo_row(output_tab, "Rotation", self.rotation_var, ("0", "90", "180", "270"), self.rotation_changed)
        ttk.Label(output_tab, text="Drag the green 3.75:1 frame over the source. The strip below is exactly what the panel receives.", style="PanelMuted.TLabel", wraplength=300, justify="left").pack(fill="x", pady=(2, 16))

        self.section(output_tab, "PLAYBACK")
        self.scale_row(output_tab, "Panel FPS", self.fps_var, 1, 30, lambda value: f"{float(value):.0f}")
        self.scale_row(output_tab, "JPEG quality", self.quality_var, 35, 95, lambda value: f"{float(value):.0f}%")
        self.combo_row(output_tab, "Speed", self.speed_var, ("0.25", "0.5", "0.75", "1.0", "1.25", "1.5", "2.0"))
        ttk.Checkbutton(output_tab, text="Loop animated media", variable=self.loop_var).pack(anchor="w", pady=(8, 16))

        self.section(output_tab, "ACTIONS")
        self.play_button = ttk.Button(output_tab, text="▶  PLAY ON LCD", style="Accent.TButton", command=self.start_playback)
        self.play_button.pack(fill="x", pady=(2, 7))
        row = ttk.Frame(output_tab, style="Panel.TFrame")
        row.pack(fill="x", pady=3)
        ttk.Button(row, text="Send this frame", command=self.send_current_frame).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(row, text="Test card", command=self.send_test_card).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.export_button = ttk.Button(output_tab, text="Export as MP4 or WebM…", style="Blue.TButton", command=self.export_dialog)
        self.export_button.pack(fill="x", pady=(7, 3))
        self.webm_export_button = ttk.Button(output_tab, text="Convert video → WebM…", command=self.export_webm)
        self.webm_export_button.pack(fill="x", pady=(3, 7))
        self.stop_button = ttk.Button(output_tab, text="■  STOP / CANCEL", command=self.stop_work, state="disabled")
        self.stop_button.pack(fill="x", pady=(0, 10))
        ttk.Label(output_tab, text="MP4: compact and fast to decode\nWebM/VP9: often smaller, slower to encode/decode\nJPG: exact static format sent by the panel", style="PanelMuted.TLabel", justify="left").pack(fill="x", pady=(5, 0))

        self.section(overlay_tab, "PERFORMANCE OVERLAY")
        ttk.Checkbutton(
            overlay_tab,
            text="Show live system metrics over media",
            variable=self.overlay_enabled_var,
            command=self.overlay_changed,
        ).pack(anchor="w", pady=(0, 7))
        ttk.Label(
            overlay_tab,
            text="Cards rotate over the selected image or video. Play on LCD keeps metrics live; Send this frame captures one reading.",
            style="PanelMuted.TLabel",
            wraplength=310,
            justify="left",
        ).pack(fill="x", pady=(0, 14))

        self.combo_row(overlay_tab, "Preview card", self.overlay_preview_card_var, ("CPU", "GPU", "Memory", "Clock"), self.overlay_changed)
        self.combo_row(overlay_tab, "Cycle every", self.overlay_cycle_var, ("2", "3", "4", "5", "8", "10"), self.overlay_changed)
        self.combo_row(
            overlay_tab,
            "Font",
            self.overlay_font_var,
            ("Segoe UI", "Segoe UI Semibold", "Bahnschrift", "Consolas"),
            self.overlay_changed,
        )
        self.scale_row(
            overlay_tab,
            "Font scale",
            self.overlay_font_scale_var,
            70,
            130,
            lambda value: f"{float(value):.0f}%",
            self.overlay_changed,
        )
        self.scale_row(
            overlay_tab,
            "Glass opacity",
            self.overlay_opacity_var,
            0,
            100,
            lambda value: f"{float(value):.0f}%",
            self.overlay_changed,
        )
        ttk.Checkbutton(
            overlay_tab,
            text="Dark glass background behind metrics",
            variable=self.overlay_background_var,
            command=self.overlay_changed,
        ).pack(anchor="w", pady=(7, 5))

        colors = ttk.Frame(overlay_tab, style="Panel.TFrame")
        colors.pack(fill="x", pady=(8, 12))
        ttk.Label(colors, text="Colors", style="Panel.TLabel").pack(side="left")
        self.overlay_accent_button = tk.Button(
            colors,
            text="Accent",
            bg=self.overlay_accent_var.get(),
            fg="#ffffff",
            activebackground=self.overlay_accent_var.get(),
            relief="flat",
            command=lambda: self.choose_overlay_color("accent"),
        )
        self.overlay_accent_button.pack(side="right", padx=(5, 0))
        self.overlay_text_button = tk.Button(
            colors,
            text="Text",
            bg=self.overlay_text_var.get(),
            fg="#000000",
            activebackground=self.overlay_text_var.get(),
            relief="flat",
            command=lambda: self.choose_overlay_color("text"),
        )
        self.overlay_text_button.pack(side="right")

        self.section(overlay_tab, "CARDS IN ROTATION")
        for label, variable in (
            ("CPU load, package temperature, and clock", self.overlay_cpu_var),
            ("GPU load, temperature, clock, and VRAM", self.overlay_gpu_var),
            ("Memory use, capacity, and configured speed", self.overlay_memory_var),
            ("Clock, date, and day", self.overlay_clock_var),
        ):
            ttk.Checkbutton(overlay_tab, text=label, variable=variable, command=self.overlay_changed).pack(anchor="w", pady=3)

        self.section(overlay_tab, "SENSOR SOURCE")
        ttk.Entry(overlay_tab, textvariable=self.lhm_url_var).pack(fill="x", pady=(0, 6))
        ttk.Label(
            overlay_tab,
            text="GPU metrics use NVIDIA's driver when available. CPU temperature uses LibreHardwareMonitor's web server; other readings work without it.",
            style="PanelMuted.TLabel",
            wraplength=310,
            justify="left",
        ).pack(fill="x")
        ttk.Button(
            overlay_tab,
            text="✓  APPLY OVERLAY SETTINGS",
            style="Accent.TButton",
            command=self.apply_overlay_settings,
        ).pack(fill="x", pady=(15, 0))
        ttk.Button(
            overlay_tab,
            text="Reset overlay defaults…",
            command=self.reset_overlay_defaults,
        ).pack(fill="x", pady=(7, 0))

        ttk.Label(activity_tab, text="STATUS", style="PanelMuted.TLabel").pack(anchor="w")
        ttk.Label(activity_tab, textvariable=self.status_var, style="Panel.TLabel", wraplength=300, justify="left").pack(fill="x", pady=(5, 12))
        self.log_text = tk.Text(activity_tab, bg="#090e13", fg="#bcd0dc", insertbackground=TEXT, relief="flat", wrap="word", font=("Consolas", 9), padx=10, pady=10, state="disabled")
        self.log_text.pack(fill="both", expand=True)

        footer = ttk.Frame(self, padding=(24, 0, 24, 14))
        footer.pack(fill="x")
        drop_text = "Explorer drag-and-drop enabled" if HAS_DROP else "Install tkinterdnd2 to enable Explorer drag-and-drop"
        ttk.Label(footer, text=drop_text, style="Muted.TLabel").pack(side="left")
        ttk.Checkbutton(
            footer,
            text="Start hidden with Windows",
            variable=self.startup_var,
            command=self.toggle_startup,
        ).pack(side="left", padx=24)
        ttk.Label(footer, text="TT RGB Plus must remain closed", style="Muted.TLabel").pack(side="right")

    def section(self, parent, text: str) -> None:
        ttk.Label(parent, text=text, style="PanelMuted.TLabel", font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(2, 8))

    def combo_row(self, parent, label, variable, values, callback=None) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, style="Panel.TLabel").pack(side="left")
        combo = ttk.Combobox(row, textvariable=variable, values=values, state="readonly", width=9)
        combo.pack(side="right")
        if callback:
            combo.bind("<<ComboboxSelected>>", lambda _event: callback())

    def scale_row(self, parent, label, variable, minimum, maximum, formatter, callback=None) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=5)
        ttk.Label(row, text=label, style="Panel.TLabel", width=12).pack(side="left")
        value_label = ttk.Label(row, text=formatter(variable.get()), style="PanelMuted.TLabel", width=6, anchor="e")
        value_label.pack(side="right")
        def changed(value):
            value_label.configure(text=formatter(value))
            if callback:
                callback()

        scale = ttk.Scale(row, from_=minimum, to=maximum, variable=variable, command=changed)
        scale.pack(side="left", fill="x", expand=True, padx=7)

    def overlay_config(self) -> dict:
        cards = []
        for name, variable in (
            ("cpu", self.overlay_cpu_var),
            ("gpu", self.overlay_gpu_var),
            ("memory", self.overlay_memory_var),
            ("clock", self.overlay_clock_var),
        ):
            if variable.get():
                cards.append(name)
        return {
            "enabled": self.overlay_enabled_var.get(),
            "cycle_seconds": float(self.overlay_cycle_var.get()),
            "accent": self.overlay_accent_var.get(),
            "text": self.overlay_text_var.get(),
            "font_family": self.overlay_font_var.get(),
            "font_scale": round(self.overlay_font_scale_var.get()),
            "panel_opacity": round(self.overlay_opacity_var.get()),
            "panel_background": self.overlay_background_var.get(),
            "cards": cards or ["cpu"],
            "lhm_url": self.lhm_url_var.get().strip() or DEFAULT_LHM_URL,
        }

    def overlay_style(self) -> OverlayStyle:
        config = self.overlay_config()
        return OverlayStyle(
            accent=config["accent"],
            text=config["text"],
            font_family=config["font_family"],
            font_scale=config["font_scale"],
            panel_opacity=config["panel_opacity"],
            panel_background=config["panel_background"],
            cycle_seconds=config["cycle_seconds"],
            cards=tuple(config["cards"]),
        )

    def apply_overlay_config(self, config: dict) -> None:
        if not config:
            return
        self.overlay_enabled_var.set(bool(config.get("enabled", False)))
        self.overlay_cycle_var.set(str(config.get("cycle_seconds", "4")))
        self.overlay_accent_var.set(str(config.get("accent", "#20e39a")))
        self.overlay_text_var.set(str(config.get("text", "#ffffff")))
        self.overlay_font_var.set(str(config.get("font_family", "Segoe UI")))
        self.overlay_font_scale_var.set(int(config.get("font_scale", 100)))
        self.overlay_opacity_var.set(int(config.get("panel_opacity", 78)))
        self.overlay_background_var.set(bool(config.get("panel_background", True)))
        cards = set(config.get("cards", ("cpu", "gpu", "memory", "clock")))
        self.overlay_cpu_var.set("cpu" in cards)
        self.overlay_gpu_var.set("gpu" in cards)
        self.overlay_memory_var.set("memory" in cards)
        self.overlay_clock_var.set("clock" in cards)
        self.lhm_url_var.set(str(config.get("lhm_url", DEFAULT_LHM_URL)))
        if hasattr(self, "overlay_accent_button"):
            accent = self.overlay_accent_var.get()
            text_color = self.overlay_text_var.get()
            self.overlay_accent_button.configure(
                bg=accent,
                activebackground=accent,
                fg=self.contrasting_text(accent),
            )
            self.overlay_text_button.configure(
                bg=text_color,
                activebackground=text_color,
                fg=self.contrasting_text(text_color),
            )

    def overlay_changed(self) -> None:
        self.update_output_preview()

    def apply_overlay_settings(self) -> None:
        config = self.overlay_config()
        self.save_config()
        self.update_output_preview()
        if self.operation_kind != "playback":
            self.log("Overlay settings saved. They will be used the next time playback starts.")
            return

        running_with_overlay = self.active_overlay_renderer is not None
        if running_with_overlay != config["enabled"]:
            self.overlay_restart_pending = True
            self.stop_event.set()
            self.log("Applying overlay mode to the LCD…")
            return

        if self.active_overlay_renderer:
            self.active_overlay_renderer.style = self.overlay_style()
            if self.active_overlay_renderer.monitor:
                self.active_overlay_renderer.monitor.lhm_url = config["lhm_url"]
                self.active_overlay_renderer.monitor.lhm_next_poll = 0
        self.log("Overlay settings applied to the live LCD.")

    def choose_overlay_color(self, target: str) -> None:
        variable = self.overlay_accent_var if target == "accent" else self.overlay_text_var
        LiveColorPicker(
            self,
            f"Choose overlay {target} color",
            variable.get(),
            lambda color: self.preview_overlay_color(target, color),
            lambda color: self.accept_overlay_color(target, color),
        )

    @staticmethod
    def contrasting_text(color: str) -> str:
        red, green, blue = ImageColor.getrgb(color)
        luminance = (299 * red + 587 * green + 114 * blue) / 1000
        return "#000000" if luminance > 150 else "#ffffff"

    def preview_overlay_color(self, target: str, color: str) -> None:
        variable = self.overlay_accent_var if target == "accent" else self.overlay_text_var
        variable.set(color)
        button = self.overlay_accent_button if target == "accent" else self.overlay_text_button
        button.configure(bg=color, activebackground=color, fg=self.contrasting_text(color))
        self.update_output_preview()
        if self.active_overlay_renderer:
            self.active_overlay_renderer.style = self.overlay_style()

    def accept_overlay_color(self, target: str, color: str) -> None:
        self.preview_overlay_color(target, color)
        self.apply_overlay_settings()

    def reset_overlay_defaults(self) -> None:
        if not messagebox.askyesno(
            "Reset overlay defaults",
            "Reset colors, font, size, transparency, card selection, timing, and sensor URL?",
            parent=self,
        ):
            return
        defaults = {**DEFAULT_OVERLAY_VISUALS, "enabled": self.overlay_enabled_var.get()}
        self.apply_overlay_config(defaults)
        self.overlay_preview_card_var.set("CPU")
        self.apply_overlay_settings()

    def load_config(self) -> dict:
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def legacy_setup(self) -> dict | None:
        path = self.config_data.get("last_media", "")
        if not path:
            return None
        return {
            "media_path": path,
            "fps": self.config_data.get("fps", 8),
            "quality": self.config_data.get("quality", 85),
            "speed": self.config_data.get("speed", "1.0"),
            "rotation": self.config_data.get("rotation", "0"),
            "loop": self.config_data.get("loop", True),
        }

    def capture_setup(self) -> dict | None:
        if not self.media_info:
            return self.config_data.get("last_setup") or self.legacy_setup()
        crop = self.crop_canvas.crop
        return {
            "media_path": str(self.media_info.path),
            "crop": {"x": crop.x, "y": crop.y, "width": crop.width, "height": crop.height},
            "fps": round(self.fps_var.get()),
            "quality": round(self.quality_var.get()),
            "speed": self.speed_var.get(),
            "rotation": self.rotation_var.get(),
            "loop": self.loop_var.get(),
            "overlay": self.overlay_config(),
        }

    def save_config(self) -> None:
        setup = self.capture_setup()
        data = {
            "last_media": str(self.media_info.path) if self.media_info else "",
            "fps": round(self.fps_var.get()),
            "quality": round(self.quality_var.get()),
            "speed": self.speed_var.get(),
            "rotation": self.rotation_var.get(),
            "loop": self.loop_var.get(),
            "overlay": self.overlay_config(),
            "last_setup": setup,
            "resume_on_startup": self.resume_on_startup,
            "active_profile": self.profile_var.get(),
            "profiles": self.profiles,
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.config_data = data
        except OSError as exc:
            self.log(f"Could not save settings: {exc}")

    def apply_setup(self, setup: dict, auto_play: bool = False) -> None:
        path = resolve_media_path(str(setup.get("media_path", "")))
        setup = {**setup, "media_path": str(path)}
        if not path.is_file():
            self.show_error("Profile media missing", f"The saved media file no longer exists:\n{path}")
            return
        self.fps_var.set(float(setup.get("fps", 8)))
        self.quality_var.set(int(setup.get("quality", 85)))
        self.speed_var.set(str(setup.get("speed", "1.0")))
        self.rotation_var.set(str(setup.get("rotation", "0")))
        self.loop_var.set(bool(setup.get("loop", True)))
        self.apply_overlay_config(setup.get("overlay", self.config_data.get("overlay", {})))
        self.open_media(path, setup=setup, auto_play=auto_play)

    def refresh_profile_choices(self) -> None:
        self.profile_combo.configure(values=sorted(self.profiles))
        self.refresh_tray_menu()

    def save_profile(self) -> None:
        setup = self.capture_setup()
        if not self.media_info or not setup:
            messagebox.showinfo("Save profile", "Choose media and frame it before saving a profile.")
            return
        suggested = self.profile_var.get() or self.media_info.path.stem
        name = simpledialog.askstring("Save LCD profile", "Profile name:", initialvalue=suggested, parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        self.profiles[name] = setup
        self.profile_var.set(name)
        self.refresh_profile_choices()
        self.save_config()
        self.log(f"Saved profile: {name}")

    def load_selected_profile(self, auto_play: bool = False) -> None:
        name = self.profile_var.get()
        setup = self.profiles.get(name)
        if not setup:
            messagebox.showinfo("Load profile", "Choose a saved profile first.")
            return
        self.apply_setup(setup, auto_play=auto_play)
        self.log(f"Loading profile: {name}")

    def activate_profile(self, name: str) -> None:
        if name not in self.profiles:
            return
        self.profile_var.set(name)
        self.load_selected_profile(auto_play=True)

    def delete_profile(self) -> None:
        name = self.profile_var.get()
        if not name or name not in self.profiles:
            messagebox.showinfo("Delete profile", "Choose a saved profile first.")
            return
        if not messagebox.askyesno("Delete profile", f'Delete the saved profile "{name}"?'):
            return
        del self.profiles[name]
        self.profile_var.set("")
        self.refresh_profile_choices()
        self.save_config()
        self.log(f"Deleted profile: {name}")

    def post(self, callback, *args) -> None:
        self.ui_events.put((callback, args))

    def process_ui_events(self) -> None:
        try:
            while True:
                callback, args = self.ui_events.get_nowait()
                callback(*args)
        except queue.Empty:
            pass
        if not self.closing:
            self.after(50, self.process_ui_events)

    def start_instance_listener(self) -> None:
        if not self.instance_listener:
            return

        def listen() -> None:
            while not self.closing:
                try:
                    client, _address = self.instance_listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                with client:
                    if client.recv(64).strip().upper() == b"SHOW":
                        self.post(self.show_window)

        threading.Thread(target=listen, name="instance-listener", daemon=True).start()

    def build_tray_menu(self):
        if not HAS_TRAY:
            return None
        profile_items = [
            pystray.MenuItem(name, lambda _icon, _item, selected=name: self.post(self.activate_profile, selected))
            for name in sorted(self.profiles)
        ]
        if not profile_items:
            profile_items = [pystray.MenuItem("No saved profiles", None, enabled=False)]
        return pystray.Menu(
            pystray.MenuItem("Open Kraken LCD Studio", lambda _icon, _item: self.post(self.show_window), default=True),
            pystray.MenuItem("Saved profiles", pystray.Menu(*profile_items)),
            pystray.MenuItem("Play last setup", lambda _icon, _item: self.post(self.play_last_setup)),
            pystray.MenuItem("Stop LCD playback", lambda _icon, _item: self.post(self.stop_lcd_playback)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start hidden with Windows",
                lambda _icon, _item: self.post(self.toggle_startup, not is_startup_enabled()),
                checked=lambda _item: is_startup_enabled(),
            ),
            pystray.MenuItem("Exit", lambda _icon, _item: self.post(self.exit_app)),
        )

    def start_tray(self) -> None:
        if not HAS_TRAY:
            return
        self.tray_icon = pystray.Icon(
            "kraken_lcd_studio",
            tray_image(),
            "Kraken LCD Studio",
            self.build_tray_menu(),
        )
        threading.Thread(target=self.tray_icon.run, name="system-tray", daemon=True).start()

    def refresh_tray_menu(self) -> None:
        if self.tray_icon:
            self.tray_icon.menu = self.build_tray_menu()
            self.tray_icon.update_menu()

    def show_window(self) -> None:
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def play_last_setup(self) -> None:
        setup = self.capture_setup()
        if not setup:
            self.show_window()
            messagebox.showinfo("Play last setup", "Choose media or load a profile first.")
            return
        self.apply_setup(setup, auto_play=True)

    def toggle_startup(self, enabled: bool | None = None) -> None:
        desired = self.startup_var.get() if enabled is None else enabled
        try:
            set_startup_enabled(desired)
            self.startup_var.set(desired)
            self.refresh_tray_menu()
            self.log("Windows startup enabled." if desired else "Windows startup disabled.")
        except OSError as exc:
            self.startup_var.set(is_startup_enabled())
            self.show_error("Could not update Windows startup", str(exc))

    def log(self, message: str, persist: bool = True) -> None:
        stamp = time.strftime("%H:%M:%S")
        if persist:
            append_log_file(f"[{time.strftime('%Y-%m-%d')} {stamp}] {message}")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.status_var.set(message)

    def browse_media(self) -> None:
        initial = self.media_info.path.parent if self.media_info else APP_DIR
        selected = filedialog.askopenfilename(title="Choose media", initialdir=initial, filetypes=MEDIA_TYPES)
        if selected:
            self.open_media(Path(selected))

    def handle_drop(self, event):
        paths = self.tk.splitlist(event.data)
        if paths:
            self.open_media(Path(paths[0]))

    def open_media(self, path: Path, setup: dict | None = None, auto_play: bool = False) -> None:
        self.stop_work(user_requested=False)
        self.pending_setup = setup
        self.pending_auto_play = auto_play
        self.path_var.set(str(path))
        self.meta_var.set("Reading media…")
        self.log(f"Opening {path.name}")
        generation = self.preview_generation = self.preview_generation + 1
        rotation = int(self.rotation_var.get())

        def worker():
            try:
                info = probe_media(path)
                data = extract_preview(path, 0, rotation)
                image = Image.open(io.BytesIO(data)).convert("RGB")
                self.post(self.media_loaded, generation, info, image)
            except Exception as exc:
                self.post(self.show_error, "Could not open media", str(exc))

        threading.Thread(target=worker, name="media-probe", daemon=True).start()

    def media_loaded(self, generation: int, info: MediaInfo, image: Image.Image) -> None:
        if generation != self.preview_generation:
            return
        self.media_info = info
        export_label = "Export as MP4 or WebM…" if info.animated else "Export LCD-ready JPG…"
        webm_label = "Convert MP4 → WebM…" if info.path.suffix.lower() == ".mp4" else "Export video as WebM…"
        self.export_button.configure(text=export_label)
        self.webm_export_button.configure(text=webm_label)
        self.preview_image = image
        frames = f" • {info.frame_count} frames" if info.frame_count else ""
        self.meta_var.set(
            f"{info.display_dimensions} • {info.codec.upper()} • {format_duration(info.duration)}"
            f" • {info.fps:.2f} source FPS{frames} • {human_size(info.size)}"
        )
        self.time_scale.configure(to=max(info.duration, 0.1), state="normal" if info.animated else "disabled")
        self.time_var.set(0)
        self.time_label.configure(text="0:00.0")
        self.crop_size_var.set(100)
        self.crop_canvas.set_image(image, reset=True)
        setup = self.pending_setup
        self.pending_setup = None
        if setup and isinstance(setup.get("crop"), dict):
            saved_crop = setup["crop"]
            try:
                self.crop_canvas.crop = Crop(
                    float(saved_crop["x"]),
                    float(saved_crop["y"]),
                    float(saved_crop["width"]),
                    float(saved_crop["height"]),
                ).clamped()
                largest = maximum_crop(image.width, image.height)
                scale = min(
                    self.crop_canvas.crop.width / largest.width,
                    self.crop_canvas.crop.height / largest.height,
                )
                self.crop_size_var.set(max(25, min(100, scale * 100)))
                self.crop_canvas.redraw_overlay()
                self.crop_changed(self.crop_canvas.crop)
            except (KeyError, TypeError, ValueError):
                self.log("The saved crop was invalid; using the centered crop instead.")
        self.log("Media ready. Drag the green frame to compose the LCD output.")
        self.save_config()
        auto_play = self.pending_auto_play
        self.pending_auto_play = False
        if auto_play:
            self.after(100, self.start_playback_when_idle)

    def start_playback_when_idle(self) -> None:
        if self.closing:
            return
        if self.worker and self.worker.is_alive():
            self.after(150, self.start_playback_when_idle)
            return
        self.start_playback()

    def refresh_preview(self, reset_crop: bool = False) -> None:
        if not self.media_info:
            return
        generation = self.preview_generation = self.preview_generation + 1
        at = self.time_var.get()
        self.time_label.configure(text=format_duration(at))
        rotation = int(self.rotation_var.get())
        path = self.media_info.path

        def worker():
            try:
                data = extract_preview(path, at, rotation)
                image = Image.open(io.BytesIO(data)).convert("RGB")
                self.post(self.preview_loaded, generation, image, reset_crop)
            except Exception as exc:
                self.post(self.show_error, "Preview failed", str(exc))

        threading.Thread(target=worker, name="preview-render", daemon=True).start()

    def preview_loaded(self, generation: int, image: Image.Image, reset_crop: bool) -> None:
        if generation != self.preview_generation:
            return
        self.preview_image = image
        self.crop_canvas.set_image(image, reset=reset_crop)

    def rotation_changed(self) -> None:
        self.crop_size_var.set(100)
        self.refresh_preview(reset_crop=True)

    def center_crop(self) -> None:
        if not self.preview_image:
            return
        self.crop_canvas.crop = maximum_crop(self.preview_image.width, self.preview_image.height, self.crop_size_var.get() / 100)
        self.crop_canvas.redraw_overlay()
        self.crop_changed(self.crop_canvas.crop)

    def crop_changed(self, _crop: Crop) -> None:
        self.update_output_preview()

    def update_output_preview(self) -> None:
        image = self.crop_canvas.cropped_image()
        self.output_canvas.delete("all")
        if not image:
            self.output_canvas.create_text(self.output_canvas.winfo_width() / 2, 75, text="LCD output preview", fill=MUTED)
            return
        if self.overlay_enabled_var.get():
            preview_card = self.overlay_preview_card_var.get().lower()
            image = PerformanceOverlayRenderer(None, self.overlay_style()).render(image, forced_card=preview_card)
        width = max(self.output_canvas.winfo_width() - 20, 480)
        height = min(max(round(width / LCD_ASPECT), 128), 150)
        width = round(height * LCD_ASPECT)
        shown = image.resize((width, height), Image.Resampling.NEAREST)
        self.preview_photo = ImageTk.PhotoImage(shown)
        self.output_canvas.create_image(self.output_canvas.winfo_width() / 2, self.output_canvas.winfo_height() / 2, image=self.preview_photo)

    def settings(self):
        return (
            self.crop_canvas.crop,
            int(self.rotation_var.get()),
            max(1.0, float(self.fps_var.get())),
            int(self.quality_var.get()),
            float(self.speed_var.get()),
            bool(self.loop_var.get()),
        )

    def begin_work(self, message: str, kind: str = "other") -> bool:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Kraken LCD", "Another LCD or export operation is already running.")
            return False
        self.stop_event.clear()
        self.operation_kind = kind
        self.play_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.webm_export_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.log(message)
        return True

    def finish_work(self, message: str) -> None:
        self.play_button.configure(state="normal")
        self.export_button.configure(state="normal")
        self.webm_export_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.worker = None
        self.operation_kind = None
        self.log(message)
        if self.overlay_restart_pending:
            self.overlay_restart_pending = False
            self.after(150, self.start_playback_when_idle)

    def start_playback(self) -> None:
        if not self.media_info:
            self.browse_media()
            return
        if not self.begin_work("Connecting to the LCD…", kind="playback"):
            return
        self.resume_on_startup = True
        self.save_config()
        info = self.media_info
        crop, rotation, fps, quality, speed, loop = self.settings()
        overlay_config = self.overlay_config()
        overlay_style = self.overlay_style()

        def worker():
            monitor = None
            renderer = None
            try:
                frame_transform = None
                if overlay_config["enabled"]:
                    monitor = MetricsMonitor(lhm_url=overlay_config["lhm_url"])
                    monitor.start()
                    renderer = PerformanceOverlayRenderer(monitor, overlay_style)
                    self.active_overlay_renderer = renderer
                    frame_transform = lambda jpeg: renderer.render_jpeg(jpeg, quality)
                with ThermaltakeLcd() as lcd:
                    self.last_error_notice = None
                    overlay_status = " with rotating performance cards" if frame_transform else ""
                    self.post(self.log, f"Connected. Streaming at {fps:.0f} FPS{overlay_status}…")
                    frames = stream_media(
                        lcd,
                        info,
                        crop,
                        rotation,
                        fps,
                        quality,
                        speed,
                        loop,
                        self.stop_event,
                        lambda text: self.post(self.log, text, False),
                        frame_transform=frame_transform,
                    )
                self.post(self.finish_work, f"Stopped cleanly after {frames} frame{'s' if frames != 1 else ''}.")
            except Exception as exc:
                self.post(self.work_failed, exc)
            finally:
                if self.active_overlay_renderer is renderer:
                    self.active_overlay_renderer = None
                if monitor:
                    monitor.stop()

        self.worker = threading.Thread(target=worker, name="lcd-stream", daemon=True)
        self.worker.start()

    def send_current_frame(self) -> None:
        if not self.media_info or not self.begin_work("Rendering the selected frame…"):
            return
        info = self.media_info
        crop, rotation, _fps, quality, _speed, _loop = self.settings()
        at = self.time_var.get()
        overlay_config = self.overlay_config()
        overlay_style = self.overlay_style()

        def worker():
            monitor = None
            try:
                jpeg = render_lcd_jpeg(info.path, crop, rotation, at, quality)
                if overlay_config["enabled"]:
                    monitor = MetricsMonitor(lhm_url=overlay_config["lhm_url"])
                    monitor.start()
                    self.stop_event.wait(1.1)
                    jpeg = PerformanceOverlayRenderer(monitor, overlay_style).render_jpeg(jpeg, quality)
                with ThermaltakeLcd() as lcd:
                    lcd.send_jpeg(jpeg, log_frame=False)
                self.post(self.finish_work, "Selected frame sent. It will remain after the app stops.")
            except Exception as exc:
                self.post(self.work_failed, exc)
            finally:
                if monitor:
                    monitor.stop()

        self.worker = threading.Thread(target=worker, name="lcd-still", daemon=True)
        self.worker.start()

    def send_test_card(self) -> None:
        if not self.begin_work("Sending USB test card…"):
            return

        def worker():
            try:
                with ThermaltakeLcd() as lcd:
                    lcd.send_jpeg(make_test_frame(), log_frame=False)
                self.post(self.finish_work, "Test card acknowledged by the LCD.")
            except Exception as exc:
                self.post(self.work_failed, exc)

        self.worker = threading.Thread(target=worker, name="lcd-test", daemon=True)
        self.worker.start()

    def export_webm(self) -> None:
        if not self.media_info:
            self.browse_media()
            return
        if not self.media_info.animated:
            messagebox.showinfo("Export WebM", "Choose a video or animated file to export as WebM.")
            return
        self.export_dialog(".webm")

    def choose_video_export_format(self) -> str | None:
        choice: list[str | None] = [None]
        dialog = tk.Toplevel(self)
        dialog.title("Choose export format")
        dialog.configure(bg=PANEL)
        dialog.resizable(False, False)
        dialog.transient(self)

        content = ttk.Frame(dialog, style="Panel.TFrame", padding=22)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="CHOOSE VIDEO FORMAT", style="Panel.TLabel", font=("Segoe UI Semibold", 13)).pack(anchor="w")
        ttk.Label(
            content,
            text="Both options crop and resize the video to the LCD's exact 480×128 resolution.",
            style="PanelMuted.TLabel",
            wraplength=410,
            justify="left",
        ).pack(anchor="w", pady=(5, 16))

        def select(extension: str) -> None:
            choice[0] = extension
            dialog.destroy()

        ttk.Button(
            content,
            text="MP4 / H.264  —  faster and broadly compatible",
            style="Blue.TButton",
            command=lambda: select(".mp4"),
        ).pack(fill="x", pady=(0, 8))
        ttk.Button(
            content,
            text="WebM / VP9  —  convert to a smaller WebM file",
            style="Blue.TButton",
            command=lambda: select(".webm"),
        ).pack(fill="x", pady=(0, 8))
        ttk.Button(content, text="Cancel", command=dialog.destroy).pack(fill="x")

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_reqheight()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        dialog.focus_force()
        self.wait_window(dialog)
        return choice[0]

    def export_dialog(self, requested_extension: str | None = None) -> None:
        if not self.media_info:
            self.browse_media()
            return
        if self.media_info.animated and requested_extension is None:
            requested_extension = self.choose_video_export_format()
            if requested_extension is None:
                return
        if requested_extension == ".webm":
            types = [("VP9 WebM", "*.webm")]
            default_ext = ".webm"
            title = "Convert to WebM — exact 480×128 output"
        elif requested_extension == ".mp4":
            types = [("H.264 MP4", "*.mp4")]
            default_ext = ".mp4"
            title = "Export to MP4 — exact 480×128 output"
        else:
            types = [("Baseline JPEG", "*.jpg")]
            default_ext = ".jpg"
            title = "Export exact 480×128 image"
        default_name = f"{self.media_info.path.stem}_lcd{default_ext}"
        destination = filedialog.asksaveasfilename(
            title=title,
            initialdir=self.media_info.path.parent,
            initialfile=default_name,
            defaultextension=default_ext,
            filetypes=types,
        )
        if not destination or not self.begin_work(f"Exporting {Path(destination).name}…"):
            return
        info = self.media_info
        crop, rotation, fps, quality, speed, _loop = self.settings()

        def worker():
            try:
                export_media(info, Path(destination), crop, rotation, fps, quality, speed, self.stop_event)
                self.post(self.finish_work, f"Export complete: {destination}")
            except Exception as exc:
                self.post(self.work_failed, exc)

        self.worker = threading.Thread(target=worker, name="media-export", daemon=True)
        self.worker.start()

    def stop_work(self, user_requested: bool = True) -> None:
        if user_requested and self.operation_kind == "playback":
            self.resume_on_startup = False
            self.save_config()
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.status_var.set("Stopping cleanly…")

    def stop_lcd_playback(self) -> None:
        self.resume_on_startup = False
        self.save_config()
        self.stop_work(user_requested=False)
        self.log("Automatic LCD restore stopped. It will remain off at the next sign-in.")

    def work_failed(self, exc: Exception) -> None:
        failed_kind = self.operation_kind
        self.play_button.configure(state="normal")
        self.export_button.configure(state="normal")
        self.webm_export_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.worker = None
        self.operation_kind = None
        self.overlay_restart_pending = False
        append_log_file("".join(traceback.format_exception(exc)))
        self.log(f"Error: {exc}")
        if self.state() == "withdrawn":
            if self.tray_icon and str(exc) != self.last_error_notice:
                self.tray_icon.notify(str(exc), "Kraken LCD playback error")
            self.last_error_notice = str(exc)
            # A busy or missing panel is resumed by poll_device once it is ready again.
            device_ready = self.device_state == STATE_READY and not isinstance(exc, LcdBusyError)
            if failed_kind == "playback" and device_ready and self.resume_on_startup and not self.closing:
                self.after(15000, self.resume_playback)
        else:
            messagebox.showerror("Kraken LCD", str(exc))

    def resume_playback(self) -> None:
        """Restart the remembered LCD stream once the panel can be reached again."""
        if self.closing or not self.resume_on_startup or not self.media_info:
            return
        if self.worker and self.worker.is_alive():
            return
        self.start_playback()

    def report_callback_exception(self, exc_type, exc, tb) -> None:
        append_log_file("".join(traceback.format_exception(exc_type, exc, tb)))
        self.log(f"Unexpected error: {exc}")

    def show_error(self, title: str, message: str) -> None:
        self.log(f"{title}: {message}")
        if self.state() == "withdrawn" and self.tray_icon:
            self.tray_icon.notify(message, title)
        else:
            messagebox.showerror(title, message)

    def poll_device(self) -> None:
        def worker():
            try:
                state, serial = ThermaltakeLcd.status()
            except Exception:
                state, serial = STATE_MISSING, ""
            self.post(self.device_result, state, serial)

        threading.Thread(target=worker, name="device-poll", daemon=True).start()
        if not self.closing:
            self.after(4000, self.poll_device)

    def device_result(self, state: str, serial: str) -> None:
        previous = self.device_state
        self.device_state = state
        if state == STATE_READY:
            self.device_var.set(f"● LCD CONNECTED  {serial}")
            self.device_label.configure(bg="#18251f", fg=ACCENT)
        elif state == STATE_BUSY:
            self.device_var.set("● LCD IN USE BY ANOTHER PROGRAM")
            self.device_label.configure(bg="#2b261c", fg=WARN)
        else:
            self.device_var.set("● LCD NOT FOUND")
            self.device_label.configure(bg="#2b1c1f", fg=ERROR)

        if state == previous:
            return
        if state == STATE_BUSY:
            self.log(BUSY_MESSAGE)
        elif state == STATE_MISSING:
            self.log("The LCD is not connected to USB.")
        elif previous is not None:
            self.log("The LCD is available again.")
            self.resume_playback()

    def request_close(self) -> None:
        self.save_config()
        if self.tray_icon:
            self.withdraw()
            if not self.tray_notice_shown:
                self.tray_icon.notify(
                    "LCD playback continues in the background. Double-click the tray icon to reopen the Studio.",
                    "Kraken LCD Studio is still running",
                )
                self.tray_notice_shown = True
            return
        self.exit_app()

    def exit_app(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.save_config()
        self.stop_event.set()
        if self.instance_listener:
            self.instance_listener.close()
            self.instance_listener = None
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        deadline = time.monotonic() + 6

        def wait_for_worker():
            if self.worker and self.worker.is_alive() and time.monotonic() < deadline:
                self.after(100, wait_for_worker)
            else:
                self.destroy()

        wait_for_worker()


def main() -> None:
    background = "--background" in sys.argv[1:]
    listener = acquire_single_instance(show_existing=not background)
    if listener is None:
        return
    app = KrakenLcdApp(background=background, instance_listener=listener)
    app.mainloop()


if __name__ == "__main__":
    main()
