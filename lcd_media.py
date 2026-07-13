"""Media probing, visual-crop filters, LCD streaming, and export helpers.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from thermaltake_lcd import ThermaltakeLcd


LCD_WIDTH = 480
LCD_HEIGHT = 128
LCD_ASPECT = LCD_WIDTH / LCD_HEIGHT
STILL_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}
MEDIA_EXTENSIONS = {
    *STILL_EXTENSIONS,
    ".avi",
    ".gif",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".webm",
    ".webp",
}


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    width: int
    height: int
    duration: float
    fps: float
    codec: str
    frame_count: int | None
    size: int
    animated: bool

    @property
    def display_dimensions(self) -> str:
        return f"{self.width} × {self.height}"


@dataclass(frozen=True)
class Crop:
    """Normalized crop rectangle in the rotated source coordinate system."""

    x: float
    y: float
    width: float
    height: float

    def clamped(self) -> "Crop":
        width = min(max(self.width, 0.001), 1.0)
        height = min(max(self.height, 0.001), 1.0)
        x = min(max(self.x, 0.0), 1.0 - width)
        y = min(max(self.y, 0.0), 1.0 - height)
        return Crop(x, y, width, height)


def _run(command: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            creationflags=flags,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"Required program not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"Timed out while running {command[0]}") from exc


def _fraction(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        try:
            return float(value)
        except ValueError:
            return 0.0


def probe_media(path: Path) -> MediaInfo:
    path = path.resolve()
    if not path.is_file():
        raise MediaError(f"Media file not found: {path}")
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate,nb_frames,duration:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    if result.returncode:
        raise MediaError(result.stderr.decode(errors="replace").strip() or "ffprobe failed")
    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise MediaError("The selected file has no readable video/image stream") from exc
    duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
    raw_frames = stream.get("nb_frames")
    frame_count = int(raw_frames) if raw_frames and raw_frames != "N/A" else None
    suffix = path.suffix.lower()
    animated = suffix not in STILL_EXTENSIONS or (frame_count is not None and frame_count > 1)
    return MediaInfo(
        path=path,
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        duration=max(duration, 0.0),
        fps=_fraction(stream.get("avg_frame_rate")),
        codec=str(stream.get("codec_name") or "unknown"),
        frame_count=frame_count,
        size=path.stat().st_size,
        animated=animated,
    )


def rotated_dimensions(info: MediaInfo, rotation: int) -> tuple[int, int]:
    return (info.height, info.width) if rotation % 180 else (info.width, info.height)


def maximum_crop(width: int, height: int, scale: float = 1.0) -> Crop:
    if width <= 0 or height <= 0:
        return Crop(0, 0, 1, 1 / LCD_ASPECT)
    source_aspect = width / height
    scale = min(max(scale, 0.1), 1.0)
    if source_aspect >= LCD_ASPECT:
        max_height = 1.0
        max_width = LCD_ASPECT / source_aspect
    else:
        max_width = 1.0
        max_height = source_aspect / LCD_ASPECT
    crop_width = max_width * scale
    crop_height = max_height * scale
    return Crop((1 - crop_width) / 2, (1 - crop_height) / 2, crop_width, crop_height)


def rotation_filter(rotation: int) -> list[str]:
    rotation %= 360
    if rotation == 90:
        return ["transpose=1"]
    if rotation == 180:
        return ["hflip", "vflip"]
    if rotation == 270:
        return ["transpose=2"]
    return []


def exact_filter(crop: Crop, rotation: int, speed: float = 1.0, fps: float | None = None) -> str:
    crop = crop.clamped()
    filters = rotation_filter(rotation)
    filters.append(
        "crop="
        f"trunc(iw*{crop.width:.8f}/2)*2:"
        f"trunc(ih*{crop.height:.8f}/2)*2:"
        f"trunc(iw*{crop.x:.8f}/2)*2:"
        f"trunc(ih*{crop.y:.8f}/2)*2"
    )
    filters.extend([f"scale={LCD_WIDTH}:{LCD_HEIGHT}:flags=lanczos", "setsar=1"])
    if speed != 1.0:
        filters.append(f"setpts=PTS/{speed:.6f}")
    if fps is not None:
        filters.append(f"fps={fps:.6f}")
    return ",".join(filters)


def extract_preview(path: Path, at_seconds: float, rotation: int = 0) -> bytes:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if at_seconds > 0:
        command.extend(["-ss", f"{at_seconds:.4f}"])
    command.extend(["-i", str(path), "-frames:v", "1"])
    filters = rotation_filter(rotation)
    if filters:
        command.extend(["-vf", ",".join(filters)])
    command.extend(["-c:v", "png", "-f", "image2pipe", "pipe:1"])
    result = _run(command)
    if result.returncode or not result.stdout:
        raise MediaError(result.stderr.decode(errors="replace").strip() or "Could not render preview")
    return result.stdout


def render_lcd_jpeg(
    path: Path,
    crop: Crop,
    rotation: int,
    at_seconds: float = 0,
    jpeg_quality: int = 85,
) -> bytes:
    qscale = quality_to_qscale(jpeg_quality)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if at_seconds > 0:
        command.extend(["-ss", f"{at_seconds:.4f}"])
    command.extend(
        [
            "-i",
            str(path),
            "-an",
            "-vf",
            exact_filter(crop, rotation),
            "-frames:v",
            "1",
            "-c:v",
            "mjpeg",
            "-q:v",
            str(qscale),
            "-f",
            "image2pipe",
            "pipe:1",
        ]
    )
    result = _run(command)
    if result.returncode or not result.stdout:
        raise MediaError(result.stderr.decode(errors="replace").strip() or "Could not render LCD frame")
    return result.stdout


def quality_to_qscale(quality: int) -> int:
    quality = min(max(int(quality), 1), 100)
    return max(2, min(31, round(31 - (quality - 1) * 29 / 99)))


def iter_mjpeg(stream, stop_event: threading.Event) -> Iterator[bytes]:
    buffer = bytearray()
    while not stop_event.is_set():
        chunk = stream.read(16384)
        if not chunk:
            return
        buffer.extend(chunk)
        while True:
            start = buffer.find(b"\xFF\xD8")
            if start < 0:
                buffer.clear()
                break
            end = buffer.find(b"\xFF\xD9", start + 2)
            if end < 0:
                if start:
                    del buffer[:start]
                break
            end += 2
            yield bytes(buffer[start:end])
            del buffer[:end]


def stream_media(
    lcd: ThermaltakeLcd,
    info: MediaInfo,
    crop: Crop,
    rotation: int,
    fps: float,
    jpeg_quality: int,
    speed: float,
    loop: bool,
    stop_event: threading.Event,
    status: Callable[[str], None] | None = None,
) -> int:
    if not info.animated:
        jpeg = render_lcd_jpeg(info.path, crop, rotation, jpeg_quality=jpeg_quality)
        lcd.send_jpeg(jpeg, log_frame=False)
        if status:
            status("Static image sent; the panel will retain it.")
        return 1

    command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if loop:
        command.extend(["-stream_loop", "-1"])
    command.extend(
        [
            "-i",
            str(info.path),
            "-an",
            "-vf",
            exact_filter(crop, rotation, speed=speed, fps=fps),
            "-c:v",
            "mjpeg",
            "-q:v",
            str(quality_to_qscale(jpeg_quality)),
            "-f",
            "image2pipe",
            "pipe:1",
        ]
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    started = time.monotonic()
    next_frame_at = started
    interval = 1.0 / fps
    count = 0
    lcd.start_heartbeat()
    try:
        assert process.stdout is not None
        for jpeg in iter_mjpeg(process.stdout, stop_event):
            now = time.monotonic()
            if next_frame_at > now and stop_event.wait(next_frame_at - now):
                break
            lcd.send_jpeg(jpeg, log_frame=False)
            count += 1
            next_frame_at += interval
            if status and count % max(1, round(fps)) == 0:
                status(f"Streaming {count} frames • {count / max(time.monotonic() - started, .001):.1f} FPS")
            if stop_event.is_set():
                break
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.stdout:
            process.stdout.close()
        error = b""
        if process.stderr:
            error = process.stderr.read()
            process.stderr.close()
        lcd.stop_heartbeat()
        if error and not stop_event.is_set():
            raise MediaError(error.decode(errors="replace").strip())
    return count


def export_media(
    info: MediaInfo,
    destination: Path,
    crop: Crop,
    rotation: int,
    fps: float,
    jpeg_quality: int,
    speed: float,
    stop_event: threading.Event,
) -> None:
    destination = destination.resolve()
    suffix = destination.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        data = render_lcd_jpeg(info.path, crop, rotation, jpeg_quality=jpeg_quality)
        destination.write_bytes(data)
        return

    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(info.path),
        "-an",
        "-vf",
        exact_filter(crop, rotation, speed=speed, fps=fps),
    ]
    if suffix == ".webm":
        command.extend(["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0"])
    elif suffix == ".mp4":
        command.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"])
    else:
        raise MediaError("Export format must be .jpg, .mp4, or .webm")
    command.append(str(destination))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)
    try:
        while process.poll() is None:
            if stop_event.wait(0.1):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                raise MediaError("Export cancelled")
        error = process.stderr.read() if process.stderr else b""
        if process.returncode:
            raise MediaError(error.decode(errors="replace").strip() or "ffmpeg export failed")
    finally:
        if process.stderr:
            process.stderr.close()


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "still image"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:04.1f}"
    return f"{int(minutes)}:{secs:04.1f}"
