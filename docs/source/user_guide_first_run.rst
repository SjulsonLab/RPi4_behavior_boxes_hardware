First Run
=========

Goal
----

The safest first check is to start the mock BehavBox and confirm that the mock
web user interface (UI) opens.

This does **not** run a task yet. It only confirms that the runtime starts and
that the browser controls are reachable.

Before You Start
----------------

The commands below assume:

- you are in the hardware repo root
- the required Python dependencies are already available
- you are willing to start in mock mode first

Go to the repo root:

.. code-block:: bash

   cd /Users/lukesjulson/codex/RPi4_refactor/targets/RPi4_behavior_boxes_hardware

Start the mock BehavBox:

.. code-block:: bash

   uv run python debug/run_mock_behavbox.py

What You Should See
-------------------

The command should print:

- the mock UI URL
- the session output directory
- a message saying the mock BehavBox has started

Open the printed URL in a browser. By default this is:

.. code-block:: text

   http://127.0.0.1:8765

What To Check In The Browser
----------------------------

Confirm that:

- the page loads without obvious errors
- input controls are visible
- output controls are visible
- the page updates when you interact with it

Stopping
--------

Press ``Ctrl-C`` in the terminal to stop the mock BehavBox.

What This First Run Proves
--------------------------

If this step works, you know that:

- the mock runtime can start
- the browser UI is reachable
- the local environment can at least exercise the safe mock path

The next step is to run the sample task rather than only bringing up the box.
