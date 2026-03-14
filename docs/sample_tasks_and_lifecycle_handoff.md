# Sample Tasks and Lifecycle Handoff

## Purpose

This document describes a proposed reorganization of task structure in the
hardware repository. It is intended as a handoff for future development work in
`RPi4_behavior_boxes_hardware`.

The immediate goal is to:

- add two sample tasks to the hardware repo for box bring-up and regression
  testing
- reorganize task execution around an explicit `BehavBox` lifecycle
- preserve finite state machine (FSM) task logic
- support adaptive task parameters that change in response to animal behavior
- standardize the artifacts written at the end of a session

This is not a proposal to turn the hardware repo into a second protocol repo.

## Key Decisions

### 1. The hardware repo should contain two sample tasks

The hardware repo should gain exactly two reference tasks:

- `freely_moving_video`
- `head_fixed_gonogo`

These are not meant to become living scientific protocols. They are reference
tasks used to debug and validate the box stack end to end.

The separate task repo remains the home for:

- active experimental protocols
- parameter evolution over time
- user- or lab-specific forks

### 2. `BehavBox` should expose an explicit lifecycle

`BehavBox` should continue to be the main hardware-facing object, but startup
and shutdown should no longer depend mainly on constructor side effects and task
specific glue.

The target lifecycle is:

1. `prepare_session()`
2. `start_session()`
3. `poll_runtime()`
4. `stop_session()`
5. `finalize_session()`
6. `close()`

### 3. Tasks should remain FSM-driven

The lifecycle model is not a replacement for the existing FSM task style.

The separation should be:

- `BehavBox` owns appliance orchestration
- the task owns protocol logic, phase transitions, and adaptive behavior

### 4. Adaptive parameters are task state, not session configuration

Some tasks need to update parameters in response to the animal, for example:

- increasing one reward amount and decreasing another after a repeated choice
- changing shaping thresholds
- modifying stimulus probabilities or timeouts

Those changes should not be made by mutating `session_info` ad hoc during the
run. Instead, they should live in task-owned mutable state and be written to
standard artifacts at the end of the session.

### 5. Every session should write a final task-state snapshot

At the end of a run, the system should preserve both:

- the event and log history
- the final serializable task state

The final task state is important because later analysis should not need to
replay every event just to know the last shaping stage, final reward values, or
stop reason.

## Repo Boundary

The hardware repo should contain:

- `BehavBox`
- hardware services
- lifecycle runner
- plotting support
- simulated mouse support
- two reference tasks used for validation

The task repo should contain:

- actual lab protocols
- real protocol parameter sets
- protocol development over time
- lab-specific forks

Guardrail:

The hardware repo should not accumulate additional scientific tasks unless they
serve the same role as the two reference tasks. Otherwise the repo split loses
its value.

## Proposed Architecture

### Runtime responsibilities

`BehavBox` should own:

- session preparation
- hardware and service initialization
- camera control
- sound runtime control
- visual stimulus runtime control
- plotting and state publication hooks
- standardized session finalization
- resource cleanup

### Task responsibilities

Each task should own:

- FSM phase definitions
- event handling
- time-based updates
- reward and stimulus decisions
- adaptive parameter updates
- trial counters and derived performance state

### Runner responsibilities

A small `TaskRunner` should own the sequencing between the box lifecycle and the
task callbacks.

The runner should:

- create the box
- call lifecycle methods in the right order
- call task hooks
- guarantee stop/finalize/close on normal exit or task error
- write standardized artifacts, including final task state

## Proposed File Layout

Suggested structure inside the hardware repo:

- `sample_tasks/common/task_api.py`
- `sample_tasks/common/runner.py`
- `sample_tasks/common/fsm.py`
- `sample_tasks/common/session_artifacts.py`
- `sample_tasks/common/simulated_mouse.py`
- `sample_tasks/freely_moving_video/task.py`
- `sample_tasks/freely_moving_video/defaults.json`
- `sample_tasks/head_fixed_gonogo/task.py`
- `sample_tasks/head_fixed_gonogo/defaults.json`
- `sample_tasks/head_fixed_gonogo/simulated_mouse_profiles.json`

