# Robust Treadmill Acquisition for Modern BehavBox

## 1. Status and purpose

This document specifies the next treadmill acquisition system for the active
`RPi4_behavior_boxes_hardware` codebase. It is a requirements document. It is
not evidence that the implementation exists or is ready for experiments.

The active treadmill path currently uses `gpiozero.RotaryEncoder` inside
`box_runtime.input.service.InputService`. The replacement described here must
preserve the modern BehavBox lifecycle and recording architecture while making
rotary acquisition accurate, observable, and resistant to load from live
behavior, cameras, displays, audio, networking, and disk activity.

The primary goal is:

> Decode treadmill motion exactly throughout a measured and documented
> operating envelope, and loudly detect observable loss or corruption instead
> of returning plausible but untrustworthy motion data.

No software design can prove that an electrical pulse which never reaches the
GPIO controller existed. Electrical validation and an external known-good
quadrature source are therefore part of production acceptance.

In this document, "must" and "must not" are requirements. "Should" is the
recommended default unless testing produces a documented reason to differ.

## 2. Supported platform

The initial supported platform is deliberately narrow:

- Raspberry Pi 5 running 64-bit Raspberry Pi OS / Debian 13 Trixie;
- system Python 3.13;
- the official libgpiod v2-style Python binding for treadmill acquisition.

Raspberry Pi 5 must pass the complete hardware acceptance procedure before the
new treadmill path is used for experiments.

Raspberry Pi 4B support is deferred until after the Pi 5 implementation is
working and accepted. Bookworm, older Python releases, 32-bit images,
non-Raspberry Pi Linux hosts, and non-Linux systems are not production targets
for the hardware backend in this specification. Pure decoding, configuration,
state, and logging tests must still run without Raspberry Pi hardware.

The rest of BehavBox may continue using gpiozero and the current rpi-lgpio
stack for non-treadmill GPIO. The treadmill backend may coexist with those
libraries only when line ownership is disjoint and verified.

## 3. Existing systems and migration references

There are three relevant generations. They must not be described as one
implementation.

### 3.1 Archived Teensy / I2C system

The oldest system decoded the encoder on a Teensy or Arduino and returned
distance or speed to the Pi over I2C. Its source remains under:

```text
box_runtime/old_hardware/treadmill/
```

Related copies also remain in the older behavior-system repository. These
files are historical and calibration references only. They must not be edited
or imported by the new runtime.

The commonly used legacy calibration was:

```text
legacy_mm_per_encoder_cycle = 0.41095 mm
legacy_mm_per_x4_transition = 0.1027375 mm
```

The firmware generally counted one channel-A rising edge per quadrature cycle,
sampled channel B for direction, and used a 50 ms motion timeout.

### 3.2 Direct-Pi RPi.GPIO prototype in the older repository

The older behavior repository later added an `RPi.GPIO` decoder which:

- watched only channel-A rising edges;
- read A and B after callback dispatch;
- used Python callback time for speed;
- buffered one row per callback in memory;
- wrote the full CSV at shutdown.

That prototype motivated many of the integrity requirements in this document,
but it is not the active implementation and must remain untouched.

### 3.3 Active modern implementation

The active implementation is in:

```text
box_runtime/input/service.py
box_runtime/behavior/gpio_backend.py
box_runtime/io_manifest.py
box_runtime/io_recording.py
```

For the `head_fixed` profile it currently:

- claims BCM13 and BCM16 from the central IO manifest;
- constructs `gpiozero.RotaryEncoder` in the BehavBox process;
- samples the encoder's cumulative `steps` value in a Python thread;
- defaults to 30 Hz state sampling;
- converts step differences using a nominal 25 mm roller diameter and 200
  encoder cycles per revolution;
- writes `treadmill_speed.tsv` with wall-clock time and signed speed.

The current implementation is useful as a behavior and artifact reference,
but it is not the acquisition architecture to preserve. In particular, it has
no kernel event history, loss diagnostics, process heartbeat, integrity latch,
or physical position API. It also constructs the rotary encoder for every
head-fixed profile regardless of the current `session_info["treadmill"]`
value.

Current gpiozero versions also bound `RotaryEncoder.steps` unless
`max_steps=0` is requested. The active construction does not explicitly set
that option, while the repository mock defaults to an unlimited counter. The
new implementation must not depend on gpiozero step accumulation and must not
inherit this possible saturation behavior.

## 4. Scope

### 4.1 In scope

Implement and validate:

- direct two-channel quadrature acquisition on one Raspberry Pi;
- Pi 5 RP1 GPIO-controller resolution;
- both rising and falling edges on channels A and B;
- kernel monotonic event timestamps and sequence information;
- x4 quadrature decoding;
- a dedicated acquisition process;
- physical signed position, absolute distance travelled, and signed speed;
- explicit encoder-versus-locomotion direction mapping;
- coherent live state for behavior code;
- independent fixed-rate treadmill recording;
- health, heartbeat, integrity, and event-loss diagnostics;
- bounded diagnostic raw-event history;
- synthetic, multiprocessing, stress, and hardware tests;
- calibration and direction-sign utilities;
- graceful startup, recording, zeroing, shutdown, and error behavior;
- integration with the current `InputService`, `BehavBox`, manifest, and
  recording lifecycle.

### 4.2 Out of scope

The treadmill subsystem must not decide:

- behavioral movement thresholds;
- movement onset or offset rules;
- reward logic;
- trial structure;
- task-specific filtering;
- task-specific pause or resume transitions;
- GUI design;
- camera, visual-stimulus, audio, or neural-acquisition behavior.

