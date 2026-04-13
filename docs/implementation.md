## 2026/04/08

Provisioning and desktop plotting verification were brought to a reproducible checkpoint for the Raspberry Pi 5 hardware setup. A Pi 5 / Trixie provisioning manifest, verifier, bootstrap script, and Ansible scaffold were added so the runtime and development dependencies can be installed in a consistent way. The plotting path was refactored so desktop plotting is checked separately from the DRM/headless media runtime: plotting dependency checks are now lightweight, desktop-session aware, and guarded by a subprocess probe with a timeout instead of happening at `BehavBox` import time.

The current hardware verification step passed on the Pi for the provisioning checks and the desktop plotting checks. Specifically, the provisioning-oriented tests passed, the repository verifier passed both with and without the desktop plotting requirement, and the visible desktop plotting smoke test opened successfully on hardware. One separate GPIO-related incompatibility was also exposed during broader `BehavBox.prepare_session()` testing on the Pi 5, but that is a different issue from the plotting-path work and was not treated as a blocker for this checkpoint.

Files to push to git to save this progress:

- `.gitignore`
- `box_runtime/behavior/behavbox.py`
- `box_runtime/behavior/plotting_support.py`
- `deploy/ansible/README.md`
- `deploy/ansible/inventory.example.ini`
- `deploy/ansible/pi5_trixie.yml`
- `docs/implementation.md`
- `docs/rpi_os_package_requirements.md`
- `environment/rpi5_trixie.py`
- `environment/rpi5_trixie_manifest.json`
- `environment/rpi5_trixie_verifier.py`
- `scripts/bootstrap_pi5_trixie.sh`
- `tests/test_behavbox_plotting.py`
- `tests/test_plotting_support.py`
- `tests/test_rpi5_trixie_provisioning.py`

## 2026/04/09

Dual-camera bring-up and runtime stabilization were completed for the Pi 5 test host at `10.49.98.223`. Initial dual-camera detection failed when relying on auto-detection alone, so the Pi boot camera configuration was switched to explicit dual IMX708 overlays. After reboot, both cameras were detected and could be opened individually and simultaneously through `picamera2`.

Two camera-runtime regressions were then fixed in project code:

- `Picamera2Recorder` helper methods were restored as proper class methods (`configure`, `_finalize_current_session`, `_state_path`, `_load_state`, `_write_state`) after an indentation regression that made them unreachable.
- The preview-stream sink was made compatible with current `picamera2` expectations by making `_StreamingOutput` implement `io.BufferedIOBase`.
- A stop-time callback race was fixed by:
  - adding `_append_frame_metadata()` with a writer-availability guard, and
  - clearing `pre_callback` before recording teardown.

Regression tests were added for the above fixes:

- `test_picamera2_recorder_exposes_state_helper_methods`
- `test_picamera2_recorder_recover_live_sessions_marks_ready`
- `test_picamera2_streaming_output_is_bufferedio_compatible`
- `test_picamera2_append_frame_metadata_is_noop_without_frame_writer`
- `test_picamera2_append_frame_metadata_writes_expected_values`

Validation summary:

- Local camera test subset: `27 passed` for
  - `tests/test_one_pi_media_runtime.py`
  - `tests/test_camera_service.py`
- Pi hardware checks (real cameras):
  - `rpicam-hello --list-cameras` reports two cameras.
  - `Picamera2.global_camera_info()` reports count `2`.
  - `CameraManager` two-camera session smoke (`camera0` + `camera1`) starts and stops cleanly.
  - Session artifacts (`session.mp4`, `session.tsv`, manifests, and raw attempt files) are produced for both cameras under `/tmp/dual_camera_runtime_smoke_20260409/`.

Files to push to git to save this progress:

- `box_runtime/video_recording/picamera2_recorder.py`
- `tests/test_camera_service.py`
- `docs/implementation.md`

## 2026/04/10

Head-fixed go/no-go visual stimulus integration was updated so drifting gratings are now part of the normal task path on Raspberry Pi 5 hardware runs. The task/runtime wiring already had compiled grating playback support, but the sample task configuration and task logic were not actually using it.

The following functional gaps were fixed:

- `sample_tasks/head_fixed_gonogo/session_config.py`
  - enabled `visual_stimulus` by default for this task profile,
  - added explicit `vis_gratings` entries for `go_grating.yaml` and `nogo_grating.yaml`,
  - added visual display configuration defaults (connector `HDMI-A-2`, refresh/degrees fields),
  - selected display backend dynamically (`drm` on Raspberry Pi, `fake` off-Pi) so tests and non-Pi development remain usable.
