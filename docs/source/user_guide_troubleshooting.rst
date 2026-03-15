Troubleshooting
===============

The Browser Page Does Not Open
------------------------------

Check these first:

- the task or mock launcher is still running in the terminal
- you opened the URL printed by the command
- nothing else is already using port ``8765``

If needed, stop the process with ``Ctrl-C`` and start it again.

The Task Runs But Nothing Happens
---------------------------------

For the current ``head_fixed_gonogo`` sample task, the expected manual response
is:

- ``lick_3``

If you click a different input, the task may stay idle because the sample task
is intentionally narrow.

I Expected Real Hardware, But I Got Mock Mode
---------------------------------------------

The current sample task launcher forces mock mode on purpose.

That is the safe default for the tutorial because it lets users:

- test the workflow on a desktop machine
- test the workflow on a Raspberry Pi (RPi) without committing to a real
  hardware session immediately

If you are trying to validate a real box, do not assume the sample-task
tutorial is the same thing as a full real-hardware experiment run.

I Cannot Find My Output Files
-----------------------------

The most common cause is forgetting the output root or session tag.

By default the sample task writes under:

.. code-block:: text

   tmp_task_runs/

If you passed ``--output-root`` or ``--session-tag``, look there instead.

The Fastest Check After A Run
-----------------------------

Open:

.. code-block:: text

   final_task_state.json

If that file exists and contains a plausible final state, the run usually
completed far enough to produce useful artifacts.

The Sphinx Reference Looks Too Technical
----------------------------------------

That is expected. The other Sphinx pages are mostly reference documentation.

For practical use, start with this user-guide section and then jump to the
reference pages only when you need detail on:

- audio
- camera
- inputs
- outputs
- task lifecycle