The baseline must not require:

- a Teensy or other microcontroller;
- a real-time Linux kernel;
- root-only real-time scheduling;
- CPU affinity;
- a C extension, Cython, Numba, or another premature optimization.

## 5. Modern integration boundary

### 5.1 Intended package boundary

New treadmill code should live under a focused package such as:

```text
box_runtime/input/treadmill/
```

The eventual implementation plan may adjust exact filenames, but the design
must keep these responsibilities separate:

- validated configuration and calibration;
- pure quadrature decoding;
- public state and health records;
- libgpiod GPIO adaptation and pin resolution;
- acquisition-process lifecycle and control;
- coherent shared-state publication;
- fixed-rate recording;
- diagnostics and summaries;
- one behavior-facing runtime facade.

Legacy code under `box_runtime/old_hardware/treadmill/` and the older external
repository must remain unchanged.

### 5.2 Required eventual changes to InputService

The final `InputService` change must be localized to its treadmill-specific
sections. It must not redesign lick, poke, trigger, profile, or general IO
recording behavior.

The expected changes are:

1. Stop constructing `gpiozero.RotaryEncoder` for the active treadmill path.
2. Read BCM13 and BCM16 from the existing head-fixed manifest; do not duplicate
   pin numbers in treadmill defaults.
3. When `session_info["treadmill"]` is true, construct the new treadmill facade
   from the manifest pins plus validated treadmill settings.
4. During `BehavBox.prepare_session()`, start acquisition and wait for its
   startup handshake to reach a usable state.
5. When shared recording first starts, give the treadmill logger the selected
   recording directory and establish the recording's zero origin.
6. When shared recording stops, finalize treadmill artifacts without stopping
   live acquisition prematurely.
7. Expose the facade as `BehavBox.treadmill` for task code. This attribute is
   `None` when treadmill acquisition is disabled and is not an alias for the
   former low-level `treadmill_encoder` object.
8. During `InputService.close()`, close the treadmill facade idempotently.
9. Remove the existing treadmill-only sampler thread, step-difference speed
   calculation, and direct treadmill TSV handle after the replacement is
   accepted.

The existing `SharedIoRecorder` ownership rules must remain intact:

- user-owned and task-owned recording demands may overlap;
- the first demand opens one recording session;
- a task reuses an already active user recording;
- treadmill zeroing and logger startup happen only when a new shared recording
  actually begins, not whenever another owner joins it;
- the final owner stopping closes the treadmill recording.

### 5.3 Enablement and profile rules

The `treadmill` session field becomes authoritative:

- `treadmill = false`: do not claim treadmill lines, start treadmill processes,
  or create treadmill artifacts;
- `treadmill = true` with `box_profile = "head_fixed"`: use BCM13 and BCM16
  from the manifest and start the robust runtime;
- `treadmill = true` with `box_profile = "freely_moving"`: reject the
  configuration because those pins belong to `poke_extra1` and `poke_extra2`;
- acquisition failure must never silently activate the current gpiozero path.

No two devices or libraries may claim the same GPIO line.

## 6. Required architecture

```text
Encoder A/B on manifest-defined BCM pins
        |
        v
Linux GPIO character-device capture
(libgpiod v2, both edges, kernel timestamps, sequence numbers)
        |
        v
Dedicated treadmill acquisition process
        |-- drain events in batches
        |-- x4 quadrature decoder
        |-- position, distance, speed, and direction
        |-- heartbeat, health, integrity, and lag diagnostics
        |-- bounded recent-event ring
        |
        v
Single-writer coherent shared state
        |                         |
        v                         v
Behavior-facing facade      Separate logger process
cheap snapshots             fixed-rate TSV/JSON artifacts
```

The three output paths remain distinct:

1. Live state for behavior code.
2. Fixed-rate physical state for offline analysis.
3. Event-driven diagnostics which determine whether the measurements are
   trustworthy.

The behavior process and logger are readers. The acquisition process is the
only writer of canonical decoder state.

## 7. Configuration

### 7.1 Sources and precedence

The intended readable default file is:

```text
box_runtime/input/treadmill/defaults.yaml
```

It will be added during implementation, not during this specification pass.

Configuration sources have explicit ownership and precedence:

1. `box_runtime/io_manifest.py` owns BCM pin assignments.
2. `defaults.yaml` owns readable treadmill calibration and runtime defaults.
3. `session_info["treadmill_config"]` may override documented settings for a
   particular rig or session.
4. The validated effective configuration is written to session metadata.

Pins must not be copied into `defaults.yaml`. A session override must not be
able to bypass manifest ownership silently.

### 7.2 Configuration fields

The validated configuration must cover at least:

```text
mm_per_encoder_cycle
locomotion_sign
calibration_verified
calibration_source
calibration_note

gpio_bias
debounce_period_us
kernel_event_buffer_size
read_batch_size

speed_timeout_s
continuous_log_enabled
continuous_log_rate_hz
continuous_log_flush_interval_s

diagnostic_ring_buffer_size
raw_edge_logging_enabled

heartbeat_interval_s
heartbeat_failure_timeout_s
processing_lag_warning_ns
startup_timeout_s
shutdown_timeout_s

treadmill_required
failure_policy
continuous_logger_required
expected_max_transition_rate_hz
```

Recommended initial values include:

```yaml
mm_per_encoder_cycle: 0.39269908169872414
locomotion_sign: 1
calibration_verified: false
calibration_source: nominal_25_mm_roller_200_cycles_per_revolution
calibration_note: UNVERIFIED - perform physical treadmill calibration

gpio_bias: pull_up
debounce_period_us: 0
kernel_event_buffer_size: 8192
read_batch_size: 256

speed_timeout_s: 0.050
continuous_log_enabled: true
continuous_log_rate_hz: 200.0
continuous_log_flush_interval_s: 1.0

diagnostic_ring_buffer_size: 8192
raw_edge_logging_enabled: false

treadmill_required: false
failure_policy: warn
continuous_logger_required: false
```

Heartbeat, lag-warning, startup, shutdown, and expected-rate defaults must be
chosen and documented after timing measurements on the target Pi 5.

Configuration validation must reject at least:

- missing or identical A/B manifest pins;
- a non-head-fixed profile when treadmill acquisition is enabled;
- nonpositive calibration, timeouts, rates, or buffer sizes;
- `locomotion_sign` outside `{-1, +1}`;
- invalid bias, health-policy, or logger-policy values;
- a heartbeat failure timeout not greater than its update interval;
- an unwritable recording directory;
- a requested event-rate envelope without adequate documented buffer headroom.

## 8. Calibration and units

### 8.1 Canonical calibration

Physical outputs are canonical. Raw counts are diagnostic.

The authoritative calibration setting is:

```text
mm_per_encoder_cycle
```

The decoder derives:

```text
mm_per_transition = mm_per_encoder_cycle / 4
```

Do not store both as independently editable values.

The modern nominal defaults are:

```text
nominal roller diameter        = 25.0 mm
nominal encoder cycles/rev     = 200
nominal mm/encoder cycle       = pi * 25.0 / 200
                               = 0.39269908169872414 mm
nominal mm/x4 transition       = 0.09817477042468103 mm
```

These are unverified starting values, not production calibration evidence.

For migration comparison, retain:

```text
legacy mm/encoder cycle        = 0.41095 mm
legacy mm/x4 transition        = 0.1027375 mm
legacy implied roller diameter = approximately 26.16 mm (1.03 inches)
```

Old and new raw counts must not be compared directly. One legacy A-rising
count corresponds nominally to one encoder cycle and four x4 transitions.

### 8.2 Unverified-calibration behavior

`calibration_verified = false` is allowed during framework development and
initial hardware bring-up. It must cause:

- a prominent startup warning;
- a diagnostic event;
- an explicit metadata value;
- an explicit final-summary warning.

It does not currently block a session, including a required treadmill session.
This policy may be tightened after the calibration workflow has been exercised.

### 8.3 Required user calibration

After the framework is ready, each distinct mechanical rig must be calibrated:

1. Start diagnostic acquisition.
2. Move the treadmill through a measured physical distance over several
   rotations to reduce measurement error.
3. Record decoded encoder cycles or x4 transitions.
4. Calculate `mm_per_encoder_cycle` from measured distance and cycles.
5. Repeat in both directions and check for meaningful asymmetry or slip.
6. Move in the direction corresponding to animal-forward locomotion.
7. Set `locomotion_sign` so animal-forward position and speed are positive.
8. Save the value, rig identity, date, method, and operator in readable
   configuration/metadata.
9. Set `calibration_verified = true` only after review.

If multiple boxes differ mechanically, use explicit per-rig session overrides
or separate reviewed calibration records. Do not silently change a global
default for one exceptional rig.

## 9. GPIO backend

### 9.1 Required capabilities

Use the Linux GPIO character-device interface through an official libgpiod
v2-compatible Python API. At startup, verify support for:

- one joint request for both input lines;
- both-edge detection;
- explicit pull-up bias;
- explicit zero debounce;
- monotonic event timestamps;
- event line offset and rising/falling type;
- global and per-line event sequence numbers;
- configurable kernel event buffering;
- waiting for events and reading them in batches.

If a required capability is absent, fail with an actionable error. Do not
silently fall back to gpiozero, RPi.GPIO, polling, or the archived microcontroller
path.

The exact Trixie package and binding version must be verified from the installed
package source and official documentation on the target Pi 5 before adapter
code is written. Provisioning, environment verification, and installation
docs must then be updated as part of implementation.

### 9.2 BCM-to-gpiochip resolution

External configuration and the manifest continue using familiar BCM numbers.
The backend must resolve them to the correct runtime gpiochip and line offsets.

It must not assume `/dev/gpiochip0`.

Resolution must:

- enumerate available gpiochips;
- inspect chip name, label, line count, and relevant line information;
- recognize the Pi 5 RP1 header controller;
- tolerate gpiochip-number changes across kernels/device trees;
- confirm both configured BCM lines belong to one unambiguous header
  controller;
- reject missing, ambiguous, out-of-range, or already claimed lines;
- record every candidate and the reason it was accepted or rejected;
- record the final chip path, chip label, BCM pins, and resolved offsets.

### 9.3 Electrical defaults

Preserve the current/legacy pull-up convention initially:

```text
gpio_bias = pull_up
debounce_period_us = 0
```

Do not silently enable software or kernel debounce. Debounce can erase valid
high-frequency edges from an optical or magnetic encoder.

Before production acceptance, document:

- encoder manufacturer/model if identifiable;
- encoder type and cycles per revolution;
- output voltage and output stage;
- external pull resistors;
- whether Pi internal pull-ups are required;
- whether signal conditioning or debounce is required;
- electrical compatibility with 3.3 V Pi GPIO.

### 9.4 Initial state synchronization