- `sample_tasks/head_fixed_gonogo/task.py`
  - added trial-type-to-grating mapping:
    - `go` trials show `go_grating`
    - `nogo` trials show `nogo_grating`
  - added guarded visual-stimulus invocation so non-visual sessions keep working.
- `sample_tasks/head_fixed_gonogo/defaults.json`
  - added `go_grating_name` and `nogo_grating_name` task defaults.
- `box_runtime/behavior/behavbox.py`
  - added user-facing `show_grating(grating_name)` wrapper with explicit runtime-availability error handling.
- `box_runtime/visual_stimuli/visualstim.py`
  - added idempotent `close()` and routed `__del__` through it.

Tests were written first and used as the implementation gate. New/updated test coverage includes:

- go/nogo trial-to-grating mapping checks,
- session-config visual defaults checks,
- `BehavBox.show_grating(...)` behavior (error and delegation),
- `VisualStim.close()` idempotence/worker shutdown behavior.

Validation run:

- `BEHAVBOX_MOCK_UI_AUTOSTART=0 python3 -m pytest tests/test_head_fixed_gonogo.py tests/test_task_runner.py tests/test_visualstim_runtime.py`
- Result: `32 passed, 1 skipped`.

Additional Pi 5 DRM hardware validation and fix (host `10.49.98.223`):

- Initial HDMI-A-2 smoke from SSH failed with:
  - `atomic mode set failed with -13` while desktop compositor (`labwc` via `lightdm`) owned DRM planes.
- Running in compositor-free mode exposed a second runtime issue:
  - transient `atomic page flip failed with -16` (busy) in DRM playback.
- `box_runtime/visual_stimuli/visual_runtime/drm_runtime.py` was updated to retry transient atomic commit failures (`EBUSY` / `EAGAIN`) with short bounded backoff.
- Added regression tests:
  - `test_atomic_commit_with_retry_succeeds_after_transient_busy`
  - `test_atomic_commit_with_retry_returns_last_retryable_failure`
- Local validation:
  - `BEHAVBOX_MOCK_UI_AUTOSTART=0 python3 -m pytest tests/test_visualstim_runtime.py`
  - Result: `19 passed, 1 skipped`.
- Pi hardware smoke in headless compositor-free mode:
  - `go_grating` then `nogo_grating` both queued and rendered,
  - runtime metrics reported `play_count = 2`, `timing_entries = 2`,
  - session artifact root used: `/tmp/behavbox_visual_smoke/visual_grating_smoke_final_<timestamp>`.
- After smoke, `lightdm`/`labwc` desktop service was restored.

Known residual issue observed during headless smoke cleanup:

- shutdown path can raise `lgpio.error: 'GPIO busy'` during `box.close()` in this forced compositor-free test mode. This did not block grating playback validation but should be cleaned up separately if headless visual smoke is made part of routine bench automation.

Files to push to git to save this progress:

- `sample_tasks/head_fixed_gonogo/session_config.py`
- `sample_tasks/head_fixed_gonogo/defaults.json`
- `sample_tasks/head_fixed_gonogo/task.py`
- `box_runtime/behavior/behavbox.py`
- `box_runtime/visual_stimuli/visualstim.py`
- `box_runtime/visual_stimuli/visual_runtime/drm_runtime.py`
- `tests/test_head_fixed_gonogo.py`
- `tests/test_task_runner.py`
- `tests/test_visualstim_runtime.py`
- `docs/implementation.md`

### Task Lifecycle Updates

`BehavBox` lifecycle handling was tightened on branch `implement_gonogo` so that lifecycle state publication now routes through a single helper instead of being updated ad hoc in each method. `poll_runtime()` was changed to allow pre-start housekeeping in the `prepared` state while returning no drained events, and it continues to raise cleanly after `stop_session()`. This keeps pre-run status/housekeeping possible without letting prepared-state polling consume task inputs or mutate task-facing behavior.

Lifecycle regression coverage was expanded to include:

- pre-start `poll_runtime()` housekeeping behavior
- post-stop `poll_runtime()` rejection
- safe `close()` after `prepare_session()` without `start_session()`

Validation summary:

- Pi RED phase reproduced the old failure: `poll_runtime()` rejected the `prepared` state.
- Pi GREEN phase passed after the lifecycle change: `20 passed` for
  - `tests/test_task_runner.py`
  - `tests/test_head_fixed_gonogo.py`
  - `tests/test_behavbox_plotting.py`
  - `tests/test_one_pi_media_runtime.py`

