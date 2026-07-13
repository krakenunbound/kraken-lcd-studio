"""System metrics collection and 480x128 performance overlay rendering."""

from __future__ import annotations

import io
import json
import re
import subprocess
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

try:
    import psutil
except ImportError:  # The GUI remains usable before the installer is rerun.
    psutil = None


LCD_SIZE = (480, 128)
DEFAULT_LHM_URL = "http://localhost:8085/data.json"
FONT_FILES = {
    "Segoe UI": "segoeui.ttf",
    "Segoe UI Semibold": "seguisb.ttf",
    "Consolas": "consola.ttf",
    "Bahnschrift": "bahnschrift.ttf",
}


@dataclass(frozen=True)
class MetricSnapshot:
    timestamp: float = 0.0
    cpu_percent: float | None = None
    cpu_temp: float | None = None
    cpu_ghz: float | None = None
    memory_percent: float | None = None
    memory_used_gb: float | None = None
    memory_total_gb: float | None = None
    memory_speed_mts: float | None = None
    gpu_percent: float | None = None
    gpu_temp: float | None = None
    gpu_clock_mhz: float | None = None
    vram_used_gb: float | None = None
    vram_total_gb: float | None = None
    gpu_name: str = "GPU"
    cpu_temp_source: str = ""
    gpu_source: str = ""


@dataclass(frozen=True)
class OverlayStyle:
    accent: str = "#20e39a"
    text: str = "#ffffff"
    font_family: str = "Segoe UI"
    font_scale: int = 100
    panel_opacity: int = 78
    panel_background: bool = True
    cycle_seconds: float = 4.0
    cards: tuple[str, ...] = ("cpu", "gpu", "memory", "clock")


def _number(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def _flatten_lhm(node, path: tuple[str, ...] = ()) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    if isinstance(node, dict):
        label = str(node.get("Text") or node.get("Name") or node.get("SensorType") or "")
        next_path = path + ((label,) if label else ())
        value = _number(node.get("Value"))
        if value is not None and label:
            found.append((" / ".join(next_path).lower(), value))
        for key in ("Children", "Sensors", "Sensor", "Items", "Nodes"):
            children = node.get(key)
            if isinstance(children, list):
                for child in children:
                    found.extend(_flatten_lhm(child, next_path))
    elif isinstance(node, list):
        for child in node:
            found.extend(_flatten_lhm(child, path))
    return found


def read_lhm(url: str = DEFAULT_LHM_URL, timeout: float = 0.35) -> dict[str, float]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            values = _flatten_lhm(json.load(response))
    except Exception:
        return {}

    def pick(required: tuple[str, ...], preferred: tuple[str, ...] = ()) -> float | None:
        matches = [(label, value) for label, value in values if all(word in label for word in required)]
        if not matches:
            return None
        for word in preferred:
            preferred_matches = [value for label, value in matches if word in label]
            if preferred_matches:
                return max(preferred_matches)
        return max(value for _label, value in matches)

    result = {
        "cpu_temp": pick(("cpu", "temperature"), ("package", "core max")),
        "gpu_percent": pick(("gpu", "load"), ("core", "d3d")),
        "gpu_temp": pick(("gpu", "temperature"), ("core",)),
        "gpu_clock_mhz": pick(("gpu", "clock"), ("core",)),
    }
    return {key: value for key, value in result.items() if value is not None}


def read_nvidia_smi(timeout: float = 1.5) -> dict[str, float | str]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,temperature.gpu,clocks.current.graphics,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=True,
        )
        values = [part.strip() for part in result.stdout.splitlines()[0].split(",")]
        return {
            "gpu_name": values[0],
            "gpu_percent": float(values[1]),
            "gpu_temp": float(values[2]),
            "gpu_clock_mhz": float(values[3]),
            "vram_used_gb": float(values[4]) / 1024,
            "vram_total_gb": float(values[5]) / 1024,
        }
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return {}


def read_memory_speed(timeout: float = 2.5) -> float | None:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "(Get-CimInstance Win32_PhysicalMemory | Measure-Object ConfiguredClockSpeed -Maximum).Maximum",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=True,
        )
        return _number(result.stdout)
    except (OSError, subprocess.SubprocessError):
        return None


