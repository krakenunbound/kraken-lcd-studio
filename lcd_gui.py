"""Kraken LCD visual cropper and native Thermaltake panel controller.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import io
import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

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
from thermaltake_lcd import LcdError, ThermaltakeLcd, make_test_frame

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    RootBase = TkinterDnD.Tk
    HAS_DROP = True
except ImportError:
    DND_FILES = None
    RootBase = tk.Tk
    HAS_DROP = False


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "lcd_gui_config.json"
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
ACCENT_2 = "#16a8ff"
WARN = "#ffbc57"
ERROR = "#ff657a"


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
    def __init__(self):
        super().__init__()
        self.title(f"Kraken LCD Studio {VERSION}")
        self.geometry("1380x900")
        self.minsize(1120, 760)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.close_app)

        self.config_data = self.load_config()
        self.media_info: MediaInfo | None = None
        self.preview_image: Image.Image | None = None
        self.preview_photo = None
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.ui_events: queue.Queue[tuple] = queue.Queue()
        self.preview_generation = 0
        self.closing = False

        self.path_var = tk.StringVar(value="")
        self.device_var = tk.StringVar(value="Checking USB panel…")
        self.meta_var = tk.StringVar(value="Choose an image, animation, or video to begin.")
        self.status_var = tk.StringVar(value="Ready")
        self.fps_var = tk.DoubleVar(value=float(self.config_data.get("fps", 8)))
        self.quality_var = tk.IntVar(value=int(self.config_data.get("quality", 85)))
        self.speed_var = tk.StringVar(value=str(self.config_data.get("speed", "1.0")))
        self.rotation_var = tk.StringVar(value=str(self.config_data.get("rotation", "0")))
        self.loop_var = tk.BooleanVar(value=bool(self.config_data.get("loop", True)))
        self.crop_size_var = tk.DoubleVar(value=100)
        self.time_var = tk.DoubleVar(value=0)

        self.configure_styles()
        self.build_ui()
        self.after(50, self.process_ui_events)
        self.after(150, self.poll_device)

        last_path = self.config_data.get("last_media")
        if last_path and Path(last_path).is_file():
            self.after(300, lambda: self.open_media(Path(last_path)))

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
        style.configure("Blue.TButton", background=ACCENT_2, foreground="#041019", padding=(14, 9), font=("Segoe UI Semibold", 10))
        style.map("Blue.TButton", background=[("active", "#53bfff")])
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
        activity_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=12)
        notebook.add(output_tab, text="OUTPUT")
        notebook.add(activity_tab, text="ACTIVITY")

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
        self.export_button = ttk.Button(output_tab, text="Export LCD-ready file…", style="Blue.TButton", command=self.export_dialog)
        self.export_button.pack(fill="x", pady=7)
        self.stop_button = ttk.Button(output_tab, text="■  STOP / CANCEL", command=self.stop_work, state="disabled")
        self.stop_button.pack(fill="x", pady=(0, 10))
        ttk.Label(output_tab, text="MP4: compact and fast to decode\nWebM/VP9: often smaller, slower to encode/decode\nJPG: exact static format sent by the panel", style="PanelMuted.TLabel", justify="left").pack(fill="x", pady=(5, 0))

        ttk.Label(activity_tab, text="STATUS", style="PanelMuted.TLabel").pack(anchor="w")
        ttk.Label(activity_tab, textvariable=self.status_var, style="Panel.TLabel", wraplength=300, justify="left").pack(fill="x", pady=(5, 12))
        self.log_text = tk.Text(activity_tab, bg="#090e13", fg="#bcd0dc", insertbackground=TEXT, relief="flat", wrap="word", font=("Consolas", 9), padx=10, pady=10, state="disabled")
        self.log_text.pack(fill="both", expand=True)

        footer = ttk.Frame(self, padding=(24, 0, 24, 14))
        footer.pack(fill="x")
        drop_text = "Explorer drag-and-drop enabled" if HAS_DROP else "Install tkinterdnd2 to enable Explorer drag-and-drop"
        ttk.Label(footer, text=drop_text, style="Muted.TLabel").pack(side="left")
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

    def scale_row(self, parent, label, variable, minimum, maximum, formatter) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=5)
        ttk.Label(row, text=label, style="Panel.TLabel", width=12).pack(side="left")
        value_label = ttk.Label(row, text=formatter(variable.get()), style="PanelMuted.TLabel", width=6, anchor="e")
        value_label.pack(side="right")
        scale = ttk.Scale(row, from_=minimum, to=maximum, variable=variable, command=lambda value: value_label.configure(text=formatter(value)))
        scale.pack(side="left", fill="x", expand=True, padx=7)

    def load_config(self) -> dict:
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_config(self) -> None:
        data = {
            "last_media": str(self.media_info.path) if self.media_info else "",
            "fps": round(self.fps_var.get()),
            "quality": round(self.quality_var.get()),
            "speed": self.speed_var.get(),
            "rotation": self.rotation_var.get(),
            "loop": self.loop_var.get(),
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            self.log(f"Could not save settings: {exc}")

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

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
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

    def open_media(self, path: Path) -> None:
        self.stop_work()
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
        self.log("Media ready. Drag the green frame to compose the LCD output.")

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

    def begin_work(self, message: str) -> bool:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Kraken LCD", "Another LCD or export operation is already running.")
            return False
        self.stop_event.clear()
        self.play_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.log(message)
        return True

    def finish_work(self, message: str) -> None:
        self.play_button.configure(state="normal")
        self.export_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.worker = None
        self.log(message)

    def start_playback(self) -> None:
        if not self.media_info:
            self.browse_media()
            return
        if not self.begin_work("Connecting to the LCD…"):
            return
        info = self.media_info
        crop, rotation, fps, quality, speed, loop = self.settings()

        def worker():
            try:
                with ThermaltakeLcd() as lcd:
                    self.post(self.log, f"Connected. Streaming at {fps:.0f} FPS…")
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
                        lambda text: self.post(self.log, text),
                    )
                self.post(self.finish_work, f"Stopped cleanly after {frames} frame{'s' if frames != 1 else ''}.")
            except Exception as exc:
                self.post(self.work_failed, exc)

        self.worker = threading.Thread(target=worker, name="lcd-stream", daemon=True)
        self.worker.start()

    def send_current_frame(self) -> None:
        if not self.media_info or not self.begin_work("Rendering the selected frame…"):
            return
        info = self.media_info
        crop, rotation, _fps, quality, _speed, _loop = self.settings()
        at = self.time_var.get()

        def worker():
            try:
                jpeg = render_lcd_jpeg(info.path, crop, rotation, at, quality)
                with ThermaltakeLcd() as lcd:
                    lcd.send_jpeg(jpeg, log_frame=False)
                self.post(self.finish_work, "Selected frame sent. It will remain after the app stops.")
            except Exception as exc:
                self.post(self.work_failed, exc)

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

    def export_dialog(self) -> None:
        if not self.media_info:
            self.browse_media()
            return
        if self.media_info.animated:
            types = [("H.264 MP4", "*.mp4"), ("VP9 WebM", "*.webm")]
            default_ext = ".mp4"
        else:
            types = [("Baseline JPEG", "*.jpg")]
            default_ext = ".jpg"
        default_name = f"{self.media_info.path.stem}_lcd{default_ext}"
        destination = filedialog.asksaveasfilename(
            title="Export exact 480×128 media",
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

    def stop_work(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.status_var.set("Stopping cleanly…")

    def work_failed(self, exc: Exception) -> None:
        self.play_button.configure(state="normal")
        self.export_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.worker = None
        self.log(f"Error: {exc}")
        messagebox.showerror("Kraken LCD", str(exc))

    def show_error(self, title: str, message: str) -> None:
        self.log(f"{title}: {message}")
        messagebox.showerror(title, message)

    def poll_device(self) -> None:
        def worker():
            try:
                devices = ThermaltakeLcd.discover()
                connected = len(devices) == 2
                serial = devices[0].serial_number if connected else ""
                self.post(self.device_result, connected, serial)
            except Exception:
                self.post(self.device_result, False, "")

        threading.Thread(target=worker, name="device-poll", daemon=True).start()
        if not self.closing:
            self.after(4000, self.poll_device)

    def device_result(self, connected: bool, serial: str) -> None:
        if connected:
            self.device_var.set(f"● LCD CONNECTED  {serial}")
            self.device_label.configure(bg="#18251f", fg=ACCENT)
        else:
            self.device_var.set("● LCD NOT FOUND")
            self.device_label.configure(bg="#2b1c1f", fg=ERROR)

    def close_app(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.save_config()
        self.stop_event.set()
        deadline = time.monotonic() + 6

        def wait_for_worker():
            if self.worker and self.worker.is_alive() and time.monotonic() < deadline:
                self.after(100, wait_for_worker)
            else:
                self.destroy()

        wait_for_worker()


def main() -> None:
    app = KrakenLcdApp()
    app.mainloop()


if __name__ == "__main__":
    main()