### Direct Camera Preview DRM Resilience Update

To address repeated camera preview warnings like `preview atomic mode set failed with -13` while keeping preview in retry mode, the direct DRM preview loop now uses bounded recovery behavior instead of unbounded per-frame warning spam.

Code changes:

- `box_runtime/video_recording/drm_preview_viewer.py`
  - `DirectJpegPreviewViewer` now accepts:
    - `max_consecutive_errors_before_reinit` (default `3`)
    - `runtime_retry_backoff_s` (default `1.0`)
  - on repeated init/render errors, preview runtime tears down and reinitializes after backoff (keeps retrying, does not hard-disable),
  - warning logs are throttled to first/power-of-two repeats (`1, 2, 4, 8, ...`) to prevent log flooding.

Tests added first (RED->GREEN):

- `tests/test_drm_preview_viewer.py`
  - `test_direct_preview_viewer_recovers_by_reinitializing_backend`
  - `test_direct_preview_viewer_throttles_repeated_error_logs`

Validation:

- `python3 -m pytest tests/test_drm_preview_viewer.py -q` -> `7 passed`
- `python3 -m pytest tests/test_one_pi_media_runtime.py tests/test_head_fixed_gonogo.py tests/test_visualstim_runtime.py tests/test_task_runner.py -q`
  -> `42 passed, 1 skipped`

Note:

- Pi-side hardware validation of this resilience patch is pending because SSH authentication to `pi@10.49.98.223` was unavailable in the current session.

### Follow-up Pi Validation and Repo Cleanup

After SSH access was restored to `pi@10.49.98.223`, the resilience patch was synced to the Pi and validated in-place.

Pi sync/update:

- Synced:
  - `box_runtime/video_recording/drm_preview_viewer.py`
  - `tests/test_drm_preview_viewer.py`
  - `docs/implementation.md`
- Pi test result:
  - `python3 -m pytest tests/test_drm_preview_viewer.py -q`
  - `7 passed`

Pi dual-display smoke (camera preview + drifting gratings):

- Ran in compositor-free mode (temporarily stopped `lightdm`) using:
  - camera preview: `camera0` on `HDMI-A-1`
  - visual gratings: `go_grating` / `nogo_grating` on `HDMI-A-2`
- Observed behavior:
  - grating queue/display path remained functional,
  - preview kept retrying (as configured),
  - warning spam reduced from per-frame flooding to bounded repeated warnings per retry cycle.
- Residual behavior still present:
  - preview modeset can still fail with `-13` on this setup (underlying DRM ownership/permissions path not fully resolved),
  - close path may still raise `lgpio.error: 'GPIO busy'` in this forced headless smoke mode.
- Desktop service restored after validation:
  - `sudo systemctl start lightdm`

Repository hygiene cleanup on Pi:

- Removed accidentally copied root-level files:
  - `/home/pi/RPi4_behavior_boxes_hardware/drm_preview_viewer.py`
  - `/home/pi/RPi4_behavior_boxes_hardware/test_drm_preview_viewer.py`
  - `/home/pi/RPi4_behavior_boxes_hardware/implementation.md`
- Confirmed they no longer appear as untracked files in Pi `git status`.

### Virtual-Display Safe Preview Default

To support workflows where Raspberry Pi desktop/virtual display (`lightdm`) remains active, camera preview default behavior was changed from DRM-local preview to Qt desktop preview.

Code changes:

- `box_runtime/video_recording/local_camera_runtime.py`
  - added `RpicamQtPreviewProcess` wrapper for `rpicam-hello --qt-preview`,
  - added `qt_local` camera preview mode in `LocalCameraRuntime.start_preview()`,
  - made recorder instantiation lazy for preview-only `qt_local` sessions so preview can own the camera without Picamera2 pre-claiming it,
  - threaded optional `qt_preview_process_factory` through `CameraManager` for testability.
- `sample_tasks/head_fixed_gonogo/session_config.py`
  - changed default preview mode to `{"camera0": "qt_local"}`.

Tests (RED -> GREEN):

- `tests/test_one_pi_media_runtime.py`
  - added `test_local_camera_runtime_qt_preview_mode_starts_desktop_preview_process`.
- `tests/test_head_fixed_gonogo.py`
  - updated session-config expectation to `qt_local` preview default.

