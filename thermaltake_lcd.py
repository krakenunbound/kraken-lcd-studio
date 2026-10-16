#!/usr/bin/env python3
"""Native Windows controller for Thermaltake's 3.9-inch 480x128 LCD.

The panel is a USB HID device (VID 264A, PID 233D), not a Windows monitor.
This implementation uses the built-in Microsoft HID driver through pywinusb;
it does not require TT RGB Plus, WinUSB, libusb, or a replacement driver.

Protocol behavior was independently validated against the GPL-3.0 `ttlcd`
project by bekindpleaserewind:
https://github.com/bekindpleaserewind/ttlcd

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import argparse
import ctypes
import io
import math
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable

import pywinusb.hid as hid
from pywinusb.hid import winapi
from PIL import Image, ImageDraw, ImageFont


VENDOR_ID = 0x264A
PRODUCT_ID = 0x233D
DEVICE_ID = f"vid_{VENDOR_ID:04x}&pid_{PRODUCT_ID:04x}"
WIDTH = 480
HEIGHT = 128
COMMAND_SIZE = 440
FRAME_PACKET_SIZE = 1024
FRAME_DATA_SIZE = 1020

STATE_READY = "ready"
STATE_BUSY = "busy"
STATE_MISSING = "missing"
BUSY_MESSAGE = (
    "The LCD is plugged in, but another program has it open exclusively. Close "
    "TT RGB Plus or any other RGB, fan, or monitoring tool that controls USB "
    "devices; the panel is picked up again as soon as it is released."
)


class LcdError(RuntimeError):
    pass


class LcdBusyError(LcdError):
    pass


class HidChannel:
    """One HID interface with queued asynchronous input reports."""

    def __init__(self, device: hid.HidDevice, payload_size: int):
        self.device = device
        self.payload_size = payload_size
        self.received: queue.Queue[list[int]] = queue.Queue()
        self.report = None

    def open(self) -> None:
        self.device.open()
        self.device.set_raw_data_handler(self.received.put)
        reports = self.device.find_output_reports()
        if len(reports) != 1:
            raise LcdError(
                f"Expected one output report on {self.device.device_path}, found {len(reports)}"
            )
        self.report = reports[0]
        raw_size = len(self.report.get_raw_data())
        expected = self.payload_size + 1  # leading HID report ID
        if raw_size != expected:
            raise LcdError(f"Expected HID report size {expected}, found {raw_size}")

    def close(self) -> None:
        try:
            self.device.close()
        except Exception:
            pass

    def send(self, payload: bytes) -> None:
        if self.report is None:
            raise LcdError("HID channel is not open")
        if len(payload) != self.payload_size:
            raise ValueError(f"Expected {self.payload_size} payload bytes, got {len(payload)}")
        self.report.set_raw_data([0, *payload])
        self.report.send()

    def wait(self, timeout: float) -> list[int]:
        try:
            return self.received.get(timeout=timeout)
        except queue.Empty as exc:
            raise LcdError(f"LCD did not respond within {timeout:.1f} seconds") from exc

    def drain(self) -> None:
        while True:
            try:
                self.received.get_nowait()
            except queue.Empty:
                return


class ThermaltakeLcd:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.command: HidChannel | None = None
        self.frame: HidChannel | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat: threading.Thread | None = None
        self._command_lock = threading.Lock()

    @staticmethod
    def interface_paths() -> list[str]:
        """List the panel's HID interface paths without opening the device.

        pywinusb's HidDeviceFilter opens every HID device for read/write while
        enumerating and silently drops any it cannot open, so a panel held by
        another program would be indistinguishable from an unplugged one.
        """
        guid = winapi.GetHidGuid()
        info_data = winapi.SP_DEVINFO_DATA()
        info_data.cb_size = ctypes.sizeof(info_data)
        paths = []
        with winapi.DeviceInterfaceSetInfo(guid) as h_info:
            for interface_data in winapi.enum_device_interfaces(h_info, guid):
                path = winapi.get_device_path(h_info, interface_data, ctypes.byref(info_data))
                if DEVICE_ID in path.lower():
                    paths.append(path)
        return sorted(paths)

    @classmethod
    def discover(cls) -> list[hid.HidDevice]:
        """Return the panel's interfaces; ones that could not be opened have vendor_id 0."""
        return [hid.HidDevice(path) for path in cls.interface_paths()]

    @classmethod
    def status(cls) -> tuple[str, str]:
        """Return (STATE_READY | STATE_BUSY | STATE_MISSING, serial number)."""
        devices = cls.discover()
        if len(devices) < 2:
            return STATE_MISSING, ""
        if not all(device.vendor_id for device in devices):
            return STATE_BUSY, ""
        return STATE_READY, devices[0].serial_number

    def connect(self) -> None:
        devices = self.discover()
        by_interface = {}
        for device in devices:
            path = device.device_path.lower()
            if "&mi_00" in path:
                by_interface[0] = device
            elif "&mi_01" in path:
                by_interface[1] = device
        if set(by_interface) != {0, 1}:
            found = [device.device_path for device in devices]
            raise LcdError(
                f"Thermaltake LCD {VENDOR_ID:04X}:{PRODUCT_ID:04X} not found "
                f"(expected HID interfaces MI_00 and MI_01; found: {found or 'none'})"
            )
        if not all(device.vendor_id for device in by_interface.values()):
            raise LcdBusyError(BUSY_MESSAGE)

        self.command = HidChannel(by_interface[0], COMMAND_SIZE)
        self.frame = HidChannel(by_interface[1], FRAME_PACKET_SIZE)
        try:
            self.command.open()
            self.frame.open()
        except hid.HIDError as exc:
            self.close()
            raise LcdBusyError(BUSY_MESSAGE) from exc
        except Exception:
            self.close()
            raise

        if self.verbose:
            print(f"Connected: {by_interface[0].product_name} ({VENDOR_ID:04X}:{PRODUCT_ID:04X})")

    def close(self) -> None:
        self.stop_heartbeat()
        if self.frame:
            self.frame.close()
        if self.command:
            self.command.close()
        self.frame = None
        self.command = None

    @staticmethod
    def _padded(prefix: Iterable[int], size: int) -> bytes:
        data = bytes(prefix)
        if len(data) > size:
            raise ValueError("Packet prefix is larger than packet")
        return data + bytes(size - len(data))

    def initialize(self) -> None:
        if not self.command or not self.frame:
            raise LcdError("LCD is not connected")
        self.command.drain()
        self.frame.drain()
        for opcode in (0x85, 0x87, 0x85, 0x87, 0x84, 0x81):
            with self._command_lock:
                self.command.send(self._padded((opcode, 0x01, 0x00, 0x80), COMMAND_SIZE))
                reply = self.command.wait(2.0)
            if self.verbose:
                print(f"Handshake {opcode:02X}: {len(reply)}-byte HID report")

        with self._command_lock:
            self.command.send(
                self._padded((0x12, 0x01, 0x00, 0x80, 0x64), COMMAND_SIZE)
            )

    def start_heartbeat(self) -> None:
        if self._heartbeat and self._heartbeat.is_alive():
            return
        self._heartbeat_stop.clear()

        def worker() -> None:
            assert self.command is not None
            while not self._heartbeat_stop.wait(2.0):
                try:
                    with self._command_lock:
                        self.command.send(
                            self._padded((0x82, 0x01, 0x00, 0x80), COMMAND_SIZE)
                        )
                except Exception as exc:
                    if self.verbose:
                        print(f"Heartbeat stopped: {exc}", file=sys.stderr)
                    return

        self._heartbeat = threading.Thread(target=worker, name="lcd-heartbeat", daemon=True)
        self._heartbeat.start()

    def stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat and self._heartbeat.is_alive():
            self._heartbeat.join(timeout=3.0)
        self._heartbeat = None

    def send_jpeg(
        self, jpeg: bytes, timeout: float = 2.0, *, log_frame: bool = True
    ) -> None:
        if not self.frame:
            raise LcdError("LCD is not connected")
        if not jpeg.startswith(b"\xFF\xD8") or not jpeg.rstrip().endswith(b"\xFF\xD9"):
            raise ValueError("Frame is not a complete JPEG stream")

        packet_count = math.ceil(len(jpeg) / FRAME_DATA_SIZE)
        if not 1 <= packet_count <= 255:
            raise ValueError(
                f"JPEG needs {packet_count} packets; the panel protocol supports 1..255"
            )

        self.frame.drain()
        for index in range(packet_count):
            start = index * FRAME_DATA_SIZE
            chunk = jpeg[start : start + FRAME_DATA_SIZE]
            if index == 0:
                header = bytes((0x08, packet_count, 0x00, 0x80))
            else:
                header = bytes((0x08, index, 0x00, 0x00))
            self.frame.send((header + chunk).ljust(FRAME_PACKET_SIZE, b"\x00"))

        acknowledgement = self.frame.wait(timeout)
        if self.verbose and log_frame:
            print(
                f"Frame: {len(jpeg)} bytes, {packet_count} packets, "
                f"ack={len(acknowledgement)}-byte HID report"
            )

    def __enter__(self) -> "ThermaltakeLcd":
        self.connect()
        try:
            self.initialize()
        except BaseException:
            # __exit__ does not run when __enter__ raises; release the HID handles.
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def make_test_frame() -> bytes:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#05070a")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline="#00ff88", width=4)
    try:
        title_font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 35)
        detail_font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 17)
    except OSError:
        title_font = detail_font = ImageFont.load_default()
    draw.text((22, 19), "KRAKEN LCD ONLINE", font=title_font, fill="#00ff88")
    draw.text(
        (24, 76),
        "Native USB control - no TT RGB Plus",
        font=detail_font,
        fill="#d6fff0",
    )
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True, progressive=False)
    return output.getvalue()


