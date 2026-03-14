# BehavBox Lifecycle Reorganization Proposal

## Purpose

This document is for current box users. It explains a proposed reorganization of
the `BehavBox` runtime API, why it is being considered, how it would change the
day-to-day workflow, and what trade-offs it introduces.

This is not a web-interface proposal. The web interface is a later consumer of
the same runtime changes. The question in this document is narrower:

- should `BehavBox` continue to expose the current "constructor plus many ad hoc
  helper methods" workflow, or
- should it move to a cleaner generic task lifecycle API with explicit session
  phases?

The goal is to decide whether current box users actually want this change before
we do a large reorganization.

## Executive Summary

The current `BehavBox` design works, but it has grown organically. It mixes:

- session directory setup
- log initialization
- hardware setup
- camera control
- audio setup
- plotting setup
- mock keyboard control
- runtime event collection
- metadata dumping and offload

inside a single object, with many of those behaviors starting as side effects of
`BehavBox(session_info)`.

That is convenient for quick iteration, but it creates real costs:

- tasks need to know too many `BehavBox` details
- starting and stopping a session is inconsistent across tasks
- cleanup and finalization are not centralized
- plotting, logging, and camera behavior are harder to standardize
- the future browser interface has no clean control surface to call

The proposed change is to keep `BehavBox` as the main hardware-facing object,
but make it expose a smaller and more explicit lifecycle:

1. prepare the session
2. start the session
3. poll runtime services during the task
4. stop the session
5. finalize the session
6. close resources

The main argument for doing this is not aesthetics. It is to reduce hidden
behavior, standardize task startup and shutdown, and create a durable runtime
contract that works for local plotting now and the web interface later.

The main argument against doing it is that it is a real reorganization. Existing
tasks and user habits will need to change, and there is some risk of disrupting
current workflows if the transition is rushed.

## Current Workflow

### What users generally do now

Today, the common workflow is close to this:

1. SSH to the Raspberry Pi.
2. Edit or prepare `session_info`.
3. Launch a task script.
4. The task instantiates `BehavBox(session_info)`.
5. The constructor immediately performs many side effects:
   - creates the session directory
   - changes the process working directory
   - configures logging
   - initializes hardware
   - initializes sound runtime
   - optionally initializes visual stimulus
   - optionally initializes treadmill support
   - optionally opens the `pygame` plotting/keyboard window
6. The task then calls a task-specific mixture of helper methods during the run.
7. The task is responsible for remembering how and when to stop or offload
   camera data, dump metadata, update plots, and clean up.

### Current code shape

The current code reflects this directly:

- `BehavBox.__init__` performs setup and side effects immediately.
- Plotting and keyboard simulation are handled by direct methods such as
  `check_plot()` and `check_keybd()`.
- Camera recording and offload are controlled through separate methods.
- Hardware callbacks push events directly into `event_list`.
- Cleanup is incomplete and distributed rather than strongly centralized.

### Strengths of the current workflow

- It is familiar to current users.
- It makes quick experiments easy when the user already knows the required
  sequence.
- It lets experienced users bypass abstractions and get directly to the box
  behaviors they care about.
- It was adequate while the system was smaller and more uniform.

### Weaknesses of the current workflow

- Too much important behavior happens implicitly at object construction time.
- Task authors must know internal `BehavBox` details that should not really be
  task responsibilities.
- It is easy for different tasks to diverge in how they start recording,
  finalize metadata, update plots, or tear down resources.
- It is hard to tell which behaviors are always required versus optional.
- It makes automated testing harder because there is no clean session-state
  machine to validate.
- It makes future browser control awkward because there is no explicit
  `prepare`, `start`, `stop`, or `finalize` surface to call.

## Proposed Workflow

### High-level idea

Keep `BehavBox`, but reorganize it around explicit lifecycle phases instead of
constructor side effects plus scattered helper calls.

The intended high-level workflow would be:

1. build or load `session_info`
2. instantiate `BehavBox`
3. call `prepare_session()`
4. call `start_session()`
5. run the task loop while periodically calling a standard runtime poll/update
   method
6. call `stop_session()`
7. call `finalize_session()`
8. call `close()`

### What would move into the lifecycle

`prepare_session()` would own setup that should happen before a run exists:

- validate and normalize `session_info`
- create directories
- set up logging
- configure hardware and long-lived services
- load or validate plotting/state publishers
- verify required resources are available

