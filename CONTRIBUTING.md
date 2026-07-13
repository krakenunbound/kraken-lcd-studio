# Contributing

Contributions are welcome, especially reproducible protocol observations for
the exact `264A:233D` device.

## Development setup

```powershell
py -3.12 -m pip install -r requirements-direct.txt
py -3.12 -m unittest discover -s tests -v
py -3.12 -m py_compile thermaltake_lcd.py lcd_media.py lcd_gui.py
```

ffmpeg and ffprobe must be on `PATH` for media integration tests.

## Protocol changes

Do not add unverified USB commands. Document device identity, interface, packet
capture or open-source reference, expected response, timeout behavior, and
recovery. Firmware-writing commands do not belong in ordinary playback.

## Releases

Use semantic versions. Update `VERSION`, `pyproject.toml`, and `CHANGELOG.md`
together. Keep personal media, logs, and local configuration out of commits.