class MetricsMonitor:
    def __init__(self, interval: float = 1.0, lhm_url: str = DEFAULT_LHM_URL):
        self.interval = max(0.5, interval)
        self.lhm_url = lhm_url
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.snapshot = MetricSnapshot(timestamp=time.time())
        self.history = {
            "cpu": deque(maxlen=72),
            "gpu": deque(maxlen=72),
            "memory": deque(maxlen=72),
        }
        self.memory_speed = read_memory_speed()
        self.lhm_cache: dict[str, float] = {}
        self.lhm_next_poll = 0.0
        self.gpu_cache: dict[str, float | str] = {}
        self.gpu_next_poll = 0.0
        self.thread: threading.Thread | None = None
        if psutil:
            psutil.cpu_percent(None)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="metrics-monitor", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.thread = None

    def _collect(self) -> MetricSnapshot:
        values: dict = {"timestamp": time.time(), "memory_speed_mts": self.memory_speed}
        if psutil:
            values["cpu_percent"] = psutil.cpu_percent(None)
            frequency = psutil.cpu_freq()
            values["cpu_ghz"] = frequency.current / 1000 if frequency and frequency.current else None
            memory = psutil.virtual_memory()
            values.update(
                memory_percent=memory.percent,
                memory_used_gb=memory.used / (1024**3),
                memory_total_gb=memory.total / (1024**3),
            )
        monotonic_now = time.monotonic()
        if monotonic_now >= self.lhm_next_poll:
            self.lhm_cache = read_lhm(self.lhm_url)
            self.lhm_next_poll = monotonic_now + (2.0 if self.lhm_cache else 10.0)
        lhm = self.lhm_cache
        if "cpu_temp" in lhm:
            values["cpu_temp"] = lhm["cpu_temp"]
            values["cpu_temp_source"] = "LibreHardwareMonitor"
        if monotonic_now >= self.gpu_next_poll:
            self.gpu_cache = read_nvidia_smi()
            self.gpu_next_poll = monotonic_now + 2.0
        gpu = self.gpu_cache
        if gpu:
            values.update(gpu)
            values["gpu_source"] = "NVIDIA"
        else:
            values.update({key: value for key, value in lhm.items() if key.startswith("gpu_")})
            if any(key.startswith("gpu_") for key in lhm):
                values["gpu_source"] = "LibreHardwareMonitor"
        return MetricSnapshot(**values)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            snapshot = self._collect()
            with self.lock:
                self.snapshot = snapshot
                self.history["cpu"].append(snapshot.cpu_percent)
                self.history["gpu"].append(snapshot.gpu_percent)
                self.history["memory"].append(snapshot.memory_percent)
            self.stop_event.wait(self.interval)

    def current(self) -> tuple[MetricSnapshot, dict[str, list[float | None]]]:
        with self.lock:
            return self.snapshot, {key: list(values) for key, values in self.history.items()}


def demo_snapshot() -> MetricSnapshot:
    return MetricSnapshot(
        timestamp=time.time(),
        cpu_percent=31,
        cpu_temp=54,
        cpu_ghz=4.8,
        memory_percent=42,
        memory_used_gb=27.1,
        memory_total_gb=64,
        memory_speed_mts=6000,
        gpu_percent=9,
        gpu_temp=65,
        gpu_clock_mhz=1860,
        vram_used_gb=1.8,
        vram_total_gb=24,
        gpu_name="RTX 3090",
    )