Acquisition must establish a coherent initial A/B state before reporting
`READY`. It must account for motion during startup and queued events.

The implementation may use a bounded quiet-period/retry algorithm, but it must
prove through deterministic tests that an event at any synchronization boundary
is either:

- included exactly once after the accepted initial state; or
- explicitly excluded as pre-session startup motion.

It must never read current levels and then apply an already represented queued
event a second time.

If a clean initial state cannot be established before the startup deadline,
startup fails with an actionable error.

### 9.5 Event draining

The acquisition hot loop must:

1. wait no longer than the next heartbeat/control deadline;
2. read all currently available events in bounded batches;
3. preserve kernel order;
4. adapt events to the pure decoder representation;
5. update canonical state;
6. publish a coherent snapshot after an appropriate batch;
7. update heartbeat on schedule even while stationary;
8. service only a bounded amount of lower-priority control work after pending
   edge events are drained.

The hot path must contain no:

- console output;
- disk writes;
- network access;
- GUI work;
- behavior callbacks;
- blocking communication with readers;
- unbounded queues or histories;
- avoidable high-cost allocation.

## 10. Generic edge and pure decoder

### 10.1 Internal edge representation

Production and synthetic sources must feed the same small internal event:

```text
timestamp_ns
channel              # A or B
edge_type            # rising or falling
global_sequence_number
line_sequence_number
```

The pure decoder must have no dependency on:

- Raspberry Pi hardware;
- `/dev/gpiochip*`;
- libgpiod objects;
- multiprocessing;
- files;
- wall-clock time.

### 10.2 x4 quadrature state machine

Represent A/B as two-bit states. Define one internal positive sequence:

```text
00 -> 01 -> 11 -> 10 -> 00
```

and the reverse as negative:

```text
00 -> 10 -> 11 -> 01 -> 00
```

Each valid single-bit transition changes signed transition position by `+1`
or `-1`. Direction reconstruction must use the ordered edge stream, not a later
read of both GPIO levels.

Use an explicit and exhaustively tested transition table. Do not synthesize a
missing intermediate transition.

### 10.3 Position and distance

Maintain integer lifetime values:

```text
raw_transition_position
absolute_transition_total
zero_transition_offset
zero_absolute_offset
```

Calculate physical values instead of accumulating floating point:

```text
position_mm =
    (raw_transition_position - zero_transition_offset)
    * mm_per_transition
    * locomotion_sign

distance_travelled_mm =
    (absolute_transition_total - zero_absolute_offset)
    * mm_per_transition
```

`position_mm` is signed displacement from the current zero.
`distance_travelled_mm` is cumulative absolute movement since the current zero.

Behavior code must not rely on raw transition totals.

### 10.4 Direction

Keep these concepts separate:

```text
encoder_direction
locomotion_direction
locomotion_sign
```

Directions use `-1`, `0`, and `+1`; zero means no current motion/direction.
Animal-forward motion is positive in the public physical API.

Do not preserve ambiguous `FW`/`BW` constants from legacy code.

### 10.5 Speed

For consecutive valid position-changing transitions:

```text
dt_ns = current_kernel_timestamp_ns - previous_motion_timestamp_ns

last_edge_speed_mm_s =
    transition_delta
    * mm_per_transition
    * locomotion_sign
    / (dt_ns / 1_000_000_000)
```

Use kernel event time, not Python processing time or wall time.

Expose both:

```text
last_edge_speed_mm_s
speed_mm_s
```

`speed_mm_s` returns the latest edge speed while motion is newer than the
configured timeout and zero after the timeout. `last_edge_speed_mm_s` remains
available after timeout. The first valid transition has no interval-derived
speed estimate.

No hidden smoothing, movement threshold, or task-specific filter belongs in
the acquisition layer.

### 10.6 Zeroing

`zero()` updates offsets in the acquisition process at a defined event boundary
and returns an acknowledgement containing command identity, timestamp, and
published state version.

Zeroing must not reset:

- lifetime event/transition totals;
- sequence gaps;
- inconsistency counters;
- lag metrics;
- health history;
- a latched integrity failure.

## 11. Integrity and diagnostics

### 11.1 Sequence tracking

Track global and per-line sequence continuity when supplied by libgpiod.

A forward sequence jump means one or more delivered kernel events were not
observed by the decoder. It must:

- increment the appropriate gap counters;
- estimate the number of missing events;
- set `integrity_valid = false`;
- latch acquisition health as `FAILED`;
- preserve recent diagnostic events;
- state clearly that exact position after the gap is unknown.

Do not infer replacement counts from previous speed or direction.

Sequence continuity cannot detect an electrical transition that the kernel
never recognized. Hardware signal validation remains separate.

### 11.2 Event consistency

Track and distinguish at least:

- global sequence gaps;
- per-line sequence gaps;
- duplicate/inconsistent edge type for the remembered line level;
- impossible two-bit transition;
- timestamp regression or nonpositive speed interval;
- out-of-order sequence values;
- malformed channel or edge identifiers;
- backend read/request failures;
- processing lag warnings;
- shared-state publication skips;
- acquisition and logger process failures.

Before decoder code is written, tests must define a failure matrix specifying
for each anomaly:

- whether the event changes position;
- whether A/B state is resynchronized;
- which counters change;
- whether health becomes `DEGRADED` or `FAILED`;
- whether integrity latches false.

Sequence gaps and any ambiguity which can change exact position must always
invalidate integrity.

### 11.3 Processing lag

For each event:

