# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-07-13

### Added

- Configurable rotating CPU, GPU, memory, and clock cards composited directly
  onto static or animated LCD media.
- Live CPU/memory metrics through `psutil`, NVIDIA metrics through `nvidia-smi`,
  configured memory speed through Windows CIM, and optional temperatures from
  LibreHardwareMonitor's JSON endpoint.
- Exact overlay preview plus profile persistence for colors, font, scale,
  opacity, card selection, sensor URL, and cycle timing.
- Notification-area companion, Windows sign-in restore, named profiles, and
  single-instance activation.
- Explicit MP4/WebM export chooser and direct MP4-to-WebM conversion.

### Changed

- Performance cards can remove the dark glass background entirely, and the
  opacity control spans the full 0–100% range.
- Replaced the blocking system color dialog with a live HSV palette that updates
  the Studio and LCD while dragging, with Cancel rollback and OK persistence.
- Added a confirmed reset action for all overlay design defaults.
- Improved action-button contrast in every interaction state.

## [0.2.0] - 2026-07-13

### Added

- Notification-area companion that keeps LCD playback alive when the window is
  hidden and restores the last played setup at Windows sign-in.
- Per-user Windows startup registration, single-instance window activation, and
  background retry when the LCD is not ready during sign-in.
- Named profiles containing the media path, crop, playback settings, and loop
  behavior, with profile selection from the Studio and tray icon.
- Explicit MP4/WebM format chooser and direct MP4-to-WebM action.

### Changed

- Improved action-button contrast in every interaction state.

## [0.1.0] - 2026-07-13

### Added

- Direct Windows HID control of USB device `264A:233D` without TT RGB Plus.
- Safe device detection, protocol handshake, JPEG packet streaming, and heartbeat.
- Static test card and paced MP4 playback through ffmpeg.
- Visual 480:128 crop editor and LCD-ready image/video export.
- Dark desktop GUI, persistent settings, device status, and activity log.