class PerformanceOverlayRenderer:
    def __init__(self, monitor: MetricsMonitor | None, style: OverlayStyle, started: float | None = None):
        self.monitor = monitor
        self.style = style
        self.started = started or time.monotonic()
        self.font_cache: dict[tuple[str, int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def card_at(self, elapsed: float) -> str:
        cards = self.style.cards or ("cpu",)
        return cards[int(max(0, elapsed) / max(1, self.style.cycle_seconds)) % len(cards)]

    def _font(self, size: int, bold: bool = False):
        scaled = max(8, round(size * max(60, min(150, self.style.font_scale)) / 100))
        key = (self.style.font_family, scaled, bold)
        if key in self.font_cache:
            return self.font_cache[key]
        filename = FONT_FILES.get(self.style.font_family, "segoeui.ttf")
        if bold and filename == "segoeui.ttf":
            filename = "seguisb.ttf"
        path = Path("C:/Windows/Fonts") / filename
        try:
            font = ImageFont.truetype(str(path), scaled)
        except OSError:
            font = ImageFont.load_default(size=scaled)
        self.font_cache[key] = font
        return font

    @staticmethod
    def _fmt(value: float | None, suffix: str = "", decimals: int = 0) -> str:
        return "--" if value is None else f"{value:.{decimals}f}{suffix}"

    def _data(self):
        if self.monitor:
            return self.monitor.current()
        wave = [18, 25, 22, 31, 38, 33, 41, 36, 29, 31]
        return demo_snapshot(), {"cpu": wave, "gpu": [5, 8, 12, 9, 7, 14, 11, 9], "memory": [38, 39, 40, 40, 41, 42]}

    def render(self, frame: Image.Image, now: float | None = None, forced_card: str | None = None) -> Image.Image:
        base = frame.convert("RGBA").resize(LCD_SIZE, Image.Resampling.LANCZOS)
        snapshot, history = self._data()
        elapsed = (now if now is not None else time.monotonic()) - self.started
        card = forced_card or self.card_at(elapsed)
        accent = ImageColor.getrgb(self.style.accent)
        text = ImageColor.getrgb(self.style.text)
        panel_alpha = round(255 * max(0, min(100, self.style.panel_opacity)) / 100)

        layer = Image.new("RGBA", LCD_SIZE, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        if self.style.panel_background and panel_alpha:
            draw.rounded_rectangle(
                (7, 7, 473, 121),
                radius=13,
                fill=(5, 10, 15, panel_alpha),
                outline=(*accent, 210),
                width=1,
            )
        draw.rounded_rectangle((7, 7, 12, 121), radius=3, fill=(*accent, 255))
        draw.ellipse((24, 19, 32, 27), fill=(*accent, 255))

        clock_now = datetime.now()
        if card == "clock":
            label, value, subtitle = "LOCAL TIME", clock_now.strftime("%H:%M"), clock_now.strftime("%A").upper()
            stat_a, stat_b = clock_now.strftime("%b %d"), clock_now.strftime("%Y")
            stat_a_label, stat_b_label = "DATE", "YEAR"
            footer = clock_now.astimezone().tzname() or "LOCAL"
        elif card == "gpu":
            label, value, subtitle = "GPU", self._fmt(snapshot.gpu_percent, "%"), snapshot.gpu_name.replace("NVIDIA GeForce ", "")
            stat_a, stat_b = self._fmt(snapshot.gpu_temp, "°C"), self._fmt(snapshot.gpu_clock_mhz, " MHz")
            stat_a_label, stat_b_label = "TEMP", "CLOCK"
            footer = f"VRAM {self._fmt(snapshot.vram_used_gb, '', 1)} / {self._fmt(snapshot.vram_total_gb, ' GB', 0)}"
        elif card == "memory":
            label, value, subtitle = "MEMORY", self._fmt(snapshot.memory_percent, "%"), "SYSTEM RAM"
            stat_a = self._fmt(snapshot.memory_speed_mts, " MT/s")
            stat_b = f"{self._fmt(snapshot.memory_used_gb, '', 1)} / {self._fmt(snapshot.memory_total_gb, ' GB', 0)}"
            stat_a_label, stat_b_label = "SPEED", "USED"
            footer = "PHYSICAL MEMORY"
        else:
            label, value, subtitle = "CPU", self._fmt(snapshot.cpu_percent, "%"), "TOTAL LOAD"
            stat_a, stat_b = self._fmt(snapshot.cpu_temp, "°C"), self._fmt(snapshot.cpu_ghz, " GHz", 1)
            stat_a_label, stat_b_label = "TEMP", "CLOCK"
            footer = "CPU TEMP NEEDS LIBREHARDWAREMONITOR" if snapshot.cpu_temp is None else "PACKAGE SENSOR"

        shadow = {"stroke_width": 2, "stroke_fill": (0, 0, 0, 210)} if not self.style.panel_background else {}
        draw.text((39, 14), label, font=self._font(14, True), fill=(*accent, 255), **shadow)
        draw.text((19, 35), value, font=self._font(39, True), fill=(*text, 255), stroke_width=1, stroke_fill=(0, 0, 0, 180))
        draw.text((23, 89), subtitle[:20], font=self._font(10, True), fill=(*text, 220), **shadow)

        draw.text((184, 17), stat_a_label, font=self._font(9, True), fill=(*text, 210), **shadow)
        draw.text((184, 30), stat_a, font=self._font(16, True), fill=(*text, 255), **shadow)
        draw.text((326, 17), stat_b_label, font=self._font(9, True), fill=(*text, 210), **shadow)
        draw.text((326, 30), stat_b, font=self._font(15, True), fill=(*text, 255), **shadow)

        graph_box = (184, 62, 457, 98)
        if self.style.panel_background:
            draw.rounded_rectangle(graph_box, radius=5, fill=(255, 255, 255, 18), outline=(255, 255, 255, 35))
        if card == "clock":
            draw.text((197, 68), clock_now.strftime("%A · %B %d").upper(), font=self._font(14, True), fill=(*accent, 240))
        else:
            samples = [value for value in history.get(card, []) if value is not None][-48:]
            if len(samples) >= 2:
                x1, y1, x2, y2 = graph_box
                points = []
                for index, sample in enumerate(samples):
                    x = x1 + 5 + index * (x2 - x1 - 10) / max(1, len(samples) - 1)
                    y = y2 - 5 - max(0, min(100, sample)) * (y2 - y1 - 10) / 100
                    points.append((x, y))
                fill_points = [(points[0][0], y2 - 4), *points, (points[-1][0], y2 - 4)]
                draw.polygon(fill_points, fill=(*accent, 45))
                draw.line(points, fill=(*accent, 245), width=3, joint="curve")
        draw.text((184, 102), footer[:42], font=self._font(8, True), fill=(*text, 190), **shadow)

        return Image.alpha_composite(base, layer).convert("RGB")

    def render_jpeg(self, jpeg: bytes, quality: int = 85) -> bytes:
        frame = Image.open(io.BytesIO(jpeg)).convert("RGB")
        rendered = self.render(frame)
        output = io.BytesIO()
        rendered.save(output, format="JPEG", quality=quality, optimize=True, progressive=False)
        return output.getvalue()
