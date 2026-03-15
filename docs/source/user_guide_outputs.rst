Find Your Output Files
======================

Default Output Location
-----------------------

The sample task writes into ``tmp_task_runs/`` by default.

For example, if you run:

.. code-block:: bash

   uv run python -m sample_tasks.head_fixed_gonogo.run --session-tag tutorial_run

the session directory will be:

.. code-block:: text

   tmp_task_runs/tutorial_run

Change the Output Root
----------------------

If you want the files somewhere else, pass ``--output-root``:

.. code-block:: bash

   uv run python -m sample_tasks.head_fixed_gonogo.run \
     --output-root /path/to/my_runs \
     --session-tag tutorial_run

Main Files To Look For
----------------------

After a completed run, the most useful files are:

- ``final_task_state.json``
  a compact summary of the final task state
- ``task_events.jsonl``
  task and lifecycle events in newline-delimited JavaScript Object Notation
  (JSON)
- ``input_events.log``
  human-readable input and output event log
- ``events.jsonl``
  minimal shared input/output event stream

If camera recording is enabled for a run, camera outputs are written under:

.. code-block:: text

   <session_dir>/camera_recordings/

What To Open First
------------------

If you only want a quick answer about whether the run worked:

1. open ``final_task_state.json``
2. check whether the task reached the expected stop condition
3. then inspect ``task_events.jsonl`` if you need more detail

Why There Are Multiple Logs
---------------------------

The files are meant for different audiences:

- ``input_events.log`` is the easiest human-readable log
- ``events.jsonl`` is a simple machine-readable input/output log
- ``task_events.jsonl`` is the task-layer event stream
- ``final_task_state.json`` is the easiest summary artifact
