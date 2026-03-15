Input Subsystem
===============

Overview
--------

The BehavBox input subsystem now lives in a dedicated runtime service rather
than direct general-purpose input/output (GPIO) ownership inside
``BehavBox``. The active runtime covers:

- profile-dependent lick / poke / beam-break inputs
- a dedicated trigger input on GPIO23
- a generic user-configurable GPIO4 input when explicitly claimed
- head-fixed treadmill decoding through ``gpiozero.RotaryEncoder``
- shared recording of input and output events plus treadmill speed independent
  of structured task execution

The input service remains task-facing through ``BehavBox``. Existing task code
should continue to consume minimal ``BehaviorEvent`` objects and legacy
human-readable log files while the runtime appends minimal structured
``events.jsonl`` artifacts in parallel.

Profiles
--------

The active input mapping is profile-dependent and is loaded from the tracked
``unified_GPIO_pin_arrangement_v4.csv`` manifest.

Supported version 1 profiles are:

- ``head_fixed``
  GPIO13 and GPIO16 are reserved for the treadmill quadrature encoder.
- ``freely_moving``
  GPIO13 and GPIO16 are reserved for ``poke_extra1`` and ``poke_extra2``
  beam-break inputs.

The canonical runtime profile key is ``box_profile``. The default profile is
``head_fixed``.

Configuration
-------------

The active input-service configuration surface includes:

- ``box_profile``
  Either ``"head_fixed"`` or ``"freely_moving"``.
- ``treadmill_speed_hz``
  Fixed-bin treadmill speed sampling rate in hertz. Defaults to ``30.0``.
- ``treadmill_wheel_diameter_cm``
  Wheel diameter in centimeters. Defaults to ``2.5`` for now.
- ``treadmill_pulses_per_rotation``
  Rotary encoder pulses per full wheel rotation. Defaults to ``200``.

Head-Fixed Inputs
-----------------

In the ``head_fixed`` profile:

- GPIO5, GPIO6, and GPIO12 are ``ir_lick_left``, ``ir_lick_right``, and
  ``ir_lick_center``
- GPIO26, GPIO27, and GPIO15 are ``lick_left``, ``lick_right``, and
  ``lick_center``
- GPIO13 and GPIO16 are owned by ``gpiozero.RotaryEncoder`` as
  ``treadmill_1`` and ``treadmill_2``

The encoder sign convention is provisional in version 1. If the physical wheel
direction is reversed relative to the software convention, the runtime should
correct this through configuration rather than by changing file formats.

Freely Moving Inputs
--------------------

In the ``freely_moving`` profile:

- GPIO5, GPIO6, and GPIO12 become ``poke_left``, ``poke_right``, and
  ``poke_center``
- GPIO13 and GPIO16 become ``poke_extra1`` and ``poke_extra2`` beam-break
  inputs
- no treadmill rotary encoder is created on those pins

This keeps the freely moving beam-break mapping explicit instead of trying to
infer it from task type or other session settings.

Trigger Input and User GPIO
---------------------------

GPIO23 is the dedicated ``trigger_in`` input and records rising and falling
edges. In version 1 it does not trigger task behavior
directly; it only produces input events and log records.

GPIO4 is no longer the default trigger. It is treated as a generic
user-configurable line that can be claimed later as either an input or an
output.

Recording Lifecycle
-------------------

Input/output recording is independent of structured task execution. The runtime
keeps two recording-demand flags:

- ``user_wants_recording``
- ``task_wants_recording``

Recording runs while either flag is true.

The recording rules are:

- when recording first becomes active, create one timestamped recording
  directory
- if a task starts while recording is already active, reuse the active
  recording directory
- if a task starts and no recording is active, start recording automatically
- if the task started recording, recording stops at task end unless the user
  also still wants recording
- if the user started recording, recording continues beyond task end
- if the user requests stop during a task, the runtime warns that recording
  will continue until the task ends and then stop automatically

Output Locations
----------------

If a structured task provides a session directory, input-recording artifacts
should go there.

If no task/session directory is active, the default root selection is:

1. ``session_info["external_storage"]`` when available
2. ``INPUT_RECORDING_ROOT`` from the environment or config
3. ``~/behavbox_recordings``

This keeps task-linked runs colocated with task outputs while still giving
standalone input recordings a predictable appliance-style home.

Recorded Artifacts
------------------

The runtime currently writes three parallel input-facing artifacts:

- the existing semicolon-delimited human-readable ``.log`` file
- minimal structured ``events.jsonl``
- treadmill speed ``.tsv``

The structured event file remains intentionally minimal in version 1. It uses
the same minimal event shape currently used in memory rather than introducing a
richer event schema before the input service itself is stable.

Treadmill Logging
-----------------

The old inter-integrated circuit (I2C) / Arduino treadmill path is not the
supported path for the one-Pi refactor. The supported path is
``gpiozero.RotaryEncoder`` in the ``head_fixed`` profile.

Running speed is derived from:

- ``200`` pulses per rotation
- wheel circumference ``pi * diameter_cm``
- default wheel diameter ``2.5 cm``

The treadmill artifact is a fixed-bin tab-separated value (TSV) file with:

- column 1: coordinated universal time (UTC) in POSIX seconds
- column 2: signed running speed in centimeters per second

Each row represents speed over the preceding time bin. The default sampling
rate is ``30 Hz``, but this should remain user-configurable.

Legacy Compatibility
--------------------

The refactor preserves these compatibility requirements:

- lick callbacks still enqueue minimal ``BehaviorEvent`` objects
- the legacy ``interact_list`` continues to update
- the human-readable log style remains available to operators
- alias labels such as ``lick_3`` and board names such as ``IR_rx1`` remain
  usable in mock/manual control surfaces

Planned Public Surface
----------------------

The input service exposes a small recording-oriented surface through
``BehavBox`` and a shared recorder:

- ``start_recording(...)``
- ``stop_recording(...)``
- task-aware recording hooks or equivalents
- generic GPIO4 user-input claiming

``BehavBox`` is now the composition layer over manifest-driven input and output
services rather than directly wiring lick, beam-break, trigger, treadmill, and
reward GPIO devices itself.
