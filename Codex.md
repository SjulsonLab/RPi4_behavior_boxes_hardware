# Codex.md

Last updated: 2026-03-08
Repo: `RPi4_behavior_boxes_hardware`
Primary branch in progress: `main`

## Purpose
Hardware/runtime support for RPi4 behavior boxes, including strict head-fixed GPIO mapping and a non-Pi mock hardware mode.

## Current Architecture
- Runtime root: `box_runtime/`
- Behavior orchestration and GPIO backend:
- `box_runtime/behavior/behavbox.py`
- `box_runtime/behavior/gpio_backend.py`
- `box_runtime/behavior/ADS1x15.py`
- Camera HTTP control:
- `box_runtime/video_recording/http_camera_service.py`
- `box_runtime/video_recording/picamera2_recorder.py`
- `box_runtime/video_recording/camera_client.py`
- `box_runtime/video_recording/camera_session.py`
- `box_runtime/video_recording/VideoCapture.py`
- Archived legacy camera scripts:
- `box_runtime/video_recording/old/`
- Visual stimulus runtime:
- `box_runtime/visual_stimuli/visualstim.py`
- `box_runtime/visual_stimuli/visual_runtime/grating_specs.py`
- `box_runtime/visual_stimuli/visual_runtime/grating_compiler.py`
- `box_runtime/visual_stimuli/visual_runtime/drm_runtime.py`
- Treadmill support:
- `box_runtime/treadmill/Treadmill.py`
- Mock hardware stack:
- `box_runtime/mock_hw/devices.py`
- `box_runtime/mock_hw/registry.py`
- `box_runtime/mock_hw/server.py`
- `box_runtime/mock_hw/web.py`
- `box_runtime/mock_hw/visual_stim.py`
- Shared support code:
- `box_runtime/support/`
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
- `BehaviorEvent` is defined in `box_runtime/behavior/behavbox.py` and includes:
- `name: str`
- `timestamp: float` (wall-clock from `time.time()`, captured at detection time)
- Callback helper: `BehavBox._push_event(...)`.
- Compatibility helpers for downstream task code:
- `BehavBox.event_name(event)`
- `BehavBox.event_timestamp(event)`
- `interact_list` entries now reuse the same detection timestamp used for queue event creation.

## Profile-Aware GPIO Mapping
- Active GPIO mapping is defined in one fixed Python source of truth:
  `box_runtime/io_manifest.py`.
- There is no runtime CSV loading or alternate GPIO configuration source.
- If the pinout changes, the code should be edited directly.
- Canonical profile key: `box_profile`
  - fallback: `input_profile` if `box_profile` is absent
- Dedicated trigger lines:
  - `trigger_in`: GPIO23
  - `trigger_out`: GPIO24
- Generic user-configurable line:
  - GPIO4, claimed later as input or output if requested
- Head-fixed inputs:
  - `ir_lick_left/right/center`: GPIO5/6/12
  - `lick_left/right/center`: GPIO26/27/15
  - `treadmill_1/2`: GPIO13/16
- Freely-moving inputs:
  - `poke_left/right/center`: GPIO5/6/12
  - `poke_extra1/2`: GPIO13/16
- Shared outputs:
  - `reward_left/right/center`: GPIO19/20/21
  - `reward_4`: GPIO7
  - `vacuum`: GPIO25
  - `cue_led_1..6`: GPIO22/18/17/14/10/11
  - `trigger_out`: GPIO24
- Profile-specific GPIO8:
  - `head_fixed`: `airpuff`
  - `freely_moving`: `reward_5`
- User-facing manual-control surfaces show canonical names plus board aliases (for example `reward_left (pump1)`).
- Reserved-pin guard:
  - `box_runtime/behavior/gpio_backend.py` raises `ReservedPinError` for GPIO9.
  - GPIO9 is reserved for the IRIG sender output and should not be claimed by active BehavBox runtime code.

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
- Replaced RPG-based `box_runtime/visual_stimuli/visualstim.py` with a YAML-spec + precompute pipeline.
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

## Audio Runtime
- Direct audio playback now lives under `box_runtime/audio/`.
- Public `BehavBox` audio methods:
- `import_wav_file(...)`
- `load_sound(name)`
- `clear_sounds()`
- `play_sound(name, side=..., gain_db=..., duration_s=...)`
- `stop_sound()`
- `start_sound_calibration(...)`
- `stop_sound_calibration()`
- `measure_sound_latency(...)`
- Canonical cue directories:
- tracked cues: `box_runtime/audio/sounds/`
- gitignored raw sources: `box_runtime/audio/local_source_wavs/`
- gitignored bench cues: `box_runtime/audio/local_sounds/`
- Canonical cues are mono `48000 Hz` WAV files normalized to a white-noise RMS
  reference during import.
- Runtime playback is stereo `48000 Hz` signed 16-bit PCM, with side routing
  (`left`, `right`, `both`), duration override, looping, early stop, and
  deferred significant-clipping warnings.

## Commands
- `cd /Users/lukesjulson/codex/RPi4_refactor/targets/RPi4_behavior_boxes_hardware`
- `uv run --with numpy --with scipy --with colorama --with flask --with pytest pytest tests/test_camera_service.py`
- `uv run --with numpy --with scipy --with colorama --with pytest pytest tests/test_mock_hardware.py tests/test_visualstim_runtime.py`
- `uv run python debug/run_mock_behavbox.py`

## Recent Changes
- Added wall-clock `BehaviorEvent` queue objects in `BehavBox`.
- Updated callback logging/interaction timestamps to use detection-time event timestamps.
- Added test coverage for event queue timestamp behavior.
- Maintained compatibility helpers for task consumers during migration.
- Added an HTTP camera service with manual/automated ownership modes and browser preview/control pages.
- Added drift-aware frame UTC derivation and session finalization utilities for raw H.264 + TSV attempts.
- Replaced BehavBox camera SSH control with `CameraClient` HTTP control and verified offload.
- Migrated standalone capture control into `box_runtime/video_recording/VideoCapture.py`.
- Added camera-service tests covering timestamp fitting, finalization, ownership blocking, preview/control UI, and offload paths.
- Replaced the RPG visual stimulus wrapper with a persistent worker runtime.
- Added YAML grating spec validation and NumPy precomputation.
- Added fake-backend timing tests and a hardware-gated DRM smoke test.

## Notes
- `sample_tasks/` exists locally but is currently untracked in this branch.
- `.gitignore` has local uncommitted changes in this working tree.