def iter_mjpeg_frames(stream, stop: threading.Event):
    buffer = bytearray()
    while not stop.is_set():
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


def play_video(lcd: ThermaltakeLcd, path: Path, fps: float, duration: float | None) -> None:
    if not path.is_file():
        raise LcdError(f"Video not found: {path}")
    ffmpeg = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stream_loop",
        "-1",
        "-i",
        str(path),
        "-an",
        "-vf",
        (
            f"fps={fps},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:black"
        ),
        "-c:v",
        "mjpeg",
        "-q:v",
        "5",
        "-f",
        "image2pipe",
        "pipe:1",
    ]
    stop = threading.Event()
    process = subprocess.Popen(
        ffmpeg,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    started = time.monotonic()
    next_frame_at = started
    frame_interval = 1.0 / fps
    frame_count = 0
    lcd.start_heartbeat()
    try:
        assert process.stdout is not None
        for frame in iter_mjpeg_frames(process.stdout, stop):
            now = time.monotonic()
            if next_frame_at > now:
                time.sleep(next_frame_at - now)
            lcd.send_jpeg(frame, log_frame=False)
            frame_count += 1
            next_frame_at += frame_interval
            if duration is not None and time.monotonic() - started >= duration:
                break
    finally:
        stop.set()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            error = process.stderr.read().decode(errors="replace").strip()
            process.stderr.close()
            if error:
                print(f"ffmpeg: {error}", file=sys.stderr)
        lcd.stop_heartbeat()
        if lcd.verbose:
            elapsed = max(time.monotonic() - started, 0.001)
            print(f"Playback: {frame_count} frames in {elapsed:.1f}s ({frame_count / elapsed:.1f} FPS)")


def print_info() -> int:
    devices = ThermaltakeLcd.discover()
    if not devices:
        print(f"Thermaltake LCD {VENDOR_ID:04X}:{PRODUCT_ID:04X} not found")
        return 1
    print(f"Found {len(devices)} HID interfaces:")
    for device in devices:
        if device.vendor_id:
            print(f"  {device.product_name}")
        else:
            print("  (in use by another program)")
        print(f"    {device.device_path}")
        print(f"    serial: {device.serial_number or '-'}")
    if not all(device.vendor_id for device in devices):
        print(BUSY_MESSAGE)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("info", help="detect the LCD without sending commands")
    subparsers.add_parser("test", help="display a static native-control test card")
    play = subparsers.add_parser("play", help="loop an MP4 directly on the USB LCD")
    play.add_argument("video", type=Path)
    play.add_argument("--fps", type=float, default=8.0)
    play.add_argument("--duration", type=float, default=None)
    args = parser.parse_args()

    if args.command == "play" and args.fps <= 0:
        parser.error("--fps must be greater than zero")

    if args.command == "info":
        return print_info()

    try:
        with ThermaltakeLcd(verbose=args.verbose) as lcd:
            if args.command == "test":
                lcd.send_jpeg(make_test_frame())
            elif args.command == "play":
                play_video(lcd, args.video.resolve(), args.fps, args.duration)
    except KeyboardInterrupt:
        print("Stopped.")
        return 130
    except (LcdError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
