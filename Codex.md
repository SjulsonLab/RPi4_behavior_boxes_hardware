# Codex.md

Last updated: 2026-03-08
Repo: `RPi4_behavior_boxes_hardware`
Primary branch in progress: `codex/hardware-integration-phase1`

## Purpose
Hardware/runtime support for RPi4 behavior boxes, including strict head-fixed GPIO mapping and a non-Pi mock hardware mode.

## Current Architecture
- Core behavior logic: `essential/behavbox.py`
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
- `uv run --with numpy --with scipy --with colorama --with pytest pytest tests/test_mock_hardware.py tests/test_visualstim_runtime.py`
- `python debug/run_mock_behavbox.py`

## Recent Changes
- Added wall-clock `BehaviorEvent` queue objects in `BehavBox`.
- Updated callback logging/interaction timestamps to use detection-time event timestamps.
- Added test coverage for event queue timestamp behavior.
- Maintained compatibility helpers for task consumers during migration.
- Replaced the RPG visual stimulus wrapper with a persistent worker runtime.
- Added YAML grating spec validation and NumPy precomputation.
- Added fake-backend timing tests and a hardware-gated DRM smoke test.

## Notes
- `sample_tasks/` exists locally but is currently untracked in this branch.
- `.gitignore` has local uncommitted changes in this working tree.
