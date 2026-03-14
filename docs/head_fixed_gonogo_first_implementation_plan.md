# Implement `head_fixed_gonogo` First With a Real Lifecycle Split and Local Mock Testing

## Summary

Implement the lifecycle/task-runner refactor around a single first sample task:
`head_fixed_gonogo`. Do **not** start with `freely_moving_video`; that is the
wrong first slice on a Mac because it mostly tests camera plumbing without the
actual Pi camera stack.

This first slice will:

- do the **hard split** of `BehavBox` into an explicit lifecycle
- add the shared task runner and FSM helpers
- add one `head_fixed_gonogo` sample task that can be run locally on the
  MacBook in mock mode
- reuse the existing generic mock hardware web UI for manual inputs
- add a programmatic mock input injector for tests
- use the audio service API, but with a mock playback backend locally so Mac
  testing does not depend on ALSA

The first go/no-go will be intentionally narrow: one response input, audio
cues, reward delivery, timeout handling, standardized artifacts, and clean
lifecycle/error handling. Visual stimulus stays out of the first slice.

## Key Changes

### 1. `BehavBox` lifecycle becomes explicit

Refactor `BehavBox` so `__init__` becomes lightweight and no longer performs the
full run setup. The new public lifecycle is:

- `prepare_session()`
- `start_session()`
- `poll_runtime()`
- `stop_session()`
- `finalize_session()`
- `close()`

Implementation contract:

- `__init__` stores config and injected factories only
- `prepare_session()` creates directories, logging, hardware services,
  plotting/mock UI hooks, and task-ready resources
- `start_session()` marks the run active and starts task-owned
  recording/services
- `poll_runtime()` drains events, mock UI state, and lightweight
  watchdog/runtime work
- `stop_session()` stops active services and emits session-stop events
- `finalize_session()` writes standardized artifacts and flushes state
- `close()` is idempotent and always safe after partial setup or task failure

For testability, `BehavBox` gains optional injected factories for:

- sound runtime
- camera client/runtime hooks
- clock/time source
- mock input server enablement

### 2. Shared sample-task framework

Add a small `sample_tasks/common/` layer with:

- `task_api.py` for the task hook contract
- `runner.py` for lifecycle sequencing and guaranteed cleanup
- `fsm.py` for simple explicit phase transitions
- `session_artifacts.py` for `final_task_state.json` and task event writing
- `mock_inputs.py` for a programmatic input injector built on the existing mock
  registry/input path

Task API for v1:

- `prepare_task(box, task_config) -> task_state`
- `start_task(box, task_state) -> None`
- `handle_event(box, task_state, event) -> None`
- `update_task(box, task_state, now_s) -> None`
- `should_stop(box, task_state) -> bool`
- `stop_task(box, task_state, reason) -> None`
- `finalize_task(box, task_state) -> dict`

Runner contract:

- creates/prepares the box
- runs the lifecycle in order
- drains box events into task handlers
- guarantees `stop_task`, `stop_session`, `finalize_session`, and `close` on
  error paths
- writes `final_task_state.json`
- writes a minimal `task_events.jsonl` for lifecycle and phase transitions

### 3. First sample task: `head_fixed_gonogo`

Implement only `sample_tasks/head_fixed_gonogo/` in this slice.

V1 task shape:

- phases: `iti`, `stimulus`, `response_window`, `reward`, `timeout`,
  `inter_trial_cleanup`
- default input profile: `head_fixed`
- default visual stimulus: off
- default local control: mock web UI plus programmatic injector

To keep the first slice narrow and manually testable, the task uses **one
response input**:

- canonical response event: `center_entry`
- local mock operator action: pulse `lick_3` in the generic mock UI

Trial semantics:

- each trial is labeled `go` or `nogo`
- `stimulus` plays the corresponding audio cue
- during `response_window`:
  - `go` + `center_entry` -> `reward`
  - `go` + no response -> omission / next ITI
  - `nogo` + `center_entry` -> `timeout`
  - `nogo` + no response -> correct reject / next ITI
- reward uses one stable output path, default `reward_center`

Task state includes:

- current phase
- current trial type
- counters for hits, misses, false alarms, correct rejects, completed trials
- stop reason
- serializable `adaptive_params` dict, but no nontrivial adaptation logic in
  this first slice

Artifacts:

- `final_task_state.json`
- `task_events.jsonl`
- existing legacy `.log`
- existing input-service artifacts continue unchanged

### 4. Local MacBook testing path

Local runs must work without Pi hardware.

Use these defaults in local/mock mode:

- `BEHAVBOX_FORCE_MOCK=1`
- existing generic mock hardware web UI for manual input
- programmatic injector API for tests
- audio service API remains in use, but `SoundRuntime` is backed by a mock
  recording backend locally instead of ALSA
- camera behavior is out of scope for this first slice

Do **not** build a dedicated go/no-go browser page yet. Reuse the current mock
UI and keep the first slice about task/lifecycle correctness.

## Test Plan

Write tests first, confirm RED, then implement.

### Lifecycle and runner tests

- valid lifecycle order succeeds
- invalid lifecycle order raises clean errors
- `close()` is idempotent after full and partial setup
- task exceptions still trigger `stop_task`, `stop_session`,
  `finalize_session`, and `close`
- runner writes `final_task_state.json`
- runner writes `task_events.jsonl` with at least session and phase-transition
  events

### Go/no-go FSM tests

- `go` trial + `center_entry` in response window yields reward and increments
  hit count
- `go` trial + no response yields miss/omission and no reward
- `nogo` trial + `center_entry` yields timeout and increments false-alarm count
- `nogo` trial + no response yields correct reject
- manual stop exits cleanly from any phase
- `max_trials` and `max_duration_s` stop conditions work as safety limits

### Local mock integration tests

- task runs on the MacBook mock backend with `BEHAVBOX_FORCE_MOCK=1`
- generic mock UI/API can pulse `lick_3` and drive the task through valid
  transitions
- programmatic injector can drive the same canonical input path as the browser
  UI
- mock audio backend records cue-play requests without requiring ALSA
- reward calls occur on the expected output path in mock mode

### Regression/compatibility tests

- existing input-service event logging still works during task runs
- legacy `.log` output still contains expected event lines
- `BehavBox` methods used by current tests are updated to the new lifecycle or
  explicitly covered by compatibility tests where needed

## Assumptions and Defaults

- First slice implements **only** `head_fixed_gonogo`; `freely_moving_video` is
  deferred until Pi camera hardware is available.
- The first go/no-go is a **single-response** task using `center_entry` as the
  canonical response event.
- Visual stimulus is out of scope for this slice and stays disabled by default.
- Audio is part of the task contract, but local Mac runs use a mock playback
  backend rather than ALSA.
- Manual local testing uses the **existing generic mock hardware web UI**; no
  task-specific UI page is added yet.
- Default stop behavior is manual stop plus optional `max_trials` /
  `max_duration_s` safety limits.
- The lifecycle split is done as a real migration now, not as an additive
  wrapper around the old constructor behavior.