```text
processing_lag_ns = processing_monotonic_ns - event.timestamp_ns
```

Maintain bounded aggregates such as count, sum, latest, maximum, and a fixed
histogram. Detailed percentiles may be derived at summary time.

Large lag is a warning about headroom. It is not proof of event loss if
sequence numbers remain continuous.

### 11.4 Recent-event ring

Maintain a bounded in-memory ring of recent events containing enough data to
reconstruct failures:

```text
kernel timestamp
processing timestamp
channel and edge
global and line sequence numbers
A/B state before and after
decoded transition delta
processing lag
diagnostic flags
```

The configured default is 8192 events. On integrity failure, transfer or write
a copy outside the acquisition hot path. Normal experiments must not stream
every raw edge to disk unless explicit debug mode is enabled.

## 12. Processes and shared state

### 12.1 Acquisition process

Acquisition must run in a dedicated process with its own interpreter. A normal
thread is not sufficient isolation from behavior-process Python load.

Use an explicitly selected multiprocessing start method compatible with Python
3.13 on Trixie. `spawn` is preferred unless target-Pi testing identifies a
documented reason to use another method.

The child constructs and owns the libgpiod request. Do not create an open GPIO
request in the parent and pass it across the process boundary.

### 12.2 Coherent publication

Readers must receive internally coherent snapshots. The acquisition writer
must never wait indefinitely for a behavior or logger reader.

An implementation may use double-buffered shared state, a sequence-counter
scheme with appropriate synchronization, or another measured design. It must
demonstrate on ARM hardware that:

- readers never receive torn field combinations;
- a stalled reader cannot stop edge draining;
- reader retry/timeout is bounded;
- skipped publication is diagnosed but does not corrupt canonical state.

### 12.3 Control channel

Infrequent controls may include:

```text
ZERO
START_RECORDING
STOP_RECORDING
STATUS
STOP
```

Controls must use bounded communication and must not carry individual GPIO
events. The acquisition process drains pending edges before lower-priority
controls. Commands requiring completion return matched acknowledgements.

### 12.4 Heartbeat

The acquisition process updates a heartbeat even when the treadmill is
stationary. Reader-side health checks compare heartbeat age with a configured
timeout.

A stale heartbeat or dead child process must appear as `FAILED`, not as
stationary speed zero.

## 13. Public state and facade

### 13.1 Public state

Use an immutable typed state record containing at least:

```text
position_mm
distance_travelled_mm

speed_mm_s
last_edge_speed_mm_s

encoder_direction
locomotion_direction

last_edge_monotonic_ns
last_motion_monotonic_ns
last_state_update_monotonic_ns
last_heartbeat_monotonic_ns

raw_transition_position
transition_position
equivalent_encoder_cycles

edge_event_count
valid_transition_count
positive_transition_count
negative_transition_count
direction_reversal_count

sequence_gap_count
estimated_missing_event_count
event_inconsistency_count

latest_processing_lag_ns
maximum_processing_lag_ns

health
integrity_valid
failure_code
state_version
```

Convenience fields such as time since last motion may be derived from a
caller-supplied current monotonic time.

### 13.2 Behavior-facing facade

Routine behavior code should need only operations conceptually equivalent to:

```python
treadmill.start()
state = treadmill.snapshot()
treadmill.zero()
report = treadmill.health_report()
treadmill.require_healthy()
treadmill.stop()
treadmill.close()
```

Exact names may follow established repository conventions. Behavior code must
not need to understand gpiochips, libgpiod objects, transition tables, shared
memory, or logger processes.

The modern BehavBox public boundary is `box.treadmill`. It is `None` when
`session_info["treadmill"]` is false. When treadmill acquisition is enabled,
it refers to the facade even if an optional startup failure leaves that facade
in a visible `FAILED` state. Do not reuse `box.treadmill_encoder` for this
object: that name describes the retired gpiozero device and its `.steps`
semantics.

## 14. Health and failure policy

### 14.1 Health states

Use explicit states:

```text
STARTING
READY
DEGRADED
FAILED
STOPPED
```

Maintain `integrity_valid` separately because logger health and trajectory
integrity are not identical.

Examples:

- `STARTING`: process exists but capability checks, line resolution, line
  claim, or synchronization are incomplete;
- `READY`: acquisition is synchronized, heartbeat is current, and no known
  integrity loss exists;
- `DEGRADED`: calibration is unverified, processing lag exceeds its warning
  threshold, or a non-required logger has failed;
- `FAILED`: startup/line/backend failure, sequence loss, ambiguous trajectory,
  stale heartbeat, or acquisition-process death;
- `STOPPED`: orderly requested shutdown completed.

Integrity loss remains latched until an explicit acquisition restart. Zeroing
must not clear it.

### 14.2 Required versus optional treadmill

`treadmill_required` controls startup behavior:

- if true, inability to reach `READY` or an allowed calibration-only
  `DEGRADED` state prevents session startup;
- if false, the session may continue after a visible startup failure, but the
  unavailable/failed state must be exposed and recorded.

An unverified calibration is currently an allowed, prominent `DEGRADED` state
and does not block startup.

### 14.3 Runtime failure policy

Support:

```text
warn
pause
abort
```

Default:

```text
warn
```

The treadmill subsystem reports a typed policy outcome. It does not directly
mutate task/presenter state.

- `warn`: log/deduplicate a prominent warning and allow task progression;
- `pause`: latch a `pause_requested` outcome for task code;
- `abort`: latch an `abort_requested` outcome for the application boundary.

