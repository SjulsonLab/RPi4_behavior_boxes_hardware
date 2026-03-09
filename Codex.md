# Codex.md

Last updated: 2026-02-22
Repo: `RPi4_behavior_boxes_hardware`
Primary branch in progress: `codex/hardware-integration-phase1`

## Purpose
Hardware/runtime support for RPi4 behavior boxes, including strict head-fixed GPIO mapping and a non-Pi mock hardware mode.

## Current Architecture
- Core behavior logic: `essential/behavbox.py`
- Backend selection: `essential/gpio_backend.py`
- Mock hardware stack:
- `essential/mock_hw/devices.py`
- `essential/mock_hw/registry.py`
- `essential/mock_hw/server.py`
- `essential/mock_hw/web.py`
- `essential/mock_hw/visual_stim.py`
- Tests: `tests/test_mock_hardware.py`

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

## Commands
- `cd /Users/lukesjulson/codex/RPi4_refactor/targets/RPi4_behavior_boxes_hardware`
- `pytest -q tests/test_mock_hardware.py`
- `python debug/run_mock_behavbox.py`

## Recent Changes
- Added wall-clock `BehaviorEvent` queue objects in `BehavBox`.
- Updated callback logging/interaction timestamps to use detection-time event timestamps.
- Added test coverage for event queue timestamp behavior.
- Maintained compatibility helpers for task consumers during migration.

## Notes
- `sample_tasks/` exists locally but is currently untracked in this branch.
- `.gitignore` has local uncommitted changes in this working tree.
