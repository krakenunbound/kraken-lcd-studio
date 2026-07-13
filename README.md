# Kraken LCD Studio

Kraken LCD Studio is a lightweight native Windows controller and visual media
cropper for the Thermaltake 3.9-inch 480×128 LCD panel (`VID 264A`, `PID 233D`,
part `AC-073-OO1NAN-A1`). It controls the panel directly through Microsoft's HID
driver—no TT RGB Plus, replacement USB driver, or packet sniffer is required.

Version: **0.1.0**

## What it does

- Detects the live HID interfaces and displays the panel serial number.
- Opens images, GIFs, MP4, WebM, MKV, MOV, AVI, and animated WebP.
- Shows a draggable, locked 480:128 crop frame over the source.
- Provides a separate exact 480×128 output preview.
- Scrubs to a representative frame when cropping video.
- Controls crop size, position, rotation, FPS, JPEG quality, speed, and looping.
- Streams directly to the LCD or sends a retained static frame.
- Exports pre-cropped 480×128 JPG, H.264 MP4, or VP9 WebM files.
- Stops ffmpeg, heartbeat threads, and USB handles cleanly.

## Quick start

1. Close TT RGB Plus completely.
2. Run `Install Kraken LCD Studio.cmd` once.
3. Double-click `Launch Kraken LCD Studio.cmd`.
4. Drag media from Explorer into the preview, or click **Browse media**.
5. Drag the green crop frame over the desired part of the source.
6. Click **Play on LCD**, **Send this frame**, or **Export LCD-ready file**.

The bundled `kraken_loop.mp4` is intentionally ignored by Git so personal media
is not accidentally published.

## WebM

WebM works as an input and export format. VP9 WebM is often smaller on disk than
H.264 MP4, but it usually costs more CPU to encode and decode. The source format
does not change live USB traffic: every displayed frame is decoded and sent to
the panel as a baseline JPEG packet stream.

For ordinary use, MP4/H.264 is the fastest and most broadly compatible choice.
WebM is useful when disk size matters more than encode/decode speed.

## Requirements

- Windows 10 or 11
- Python 3.12
- ffmpeg and ffprobe on `PATH`
- Pillow, pywinusb, and tkinterdnd2 (installed by the setup script)
- Thermaltake LCD connected by its USB cable

See [User Guide](docs/USER_GUIDE.md), [Limitations](docs/LIMITATIONS.md), and
[Protocol Notes](docs/PROTOCOL.md) for details.

## Command-line fallback

```powershell
py -3.12 thermaltake_lcd.py info
py -3.12 thermaltake_lcd.py --verbose test
py -3.12 thermaltake_lcd.py play kraken_loop.mp4 --fps 8
```

## Project status

This is an independent community project, not affiliated with or endorsed by
Thermaltake. The exact connected hardware above has been tested on Windows.
Other Thermaltake LCD products may use different resolutions or commands.

Protocol behavior was validated against
[`bekindpleaserewind/ttlcd`](https://github.com/bekindpleaserewind/ttlcd), the
GPL-3.0 Linux implementation for this device family. Kraken LCD Studio is
licensed under GPL-3.0-or-later; see [LICENSE](LICENSE).
