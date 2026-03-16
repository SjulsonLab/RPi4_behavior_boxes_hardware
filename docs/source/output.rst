Output Subsystem
================

Overview
--------

The BehavBox output subsystem now lives in a dedicated ``OutputService`` rather
than the legacy ``Pump`` helper embedded in ``BehavBox``. The output service
uses the fixed profile mappings defined in ``box_runtime/io_manifest.py`` as
the authoritative source of GPIO assignments.

The active output service owns:

- liquid reward outputs
- auxiliary reward pumps
- airpuff or other punishment outputs
- vacuum
- cue LEDs
- dedicated ``trigger_out`` on GPIO24
- generic user-configurable GPIO4 when explicitly claimed

Naming and Aliases
------------------

Runtime code uses canonical semantic names from the fixed GPIO definitions, for
example:

- ``reward_left``
- ``reward_center``
- ``trigger_out``
- ``cue_led_5``

Manual-control and browser surfaces also expose the fixed board aliases stored
in the Python mapping, for example:

- ``reward_left (pump1)``
- ``trigger_out (DIO2)``
- ``cue_led_5 (DIO4)``

Tasks and runtime code should use canonical semantic names only.

Profile-Aware Outputs
---------------------

Outputs differ by ``box_profile``.

Examples:

- ``head_fixed`` exposes ``airpuff`` on GPIO8
- ``freely_moving`` exposes ``reward_5`` on GPIO8
- both profiles expose ``reward_left``, ``reward_right``, ``reward_center``,
  ``reward_4``, ``vacuum``, ``cue_led_1`` through ``cue_led_6``, and
  ``trigger_out``

This keeps tasks profile-aware at the semantic level rather than forcing them
to reason about raw BCM pin numbers.

Reward Delivery
---------------

``BehavBox.deliver_reward(...)`` remains the stable public reward API, but it
now delegates to ``OutputService``.

Reward durations continue to use the existing linear calibration rule stored in
``session_info["calibration_coefficient"]``. Reward sizes are specified in
microliters.

Generic Output API
------------------

The active generic BehavBox-facing output methods are:

- ``deliver_reward(output_name="reward_center", reward_size_ul=None)``
- ``pulse_output(output_name, duration_s=None)``
- ``set_output(output_name, active)``
- ``toggle_output(output_name)``
- ``configure_user_output(label="ttl_output")``

Calling an output name that is not present in the active ``box_profile`` raises
a clean error rather than silently touching the wrong pin.

Shared Logging
--------------

Input and output events now write to the same active recording directory and
the same shared files:

- ``input_events.log``
- ``events.jsonl``

The human-readable log remains the detailed operator-facing trace. The JSONL
file stays intentionally minimal and records output event names and timestamps
with small scalar payloads when useful.

Mock UI and Web Interface
-------------------------

The current mock web interface uses the same output registry contract intended
for a future real operator-facing browser UI.

The active mock API includes:

- ``POST /api/output/<label>/on``
- ``POST /api/output/<label>/off``
- ``POST /api/output/<label>/toggle``
- ``POST /api/output/<label>/pulse``

The browser UI resolves either canonical semantic labels or supported aliases.

API Reference
-------------

.. automodule:: box_runtime.output.service
   :members: OutputService
