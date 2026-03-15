Task Runtime and Lifecycle
==========================

Overview
--------

The hardware repo now has an explicit task-execution layer instead of relying
on constructor side effects and task-specific setup code inside ``BehavBox``.
The current structure is:

- ``BehavBox`` owns appliance-style runtime services and session lifecycle
- ``TaskRunner`` sequences the lifecycle and task callbacks
- ``SimpleTask`` provides the primary end-user authoring path for common tasks
- each sample task owns protocol logic, finite state machine (FSM) phases, and
  task-local mutable state
- the browser-facing mock UI consumes shared runtime state instead of parsing
  task artifacts

This split is the first step toward a real web interface. The mock web page is
still generic, but it now renders task/session/audio runtime state through a
stable API shape that a future operator-facing frontend can reuse.

Simple Task API
---------------

There are now two supported task-authoring levels:

- end-user path: ``sample_tasks.simple_api.SimpleTask``
- advanced path: direct ``TaskProtocol`` implementations plus ``TaskRunner``

The simple API exists because the lower-level lifecycle hooks are too much
template code for many users. It compiles a short finite state machine (FSM)
definition into the same lifecycle contract used by the advanced path.

Use the simple API when you need:

- common cue, response-window, reward, and timeout tasks
- basic parameter updates through built-in helper actions
- one short task file with the unified launcher

Use the advanced path when you need:

- unusual contingencies or state handling
- direct task control over lower-level lifecycle hooks
- behavior that does not fit the simple built-in action set

Current Supported Sample Task
-----------------------------

The currently implemented reference task is ``head_fixed_gonogo``.

It is intentionally narrow:

- default input profile: ``head_fixed``
- response event: ``center_entry`` from ``lick_center`` (alias ``lick_3``) in
  the mock browser UI
- audio cues distinguish ``go`` from ``nogo``
- reward is delivered on the center reward output
- visual stimulus is disabled in this first slice

The task exists to validate task structure, lifecycle handling, mock hardware
integration, and standardized outputs. It is not meant to be the lab's long-
term protocol repository.

BehavBox Lifecycle
------------------

``BehavBox`` now exposes an explicit session lifecycle:

1. :meth:`box_runtime.behavior.behavbox.BehavBox.prepare_session`
2. :meth:`box_runtime.behavior.behavbox.BehavBox.start_session`
3. :meth:`box_runtime.behavior.behavbox.BehavBox.poll_runtime`
4. :meth:`box_runtime.behavior.behavbox.BehavBox.stop_session`
5. :meth:`box_runtime.behavior.behavbox.BehavBox.finalize_session`
6. :meth:`box_runtime.behavior.behavbox.BehavBox.close`

Responsibilities are split as follows:

- ``prepare_session()``
  Create directories and logs, initialize services, and prepare long-lived
  hardware/runtime resources.
- ``start_session()``
  Mark the session active and start task-owned recording/runtime behaviors.
- ``poll_runtime()``
  Drain runtime events and run lightweight non-task-specific polling work.
- ``stop_session()``
  Stop the active session, including task-owned recording and active sound.
- ``finalize_session()``
  Write standardized session metadata after the run has stopped.
- ``close()``
  Release long-lived runtime resources safely, including partial-failure paths.

Task Runner
-----------

``sample_tasks.common.runner.TaskRunner`` is the small sequencing layer between
the lifecycle and task logic.

The runner:

- prepares the box and task state
- starts the session and task
- repeatedly drains box events and calls task update hooks
- guarantees stop/finalize/close on normal completion and on task errors
- writes standard task artifacts at the end of the run

The runner is intentionally small. It is not a framework-heavy abstraction and
does not own protocol logic.

Task Module Contract
--------------------

Sample tasks implement the hook-based protocol defined by
``sample_tasks.common.task_api.TaskProtocol``.

The current hooks are:

- ``prepare_task(box, task_config)``
- ``start_task(box, task_state)``
- ``handle_event(box, task_state, event)``
- ``update_task(box, task_state, now_s)``
- ``should_stop(box, task_state)``
- ``stop_task(box, task_state, reason)``
- ``finalize_task(box, task_state)``

In practice:

- ``task_config`` is a JSON-serializable mapping
- ``task_state`` is a mutable task-owned dictionary
- ``event`` is a runtime event object that task code should interpret through
  ``BehavBox.event_name(...)`` and ``BehavBox.event_timestamp(...)``

This keeps protocol logic local to the task module while preserving a small,
reviewable contract.

Runtime State and Browser UI
----------------------------

The mock web UI now consumes shared runtime state rather than only raw pin
changes. The current API state includes:

- ``runtime.session``
- ``runtime.task``
- ``runtime.audio``

This lets the browser show:

- which protocol is running
- the current lifecycle state
- the current task phase
- the active trial index and trial type
- whether the stimulus phase is active
- the current and last audio cue name

That runtime-state contract is generic. It is meant to survive the transition
from the current mock page to a more task-aware web interface.

Standard Task Artifacts
-----------------------

At the end of a run, the task layer writes:

- ``task_events.jsonl``
- ``final_task_state.json``

These sit alongside the usual session and input artifacts written by the
runtime, such as the legacy human-readable log and input-service outputs.

``task_events.jsonl`` contains task and lifecycle events in newline-delimited
JSON format. ``final_task_state.json`` captures the final serializable task
state so later analysis does not need to reconstruct final task status from the
full event stream alone.

Local Mock Usage
----------------

Run the current sample task locally:

.. code-block:: bash

   cd /Users/lukesjulson/codex/RPi4_refactor/targets/RPi4_behavior_boxes_hardware
   uv run python -m sample_tasks.head_fixed_gonogo.run --max-trials 5 --max-duration-s 600

Then open the mock web UI:

.. code-block:: text

   http://127.0.0.1:8765

For the current ``head_fixed_gonogo`` task:

- pulse ``lick_3`` in the browser UI
- this maps to the canonical ``center_entry`` response event

API Reference
-------------

.. automodule:: sample_tasks.simple_api
   :members:

.. autoclass:: box_runtime.behavior.behavbox.BehavBox
   :members: prepare_session, start_session, poll_runtime, stop_session, finalize_session, close, event_name, event_timestamp, publish_runtime_state
   :member-order: bysource
   :no-index:

.. automodule:: sample_tasks.common.task_api
   :members: TaskProtocol

.. automodule:: sample_tasks.common.runner
   :members: TaskRunner

.. automodule:: sample_tasks.common.session_artifacts
   :members: write_final_task_state, write_task_events