`start_session()` would own transition into the active run:

- mark the session active
- start camera recording if requested
- arm or start the visual stimulus runtime if requested
- start any required task-visible monitoring state
- emit a clear structured "session started" event

`poll_runtime()` or `tick_runtime()` would own recurring non-task-specific work:

- keyboard simulation if enabled
- mock input handling if enabled
- draining or normalizing hardware events
- plot/state publishing hooks
- optional watchdog or health checks

`stop_session()` would own transition out of the active run:

- stop camera recording
- stop active sounds
- disable ongoing calibration or preview modes
- emit a clear structured "session stopped" event

`finalize_session()` would own persistent outputs:

- offload or finalize camera artifacts
- dump session metadata in the standard formats
- finalize logs
- write any standardized summaries or artifacts

`close()` would own idempotent resource shutdown:

- close sound runtime
- release display resources if owned
- release optional runtime services

## Old and New Workflow Comparison

### Comparison 1: Starting a routine behavioral session

#### Current

- User or task prepares `session_info`.
- Task constructs `BehavBox(session_info)`.
- Many side effects happen immediately.
- The task needs to know which optional helper calls are required for camera,
  plotting, keyboard simulation, and cleanup.

#### Proposed

- User or task prepares `session_info`.
- Task constructs `BehavBox(session_info)` with minimal side effects.
- Task calls `prepare_session()`.
- Task calls `start_session()`.
- The task loop focuses on protocol logic rather than box orchestration.

#### Why the new version is better

- Startup order becomes explicit.
- Missing initialization becomes easier to detect.
- Tasks become more uniform across protocols.

#### What gets worse

- Task scripts become slightly more verbose.
- Old habits and some task scripts will need to be updated.

### Comparison 2: Stopping a session and preserving data

#### Current

- Stop/finalization behavior is partly task-specific.
- Camera offload, metadata dumping, and cleanup are not owned by one clear
  phase boundary.

#### Proposed

- `stop_session()` handles "the run is over."
- `finalize_session()` handles "make persistent outputs correct and complete."
- `close()` handles resource release.

#### Why the new version is better

- Reduces the chance of forgetting to finalize one subsystem.
- Makes shutdown behavior testable and standardized.
- Creates a better foundation for future headless or browser-triggered control.

#### What gets worse

- The separation between stop/finalize/close is more conceptual overhead than
  the current "just call the few things I remember" approach.

### Comparison 3: Real-time plotting before the web interface exists

#### Current

- Plotting support is tied to the local `pygame` path and task-specific logic.
- The plotting code is part of the execution pattern rather than a generic
  published data stream.

#### Proposed

- Runtime publishes standardized plot/state data.
- `pygame` remains the local rendering target for now.
- The future browser dashboard consumes the same plot/state stream later.

#### Why the new version is better

- We only solve the plotting semantics once.
- The browser interface stops being a second plotting rewrite.
- Plotting becomes a consumer of runtime state rather than a privileged part of
  task execution.

#### What gets worse

- There is up-front refactor cost before users see a major workflow change.

### Comparison 4: Supporting future browser control

#### Current

- A browser would have to call or imitate many scattered task/runtime details.
- There is no clean generic "start a session" or "finalize a session" surface.

#### Proposed

- Browser controls eventually call explicit lifecycle methods through a control
  layer.
- The experiment remains independent of the browser, but the browser gets a
  clean supervisory control surface.

#### Why the new version is better

- Much safer control boundary.
- Easier to automate.
- Easier to expose to a dashboard later.

#### What gets worse

- We have to do the lifecycle refactor first, which delays dashboard work
  slightly.

## Rationale for the Reorganization

### 1. Hidden behavior should be reduced

The current constructor does too much. That is convenient until it is not. Once
audio, camera, visual stimulus, treadmill, plotting, and future web control are
all in play, hidden side effects become a source of uncertainty and bugs.

### 2. Tasks should define protocol logic, not appliance orchestration

A behavioral task should decide:

- what stimuli to present
- how to interpret animal behavior
- when to reward or punish
- how to advance the trial structure

It should not need to own the details of:

- session preparation
- service startup order
- standardized recording/finalization behavior
- resource cleanup policy

### 3. The same runtime contract should support local and future remote control