No policy may make a failed acquisition appear healthy.

A non-required logger failure causes `DEGRADED` health but does not invalidate
otherwise continuous encoder position. If `continuous_logger_required` is
true, logger failure becomes `FAILED` without falsely claiming an encoder
sequence gap.

## 15. Modern BehavBox lifecycle

### 15.1 Preparation

During `BehavBox.prepare_session()` and `InputService` construction:

1. Validate profile, manifest ownership, calibration, and runtime settings.
2. Construct the treadmill facade only when enabled.
3. Start the acquisition process.
4. Validate backend capabilities and resolve the header GPIO controller.
5. Claim BCM13 and BCM16 together.
6. Synchronize initial A/B state.
7. Publish initial state and heartbeat.
8. Return only after startup reaches an allowed usable health state.

Required startup failure propagates through the existing `prepare_session()`
cleanup path.

### 15.2 Recording start

When `SharedIoRecorder` first opens a recording directory:

1. Start the independent treadmill logger for that directory.
2. Apply and acknowledge a recording-origin zero after already pending events
   are decoded.
3. Write configuration, platform, GPIO mapping, calibration, and timebase
   metadata.
4. Begin fixed-rate samples from the acknowledged state version.

If another recording owner joins an existing recording, do not start another
logger and do not zero again.

### 15.3 Recording stop

When the final recording owner stops:

1. Request the logger's final sample and summary.
2. Flush buffered TSV/JSON output.
3. Close recording artifacts.
4. Leave acquisition alive until `InputService.close()` so live state does not
   disappear before overall runtime cleanup.

### 15.4 Runtime close

Normal close must:

1. stop any active logger;
2. ask acquisition to drain already available events;
3. publish final state/diagnostics;
4. release only the owned GPIO lines;
5. set health to `STOPPED`;
6. join child processes within bounded timeouts.

`close()` must be idempotent. Forced termination is a last resort and must be
diagnosed in the application log and final/fallback summary.

## 16. Recording artifacts

Use existing repository-friendly TSV and JSON formats. Do not require SQLite
for the initial implementation.

All artifacts belong inside the active `SharedIoRecorder` recording directory
so current task/user ownership and session-transfer behavior continue to work.

### 16.1 Fixed-rate state

Write:

```text
treadmill_state.tsv
```

At minimum include named columns:

```text
sample_monotonic_ns
sample_utc_posix_s
position_mm
distance_travelled_mm
speed_mm_s
last_edge_speed_mm_s
last_edge_monotonic_ns
locomotion_direction
raw_transition_position
health
integrity_valid
state_version
```

This new versioned artifact replaces the meaning of the old two-column
`treadmill_speed.tsv`; do not silently write a new schema under the old name.

The logger default is 200 Hz and configurable. This is a state-sampling rate,
not an edge-acquisition rate.

Use monotonic deadline scheduling:

```text
next_deadline += period
```

If late, write one sample at its real timestamp, increment lateness/missed
deadline diagnostics, and advance to the next future deadline. Do not create a
burst of duplicate catch-up rows.

Buffer rows and flush in bounded chunks/intervals. Do not hold a full session
in RAM and do not perform one physical disk synchronization per sample.

### 16.2 Metadata

Write:

```text
treadmill_metadata.json
```

Include at least:

- schema/software version and git commit when available;
- session/rig identifiers;
- Pi model, OS, Python, kernel, and libgpiod versions;
- requested and resolved GPIO mapping;
- bias, debounce, buffer, and batch settings;
- all effective calibration values and verification state;
- all effective health/logger settings;
- continuous sample rate;
- expected maximum transition rate if known;
- start monotonic/UTC timebase anchor.

### 16.3 Diagnostics

Write event-driven records to:

```text
treadmill_diagnostics.jsonl
```

Record startup stages, GPIO resolution, `READY`, calibration warning, zero
acknowledgements, health transitions, lag warnings, gaps/inconsistencies,
logger/acquisition errors, and shutdown. Each record includes monotonic time,
severity, stable event code, message, and structured context.

### 16.4 Final summary

Write:

```text
treadmill_summary.json
```

Include at least:

```text
session duration
total edge events
valid/positive/negative transitions
direction reversals
global and per-line sequence gaps
estimated missing events
event inconsistencies
maximum observed transition rate
processing-lag aggregates/percentiles
publication skips
heartbeat/acquisition/logger failures
logger late/missed samples
final position and distance travelled
final health and integrity
calibration and verification warning
resolved GPIO and software versions
end monotonic/UTC timebase anchor
```

### 16.5 Raw-event diagnostics

Normal sessions retain only the bounded in-memory ring. On failure, preserve it
as a versioned TSV or JSONL diagnostic artifact outside the hot path.

Optional full raw-edge recording is allowed for validation/debugging and is
off by default. Its storage rate must be measured before long runs.

### 16.6 Logger failure fallback

If the treadmill logger cannot write its own failure, publish status to the
parent/facade. The parent must emit a structured application-log error. After
acquisition stops, it may write a small best-effort fallback summary in the
recording directory. That fallback must never be presented as a complete
treadmill record.

## 17. Performance and boundedness

### 17.1 Expected event rate

After physical calibration and maximum-speed measurement:

```text
mm_per_transition = mm_per_encoder_cycle / 4

expected_transition_rate_hz =
    maximum_expected_speed_mm_s / mm_per_transition
```

Record the result in configuration and metadata. Size the kernel buffer to
provide documented scheduling-latency headroom at that rate.

