# Hardware and media limitations

## Panel protocol

- Native output is fixed at **480×128** (3.75:1).
- The panel is a proprietary USB HID framebuffer, not a Windows monitor.
- Each displayed frame must be a complete non-progressive JPEG stream.
- Frames use 1,020-byte chunks inside 1,024-byte reports.
- The one-byte packet count caps a JPEG at roughly 260 KB. Normal 480×128
  frames are far smaller.
- Audio is never sent to the LCD.
- Animated media requires the app to keep running; the last frame is retained.
- No verified hardware brightness or firmware controls are currently exposed.

## Frame rate

Useful performance depends on JPEG complexity, USB topology, panel firmware,
host load, and quality. The default is 8 FPS.

- 5–8 FPS: low load and very reliable
- 10–15 FPS: smoother animation, normally reasonable
- 16–30 FPS: experimental; quality or reliability may need reduction

The GUI permits 1–30 FPS but does not claim every panel/hub sustains the maximum.

## Source formats and size

The GUI accepts JPG, PNG, BMP, TIFF, GIF, WebP, MP4, WebM, MKV, MOV, and AVI.
Other ffmpeg-decodable formats may also work.

Thermaltake's official 20 MB upload limit does not apply. Files remain on the PC
and are decoded frame by frame. Disk size affects storage and seeking, not the
panel's framebuffer limit.

## Hardware sensor availability

Windows does not provide a consistent standard API for CPU package temperature.
Kraken LCD Studio therefore shows `--` when LibreHardwareMonitor is not running
with its JSON web server enabled. NVIDIA GPU metrics use `nvidia-smi`; AMD and
Intel GPU temperature/clock support depends on LibreHardwareMonitor exposing the
corresponding sensors. Sensor names and availability vary by motherboard,
firmware, driver, and hardware generation.

## Compatibility

Tested device:

- VID/PID `264A:233D`
- Product `3.9 inch Bar Type TFT LCD Display Module-B`
- Resolution 480×128
- 440-byte command channel and 1,024-byte frame channel

Do not assume compatibility merely because another product uses TT RGB Plus.
The controller refuses devices with a different VID/PID.
