Input Subsystem
===============

Overview
--------

The BehavBox input subsystem is being refactored around an explicit runtime
service instead of direct general-purpose input/output (GPIO) ownership inside
``BehavBox``. Version 1 of this refactor covers:

- lick inputs
- profile-dependent GPIO13/16 inputs
- an external transistor-transistor logic (TTL) trigger input
- head-fixed treadmill decoding through ``gpiozero.RotaryEncoder``
- recording of input events and treadmill speed independent of structured task
  execution

The input service remains task-facing through ``BehavBox``. Existing task code
should continue to consume minimal ``BehaviorEvent`` objects and legacy
human-readable log files while the runtime adds structured ``events.jsonl``
artifacts in parallel.

Profiles
--------

The input mapping is profile-dependent because GPIO13 and GPIO16 mean different
things on different boxes.

Supported version 1 profiles are:

- ``head_fixed``
  GPIO13 and GPIO16 are reserved for the treadmill quadrature encoder.
- ``freely_moving``
  GPIO13 and GPIO16 are reserved for ``poke_extra1`` and ``poke_extra2``
  beam-break inputs.

The default profile is ``head_fixed``.

Configuration
-------------

The planned input-service configuration surface includes:

- ``input_profile``
  Either ``"head_fixed"`` or ``"freely_moving"``.
- ``ttl_trigger_pin``
  External TTL trigger pin. Defaults to ``4``.
- ``treadmill_speed_hz``
  Fixed-bin treadmill speed sampling rate in hertz. Defaults to ``30.0``.
- ``treadmill_wheel_diameter_cm``
  Wheel diameter in centimeters. Defaults to ``2.5`` for now.
- ``treadmill_pulses_per_rotation``
  Rotary encoder pulses per full wheel rotation. Defaults to ``200``.

Head-Fixed Inputs
-----------------

In the ``head_fixed`` profile:

- existing lick pins remain unchanged
- GPIO13 and GPIO16 are owned by ``gpiozero.RotaryEncoder``
- the old button-style treadmill aliases on GPIO13 and GPIO16 are removed

The encoder sign convention is provisional in version 1. If the physical wheel
direction is reversed relative to the software convention, the runtime should
correct this through configuration rather than by changing file formats.

Freely Moving Inputs
--------------------

In the ``freely_moving`` profile:

- existing lick pins remain unchanged
- GPIO13 and GPIO16 become ``poke_extra1`` and ``poke_extra2`` beam-break
  inputs
- no treadmill rotary encoder is created on those pins

This keeps the freely moving beam-break mapping explicit instead of trying to
infer it from task type or other session settings.

TTL Trigger
-----------

The external TTL trigger is initialized as an input by default and records
rising and falling edges. In version 1 it does not trigger task behavior
directly; it only produces input events and log records.

The important ownership rule is that the TTL pin is not permanently owned by
the input service. A future output service may reclaim that pin during a
recording session. When this happens, the input service should:

- relinquish ownership of the pin cleanly
- emit a configuration-change event
- stop TTL edge logging from that point onward

This allows the same physical pin to start as an input and later become an
output without silently double-owning it.

Recording Lifecycle
-------------------

Input recording is independent of structured task execution. The runtime keeps
two recording-demand flags:

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

Version 1 writes three parallel artifacts:

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

The refactor does **not** preserve the old button-style treadmill input aliases
in the ``head_fixed`` profile, because those pins become true quadrature inputs
owned by the encoder.

Planned Public Surface
----------------------

The input service is expected to expose a small recording-oriented surface
through ``BehavBox``:

- ``start_recording(...)``
- ``stop_recording(...)``
- task-aware recording hooks or equivalents
- TTL reconfiguration / release hooks

The exact Python module layout is still implementation work, but the intent is
that ``BehavBox`` becomes the composition layer over the input service instead
of directly wiring lick, beam-break, TTL, and treadmill devices itself.