The initial 8192-event request is a starting value, not proof of adequacy. The
kernel event buffer and Python read batch size are distinct and must be measured
separately.

### 17.2 Performance invariants

Under the validated envelope:

- behavior load may delay behavior work but must not alter decoded position;
- logger load may delay samples but must not block edge acquisition;
- memory use remains bounded;
- queues/rings remain bounded;
- no event history grows without limit;
- no behavior-held lock can block the acquisition writer;
- processing lag and publication skips remain observable;
- sequence continuity and exact generated/decoded displacement remain the
  correctness criteria.

Optimize only after measurement. Preferred tuning order:

1. remove blocking work from acquisition;
2. batch event reads efficiently;
3. size kernel buffering;
4. reduce unnecessary hot-path allocation;
5. improve shared-state publication;
6. consider modest priority/affinity only with profiling evidence;
7. consider lower-level code only with approval and measured need.

## 18. Test-first requirements

Implementation must follow repository TDD policy. Tests for each phase are
committed and shown failing for the intended reason before implementation.

### 18.1 Configuration and integration tests

Test that:

- valid modern defaults and session overrides load exactly;
- invalid units, signs, rates, buffers, timeouts, and policies fail clearly;
- manifest BCM13/16 are used for head-fixed treadmill acquisition;
- pins are not duplicated in YAML;
- `treadmill=false` claims no treadmill lines and creates no artifacts;
- `treadmill=true` is accepted only for the head-fixed profile;
- freely-moving treadmill configuration fails before pin claims;
- required startup failure prevents session preparation;
- optional startup failure remains loud and inspectable;
- another recording owner does not restart or re-zero the logger;
- final-owner stop finalizes treadmill artifacts;
- non-treadmill lick, poke, trigger, output, and recording tests remain
  unchanged and passing.

### 18.2 Pure quadrature tests

Using synthetic events, exhaustively test:

- forward and reverse full cycles;
- partial cycles;
- every possible initial A/B state;
- long unidirectional motion;
- immediate and repeated reversals;
- randomized valid traces with fixed seeds;
- exact signed and absolute transition totals;
- current nominal and legacy calibration values;
- both locomotion signs;
- animal-forward public sign;
- zeroing while stationary and moving;
- repeated zeroing without erased diagnostics.

### 18.3 Speed tests

Generate kernel timestamps for known speeds and verify:

- exact magnitude and sign within numerical tolerance;
- first-transition behavior;
- direction changes;
- timeout to zero;
- retained last-edge speed after timeout;
- no dependence on Python processing delay or wall time.

### 18.4 Failure-injection tests

Inject:

- global and per-line sequence gaps;
- duplicate/inconsistent rising and falling events;
- impossible two-bit jumps;
- timestamp regression and nonpositive intervals;
- out-of-order sequences;
- malformed channel/edge identifiers;
- backend read failures;
- full control/status queues.

Verify counters, failure matrix behavior, latched integrity, health, preserved
diagnostic history, and absence of invented position corrections.

### 18.5 GPIO adapter and synchronization tests

With fake binding objects and synthetic chip inventories, test:

- required libgpiod capabilities;
- Pi 5 RP1 resolution under different gpiochip numbering;
- missing, ambiguous, claimed, and out-of-range lines;
- joint A/B requests with both edges, pull-up, zero debounce, monotonic clock,
  and requested buffer size;
- exact event adaptation and order preservation;
- multi-batch draining;
- an injected event at every startup synchronization boundary;
- cleanup after partial startup failure.

### 18.6 Shared-state and lifecycle tests

Test that:

- concurrent readers never receive torn snapshots;
- a stalled reader cannot stop acquisition progress;
- unavailable publication slots cause diagnosed skips, not blocking;
- startup waits for usable health and reports staged failures;
- heartbeat updates without motion;
- stale heartbeat or a killed worker becomes `FAILED`;
- zero acknowledgements match command and state version;
- graceful stop drains pending fake events and is idempotent;
- forced cleanup is bounded and diagnosed;
- warn, pause, and abort policy outcomes remain distinct.

### 18.7 Logger and artifact tests

Using deterministic clocks and temporary directories, test:

- nominal 200 Hz deadline scheduling;
- monotonic and UTC timestamp fields;
- late/missed deadline accounting without duplicate catch-up rows;
- stable/versioned TSV and JSON schemas;
- bounded buffering and periodic flush;
- complete normal shutdown;
- useful partial files after interruption where practical;
- logger failure never blocks acquisition;
- metadata and summary contain units, calibration, versions, health, and
  integrity;
- raw failure-ring dumps are reconstructable;
- repeated close does not overwrite a completed recording.

### 18.8 Volume and process stress tests

Run millions of seeded synthetic transitions containing speed changes, pauses,
bursts, and reversals. Require exact generated and decoded signed/absolute
totals with bounded memory.

Exercise the real acquisition/shared-state/logger process architecture while
the behavior process performs representative:

- CPU-heavy Python work;
- camera-like and GUI-like scheduling load;
- disk activity;
- console/log activity;
- network-like activity;
- periodic long sleeps.

Require exact displacement, zero injected/unexplained sequence gaps, valid
integrity, live processes, and recorded resource/lag metrics.

## 19. Hardware calibration and acceptance

### 19.1 Hardware facts required

For each rig record:

