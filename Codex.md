# Codex.md

Last updated: 2026-03-08
Repo: `RPi4_behavior_boxes_hardware`
Primary branch in progress: `codex/hardware-integration-phase1`

## Purpose
Hardware/runtime support for RPi4 behavior boxes, including strict head-fixed GPIO mapping and a non-Pi mock hardware mode.

## Current Architecture
- Core behavior logic: `essential/behavbox.py`
- Camera HTTP control:
- `video_acquisition/http_camera_service.py`
- `video_acquisition/picamera2_recorder.py`
- `video_acquisition/camera_client.py`
- `video_acquisition/camera_session.py`
- Backend selection: `essential/gpio_backend.py`
- Visual stimulus runtime:
- `essential/visualstim.py`
- `essential/visual_runtime/grating_specs.py`
- `essential/visual_runtime/grating_compiler.py`
- `essential/visual_runtime/drm_runtime.py`
- Mock hardware stack:
- `essential/mock_hw/devices.py`
- `essential/mock_hw/registry.py`
- `essential/mock_hw/server.py`
- `essential/mock_hw/web.py`
- `essential/mock_hw/visual_stim.py`
- Tests:
- `tests/test_mock_hardware.py`
- `tests/test_visualstim_runtime.py`
- `tests/test_camera_service.py`

## Camera Service Contract
- Camera Pi now owns recording via an always-on HTTP service instead of SSH-launched scripts.
- Browser endpoints:
- `/manual` for manual start/stop + preview
- `/monitor` for preview/status + emergency stop during automated runs
- API endpoints:
- `/api/status`
- `/api/config`
- `/api/configure`
- `/api/start`
- `/api/stop`
- `/api/sessions`
- `/api/sessions/<session_id>/ack_transfer`
- Recording ownership modes:
- `manual`
- `automated`
- Automated sessions block manual start/config changes, but monitor mode still exposes emergency stop.

## Camera Session Data Contract
- One uninterrupted attempt writes:
- `attempt_NNN.h264`
- `attempt_NNN_raw_frames.tsv`
- Raw TSV columns:
- `frame_index`
- `sensor_timestamp_ns`
- `arrival_utc_ns`
- Final UTC is derived offline from the full attempt using smoothed offset fitting, not a single POSIX start anchor.
- Finalization outputs:
- clean single-attempt session: `session.mp4`, `session.tsv`, `session_manifest.json`
- crash-visible multi-attempt session: `attempt_NNN.mp4`, `attempt_NNN.tsv`, `session_manifest.json`
- `session_manifest.json` includes attempt boundaries, gap intervals, hashes, and timestamp-fit diagnostics.

## Camera Offload Contract
- The BehavBox Pi now uses `CameraClient` instead of SSH for start/stop/status.
- Session transfer is two-phase:
- BehavBox pulls the finalized session directory with `rsync`
- local manifest hashes are verified
- camera-side deletion happens only after HTTP transfer acknowledgement
- This is deliberately safer than direct `rsync --remove-source-files` on the camera Pi.

## Event Queue Contract (Current)
- Hardware callbacks now enqueue structured `BehaviorEvent` objects, not plain strings.
- `BehaviorEvent` is defined in `essential/behavbox.py` and includes:
- `name: str`
- `timestamp: float` (wall-clock from `time.time()`, captured at detection time)
- Callback helper: `BehavBox._push_event(...)`.
- Compatibility helpers for downstream task code:
- `BehavBox.event_name(event)`
- `BehavBox.event_timestamp(event)`
- `interact_list` entries now reuse the same detection timestamp used for queue event creation.

## Head-Fixed GPIO Mapping
- `flipper`: 4
- `treadmill_1_input`: 13
- `treadmill_2_input`: 16
- `reward_left`: 19
- `reward_right`: 20
- `reward_center`: 21
- `pump4`: 7
- `airpuff`: 8
- `vacuum`: 25
- `cue_led_1`: 22
- `cue_led_2`: 18
- `cue_led_3`: 17
- `cue_led_4`: 14
- `user_output`: 11
- `lick_1`: 26
- `lick_2`: 27
- `lick_3`: 15
- `sound_1`: 23
- `sound_2`: 24
- `sound_3`: 9
- `sound_4`: 10
- Unused: 5, 6, 12

## Runtime Behavior (Pi vs Non-Pi)
- Raspberry Pi: real `gpiozero` devices.
- Non-Pi: mock devices + local web UI.
- Mock UI default: `127.0.0.1:8765`.
- Optional env vars:
- `BEHAVBOX_MOCK_UI_HOST`
- `BEHAVBOX_MOCK_UI_PORT`
- `BEHAVBOX_MOCK_UI_AUTOSTART`
- `BEHAVBOX_FORCE_MOCK`

## Visual Stimulus Runtime
- Replaced RPG-based `essential/visualstim.py` with a YAML-spec + precompute pipeline.
- Stimuli are described by YAML files listed in `session_info["vis_gratings"]`.
- `VisualStim` preserves task-facing compatibility for:
- `show_grating(name)`
- `process_function(name)`
- `gratings`
- `myscreen.close()`
- Runtime backends:
- `visual_backend="drm"` for Raspberry Pi DRM/KMS via `python3-kms++`
- `visual_backend="fake"` for tests and non-Pi development
- Default display calibration:
- `visual_display_degrees_subtended = 80.0`
- DRM bring-up target: Pi 4B, 64-bit Bookworm, 60 Hz, console-only
- Final validation target: fresh 64-bit Trixie on Pi 4B
- Pi 5 is follow-up validation, not guaranteed by the first pass

## Commands
- `cd /Users/lukesjulson/codex/RPi4_refactor/targets/RPi4_behavior_boxes_hardware`
- `uv run --with numpy --with scipy --with colorama --with flask --with pytest pytest tests/test_camera_service.py`
- `uv run --with numpy --with scipy --with colorama --with pytest pytest tests/test_mock_hardware.py tests/test_visualstim_runtime.py`
- `python debug/run_mock_behavbox.py`

## Recent Changes
- Added wall-clock `BehaviorEvent` queue objects in `BehavBox`.
- Updated callback logging/interaction timestamps to use detection-time event timestamps.
- Added test coverage for event queue timestamp behavior.
- Maintained compatibility helpers for task consumers during migration.
- Added an HTTP camera service with manual/automated ownership modes and browser preview/control pages.
- Added drift-aware frame UTC derivation and session finalization utilities for raw H.264 + TSV attempts.
- Replaced BehavBox camera SSH control with `CameraClient` HTTP control and verified offload.
- Migrated standalone `essential/video_acquisition/VideoCapture.py` away from SSH-based camera start/stop/offload.
- Added camera-service tests covering timestamp fitting, finalization, ownership blocking, preview/control UI, and offload paths.
- Replaced the RPG visual stimulus wrapper with a persistent worker runtime.
- Added YAML grating spec validation and NumPy precomputation.
- Added fake-backend timing tests and a hardware-gated DRM smoke test.

## Notes
- `sample_tasks/` exists locally but is currently untracked in this branch.
- `.gitignore` has local uncommitted changes in this working tree.
