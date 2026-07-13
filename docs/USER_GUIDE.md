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

## Background companion and profiles

The installer registers Kraken LCD Studio as a per-user Windows startup app. At
sign-in it starts hidden in the notification area, reloads the setup most
recently sent with **Play on LCD**, and reconnects to the panel. It retries after
a delay if the LCD is not ready yet.

Closing the Studio window hides it without stopping animated playback. Use the
tray icon to reopen the window, play the last setup, select a saved profile,
stop playback, or exit completely. **Start hidden with Windows** can be changed
from either the window footer or tray menu.

A saved profile includes the media path, exact crop, rotation, FPS, JPEG
quality, speed, loop setting, and performance-overlay design. Use **Save**,
**Load**, and **Delete** in the
Saved Profiles section. Moving or deleting a profile's source media will make
that profile unavailable until the path is restored.

## Performance overlay

The **Overlay** tab adds rotating glass-style information cards over the chosen
background. CPU, GPU, Memory, and Clock cards can be enabled independently.
Cycle time, accent and text colors, font family, font scale, and panel opacity
are configurable and appear in the Exact LCD Output preview.

Turn off **Dark glass background behind metrics** to remove the full-card tint
and border. Transparent mode leaves the source image untouched except where the
text, accent strip, and graph line are drawn. Glass opacity supports the full
0–100% range when the background is enabled.

Click **Text** or **Accent** to open the live color palette. Dragging anywhere in
the saturation/brightness field or hue strip changes both the Exact LCD Output
and active LCD immediately. **Cancel** rolls the live preview back; **OK** saves
it. Other control changes are committed with **Apply Overlay Settings**.
**Reset overlay defaults** restores colors, typography, transparency, cards,
cycle timing, and the sensor URL while preserving whether the overlay is on.

- **CPU:** total load, current clock, CPU package temperature, and load history.
- **GPU:** load, temperature, graphics clock, VRAM, and load history.
- **Memory:** use, capacity, configured transfer rate, and usage history.
- **Clock:** local time, date, day, year, and time zone.

`psutil` supplies CPU and memory readings. NVIDIA readings come from
`nvidia-smi`, which is installed with the display driver. CPU temperatures are
not exposed by standard Windows APIs on many systems; for those readings,
launch LibreHardwareMonitor, enable **Options → Remote Web Server**, and leave
the Overlay tab's sensor URL at `http://localhost:8085/data.json`.

When a static image has the overlay enabled, **Play on LCD** remains active and
refreshes the card at a low rate. Use **Stop / Cancel** or the tray menu's
**Stop LCD playback** command to stop those live updates.

## Actions

- **Play on LCD** streams an image once or continuously streams animated media.
- **Send this frame** sends the scrubbed frame as a retained static image.
- **Test card** verifies the USB protocol without needing a media file.
- **Export as MP4 or WebM** first shows a clear format chooser, then writes a
  cropped 480×128 file in the selected format.
- **Convert MP4 → WebM** is shown when an MP4 is open and goes directly to a
  VP9 WebM Save As dialog.
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