The lab still needs local plotting before the web interface exists. That means
we cannot simply defer all cleanup to "the future dashboard." The right move is
to define a runtime contract now that:

- works with local `pygame` plotting now
- works with explicit task control now
- can later be exposed to a browser safely

### 4. Testing gets easier if lifecycle states are explicit

A generic lifecycle makes it much easier to write and trust tests such as:

- session can prepare successfully
- session cannot start twice
- stopping before starting raises a clean error
- finalization writes expected artifacts
- cleanup is idempotent

Those are difficult to reason about when behavior is distributed across
constructor side effects and many unrelated helper methods.

## Proposed API Shape

This is an example of the direction, not a final signature commitment.

```python
box = BehavBox(session_info)

box.prepare_session()
box.start_session()

while not task_done:
    box.poll_runtime()
    task.step(box)

box.stop_session()
box.finalize_session()
box.close()
```

Possible supporting methods:

- `prepare_session()`
- `start_session()`
- `poll_runtime()`
- `stop_session()`
- `finalize_session()`
- `close()`
- `session_state()`
- `is_session_active()`

Important rule:

The task should still own protocol logic. The lifecycle API should not try to
become a task framework that dictates trial structure.

## What Would Stay the Same

- `BehavBox` remains the main task-facing object.
- Existing hardware capabilities remain accessible.
- Local plotting remains available before the browser interface exists.
- Old human-readable log format can remain in place during the transition.
- Tasks can still use explicit sound, reward, visual-stimulus, and input APIs.

## What Would Change for Current Users

### Likely user-visible changes

- task launch scripts would become more standardized
- some existing tasks would need to be updated to the new lifecycle calls
- startup and shutdown steps would be more explicit
- camera, plotting, and metadata behavior would become less task-specific

### Likely user-visible benefits

- fewer task-specific startup differences
- fewer "did this task remember to clean up properly?" surprises
- clearer expectations for how a session starts and ends
- easier future transition to browser monitoring and control

## Risks and Downsides

This proposal is not free.

### 1. Real migration cost

This is a significant reorganization, not a cosmetic rename. Existing tasks,
debug scripts, and user expectations will need to be audited and updated.

### 2. Transitional confusion

For a while, some code will likely use the old style while newer code uses the
new lifecycle. That can create temporary confusion unless the migration is
deliberate and documented.

### 3. Risk of over-abstracting

If the lifecycle becomes too "framework-like," it could make simple task code
harder to understand rather than easier. That would be a mistake. The API needs
to stay small and concrete.

### 4. Short-term slowdown

This work will consume time that could otherwise go directly into task features
or the web interface. The justification only holds if the new lifecycle really
reduces long-term operational and maintenance cost.

## Recommended Rollout Strategy

### Phase 1

Add the lifecycle methods without immediately deleting old entry points.

### Phase 2

Convert one or two representative tasks first:

- go/no-go
- one video-only template

### Phase 3

Use the converted tasks to stabilize:

- startup order
- shutdown/finalization behavior
- plotting integration
- camera integration
- event/log behavior

### Phase 4

Migrate remaining priority tasks only after the new lifecycle has proven itself.

### Phase 5

Deprecate older task patterns once the replacement is genuinely better and
documented.

## Questions for Current Box Users

The purpose of distributing this proposal is to get direct feedback on these
questions:

1. Is the current `BehavBox` workflow causing enough pain to justify a major
   reorganization?
2. Which current startup or shutdown steps are most error-prone in real use?
3. Would a more explicit lifecycle make your task scripts easier to trust, or
   would it feel like unnecessary overhead?
4. Are there task-specific workflows that would be made worse by this change?
5. Is preserving local `pygame` plotting during the transition enough, or are
   there other current user workflows that must remain intact?

## Recommendation

My recommendation is to do this reorganization, but only if it is kept narrow.

The right scope is:

- explicit lifecycle phases
- standardized startup and shutdown
- better separation between task logic and runtime orchestration
- plotting/state publication that supports both current `pygame` and future web
  consumers

The wrong scope would be:

- inventing a large new task framework
- rewriting every task at once
- forcing the browser interface into the same milestone

So the real proposal is not "replace everything." It is:

- keep `BehavBox`
- make its lifecycle explicit
- standardize what tasks should and should not own
- use that as the bridge from current local workflows to future browser-based
  monitoring
