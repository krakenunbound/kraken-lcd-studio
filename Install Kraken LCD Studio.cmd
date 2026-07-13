@echo off
setlocal
cd /d "%~dp0"
title Install Kraken LCD Studio
echo Installing the small Python dependencies for Kraken LCD Studio...
py -3.12 -m pip install -r requirements-direct.txt
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
echo Setup complete.
pause