This should stay small. Avoid deep inheritance trees or a framework-heavy
design.

## BehavBox Lifecycle Contract

### `prepare_session()`

Owns pre-run setup.

Expected responsibilities:

- validate and normalize `session_info`
- create session directories
- initialize logs
- initialize hardware and long-lived services
- prepare plotting and state publication
- prepare, but not yet start, optional media services

This stage should not begin the experiment.

### `start_session()`

Owns transition into the active run.

Expected responsibilities:

- mark the session as active
- start camera recording if configured
- arm or start required media services
- emit a structured `session_started` event

### `poll_runtime()`

Owns recurring non-task-specific runtime work.

Expected responsibilities:

- keyboard or mock input handling if enabled
- draining hardware event queues
- publishing runtime state to plotting or later dashboard consumers
- light watchdog or health checks if needed

This should be cheap and safe to call regularly inside the task loop.

### `stop_session()`

Owns transition out of the active run.

Expected responsibilities:

- stop active recording
- stop active sounds
- stop long-running calibration modes if any are active
- emit a structured `session_stopped` event

### `finalize_session()`

Owns writing or completing standardized outputs.

Expected responsibilities:

- finalize camera artifacts
- dump session metadata
- write final standardized artifacts
- flush logs

### `close()`

Owns idempotent resource shutdown.

Expected responsibilities:

- close sound runtime
- release display or media resources
- release other long-lived runtime resources

Calling `close()` twice should be safe.

## Task API Contract

Each task should expose a small set of functions or methods. Plain functions are
preferred unless a class is clearly simpler.

Suggested contract:

- `prepare_task(box, task_config) -> task_state`
- `start_task(box, task_state) -> None`
- `handle_event(box, task_state, event) -> None`
- `update_task(box, task_state, now_s) -> None`
- `should_stop(box, task_state) -> bool`
- `stop_task(box, task_state, reason) -> None`
- `finalize_task(box, task_state) -> dict`

`finalize_task()` should return a serializable dictionary. The runner should
write it to disk in a standardized location.

## FSM Model

Tasks should remain FSM-based.

The lifecycle wrapper should not impose a generic trial structure. It only
standardizes session setup and teardown.

Recommended FSM pattern:

- `task_state.phase: str`
- explicit transition helper such as `enter_phase(...)`
- every phase change emits a structured event
- every adaptive parameter change emits a structured event

This keeps task logic understandable while still making events and plotting
consistent across tasks.

## Adaptive Parameters

### Design rule

Adaptive parameters should be stored in mutable task state, not in
`session_info`.

Suggested structure:

- `task_state.adaptive_params: dict[str, float | int | bool | str]`

Examples:

- `reward_left_ul`
- `reward_right_ul`
- `timeout_s`
- `stimulus_probability_left`
- `shaping_stage`

### Update rule

When a task changes an adaptive parameter, it should:

1. update `task_state.adaptive_params`
2. emit a structured `parameter_changed` event
3. optionally emit a legacy text log line for continuity

The task should then call a stable box API, for example `deliver_reward(...)`,
rather than mutating hardware internals directly.

## Session Artifacts

Each session should preserve:

- initial session configuration
- structured event history
- legacy text log
- final task state snapshot

Recommended standardized artifact:

- `final_task_state.json`

Expected contents:

- final FSM phase
- adaptive parameters
- trial counters
- basic outcome summaries
- stop reason
- timestamps

This artifact should be written by the runner from the dictionary returned by
`finalize_task()`.

## Sample Task 1: Freely Moving Video

### Purpose

This task is a media-pipeline and finalization sanity check.

It should validate:

- camera lifecycle
- local preview behavior
- session directory creation
- metadata writing
- finalization behavior
- clean shutdown

### Intended complexity

Minimal. This task should stay intentionally boring.

### Suggested FSM

- `idle`
- `recording`
- `stopping`

If this task starts accumulating scientific logic, it is drifting out of scope.

## Sample Task 2: Head Fixed Go/No-Go

### Purpose

This task is the main integration and regression task for the hardware repo.

It should validate:

