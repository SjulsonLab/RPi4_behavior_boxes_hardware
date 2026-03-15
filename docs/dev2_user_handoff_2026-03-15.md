# Dev2 User Handoff 2026-03-15

Repo: `RPi4_behavior_boxes_hardware`  
Worktree: `targets/RPi4_behavior_boxes_hardware_dev2`  
Branch: `dev2`

## Purpose

This note is for users who will continue development on `dev2`. It summarizes
the current organization of the code, what has been completed, and what remains
open.

## Main Organization

### `box_runtime/`

This is the hardware/runtime layer.

- `box_runtime/behavior/`
  - `behavbox.py` is the main hardware-facing runtime object.
  - GPIO/media/session lifecycle behavior now routes through explicit prepare,
    start, stop, and finalize phases.
- `box_runtime/mock_hw/`
  - contains the mock hardware registry, HTTP server, and browser UI
  - `registry.py` is the mock runtime state/event store
  - `server.py` launches the lightweight HTTP server
  - `web.py` defines routes and API handlers
  - `operator_controller.py` manages one operator-launched run at a time
  - `static/index.html` is the low-level debug page
  - `static/operator.html` is the operator-facing page
- camera/audio/visual code remains under the rest of `box_runtime/` and is
  still the place to look for real hardware integration work

### `sample_tasks/`

This is the reference task layer used for bench testing and validation.

- `sample_tasks/common/`
  - `runner.py` contains `TaskRunner`
  - `task_api.py` defines the task contract
  - `fsm.py` contains the small FSM helper layer
  - `mock_inputs.py` contains canonical mock input injection helpers
  - `session_artifacts.py` writes standardized session artifacts
- `sample_tasks/head_fixed_gonogo/`
  - `task.py` contains the go/no-go FSM and published task state
  - `run.py` is the CLI entrypoint
  - `session_config.py` contains shared session/task config construction
  - `fake_mouse.py` contains the seeded simulated mouse
  - `plot_state.py` publishes browser-facing performance plot state

### `tests/`

The most relevant tests for this work are:

- `tests/test_operator_ui.py`
- `tests/test_task_runner.py`
- `tests/test_fake_mouse_and_plotting.py`
- `tests/test_head_fixed_gonogo.py`
- `tests/test_mock_hardware.py`
- `tests/test_camera_service.py`
- `tests/test_one_pi_media_runtime.py`

## What Has Been Completed

### Operator UI

- There is now a separate operator-facing page distinct from the low-level mock
  debug page.
- Current routing:
  - operator page: `/`
  - debug page: `/debug`
  - `/operator` still redirects to `/` for compatibility
- The operator flow is now:
  - `Arm Session`
  - `Start Task`
  - `Stop Session`
- The operator page now includes:
  - session control
  - camera preview area directly below session control
  - runtime summary
  - live performance plot
  - event summary table
  - bounded scrollable operator-state JSON panel at the bottom

### Operator Backend

- `OperatorRunController` now supports an explicit operator state machine:
  - `idle`
  - `arming`
  - `armed`
  - `starting`
  - `running`
  - `stopping`
  - `completed`
  - `error`
- Runs are prepared first and do not start until the user presses `Start Task`
  in the browser.
- Operator-launched runs use a dedicated output root.

### Head-Fixed Go/No-Go Reference Task

- `head_fixed_gonogo` is now the reference operator-launched task.
- The CLI and operator UI share one session-config path.
- Final task state and session artifacts are written in a standardized way.

### Fake Mouse

- A seeded fake mouse is implemented for go/no-go testing.
- It injects through the mock input path instead of mutating task state
  directly.
- It can be enabled from:
  - the operator UI at arm time
  - the CLI via `--fake-mouse` and `--fake-mouse-seed`

### Live Browser Plot

- The operator page renders live go/no-go performance in the browser.
- Plot data is published under runtime state rather than inferred only in the
  frontend.
- The current plot shows:
  - trial outcome timeline
  - cumulative hit rate
  - cumulative false-alarm rate

### Validation Already Done

- Focused local regression for the operator UI and related task plumbing
- Pi validation on `pi@192.168.1.204`
- Remote browser access from a laptop to the Pi-hosted operator UI
- Current memorable Pi URL convention:
  - operator page: `http://<pi-ip>:8000/`
  - debug page: `http://<pi-ip>:8000/debug`

## What Still Needs To Be Done

### Real Hardware Validation

- Real camera preview/recording still needs validation on a Pi with attached
  camera hardware.
- Real display/visual stimulus validation still needs a Pi with the intended
  monitor path attached.
- The current Pi used during development had zero detected cameras.

### Operator UI Improvements

- The operator page is usable, but it is still a bench/developer UI rather than
  a polished production operator console.
- Plotting is currently specific to go/no-go. Additional tasks will need their
  own published plot state and page rendering.
- Camera preview layout is ready, but browser preview is only as good as the
  camera runtime actually publishing preview URLs.

### Generalization

- The operator launcher currently supports only `head_fixed_gonogo`.
- If more reference tasks are added, the operator controller and UI will need a
  task-selection layer instead of fixed go/no-go assumptions.

### Cleanup / Documentation

- The README still describes the old mock UI default URL (`127.0.0.1:8765`) and
  should be updated if `8000` routing becomes the stable convention.
- Older handoff notes describe earlier intermediate states and should not be
  treated as the final `dev2` operator UI design.

## Recommended Entry Points For Future Work

- Operator routes and page structure:
  - `box_runtime/mock_hw/web.py`
  - `box_runtime/mock_hw/static/operator.html`
  - `box_runtime/mock_hw/static/operator.css`
  - `box_runtime/mock_hw/static/operator.js`
- Operator run lifecycle:
  - `box_runtime/mock_hw/operator_controller.py`
- Runtime publication:
  - `box_runtime/mock_hw/registry.py`
  - `sample_tasks/head_fixed_gonogo/plot_state.py`
- Task execution:
  - `sample_tasks/common/runner.py`
  - `sample_tasks/head_fixed_gonogo/task.py`
- Fake mouse:
  - `sample_tasks/head_fixed_gonogo/fake_mouse.py`

## Quick Validation Commands

Local focused regression:

```bash
uv run --with pytest --with flask --with numpy --with scipy --with colorama pytest tests/test_operator_ui.py
```

Run the mock/operator server locally:

```bash
BEHAVBOX_FORCE_MOCK=1 \
BEHAVBOX_MOCK_UI_AUTOSTART=0 \
BEHAVBOX_MOCK_UI_HOST=0.0.0.0 \
BEHAVBOX_MOCK_UI_PORT=8000 \
uv run python -m sample_tasks.head_fixed_gonogo.run --fake-mouse --fake-mouse-seed 11
```

## Bottom Line

`dev2` now has a coherent split between:

- hardware/runtime orchestration in `box_runtime/`
- reference task logic in `sample_tasks/`
- operator launch/control in `box_runtime/mock_hw/`

The main unfinished work is not basic operator control anymore. It is the next
layer: real hardware validation, broader task support, and UI polish/general
cleanup.
