# User guide

## Visual crop workflow

The panel is extremely wide: 480×128 pixels, or 3.75:1. Most source media is
16:9 or 4:3, so simply shrinking the whole source produces large empty bars.
Kraken LCD Studio instead gives you a locked 3.75:1 selection frame.

1. Drop a file onto the source preview or use **Browse media**.
2. For video, move **Preview time** to a representative moment.
3. Drag the green rectangle to choose the visible region.
4. Use **Crop size** to zoom further in while preserving 3.75:1.
5. Check **Exact LCD Output**. This is the final composition.
6. Set playback options and send or export.

The crop applies to every video frame. Choose a crop that works throughout the
clip; the app does not currently animate the crop position.

## Actions

- **Play on LCD** streams an image once or continuously streams animated media.
- **Send this frame** sends the scrubbed frame as a retained static image.
- **Test card** verifies the USB protocol without needing a media file.
- **Export LCD-ready file** writes a 480×128 JPG, MP4, or WebM.
- **Stop / Cancel** cleanly closes ffmpeg, heartbeat, and HID handles.

## Suggested settings

- Start at 8 FPS and 85% JPEG quality.
- Try 12–15 FPS for smoother motion if the scene is not highly detailed.
- Lower quality before lowering FPS if USB transfers begin timing out.
- Use MP4/H.264 for speed and WebM/VP9 for smaller stored files.
- Keep TT RGB Plus closed so it cannot compete for the HID interfaces.

## Troubleshooting

- **LCD not found:** reconnect the panel and verify Device Manager exposes
  `3.9 inch Bar Type TFT LCD Display Module-B` with `264A:233D`.
- **Handshake timeout:** close TT RGB Plus, then unplug/reconnect the panel.
- **No drag-and-drop:** run the installer again. Browse always remains available.
- **Export fails:** confirm `ffmpeg` and `ffprobe` work from PowerShell.
- **Motion is too fast:** set Speed to `1.0` and use the latest version.