Validation:

- `python3 -m pytest tests/test_one_pi_media_runtime.py tests/test_head_fixed_gonogo.py -q` -> `15 passed`
- `python3 -m pytest tests/test_task_runner.py tests/test_visualstim_runtime.py tests/test_drm_preview_viewer.py -q`
  -> `35 passed, 1 skipped`

### Display Mode Toggle Script (Desktop vs Experiment)

To support fast switching between normal virtual-desktop use and DRM-exclusive experiment runs, a mode-aware runner script was added:

- `scripts/run_head_fixed_gonogo_mode.py`

Behavior:

- `--display-mode desktop`
  - runs `sudo systemctl start lightdm` before launch,
  - applies session override `camera_preview_modes={"camera0": "qt_local"}`.
- `--display-mode experiment`
  - runs `sudo systemctl stop lightdm` before launch,
  - applies session override `camera_preview_modes={"camera0": "drm_local"}`,
  - always attempts `sudo systemctl start lightdm` in `finally` after the run.
- `--dry-run`
  - prints planned `systemctl` actions and exits without running a session.

Helper module:

- `sample_tasks/head_fixed_gonogo/display_mode.py`
  - `apply_display_mode_overrides(...)`
  - `build_lightdm_action_plan(...)`

Tests added first:

- `tests/test_head_fixed_display_mode.py`
  - desktop/experiment preview-mode override checks,
  - mode validation error check,
  - lightdm action-plan checks.

Validation:

- `python3 -m pytest tests/test_head_fixed_display_mode.py tests/test_one_pi_media_runtime.py tests/test_head_fixed_gonogo.py tests/test_task_runner.py -q`
  - `29 passed`

### Desktop Visual Backend (`xwindow`) for Virtual-Display Coexistence

To support simultaneous desktop camera preview and drifting gratings while `lightdm` remains active, a desktop visual backend was added.

Code changes:

- `box_runtime/visual_stimuli/visual_runtime/drm_runtime.py`
  - added `xwindow` backend support to `query_display_config(...)`,
  - added `_XWindowDisplayBackend` using `pygame` fullscreen rendering,
  - added connector-to-display index mapping (`HDMI-A-1 -> display 0`, `HDMI-A-2 -> display 1`),
  - backend selection now supports `fake`, `drm`, and `xwindow`.
- `sample_tasks/head_fixed_gonogo/display_mode.py`
  - desktop mode now applies:
    - `camera_preview_modes={"camera0": "qt_local"}`
    - `visual_display_backend="xwindow"`
  - experiment mode applies:
    - `camera_preview_modes={"camera0": "drm_local"}`
    - `visual_display_backend="drm"`

Tests added/updated first:

- `tests/test_visualstim_runtime.py`
  - `test_query_display_config_xwindow_uses_requested_resolution_and_refresh`
- `tests/test_head_fixed_display_mode.py`
  - mode override tests now assert visual backend override (`xwindow` vs `drm`).

Validation:

- `BEHAVBOX_MOCK_UI_AUTOSTART=0 python3 -m pytest tests/test_head_fixed_display_mode.py tests/test_visualstim_runtime.py -q`
  - `25 passed, 1 skipped`
- `BEHAVBOX_MOCK_UI_AUTOSTART=0 python3 -m pytest tests/test_one_pi_media_runtime.py tests/test_head_fixed_gonogo.py tests/test_task_runner.py tests/test_drm_preview_viewer.py -q`
  - `31 passed`

### GPIO Busy Close-Path Guard (Desktop Dual-Display Runs)

During desktop-mode dual-display runs (Qt camera preview + xwindow drifting gratings), teardown could previously raise:

- `lgpio.error: 'GPIO busy'`

This was occurring in hardware device close paths and could surface near shutdown even after a successful run.

Code changes:

- `box_runtime/input/service.py`
  - added GPIO-busy detection helper for close-time exceptions,
  - added safe-close wrapper that ignores known `GPIO busy` teardown errors and logs a warning,
  - updated `InputService.close()` to use safe-close behavior for each opened device,
  - disabled repeated future `close()` calls on already-closed/ignored-busy devices to reduce destructor-time re-raises.
- `box_runtime/output/service.py`
  - applied the same safe-close strategy for output devices and `OutputService.close()`.

Tests added/updated first:

- `tests/test_input_service.py`
  - safe-close ignores GPIO busy,
  - safe-close raises non-GPIO errors,
  - normal close still succeeds,
  - repeated close after busy ignore does not re-raise.
