# Codex Handoff 2026-03-14

Repo: `RPi4_behavior_boxes_hardware`
Worktree: `targets/RPi4_behavior_boxes_hardware_rpgtest`
Branch: `rpgtest`

## Current State

- Working tree was clean before this handoff file was added.
- Latest functional commit before this note:
  - `d5bf578` `Expose task runtime state in mock web UI`
- Tests currently passing:
  - `uv run --with pytest pytest tests/test_head_fixed_gonogo.py tests/test_mock_hardware.py`
  - `uv run --with pytest --with flask --with numpy --with scipy --with colorama pytest tests/test_task_runner.py tests/test_head_fixed_gonogo.py tests/test_input_service.py tests/test_mock_hardware.py tests/test_camera_service.py tests/test_audio_runtime.py tests/test_visualstim_runtime.py`
  - Result: `67 passed, 1 skipped`

## What Was Just Finished

- `head_fixed_gonogo` is implemented as the first sample task.
- `BehavBox` now has an explicit lifecycle used by the sample-task runner.
- The mock web UI is no longer pin-only. It now renders a generic runtime-state contract:
  - `runtime.session`
  - `runtime.task`
  - `runtime.audio`
- During a live run, the browser can now show:
  - protocol name
  - lifecycle state
  - current phase
  - current trial number
  - current trial type
  - completed trials
  - whether the stimulus phase is active
  - current and last audio cue name

## Relevant Files

- Runtime-state publication:
  - `box_runtime/behavior/behavbox.py`
  - `box_runtime/audio/runtime.py`
  - `box_runtime/mock_hw/registry.py`
- Browser rendering:
  - `box_runtime/mock_hw/static/index.html`
  - `box_runtime/mock_hw/static/app.js`
  - `box_runtime/mock_hw/static/style.css`
- Sample task:
  - `sample_tasks/head_fixed_gonogo/task.py`
  - `sample_tasks/head_fixed_gonogo/run.py`
  - `sample_tasks/common/runner.py`

## How To Re-Run The Browser Test

From the repo worktree:

```bash
cd /Users/lukesjulson/codex/RPi4_refactor/targets/RPi4_behavior_boxes_hardware_rpgtest
uv run python -m sample_tasks.head_fixed_gonogo.run --max-trials 5 --max-duration-s 600
```

Then open:

- `http://127.0.0.1:8765`

For the current mock task:

- use `lick_3`
- pulse input from the generic mock UI
- the runtime panel should show task/phase/trial/audio state live

## Important Constraints / Caveats

- This is still a generic mock hardware page with runtime awareness, not yet a real protocol-specific operator UI.
- The current browser page is appropriate as the first layer of the eventual web interface because it consumes generic runtime state instead of task artifacts or log scraping.
- Visual stimulus is still disabled for this first `head_fixed_gonogo` slice, so no grating display is expected during the browser test.

## Next Likely Steps

- Add task-aware operator controls without bypassing the shared runtime-state API.
- Improve mock UI labeling so the active response input is explicit during the task.
- Decide whether the eventual real web interface should consume the same `/api/state` shape directly or through a task/session service above it.