- Pi model and serial/rig identity;
- OS, Python, kernel, and libgpiod versions;
- encoder model/type/output voltage/output stage;
- encoder cycles per revolution if known;
- roller dimensions or measured distance calibration;
- external pulls and signal conditioning;
- BCM13/16 wiring and locomotion sign;
- maximum plausible treadmill speed;
- expected maximum x4 transition rate;
- longest expected session duration.

### 19.2 Known-good quadrature source

Final loss testing should use a microcontroller, hardware quadrature generator,
or another independent deterministic source that knows exactly how many
transitions it emits. A Python generator on the same Pi is useful for
development but is not sufficient as the only acceptance source.

### 19.3 Rate sweep

On the target Pi 5, test approximately:

```text
0.25 x maximum expected transition rate
0.50 x maximum expected transition rate
1.00 x maximum expected transition rate
2.00 x maximum expected transition rate
```

Optionally continue to 4x to characterize margin.

At 2x maximum expected rate under representative full behavior load require:

```text
generated signed transition position == decoded signed transition position
generated absolute transition total   == decoded absolute transition total
global sequence gaps                  == 0
unexplained per-line gaps              == 0
unexplained event inconsistencies      == 0
integrity_valid                        == true
```

Do not relax zero-loss acceptance merely because a low percentage was lost.

### 19.4 Motion cases

Physically test:

- very slow motion;
- maximum expected speed;
- starts and stops;
- partial cycles;
- long forward and reverse motion;
- immediate and repeated reversals;
- animal-forward locomotion sign;
- recording zero behavior.

### 19.5 Full-stack and soak test

Run the actual intended behavior stack with its cameras, stimuli, audio,
recording, operator/UI activity, networking, and task transitions.

Then run repeated/continuous encoder activity for at least:

```text
max(2 hours, 1.5 * longest expected behavioral session)
```

Require:

- exact known/generated position;
- no unexplained gaps or inconsistencies;
- valid integrity;
- acquisition and logger processes alive;
- stable CPU and memory;
- no growing backlog;
- bounded diagnostic structures;
- usable incremental artifacts;
- acceptable storage MB/hour;
- documented typical and worst processing lag.

## 20. Deliverables

### 20.1 Core runtime

- validated readable configuration;
- pure x4 decoder;
- libgpiod v2 backend and dynamic resolver;
- process-isolated acquisition;
- coherent live shared state;
- heartbeat, health, and failure-policy reporting;
- start, snapshot, zero, stop, and close lifecycle.

### 20.2 Modern integration

- narrow `InputService` treadmill adapter changes described in Section 5.2;
- authoritative `treadmill` enablement;
- manifest-owned BCM13/16;
- head-fixed/freely-moving validation;
- BehavBox live-state exposure;
- current shared-recording ownership preserved;
- archived and external legacy code unchanged.

### 20.3 Recording and diagnostics

- fixed-rate `treadmill_state.tsv`;
- `treadmill_metadata.json`;
- `treadmill_diagnostics.jsonl`;
- `treadmill_summary.json`;
- bounded recent-event ring;
- failure dump and optional full raw-edge debug mode;
- application-log fallback for logger failure.

### 20.4 Tools and documentation

- calibration/sign/rate diagnostic utility;
- synthetic and process stress utility;
- setup and dependency instructions for Pi 5 Trixie;
- calibration procedure;
- health/integrity interpretation guide;
- hardware rate-sweep and soak-test procedure;
- migration note containing modern and legacy calibration values.

## 21. Production acceptance checklist

The system is not production-ready until saved evidence shows:

- [ ] Pi 5/Trixie/Python 3.13 target verified;
- [ ] official libgpiod v2 capabilities verified on the target Pi 5;
- [ ] BCM13/16 resolve unambiguously and have no ownership conflict;
- [ ] electrical interface is safe for Pi GPIO;
- [ ] physical calibration and locomotion sign are recorded per rig;
- [ ] both channels and both edge types are observed;
- [ ] kernel timestamps drive speed calculations;
- [ ] x4 position/distance tests are exact;
- [ ] sequence gaps and ambiguous trajectory loss latch integrity false;
- [ ] worker death cannot appear as stationary healthy data;
- [ ] behavior and logger readers cannot block acquisition;
- [ ] fixed-rate artifacts are bounded, incremental, aligned, and versioned;
- [ ] synthetic million-event tests are exact;
- [ ] full process stress tests are exact under representative load;
- [ ] external hardware generator passes at 2x expected rate;
- [ ] full-stack direction/reversal/start/stop tests pass;
- [ ] long soak passes with stable resources and no loss/backlog;
- [ ] another lab member can interpret configuration, warnings, and summaries.

## 22. Relationship to the implementation plan

`docs/treadmill_plan.md` maps these requirements to the modern `box_runtime`
modules, current BehavBox lifecycle, Pi 5/Trixie/Python 3.13 dependency path,
repository TDD rules, and bounded Sol-coordinated/Terra-worker packages.

This specification remains authoritative. Runtime implementation begins only
after the user approves the revised plan, and plan-ledger status is evidence of
execution progress rather than a replacement for committed tests and measured
results.

## 23. Final design principle

Physical edge capture, quadrature interpretation, behavior consumption, and
continuous recording are separate responsibilities.

The kernel-captured edge stream is the historical source for motion. Python
may process an event later without changing its event timestamp or order.
Process isolation prevents ordinary behavior work from directly back-pressuring
edge interpretation. Shared physical state prevents behavior code from needing
to service every edge. Independent bounded recording prevents disk timing from
controlling acquisition correctness.

Every layer must be independently testable, observable, and able to fail
loudly without inventing trustworthy-looking treadmill data.
