# Installing Kraken LCD Studio

Kraken LCD Studio is a Windows application for the Thermaltake 3.9-inch
480×128 LCD identified by USB `VID 264A` and `PID 233D`.

## Prerequisites

Install these before running the setup script:

1. **Windows 10 or 11 (64-bit).**
2. **Python 3.12 (64-bit)** with the Windows `py` launcher. Download it from
   [python.org](https://www.python.org/downloads/) and verify:

   ```powershell
   py -3.12 --version
   ```

3. **ffmpeg and ffprobe** on `PATH`. Start from the official
   [FFmpeg download page](https://ffmpeg.org/download.html), then verify:

   ```powershell
   ffmpeg -version
   ffprobe -version
   ```

4. Connect the LCD by USB and fully exit **TT RGB Plus**. Only one application
   can own the panel's HID interfaces at a time.

## Install from a GitHub release

1. Download the latest `Kraken-LCD-Studio-v*.zip` from
   [Releases](https://github.com/krakenunbound/kraken-lcd-studio/releases).
2. Extract the archive to a permanent folder such as
   `C:\Apps\Kraken LCD Studio`. Do not run it from inside the ZIP.
3. Double-click **Install Kraken LCD Studio.cmd**. It installs the Python
   packages from `requirements.txt` and enables the per-user background startup
   entry. Administrator rights are not required for the startup entry.
4. Double-click **Launch Kraken LCD Studio.cmd**.
5. Choose media, compose the 480×128 crop, optionally configure the performance
   overlay, and click **Play on LCD**.

The close button hides the app in the notification area so playback can
continue. Use the tray menu's **Exit** command to close it completely.

## Optional temperature sensors

CPU package temperature is not consistently available through standard Windows
APIs. For CPU temperature and broader AMD/Intel GPU sensor support:

1. Download and run
   [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases).
2. Enable **Options → Remote Web Server** (wording can vary by release).
3. Keep the default overlay sensor URL:
   `http://localhost:8085/data.json`.

The overlay continues working without LibreHardwareMonitor; unavailable sensors
display `--`. NVIDIA metrics use `nvidia-smi` from the installed display driver.

## Updating

Exit Kraken LCD Studio from its tray menu, extract the new release over the same
permanent folder, and run **Install Kraken LCD Studio.cmd** again. Profiles and
local settings in `lcd_gui_config.json` remain on that PC and are not part of
release archives.

## Disabling startup or uninstalling

1. Clear **Start hidden with Windows** in the app footer or tray menu.
2. Choose **Exit** from the tray menu.
3. Delete the application folder.

The installer uses the current user's Python package directory. Removing those
shared Python packages is optional and is not required to uninstall the app.

## Troubleshooting

- **LCD not found:** reconnect USB and check Device Manager for the
  `264A:233D` device.
- **LCD in use by another program:** the panel is connected, but TT RGB Plus or
  another RGB, fan, or monitoring tool has it open. Close that program; playback
  resumes automatically once the panel is released.
- **Anything else:** check `kraken_lcd_studio.log` in the app folder for the
  exact error and its time.
- **Media or export fails:** confirm both `ffmpeg` and `ffprobe` work in a new
  PowerShell window.
- **No CPU temperature:** run LibreHardwareMonitor and enable its web server.
- **No tray icon:** open the notification-area overflow menu and rerun the
  installer if `pystray` is missing.
- **Another instance opens:** launching the app again should activate the
  existing tray instance rather than starting a second LCD connection.
