Run the Sample Task
===================

Goal
----

The current supported reference task is ``head_fixed_gonogo``.

This is the best end-user tutorial target because it exercises:

- the explicit task lifecycle
- the mock hardware path
- real-time task state updates
- standardized task output files

Start the Task
--------------

From the repo root, run:

.. code-block:: bash

   uv run python -m sample_tasks.head_fixed_gonogo.run --max-trials 5 --max-duration-s 600

This command starts in mock mode by default and automatically starts the mock
web UI if needed.

What The Task Prints
--------------------

At startup, the task prints:

- the mock UI URL
- a short hint telling you to pulse ``lick_3`` for the center response

At the end, it prints:

- the path to ``final_task_state.json``
- a summary of the final task state

How To Interact With It
-----------------------

Open the mock UI in your browser. For the current sample task:

- use ``lick_3`` to send the center response
- pulsing ``lick_3`` is the simplest manual test

The current task is intentionally narrow:

- it uses the ``head_fixed`` profile
- it uses center responses
- it delivers reward on the center reward output
- visual stimulus is currently disabled in this sample

Use the Fake Mouse
------------------

If you want the task to run without manual input, enable the fake mouse:

.. code-block:: bash

   uv run python -m sample_tasks.head_fixed_gonogo.run --max-trials 20 --fake-mouse --fake-mouse-seed 0

This is useful when you want to confirm that:

- trials advance on their own
- output files are written
- the task completes without manually clicking in the browser

Important Limitation
--------------------

This tutorial task is a supported bring-up and validation task. It is **not**
meant to be the lab's long-term protocol repository.
