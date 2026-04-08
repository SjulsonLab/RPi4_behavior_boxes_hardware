# Raspberry Pi OS Package Requirements

Last verified: 2026-04-08

Tested host:
- Raspberry Pi 5 Model B Rev 1.0
- OS: Debian GNU/Linux 13 (Trixie)
- Python: 3.13.5
- Repo checkout: `main` at `/home/pi/RPi4_behavior_boxes_hardware`

## Scope

This file records the packages that had to be added on top of the tested Pi 5
image to make the current `main` branch usable for the real hardware runtime,
plus the extra package needed for on-device testing.

This is intentionally not a blind wishlist. These lists come from:
- probing imports on the fresh image over SSH
- installing only the missing packages
- re-running key imports on the Pi
- running a focused pytest subset on the Pi

Focused verification after installation:
- `python3 -c "from box_runtime.behavior.behavbox import BehavBox"`
- `python3 -c "from box_runtime.audio.runtime import SoundRuntime"`
- `python3 -c "import sample_tasks.head_fixed_gonogo.run"`
- `python3 -m pytest tests/test_one_pi_media_runtime.py tests/test_camera_service.py tests/test_visualstim_runtime.py tests/test_audio_runtime.py`
- Result: `49 passed, 1 skipped`

## Real Hardware Runtime Packages

These are the top-level packages I had to install on the tested Pi image for the
real hardware/runtime path:

- `python3-colorama`
- `python3-scipy`
- `python3-alsaaudio`
- `python3-matplotlib`

Install command used on the Pi:

```bash
sudo apt-get install -y \
  python3-colorama \
  python3-scipy \
  python3-alsaaudio \
  python3-matplotlib
```

Why each was needed:
- `python3-colorama`: required for importing `BehavBox` and `VideoCapture`.
- `python3-scipy`: required by the active audio importer and loopback-latency code.
- `python3-alsaaudio`: required for real ALSA playback/capture instead of the null audio backend.
- `python3-matplotlib`: added because the project docs still treat local plotting as part of the supported appliance workflow.

Important caution:
- `python3-matplotlib` installed successfully, but plotting itself is not fully signed off yet. Importing the `BehavBox` plotting path with the pygame/matplotlib backend still hung long enough to hit a `30 s` timeout during this session. The package belongs in the runtime list, but plotting still needs separate validation/debugging.

## Development And Testing Packages

These are the additional top-level packages I had to install for on-device test
execution:

- `python3-pytest`

Install command used on the Pi:

```bash
sudo apt-get install -y python3-pytest
```

## Packages Already Present On The Tested Image

These packages were already present on the tested Pi image and were relied on by
the verified imports/tests above. I did not install them during this session.

- `alsa-utils`
- `ffmpeg`
- `python3-flask`
- `python3-gpiozero`
- `python3-kms++`
- `python3-numpy`
- `python3-picamera2`
- `python3-pil`
- `python3-pygame`
- `python3-rpi-lgpio`
- `python3-yaml`
- `rsync`

Why they matter:
- `python3-picamera2`: active camera runtime.
- `python3-kms++`: active DRM/KMS visual-stimulus and preview path via `pykms`.
- `python3-gpiozero` and `python3-rpi-lgpio`: GPIO stack on current Raspberry Pi OS.
- `python3-pygame`: local keyboard/display path used by `BehavBox`.
- `python3-flask`: camera service and mock/operator HTTP surfaces.
- `python3-numpy`, `python3-yaml`, `python3-pil`: active runtime dependencies.
- `ffmpeg`: fallback remux path in camera finalization.
- `rsync`: camera session transfer path.
- `alsa-utils`: provides `arecord`, used by the audio latency fallback path.

## Notes For Ansible Or Image Builds

- The lists above are top-level packages. `apt` pulled in transitive dependencies automatically.
- If you want a minimal runtime-only role, start from the runtime list and the already-present runtime dependencies.
- If you want a development/test role, add `python3-pytest`.
- I did not install `uv` during this session. Repo policy prefers `uv`, but it was not required to verify the focused imports/tests above, and it was not available as a simple `apt` package on this Pi image.
