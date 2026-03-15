Simple Task API
===============

Why This Is The Primary Path
----------------------------

Most users should **not** start by writing a full lower-level task module.

The supported default for new tasks is the ``SimpleTask`` builder:

- one short Python file
- one launcher command
- one automatic choice between local mock mode and Raspberry Pi (RPi) mode,
  with an explicit override when you need it

This keeps the common case small without teaching users to call random
``BehavBox`` methods directly.

Where The Example Lives
-----------------------

The tracked tutorial task file is:

- ``sample_tasks/simple_examples/tutorial_response_task.py``

This is the shortest supported example for writing your own task.

The Tutorial Task File
----------------------

.. literalinclude:: ../../sample_tasks/simple_examples/tutorial_response_task.py
   :language: python

What this file does:

- defines one reward-size parameter
- defines one cue called ``go``
- defines a finite state machine (FSM) with four states
- waits for ``center_entry`` during the response window
- delivers reward on success
- stops cleanly after either a response or a timeout

Why it is written this way:

- the task file contains protocol logic only
- the builder exposes a narrow, stable set of actions
- the launcher and runner own session startup, output directories, and cleanup
- standard task artifacts are still written automatically

The Main Builder Methods
------------------------

You only need a few methods for common tasks:

- ``.param(name, value)``
  defines one task-owned parameter
- ``.cue(name, duration_s, side="both")``
  defines one named cue
- ``.state(name)``
  defines one task state
- ``.on_enter(...)``
  defines actions that run when that state begins
- ``.after(seconds, goto="...")``
  defines one timer-based transition
- ``.on_event(event_name, ..., goto="...")``
  defines one event-driven transition
- ``.finish(reason)``
  marks a terminal state

This interface is intentionally limited. If a task needs arbitrary callbacks,
unusual hardware control, or more complicated adaptive logic, use the advanced
example on the next page instead of stretching the simple API too far.

Run It Locally With The Mock UI
-------------------------------

Use this workflow on a desktop or laptop. In this case ``--mode auto``
resolves to local mock mode.

Command:

.. code-block:: bash

   cd /Users/lukesjulson/codex/RPi4_refactor/targets/RPi4_behavior_boxes_hardware
   uv run python -m sample_tasks.simple_api.run \
     sample_tasks/simple_examples/tutorial_response_task.py \
     --mode auto \
     --session-tag tutorial_simple

What happens:

- the launcher auto-detects that it is not running on a Pi
- mock mode is enabled automatically
- the mock browser user interface (UI) is started if needed
- task outputs are written under ``tmp_task_runs/tutorial_simple``

How to interact with it:

- open the printed local browser URL
- pulse ``lick_3`` in the mock UI
- ``lick_3`` maps to the task's ``center_entry`` response event

Force Mock Mode On A Pi
-----------------------

If you are logged into a Pi but still want a safe mock run, force it:

.. code-block:: bash

   cd ~/behavbox/RPi_behavior_boxes_hardware
   uv run python -m sample_tasks.simple_api.run \
     sample_tasks/simple_examples/tutorial_response_task.py \
     --mode mock \
     --session-tag tutorial_simple_mock

This is useful when you want to debug task logic on the Pi without touching
real hardware.

Run It On A Headless Pi Over SSH
--------------------------------

Use this workflow when you are connected to a real box over Secure Shell
(SSH). On the Pi, ``--mode auto`` resolves to Pi mode.

Command:

.. code-block:: bash

   cd ~/behavbox/RPi_behavior_boxes_hardware
   uv run python -m sample_tasks.simple_api.run \
     sample_tasks/simple_examples/tutorial_response_task.py \
     --mode auto \
     --output-root ~/behavbox_runs \
     --session-tag tutorial_simple

What happens:

- the launcher auto-detects Pi hardware
- mock mode is not forced
- the browser mock UI is not auto-started
- inputs are expected to come from the physical box

If you want to be explicit, use ``--mode pi`` instead of ``--mode auto``.

What You Usually Change First
-----------------------------

When turning this into your own task, the first edits should usually be:

- cue duration or side
- response event name
- response-window duration
- reward amount
- timeout behavior

That covers a lot of common tasks without changing the launcher or runtime.

When To Use The Advanced API Instead
------------------------------------

Move to the advanced full lifecycle example if you need:

- custom logic that does not fit the built-in actions
- more complicated adaptive updates than simple parameter changes
- direct control over task state publication
- a task structure that needs the lower-level ``TaskProtocol`` hooks

That lower-level path is still supported. It is just no longer the first thing
most users should reach for.
