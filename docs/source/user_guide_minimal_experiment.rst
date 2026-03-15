Minimum Experiment Code
=======================

Why This Example Exists
-----------------------

After you run the sample task once, the next useful question is usually:

``What is the smallest amount of code I need to write my own experiment?``

The answer is **not** "put everything in one script and call random BehavBox
methods directly." That would be shorter, but it would teach the wrong pattern.

The supported minimum pattern is:

1. one small task module containing only task logic
2. one runner script for local mock use
3. one runner script for a headless Raspberry Pi (RPi) over Secure Shell (SSH)
4. one small session-configuration helper

This keeps the code small while still using the current supported lifecycle.

Where The Example Lives
-----------------------

The tracked example files are:

- ``sample_tasks/minimal_experiment/task.py``
- ``sample_tasks/minimal_experiment/session_config.py``
- ``sample_tasks/minimal_experiment/run_mock.py``
- ``sample_tasks/minimal_experiment/run_pi.py``

The Shared Session Configuration
--------------------------------

The session helper builds the ``session_info`` dictionary in one place:

.. literalinclude:: ../../sample_tasks/minimal_experiment/session_config.py
   :language: python
   :lines: 1-67

Why it is written this way:

- output paths are centralized
- the mock and headless-Pi versions stay consistent
- the task code does not need to know where files should be written
- the only real difference between the two modes is whether audio stays mocked

The Minimal Task
----------------

The task module contains the experiment logic itself:

.. literalinclude:: ../../sample_tasks/minimal_experiment/task.py
   :language: python

What this task does:

- plays one cue
- opens one response window
- watches for one response event
- optionally delivers reward
- stops cleanly after a response or after the response window expires

Why it is written this way:

- ``prepare_task()`` holds setup that belongs to the task, not to the runner
- ``start_task()`` starts the experiment by entering the first phase
- ``handle_event()`` reacts only to relevant input events
- ``update_task()`` advances the finite state machine (FSM) based on time
- ``finalize_task()`` returns a small summary that becomes
  ``final_task_state.json``

This is the smallest useful pattern that still matches the current task API.

Run It Locally In Mock Mode
---------------------------

Use this version on your local machine when you want the browser mock user
interface (UI).

The runner code is:

.. literalinclude:: ../../sample_tasks/minimal_experiment/run_mock.py
   :language: python

Run it with:

.. code-block:: bash

   cd /Users/lukesjulson/codex/RPi4_refactor/targets/RPi4_behavior_boxes_hardware
   uv run python -m sample_tasks.minimal_experiment.run_mock --session-tag tutorial_minimal

What is specific to local mock mode:

- it forces mock mode with ``BEHAVBOX_FORCE_MOCK=1``
- it starts the browser mock UI automatically
- it tells you to pulse ``lick_3`` in the browser

This is the right choice when:

- you are working on a desktop or laptop
- you want to test task flow without real hardware
- you want to debug the task logic first

Run It On A Headless Pi Over SSH
--------------------------------

Use this version when you are logged into a real box over SSH and want the
responses to come from the physical hardware.

The runner code is:

.. literalinclude:: ../../sample_tasks/minimal_experiment/run_pi.py
   :language: python

Example command on the Pi:

.. code-block:: bash

   cd ~/behavbox/RPi_behavior_boxes_hardware
   uv run python -m sample_tasks.minimal_experiment.run_pi \
     --output-root ~/behavbox_runs \
     --session-tag tutorial_minimal

What is specific to the headless-Pi version:

- it does **not** force mock mode
- it disables automatic mock-UI startup
- it assumes inputs come from the real box

This is the right choice when:

- you are connected to a headless Pi over SSH
- you want a real hardware run
- you do not expect a local browser control panel to appear automatically

Why There Are Two Runner Files
------------------------------

This split is intentional.

The mock and headless-Pi versions should not be hidden behind one opaque flag,
because users need to understand which environment they are running in:

- local mock mode is for safe local testing with a browser UI
- headless Pi mode is for real box execution over SSH

If those modes are mixed together carelessly, users end up not knowing whether
they are testing their code or their hardware.

What To Copy For Your Own Task
------------------------------

If you want to start a new task, copy these pieces:

1. ``task.py``
2. ``session_config.py``
3. one runner script that matches how you plan to work

Then change only the task logic first:

- cue selection
- response event
- stop condition
- reward rule

Do **not** start by rewriting the runner or bypassing ``TaskRunner`` unless you
have a clear reason. The runner is what gives you:

- consistent startup and shutdown
- standard task artifacts
- a path that still matches the current hardware repo architecture