- lick input handling
- reward delivery
- low-latency sound playback
- visual stimulus integration when enabled
- real-time plotting
- legacy log output
- structured event output
- simulated mouse support

### Suggested FSM

- `iti`
- `stimulus`
- `response_window`
- `reward`
- `timeout`
- `inter_trial_cleanup`

This task should be the first consumer of the new lifecycle model and the first
target for simulated-mouse regression testing.

## Simulated Mouse

The go/no-go sample should include simulated mouse support from the start.

The simulated mouse should live above the hardware abstraction and should:

- observe structured runtime and task state
- choose actions from a policy
- inject input events through one canonical software input path

It should not mutate task state directly.

Initial behavior families:

- side bias
- time-varying lick rate
- cue-dependent responding
- omission bursts

The simulated mouse should support two purposes:

- online protocol debugging
- regression tests that recover known behavior signatures from outputs

## Suggested Runner Flow

Target execution pattern:

```python
box = BehavBox(session_info)

box.prepare_session()
task_state = task.prepare_task(box, task_config)

box.start_session()
task.start_task(box, task_state)

while not task.should_stop(box, task_state):
    box.poll_runtime()
    for event in drain_events(box):
        task.handle_event(box, task_state, event)
    task.update_task(box, task_state, now_s=time.time())

task.stop_task(box, task_state, reason=\"completed\")
box.stop_session()

final_task_state = task.finalize_task(box, task_state)
box.finalize_session()
write_final_task_state(final_task_state)

box.close()
```

The exact helper names can change, but the sequencing should stay explicit.

## Tests to Write First

### Lifecycle tests

- valid lifecycle order succeeds
- invalid lifecycle order raises a clean error
- `close()` is idempotent
- task error still leads to stop/finalize/close

### Runner tests

- runner calls lifecycle hooks in the correct order
- runner calls task hooks in the correct order
- runner writes the final task-state artifact

### FSM tests

- freely moving video transitions are correct
- go/no-go transitions are correct
- reward, timeout, and response-window logic are correct

### Adaptive parameter tests

- adaptive updates change task state, not `session_info`
- adaptive updates emit structured events
- adaptive updates affect subsequent reward or stimulus behavior
- final task-state snapshot preserves final adaptive values

### Simulated mouse tests

- simulated mouse drives the go/no-go task through valid transitions
- bias is recoverable from outputs
- time-varying lick rate is recoverable from outputs

### Integration tests

- both sample tasks run against mock hardware
- go/no-go plotting still updates
- freely moving video task produces expected camera artifacts

## Performance Considerations

- lifecycle hooks should remain explicit and cheap
- `poll_runtime()` must stay lightweight
- adaptive parameter updates should be in-memory plus lightweight logging
- plotting and state publication must not block the task loop
- final artifact writing belongs at finalization, not inside the hot path

## Migration Strategy

### Phase 1

Add lifecycle methods to `BehavBox` without immediately deleting existing
patterns.

### Phase 2

Add the shared runner and task interface.

### Phase 3

Implement the freely moving video sample first.

### Phase 4

Implement the head-fixed go/no-go sample second.

### Phase 5

Add simulated mouse and parameter-recovery tests to the go/no-go sample.

### Phase 6

Once stable, use the same lifecycle contract in the separate task repo.

## Risks

- over-abstracting the task API and making simple tasks harder to understand
- letting the hardware repo grow into a second protocol repo
- trying to migrate too many tasks at once
- hiding too much logic in the runner instead of keeping task behavior explicit

## Lab Notebook Note

It is reasonable to think of a fork of the task repo as a kind of lab notebook,
but the Raspberry Pi should not depend on `git` commits or pushes for core
experiment logging.

Recommended approach:

- the Pi writes standardized local session artifacts
- a downstream repo or notebook process can later ingest curated summaries

This is downstream workflow, not part of the runtime critical path.

## Recommendation

Proceed with:

- a narrow lifecycle refactor
- exactly two sample tasks in the hardware repo
- FSM-based task logic
- task-owned adaptive parameters
- standardized final task-state artifacts

Do not proceed with:

- a heavy task framework
- broad protocol migration in the hardware repo
- turning the hardware repo into a second active protocol repository
