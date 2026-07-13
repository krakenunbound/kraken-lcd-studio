@echo off
setlocal
cd /d "%~dp0"
title Install Kraken LCD Studio
echo Installing the small Python dependencies for Kraken LCD Studio...
py -3.12 -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation failed. Verify Python 3.12 and internet access.
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo WARNING: ffmpeg is not on PATH. Install ffmpeg before using media.
) else (
  echo.
  echo ffmpeg found.
)
py -3.12 -c "import lcd_gui; lcd_gui.set_startup_enabled(True)"
if errorlevel 1 (
  echo.
  echo WARNING: Could not add Kraken LCD Studio to your Windows startup apps.
) else (
  echo.
  echo Background LCD restore enabled for Windows sign-in.
)
echo Setup complete. Kraken LCD Studio will start hidden at your next sign-in.
pause
