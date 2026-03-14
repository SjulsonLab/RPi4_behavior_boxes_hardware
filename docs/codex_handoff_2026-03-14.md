# Codex Handoff 2026-03-14

Repo: `RPi4_behavior_boxes_hardware`
Worktree: `targets/RPi4_behavior_boxes_hardware_rpgtest`
Branch: `rpgtest`

## Current State

- Latest commits:
  - `81b0479` `Add one-Pi media runtime tests`
  - `239450b` `Implement one-Pi local media runtime`
- Branch is pushed through `239450b`.
- Broad regression suite currently passing:
  - `uv run --with pytest --with flask --with numpy --with scipy --with colorama pytest tests/test_one_pi_media_runtime.py tests/test_input_service.py tests/test_output_service.py tests/test_mock_hardware.py tests/test_head_fixed_gonogo.py tests/test_task_runner.py tests/test_camera_service.py tests/test_audio_runtime.py tests/test_visualstim_runtime.py`
  - Result: `80 passed, 1 skipped`

## What Was Just Finished

- One-Pi automated camera control now uses a local camera runtime instead of the old HTTP client path inside `BehavBox`.
- The new camera path is multi-camera-capable at the API/config level:
  - canonical camera names are `camera0` and `camera1`
  - Pi4 may still be configured with both and fail cleanly if hardware is missing
- `BehavBox` is now the one-Pi media coordinator:
  - validates visual/camera display topology
  - owns local camera manager lifecycle
  - starts/stops local camera runtime during session lifecycle
- Visual stimulus config was standardized:
  - `visual_display_backend` is now the preferred key
  - default visual connector is now `HDMI-A-2`
- Local camera preview plumbing now exists for direct recorder frames via DRM/KMS, but browser preview is still not exposed in the one-Pi path.

## Relevant Files

- One-Pi camera runtime:
  - `box_runtime/video_recording/local_camera_runtime.py`
  - `box_runtime/video_recording/picamera2_recorder.py`
  - `box_runtime/video_recording/drm_preview_viewer.py`
- BehavBox media orchestration:
  - `box_runtime/behavior/behavbox.py`
- Runtime-state publication:
  - `box_runtime/mock_hw/registry.py`
  - `box_runtime/behavior/gpio_backend.py`
- Visual config entry point:
  - `box_runtime/visual_stimuli/visualstim.py`

## Important Constraints / Caveats

- The active one-Pi camera path is local/direct; the old HTTP camera client is no longer the active `BehavBox` path.
- Browser preview plumbing for future multi-camera use is not exposed yet.
- Current supported topology is intentionally narrow:
  - visual stimulus: `HDMI-A-2`
  - local camera preview: `HDMI-A-1`
  - only one `drm_local` camera preview at a time
- Visual stimulus still takes priority over camera preview.
- This has only been validated on the Mac mock/test environment so far, not yet on Pi4/Pi5 hardware.

## Next Likely Steps

- Bench the one-Pi media stack on real Pi4 and Pi5 hardware.
- Wire `head_fixed_gonogo` to real visual stimulus presentation and validate `HDMI-A-2` behavior.
- Expose per-camera runtime state in the eventual web UI.
- Add browser preview as a preview sink on top of the new `CameraManager`/`LocalCameraRuntime` plumbing.
- Add second-camera recording on Pi5 once the first-camera one-Pi path is validated on hardware.
