# Input Service Refactor Plan: Profiled GPIO Inputs With Recording Sessions

## Summary
Add a new `input` runtime service under `box_runtime` and have `BehavBox` compose it immediately. The service will own:
- lick inputs
- profile-dependent GPIO13/16 inputs
- external TTL trigger input
- head-fixed treadmill decoding via `gpiozero.RotaryEncoder`
- input recording lifecycle and artifact writing

Use explicit input profiles:
- `head_fixed`: GPIO13/16 are the treadmill quadrature inputs
- `freely_moving`: GPIO13/16 are `poke_extra1` / `poke_extra2` beam-break inputs

Recording will be controlled by two independent demand flags:
- `user_wants_recording`
- `task_wants_recording`

Input recording runs while either flag is true. Artifacts for v1 are:
- existing semicolon-delimited human-readable `.log`
- minimal structured `events.jsonl`
- treadmill speed TSV in cm/s

## Public Interfaces and Contracts
- Add a new package, e.g. `box_runtime/input/`, with an input service class and small helper modules for profile mapping, recorder state, and treadmill sampling.
- Add new `session_info` / config keys:
  - `input_profile`: `"head_fixed"` or `"freely_moving"`, default `"head_fixed"`
  - `ttl_trigger_pin`: default `4`
  - `treadmill_speed_hz`: default `30.0`
  - `treadmill_wheel_diameter_cm`: default `2.5`
  - `treadmill_pulses_per_rotation`: default `200`
- `BehavBox` composes the input service and exposes the task-facing behavior through it rather than constructing lick/beam-break GPIO devices directly.
- Input service API:
  - `start_recording(...)`
  - `stop_recording(...)`
  - task-scoped counterparts or explicit task hooks so task start/end can assert and clear `task_wants_recording`
  - TTL pin reconfiguration/release API so a future output service can claim the pin
- TTL contract:
  - initialized as an input by default
  - logs/emits rising and falling edges
  - can be live-reassigned later to the output service during recording
  - on handoff, input service emits a configuration-change event, stops TTL edge logging from that point, and relinquishes the pin cleanly
- Structured event contract for v1:
  - keep the current minimal local event object shape
  - write minimal JSONL event records rather than expanding the event schema now

## Key Implementation Changes
### 1. Input profiles and device ownership
- Move lick and beam-break / treadmill setup out of `BehavBox` into the input service.
- `head_fixed` profile:
  - keep existing lick pins
  - instantiate `gpiozero.RotaryEncoder` on GPIO13/16
  - drop old button-style treadmill aliases on those pins
- `freely_moving` profile:
  - keep existing lick pins
  - instantiate button-style `poke_extra1` / `poke_extra2` beam-break inputs on GPIO13/16
  - do not create a rotary encoder there
- Keep GPIO4 as user-configurable TTL input by default, but under explicit ownership by the input service.

### 2. Eventing and legacy compatibility
- Preserve current lick event names and legacy `interact_list` behavior.
- Preserve current human-readable logging style through the existing `.log` format.
- Add minimal JSONL event writing alongside the text log for all recorded input events.
- TTL emits explicit edge events such as rising/falling.
- Freely-moving beam breaks emit explicit beam-break events rather than overloaded treadmill names.
- Head-fixed treadmill emits service-owned treadmill events internally as needed, but the persisted treadmill artifact is the speed TSV.

### 3. Recording lifecycle and directories
- Recording creates one timestamped recording directory when it first becomes active.
- If a structured task starts and recording is not already active:
  - use the task/session directory when available
  - set `task_wants_recording = True`
- If recording is already active when a task starts:
  - reuse the existing active recording directory
  - set `task_wants_recording = True`
  - do not create a second directory
- If a task started recording, clearing the task flag at task end should stop recording only if the user flag is not also set.
- If the user started recording and a task runs inside it:
  - recording continues after task end until the user stops it
- If the user requests stop during an active task:
  - clear the user flag
  - emit/log a warning that recording will continue until the task ends
  - stop automatically once both flags are false
- Default recording root when no task/session directory is available:
  - use `session_info["external_storage"]` when present
  - otherwise use env/config `INPUT_RECORDING_ROOT`
  - otherwise fall back to `~/behavbox_recordings`

### 4. Treadmill sampling and files
- Replace the old I2C treadmill path in the supported one-Pi input service with `gpiozero.RotaryEncoder`.
- Compute signed running speed in cm/s from:
  - `200` pulses/rotation
  - wheel circumference `pi * diameter_cm`
  - default diameter `2.5 cm`
- Sample speed into fixed bins at user-configurable rate, default `30 Hz`.
- Write treadmill TSV with:
  - column 1: UTC POSIX seconds
  - column 2: signed speed in cm/s over the preceding bin
- Direction sign is provisional in v1 and should be invertible later without changing file format.

## Tests To Write First
- Input profile tests:
  - `head_fixed` creates licks + TTL + rotary encoder on 13/16 and no beam-break buttons there
  - `freely_moving` creates licks + TTL + `poke_extra1/2` buttons on 13/16 and no rotary encoder there
- `BehavBox` integration tests:
  - lick callbacks still enqueue minimal `BehaviorEvent`s and update `interact_list`
  - `BehavBox` uses the input service rather than directly wiring those inputs
- Recording lifecycle tests:
  - first manual start creates a timestamped recording directory
  - task start reuses an active manual recording directory
  - task-owned recording stops at task end if user flag is false
  - user-owned recording survives task end
  - user stop during task logs warning and defers stop until task end
- Output path selection tests:
  - task-owned recording uses the task/session directory
  - standalone recording uses `external_storage`, else `INPUT_RECORDING_ROOT`, else `~/behavbox_recordings`
- TTL tests:
  - default pin is GPIO4
  - configurable TTL pin is honored
  - rising/falling edges appear in `.log` and `events.jsonl`
  - live TTL handoff stops further TTL edge logging and emits a config-change record
- Treadmill tests:
  - speed conversion to cm/s is correct
  - fixed-bin writer honors configurable sampling rate
  - TSV timestamps are POSIX seconds
- Mock backend tests:
  - mock support covers lick, beam-break, TTL, and enough treadmill behavior to test service logic without Pi hardware

## Assumptions and Defaults
- `input_profile="head_fixed"` is the default.
- TTL starts as an input and records edges in v1; task-trigger behavior is deferred.
- The future output service is allowed to reclaim the TTL pin during recording through a live handoff contract.
- Human-readable input logging uses the existing `.log` style, not a new TSV behavior log.
- Minimal structured events are written as `events.jsonl`, not a richer schema yet.
- Head-fixed direction polarity for the treadmill may need inversion after bench validation; that should be a config flip, not a format change.
