# RPi4 Behavior Boxes Hardware (still in development)

This repository contains only the hardware and low-level support components
from the original `RPi4_behavior_boxes` codebase.

Included directories:
- `essential/` (device interfaces, camera, treadmill, pump, acquisition)
- `debug/` (hardware test/debug scripts)
- `environment/` (environment specification files)
- `irig_decoding/` (IRIG decode tooling from `charlie-irig`)
- `video_acquisition/` (newer camera acquisition scripts from `matt-behavior`)
- `HQ_camera/` (HQ camera support scripts from `matt-behavior`)

Excluded from this split:
- `task_protocol/` (task-specific experiment logic)
- `obsolete/` (legacy task code)

## Origin

This repository was split from:
`original/RPi4_behavior_boxes`

to support independent versioning of hardware code and task code.

## Integration Notes

- Camera stack additions were imported from `matt-behavior` in Phase 2 (`video_acquisition/`, `HQ_camera/`, and related `essential/video_acquisition/` updates).

## Head-Fixed GPIO + Mock UI

- BehavBox now uses a strict head-fixed GPIO arrangement hard-coded in:
  `essential/behavbox.py` (`HEAD_FIXED_GPIO`).
- Non-Raspberry Pi hosts automatically use a mock GPIO backend and launch a local web UI.
- Default UI URL: `http://127.0.0.1:8765`
- Optional environment overrides:
  - `BEHAVBOX_MOCK_UI_HOST`
  - `BEHAVBOX_MOCK_UI_PORT`

Quick launcher:
- `python debug/run_mock_behavbox.py`