- `tests/test_output_service.py`
  - same coverage for output device close behavior.

Validation:

- Local:
  - `BEHAVBOX_MOCK_UI_AUTOSTART=0 python3 -m pytest tests/test_input_service.py tests/test_output_service.py -q`
    - `21 passed`
  - `BEHAVBOX_MOCK_UI_AUTOSTART=0 python3 -m pytest tests/test_head_fixed_display_mode.py tests/test_one_pi_media_runtime.py tests/test_head_fixed_gonogo.py tests/test_task_runner.py tests/test_visualstim_runtime.py tests/test_input_service.py tests/test_output_service.py -q`
    - `70 passed, 1 skipped`
- Raspberry Pi 5:
  - `python3 -m pytest tests/test_input_service.py tests/test_output_service.py -q`
    - `21 passed`
  - `python3 scripts/run_head_fixed_gonogo_mode.py --display-mode desktop --max-trials 2 --max-duration-s 30 --session-tag gpio_busy_close_fix_check3`
    - run completed and wrote final task state successfully,
    - `GPIO busy` now appears as close-time warnings (no uncaught crash).

## 2026/04/13
### Raspberry Pi 5 Camera and Display Findings 

This section summarizes what was exercised on the Raspberry Pi 5 while trying to
show live camera preview on one monitor and drifting gratings on the other.

What worked:

- The bounded head-fixed go/no-go task runner completed on the Pi:
  - `python3 scripts/run_head_fixed_gonogo_mode.py --display-mode desktop --max-trials 5 --max-duration-s 120 --session-tag go_nogo_pi_run`
- Browser-based camera preview worked reliably through the HTTP camera service:
  - `/monitor`
  - `/stream.mjpg`
- Desktop-mode task runs could start the Qt preview process and queue
  `go_grating` / `nogo_grating` stimuli without crashing the session.

What did not work reliably:

- Qt local camera preview was not consistently visible on the user-observed
  desktop, even when the preview process started successfully.
- Desktop-mode drifting gratings were not visibly rendered on the intended
  monitor, despite the `xwindow` backend running and logging normal stimulus
  loop activity.
- Direct DRM/KMS grating attempts were blocked by display ownership:
  - `atomic mode set failed with -13`

What we found:

- The repository's intended one-Pi topology is:
  - camera preview -> `HDMI-A-1`
  - visual stimulus -> `HDMI-A-2`
  This is enforced in `BehavBox.validate_media_config()`.
- Desktop mode does not actually route camera preview by HDMI connector.
  Instead, `sample_tasks/head_fixed_gonogo/display_mode.py` sets
  `camera_preview_display=":0"`, so `qt_local` preview follows the desktop
  display server session rather than true connector ownership.
- The desktop visual path uses the `xwindow` backend rather than DRM:
  - `HDMI-A-1 -> display index 0`
  - `HDMI-A-2 -> display index 1`
  This relies on the active desktop compositor exposing both displays to the
  session that launches the grating process.
- In practice, that desktop-session assumption did not hold in the tested Pi
  setup. The grating loop could run, but the fullscreen window did not reach a
  visible desktop surface from the SSH-driven launch path.

Current interpretation:

- "Camera appears on monitor 0" is explained by desktop-mode preview using
  `DISPLAY=:0`, not by reliable connector-based routing.
- "Drifting grating does not show" is currently a display backend / desktop
  ownership problem, not evidence that grating generation itself is broken.
- For the tested setup, browser preview is the only confirmed reliable camera
  visualization path.

### Matt updates - camera and display

We shifted the next validation step away from task integration and onto minimal
headless debug scripts in `debug/` so the supported one-Pi topology could be
tested directly: camera preview alone on `HDMI-A-1`, drifting gratings alone on
`HDMI-A-2`, and later both together. A shared headless-mode guard was added so
these scripts fail loudly when `lightdm`, `DISPLAY`, or `WAYLAND_DISPLAY` are
still active, instead of giving misleading desktop-mode results.

Hardware validation on the Pi 5 then confirmed two things. First, the standalone
camera preview smoke succeeded on `HDMI-A-1` in compositor-free mode. Second,
the standalone grating smoke initially exposed a real DRM runtime bug:
`_PykmsDisplayBackend` was calling `_wait_for_flip_complete()` even though that
helper had been attached to the wrong backend class. After moving the helper to
the DRM backend and adding focused regression tests, the headless grating smoke
passed on `HDMI-A-2`.