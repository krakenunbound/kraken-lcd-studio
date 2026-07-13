@echo off
setlocal
cd /d "%~dp0"
start "Kraken LCD Studio" pyw -3.12 lcd_gui.py %*
