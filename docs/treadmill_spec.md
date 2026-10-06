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

Importing BehavBox or the treadmill package on an off-Pi development host, and
running with `treadmill=false`, must not require `gpiod`, inspect gpiochips, or
start treadmill resources. The binding is loaded/probed only behind the enabled
hardware-backend boundary; pure and fake-backend tests remain importable without
it.

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

- `treadmill = false`: do not make a treadmill claim, start treadmill
  processes, or create treadmill artifacts; the freely-moving profile still
  uses BCM13/16 normally as `poke_extra1`/`poke_extra2`;
- `treadmill = true` with the resolved `head_fixed` manifest: use BCM13 and
  BCM16 from that manifest and start the robust runtime;
- `treadmill = true` with the resolved `freely_moving` manifest: reject the
  configuration because those pins belong to `poke_extra1` and `poke_extra2`;
- acquisition failure must never silently activate the current gpiozero path.

Resolve and validate this combination before BCM13 or BCM16 is claimed as a
treadmill or poke input. Other services may already own their disjoint lines;
the existing preparation cleanup path releases them on failure. No two devices
or libraries may claim the same GPIO line.

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

When treadmill acquisition is enabled, reject the retired top-level keys
`treadmill_speed_hz`, `treadmill_wheel_diameter_cm`, and
`treadmill_pulses_per_rotation` with an actionable migration message rather
than silently ignoring or guessing how to combine them with new settings. A
legacy diameter `d_cm` and cycles-per-revolution `n` translate explicitly to
`mm_per_encoder_cycle = pi * (10 * d_cm) / n`; a deliberately retained logging
rate moves to `continuous_log_rate_hz`. Disabled sessions do not load or
validate treadmill-only settings.

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
continuous_log_rate_hz
continuous_log_flush_interval_s

diagnostic_ring_buffer_size
diagnostic_event_queue_size

heartbeat_interval_s
heartbeat_failure_timeout_s
processing_lag_warning_ns
startup_timeout_s
shutdown_timeout_s

treadmill_required
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
continuous_log_rate_hz: 200.0
continuous_log_flush_interval_s: 1.0

diagnostic_ring_buffer_size: 8192
diagnostic_event_queue_size: 256

treadmill_required: false
continuous_logger_required: false

# Selected from Pi 5 measurements before runtime configuration is implemented.
heartbeat_interval_s: null
heartbeat_failure_timeout_s: null
processing_lag_warning_ns: null
startup_timeout_s: null
shutdown_timeout_s: null

# Remains null until physical calibration and maximum-speed measurement.
expected_max_transition_rate_hz: null
```

The numeric buffer, batch, rings/queues, and 200 Hz logger values are provisional
starting candidates. `TW-0` measurements on the target Pi 5 must select and
document positive heartbeat, lag-warning, startup, and shutdown values before
runtime configuration implementation begins. Only
`expected_max_transition_rate_hz` may remain `null` during framework
development; it must be positive before production acceptance.

Configuration validation must reject at least:

- missing or identical A/B manifest pins;
- a resolved non-head-fixed manifest profile when treadmill acquisition is
  enabled;
- a `treadmill` value which is not a Boolean;
- unknown `treadmill_config` keys;
- retired top-level treadmill settings in an enabled session;
- non-finite numeric values, nonpositive calibration/configured
  timeouts/configured rates/buffer sizes, or non-integer count/size fields;
- `locomotion_sign` outside `{-1, +1}`;
- invalid bias or logger-criticality values;
- a heartbeat failure timeout not greater than its update interval.

Boolean values are not accepted as integers. `debounce_period_us` is the one
allowed zero-valued duration; it must be a nonnegative integer.

Supported bias values are `pull_up`, `pull_down`, and `disabled`. `pull_up` is
the initial default; another value requires documented rig electrical evidence
rather than an implicit platform-specific mapping.

Buffer headroom is a measured acceptance result, not something static schema
validation can prove. Once `expected_max_transition_rate_hz` is known,
metadata and acceptance evidence must state the requested kernel buffer's
nominal time headroom at that rate and the observed zero-loss margin.

Recording-directory existence and writability are runtime recording-start
checks, not static configuration validation, because `SharedIoRecorder`
selects and creates that directory later.

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

Debian 13 currently provides the binding as `python3-libgpiod`; the exact
Raspberry Pi OS package origin, installed binding/library versions, and Python
signatures must still be verified from the target Pi and official package
source before adapter code is written. Provisioning, environment verification,
and installation docs must then be updated as part of implementation.

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

Synchronization tracks global/per-line sequence numbers for every observed
startup event, including events deliberately excluded from motion totals. The
accepted boundary carries those last-observed baselines forward, so the first
admitted event is continuity-checked rather than blindly trusted. The target
API evidence must verify the initial sequence convention for a new request when
no startup event was observed. Any startup sequence loss fails synchronization;
calling it pre-session motion must not hide an overflow.

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
The public diagnostic `zeroed_transition_position` is
`raw_transition_position - zero_transition_offset` in encoder sign, without
`locomotion_sign`. `equivalent_encoder_cycles` is that value divided by four
and may therefore be fractional. Positive/negative transition counters also
use encoder sign; the physical position, speed, and locomotion direction are
the sign-adjusted task-facing values. A direction reversal is counted when two
successive valid position-changing transitions have opposite encoder signs,
regardless of an intervening motion timeout.

Behavior code must not rely on raw transition totals.

These physical fields are numeric only while trajectory integrity is valid and
live acquisition has not failed unexpectedly. After a sequence gap, ambiguous
resynchronization, stale heartbeat, or acquisition-process loss, effective `position_mm`,
`distance_travelled_mm`, `speed_mm_s`, and `locomotion_direction` are `None`.
Canonical integer totals continue only as explicitly untrusted diagnostics; a
zero command cannot make them trustworthy again. Logger-only failure does not
mask physical fields when acquisition integrity and heartbeat remain valid. An
orderly `STOPPED` final snapshot retains its last trustworthy physical values,
while reporting that acquisition is no longer live.

### 10.4 Direction

Keep these concepts separate:

```text
last_encoder_direction
locomotion_direction
locomotion_sign
```

Directions use `-1`, `0`, and `+1`. `last_encoder_direction` is the direction
of the most recent valid transition and remains available after motion stops;
it is zero before the first valid transition. `locomotion_direction` is the
current sign-adjusted motion direction and returns to zero after
`speed_timeout_s`. Animal-forward motion is positive in the public physical
API. Effective `locomotion_direction` is `None` when current physical state is
unavailable as described in Section 10.3.

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

`speed_mm_s` starts at `0.0`. After a position-changing transition with no safe
preceding interval, it is `None` while that motion remains within the configured
timeout. Once a valid interval-derived estimate exists it returns that latest
edge speed while motion is recent, and it returns to zero after the timeout.
`last_edge_speed_mm_s` remains available after timeout and is `None` until two
valid position-changing transitions provide an interval. Effective
`speed_mm_s` is `None`, rather than zero, when acquisition continuity is
unavailable as described in Section 10.3.

This unsmoothed edge-to-edge estimate can reflect encoder phase/duty-cycle
nonuniformity and is not a task-specific velocity filter. Calibration and
hardware validation must characterize that variation. Any later filtered or
windowed behavior metric is a separate, explicitly named consumer-layer value;
it must not replace the recorded edge-derived measurement silently.

Any anomaly which makes the immediately preceding motion timestamp unsafe for
an interval estimate clears that speed baseline. The next valid transition
re-establishes the baseline but has no new edge-speed estimate; speed is never
computed across a sequence gap, timestamp error, or explicit state
resynchronization. A historical `last_edge_speed_mm_s` may remain available,
but it is not reused as current `speed_mm_s` after the baseline reset.

No hidden smoothing, movement threshold, or task-specific filter belongs in
the acquisition layer.

### 10.6 Zeroing

`zero()` updates offsets in the acquisition process at a defined event boundary
and returns an acknowledgement containing command identity, timestamp, and
published state version.

Public zeroing requires live acquisition and valid trajectory integrity. It is
allowed for calibration-only, lag-only, or logger-only degradation when those
conditions leave acquisition continuity intact. Unavailable or
integrity-invalid acquisition raises a typed health error and does not send or
apply a zero command. The worker rechecks integrity at the drained event
boundary so a failure racing the caller's precheck cannot establish an origin.

Zeroing must not reset:

- lifetime event/transition totals;
- sequence gaps;
- inconsistency counters;
- lag metrics;
- health history;
- a latched integrity failure.

The acknowledgement is successful only after the zeroed state has been
published coherently. If publication cannot complete before the command
deadline, `zero()` fails visibly; recording must not claim that state version
as its origin. The operation is atomic: an unacknowledged zero does not change
the canonical offsets. The worker may reserve a writable publication slot
before changing them or restore the prior offsets before processing more
events.

## 11. Integrity and diagnostics

### 11.1 Sequence tracking

Track global and per-line sequence continuity when supplied by libgpiod.
Startup synchronization supplies the baselines described in Section 9.4;
events explicitly excluded from motion totals still advance those baselines.

A forward sequence jump means one or more delivered kernel events were not
observed by the decoder. It must:

- increment the appropriate gap counters;
- estimate the number of missing events;
- set `integrity_valid = false`;
- latch acquisition health as `FAILED`;
- preserve recent diagnostic events;
- state clearly that exact position after the gap is unknown.

After recording a forward jump, advance the corresponding baseline to the
observed sequence value so the same missing range is counted once. A duplicate
or out-of-order sequence never moves a baseline backward. Apply these rules to
the global and relevant per-line trackers; only the global jump contributes to
`estimated_missing_event_count`.

Do not infer replacement counts from previous speed or direction.

The global sequence is authoritative for `estimated_missing_event_count`
because it spans the joint A/B request. Per-line sequence gaps identify which
channel histories are discontinuous but must not add the same missing events a
second time. If an observed inconsistency cannot be reconciled with the global
sequence, preserve it as a separate diagnostic rather than inventing a count.

Sequence continuity cannot detect an electrical transition that the kernel
never recognized. Hardware signal validation remains separate.

### 11.2 Event consistency

Track and distinguish at least:

- global sequence gaps;
- per-line sequence gaps;
- duplicate/inconsistent edge type for the remembered line level;
- timestamp regression or nonpositive speed interval;
- out-of-order sequence values;
- malformed channel or edge identifiers;
- backend read/request failures;
- processing lag warnings;
- shared-state publication skips;
- diagnostic-event channel drops;
- acquisition and logger process failures.

Before decoder code is written, tests must define a failure matrix specifying
for each anomaly:

- whether the event changes position;
- whether A/B state is resynchronized;
- which counters change;
- whether health becomes `DEGRADED` or `FAILED`;
- whether integrity latches false.

Because the internal event identifies one channel and one edge, applying one
well-formed event can change only one remembered A/B bit. A two-bit jump is not
a separate decoder input or failure class. Any mismatch found while explicitly
checking or resynchronizing physical line levels is instead recorded as a
state-resynchronization inconsistency. Sequence gaps and any ambiguity which
can change exact position must always invalidate integrity.

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

The initial candidate is 8192 events. The accepted size must be related to a
documented amount of recent activity at the measured maximum event rate. On a
failure the acquisition process can handle, transfer a best-effort copy through
a bounded nonblocking path for writing outside the hot loop. Abrupt process or
machine death may make the process-local ring unavailable; metadata and the
summary must say whether a dump was preserved instead of implying that one
always exists. The initial implementation does not add full-session raw-edge
recording or a second continuous edge transport path.

Low-rate structured lifecycle/health diagnostics use a separate bounded,
nonblocking channel; they never share the raw-edge path. The parent retains a
startup/current-health history of at most `diagnostic_event_queue_size` records
so a logger opened later can write context which predates recording. While
active, the logger drains new records.
If the channel is full, acquisition increments an observable drop counter and
continues; it never waits for diagnostic delivery.

## 12. Processes and shared state

### 12.1 Acquisition process

Acquisition must run in a dedicated process with its own interpreter. A normal
thread is not sufficient isolation from behavior-process Python load.

Use an explicitly selected multiprocessing start method compatible with Python
3.13 on Trixie. `spawn` is preferred unless target-Pi testing identifies a
documented reason to use another method. Obtain a treadmill-local
`multiprocessing` context (for example with `get_context("spawn")`); do not
change the application's global start method. Create both child processes and
all treadmill queues, events, locks, and shared-memory primitives from that
same context.

The child constructs and owns the libgpiod request. Do not create an open GPIO
request in the parent and pass it across the process boundary.
Worker targets, factories, and injected synthetic sources must be importable and
serializable under the selected method; importing a module must not claim GPIO
or start a process.

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

If a behavior snapshot cannot obtain a coherent copy within its bounded read
attempt, `snapshot()` raises a typed state-read error. `health_report()` remains
nonthrowing and reports at least effective `DEGRADED` with a stable read-timeout
code from its cached context. A logger which cannot read coherent state for one
deadline records the read failure and skips that row; it must not duplicate the
last row under a new timestamp. Reader timeout does not by itself invalidate
encoder trajectory integrity.

### 12.3 Control channel

The acquisition control channel is deliberately small:

```text
ZERO
STOP
```

Controls must use bounded communication and must not carry individual GPIO
events. The acquisition process drains pending edges before lower-priority
controls. Commands requiring completion return matched acknowledgements.
Status is read from coherent shared state and heartbeat. The parent facade,
not the acquisition worker, owns logger-process start and stop.

### 12.4 Heartbeat

The acquisition process updates a heartbeat even when the treadmill is
stationary. Every reader, including the behavior facade and logger, compares
heartbeat age with a configured timeout when materializing effective state.

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

last_encoder_direction
locomotion_direction

last_edge_monotonic_ns
last_motion_monotonic_ns
last_state_update_monotonic_ns
last_heartbeat_monotonic_ns

raw_transition_position
zeroed_transition_position
equivalent_encoder_cycles

edge_event_count
valid_transition_count
positive_transition_count
negative_transition_count
direction_reversal_count

global_sequence_gap_count
a_line_sequence_gap_count
b_line_sequence_gap_count
estimated_missing_event_count
event_inconsistency_count
publication_skip_count
diagnostic_event_drop_count

latest_processing_lag_ns
maximum_processing_lag_ns

health
integrity_valid
acquisition_available
failure_code
state_version
```

Convenience fields such as time since last motion may be derived from a
caller-supplied current monotonic time.

`last_edge_monotonic_ns` is the newest non-regressing kernel timestamp from an
admitted, identifiable A/B edge, even if that edge is later rejected as a
duplicate/inconsistent transition. `last_motion_monotonic_ns` advances only for
an accepted position-changing x4 transition. Malformed identifiers and
regressing timestamps still increment their diagnostic/event counters but must
not move either public timestamp backward.

Before the first corresponding event:

- `last_edge_speed_mm_s` is `None`;
- `last_edge_monotonic_ns` and `last_motion_monotonic_ns` are `None`;
- `latest_processing_lag_ns` and `maximum_processing_lag_ns` are `None`;
- `last_encoder_direction` and `locomotion_direction` are zero;
- `position_mm`, `distance_travelled_mm`, and `speed_mm_s` are `0.0`.

`failure_code` is the deterministic primary stable code and is `None` when no
current or latched warning/failure applies. If several codes apply, choose by
health severity and then an explicit stable priority table covered by the
failure-matrix tests; do not join free-form strings. `health_report()` returns
the complete ordered stable-code set plus human-readable context, while
diagnostics preserve chronology.
Counter fields are nonnegative integers except the signed raw/zeroed position
fields.
`edge_event_count` counts admitted kernel events, including rejected or
inconsistent events; `valid_transition_count` counts only position-changing
events accepted by the x4 table. The global/per-line gap fields count gap
occurrences, while `estimated_missing_event_count` sums missing events from the
global sequence only. `publication_skip_count` counts coherent-state
publications the nonblocking writer could not make.
`diagnostic_event_drop_count` counts structured diagnostic records omitted
because their bounded nonblocking channel was full.

`last_state_update_monotonic_ns` is the acquisition time at which decoder,
control, or health content last changed; a routine heartbeat alone does not
change it. `last_heartbeat_monotonic_ns` is refreshed on the heartbeat schedule.
Effective `acquisition_available` is true only while the worker is live and its
heartbeat is current; it is false for startup failure, unexpected loss, and
orderly `STOPPED` state. It is a reader-materialized public field, not a claim
that an abruptly dead worker could write into canonical shared state.
`state_version` increases for each coherent publication, including heartbeat
and acknowledged zero publications; readers must not interpret it as an edge
count. Motion/heartbeat timeout materialization can change effective speed,
direction, availability, and health without changing `state_version`, because
the underlying canonical publication has not changed.

The public immutable record uses optional types for fields which can be
unavailable initially or after acquisition continuity is lost. Fixed-width
shared state uses explicit validity flags, not magic float values. TSV encodes
an unavailable optional value as an empty field; JSON encodes it as `null`.

### 13.2 Behavior-facing facade

Routine behavior code should need only operations conceptually equivalent to:

```python
treadmill.start()
state = treadmill.snapshot()
treadmill.zero()
report = treadmill.health_report()
treadmill.require_healthy()
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

For an enabled facade which never published an initial state,
`health_report()` remains non-throwing and reports the startup failure, while
`snapshot()` and `require_healthy()` raise a typed treadmill-unavailable error.
When state exists, `require_healthy()` returns only for effective `READY` with
valid integrity; it raises a typed health error for `DEGRADED`, `FAILED`, or
`STOPPED`. Callers which intentionally allow an unverified-calibration
`DEGRADED` state use `health_report()` and make that choice explicitly.

`start()` is the integration lifecycle operation used once by `InputService`;
it is not a task-level way to restart a failed acquisition. `close()` sends the
internal `STOP`, joins owned children, and is idempotent. After close,
`health_report()` reports `STOPPED` only after orderly cleanup; forced or
incomplete child cleanup reports `FAILED`, unavailable, with a stable shutdown
failure code. `snapshot()` returns the corresponding final cached state when
one exists, and commands such as `zero()` raise a typed lifecycle error. The
facade caches that immutable final state before unlinking shared resources;
post-close reads do not access released IPC. A restart requires a new facade.

The facade also has integration-only recording start/stop operations used by
`InputService`. They are not alternate public BehavBox recording APIs: task and
user code continue through the existing shared recorder ownership methods.

## 14. Health and failure reporting

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
must not clear it. A processing-lag threshold crossing also latches acquisition
health at least `DEGRADED` for that run while leaving `integrity_valid`
unchanged; the summary preserves when and how often it occurred.

The shared acquisition record contains acquisition/calibration health. The
parent facade materializes effective subsystem health by also checking child
liveness, heartbeat age, and the active logger's status. The logger applies the
same acquisition heartbeat rule to each sample. Logger state is not written
back through the acquisition process, preserving single-writer decoder state.
Effective-health precedence is `STOPPED` after orderly close, otherwise
`FAILED` over `DEGRADED` over `READY`; `STARTING` applies only before the first
usable publication. Integrity-invalid acquisition failure remains latched.
Transient heartbeat failure may recover only if the same worker resumes with
continuous sequences; every failure and recovery remains in diagnostics.

### 14.2 Required versus optional treadmill

`treadmill_required` controls acquisition startup and recording-origin
establishment behavior:

- if true, inability to reach `READY` or an allowed calibration-only
  `DEGRADED` state prevents session startup;
- if false, the session may continue after a visible startup failure, but the
  unavailable/failed state must be exposed and recorded.

If an otherwise usable acquisition cannot acknowledge the recording-origin
zero, a required treadmill causes that newly opened shared recording start to
fail and roll back its ownership/handles. An optional treadmill leaves the
other shared IO recording active but finalizes failed, zero-sample treadmill
artifacts. Rollback does not promise to delete the selected directory or
already created failure evidence.

The same required/optional rule applies if acquisition is already unavailable
or integrity-invalid when a new shared recording begins. It does not
retroactively roll back a recording which was established successfully and
then experiences a runtime acquisition failure.

An unverified calibration is currently an allowed, prominent `DEGRADED` state
and does not block startup.

### 14.3 Runtime failure reporting

The initial implementation reports and deduplicates prominent runtime
warnings; it does not add unused pause/abort policy states or mutate task or
presenter state. Task-specific automatic pause/abort wiring remains deferred.
Tasks which require valid live treadmill data must call `require_healthy()` at
their chosen control boundary or explicitly inspect `health_report()`.
`treadmill_required` does not by itself add hidden task-control behavior.

For baseline visibility, the existing `BehavBox.poll_runtime()` path performs a
cheap nonthrowing treadmill health check when enabled and emits deduplicated
application warnings on health/code transitions. This check reads published
state only; it does not service edges, block acquisition, or alter task state.

While recording is active, a non-required logger failure causes effective
`DEGRADED` health but does not invalidate otherwise continuous encoder
position. If `continuous_logger_required` is true, logger start failure rolls
back a newly opened shared recording and runtime logger failure makes effective
health `FAILED`, without falsely claiming an encoder sequence gap. After a
clean final-owner logger stop, logger status no longer degrades live acquisition
health. Any optional treadmill recording failure, including an unacknowledged
origin zero, remains latched at least `DEGRADED` until that shared recording
ends even if its logger has already exited. Its summary preserves the failure.
No warning or logger policy may make failed acquisition appear healthy.

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
cleanup path. Because `BehavBox.input_service = InputService(...)` is assigned
only after construction returns, `InputService` must also clean any facade,
process, IPC, or input device it created if its own construction/startup raises;
it must not rely on the outer BehavBox reference already existing.

### 15.2 Recording start

When `SharedIoRecorder` first opens a recording directory:

1. Confirm the directory exists.
2. Start the independent treadmill logger; the logger opens the required
   artifacts and acknowledges successful startup. Treat an open/write failure
   according to `continuous_logger_required`.
3. For usable acquisition, apply and acknowledge a recording-origin zero after
   already pending events are decoded. Here, usable means that a state exists,
   the worker and heartbeat are current, and trajectory integrity is valid;
   calibration-only or latched lag `DEGRADED` health is still usable.
4. Write configuration, platform, GPIO mapping, calibration, and timebase
   metadata, including the acknowledged state/counter baseline.
5. Begin fixed-rate samples from the acknowledged state version.

If another recording owner joins an existing recording, do not start another
logger and do not zero again.

Every newly opened shared recording while acquisition remains live repeats
this sequence with a new logger instance and a new zero. Continuous treadmill
state logging is part of every enabled treadmill recording; the initial
implementation has no configuration branch that silently omits the normal
artifacts.

If the recording-origin zero fails, follow the required/optional behavior in
Section 14.2. No samples may be labeled as belonging to an origin that was not
acknowledged.

If optional treadmill acquisition failed during startup or became unusable
before recording began, recording still creates the normal state, metadata,
and diagnostics artifacts and, by recording finalization, the normal summary.
The state TSV contains only its header, while metadata, diagnostics, and the
final summary identify the failure, zero samples, `FAILED` health, unavailable
acquisition, and the last known integrity value. An acquisition which never
established state reports integrity as false; a later heartbeat/process failure
does not invent a sequence gap and may retain the last integrity value as true.
No zero command or fixed-rate sampling occurs. A required treadmill instead
follows the rollback rule in Section 14.2. This distinguishes a
requested-but-failed treadmill from `treadmill=false`, which creates no
treadmill artifacts.

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
2. send `STOP` so acquisition drains already available events, publishes final
   state/diagnostics, and releases only its owned GPIO lines;
3. join child processes within bounded timeouts;
4. cache clean `STOPPED` state, or overlay `FAILED` with a shutdown failure code
   if forced/incomplete cleanup was required;
5. clean up every parent-owned treadmill IPC resource using its supported
   contract: close/join queue feeder resources, close/unlink shared memory, and
   release synchronization resources/references.

`close()` must be idempotent. Forced termination is a last resort and must be
diagnosed in the application log and in any still-open or fallback summary.
Do not rewrite a previously completed recording merely because later
acquisition shutdown failed. After exhausting its bounded cleanup path, the
facade records forced/incomplete shutdown in cached health rather than raising
solely for that condition, so enclosing `BehavBox.close()` can continue
cleaning unrelated resources.

## 16. Recording artifacts

Use existing repository-friendly TSV and JSON formats. Do not require SQLite
for the initial implementation.

All artifacts belong inside the active `SharedIoRecorder` recording directory
so current task/user ownership and session-transfer behavior continue to work.
Before opening normal treadmill artifacts, fail visibly if any target treadmill
path already exists. Do not truncate, append to, or silently reuse a prior
treadmill record; apply logger criticality and shared-recorder rollback rules to
the collision.

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
acquisition_available
state_version
```

This new versioned artifact replaces the meaning of the old two-column
`treadmill_speed.tsv`; do not silently write a new schema under the old name.
The artifact-set schema version in `treadmill_metadata.json` governs this TSV
and the diagnostics JSONL; column/record changes require a schema-version
change.

The initial logger candidate is 200 Hz and configurable. This is a
state-sampling rate, not an edge-acquisition rate. Pi 5 validation must compare
at least 50, 100, and 200 Hz and select the lowest rate that meets documented
behavioral and offline-analysis timing requirements.

Use monotonic deadline scheduling:

```text
next_deadline += period
```

If late, write one sample at its real timestamp, increment lateness/missed
deadline diagnostics, and advance to the next future deadline. Do not create a
burst of duplicate catch-up rows.

Buffer rows and flush in bounded chunks/intervals. Do not hold a full session
in RAM and do not perform one physical disk synchronization per sample.

The logger materializes effective health from heartbeat age for every sample.
If acquisition dies after publishing `READY`, subsequent samples must report
`FAILED` after the heartbeat timeout rather than repeating healthy stationary
state.

### 16.2 Metadata

Write:

```text
treadmill_metadata.json
```

Include at least:

- artifact-set schema version, software version, and git commit when available;
- session/rig identifiers;
- Pi model, OS, Python, kernel, and libgpiod versions;
- requested and resolved GPIO mapping;
- bias, debounce, buffer, and batch settings;
- all effective calibration values and verification state;
- all effective health/logger settings;
- continuous sample rate;
- expected maximum transition rate if known;
- recording-origin acknowledgement status, command identity, monotonic
  timestamp, state version, and counter baselines when established;
- start monotonic/UTC timebase anchor.

### 16.3 Diagnostics

Write event-driven records to:

```text
treadmill_diagnostics.jsonl
```

Record startup stages, GPIO resolution, the first usable state (`READY` or an
explicitly allowed `DEGRADED`), calibration warning, zero acknowledgements,
health transitions, lag warnings, gaps/inconsistencies,
logger/acquisition errors, and recording/logger shutdown. Acquisition shutdown
is included only when it occurs while the artifact writer remains open; later
close failures remain in the application log. Each record includes monotonic
time, severity, stable event code, message, and structured context.

### 16.4 Final summary

Write:

```text
treadmill_summary.json
```

Include at least:

```text
recording duration
recording-window edge events
recording-window valid/positive/negative transitions
recording-window direction reversals
recording-window global and per-line sequence gaps
recording-window estimated missing events
recording-window event inconsistencies
maximum observed transition rate
processing-lag aggregates/percentiles
publication skips
diagnostic-event drops
heartbeat/acquisition/logger failures
logger late/missed samples
logger shared-state read failures
final position and distance travelled
final health, integrity, and acquisition availability
calibration and verification warning
resolved GPIO and software versions
end monotonic/UTC timebase anchor
```

The summary repeats the artifact-set schema version. A standalone failure-ring
dump or `treadmill_failure.json` carries its own schema version because it may
need to be interpreted without a complete metadata file.

Recording-window counters are final lifetime counters minus the baselines saved
with the recording-origin acknowledgement; zeroing never mutates lifetime
counters. The summary also includes the baseline and final lifetime counters so
the calculation is auditable. If no recording origin was acknowledged, set
`recording_origin_acknowledged=false`, encode recording-window counters as
`null`, and include any last-known lifetime counters without inventing a
baseline or zero-valued recording result.

Final physical values are `null` when trajectory integrity or acquisition
availability was lost; the summary retains raw delivered-event totals as
explicitly untrusted diagnostics.

### 16.5 Raw-event diagnostics

Normal sessions retain only the bounded in-memory ring. On failure, preserve it
as schema-versioned `treadmill_failure_events.jsonl` outside the hot path when
the acquisition process can deliver the best-effort snapshot. Until a recording
directory exists, the parent may retain at most the first bounded failure
snapshot for a later logger; it must not accumulate failure dumps. Metadata and
the final summary identify whether the dump is present, unavailable after an
abrupt failure, or not requested because no qualifying failure occurred.

### 16.6 Logger failure fallback

If the treadmill logger cannot write its own failure, publish status to the
parent/facade. The parent must emit a structured application-log error. Once
the failed logger has stopped or been terminated, the parent may write a small
best-effort fallback summary in the recording directory as
`treadmill_failure.json`; it does not wait for acquisition shutdown. That
fallback must never be presented as a complete treadmill record or use the
normal summary filename. It is also best-effort/exclusive: do not overwrite an
existing fallback file.

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

Implementation must follow repository TDD policy. Tests for each work package are
committed and shown failing for the intended reason before implementation.

### 18.1 Configuration and integration tests

Test that:

- valid modern defaults and session overrides load exactly;
- timing fields selected during `TW-0` are positive and internally
  consistent, while the expected maximum rate may remain `null` only before
  production acceptance;
- non-Boolean enablement and unknown override keys fail clearly;
- retired enabled-session keys fail with their explicit conversion guidance;
- invalid units, signs, rates, buffers, timeouts, and logger-criticality values
  fail clearly;
- `NaN`, infinities, fractional size/count fields, and Boolean-as-integer values
  fail clearly;
- manifest BCM13/16 are used for head-fixed treadmill acquisition;
- pins are not duplicated in YAML;
- `treadmill=false` creates no treadmill claim/process/artifacts while normal
  profile inputs, including freely-moving BCM13/16 pokes, remain unchanged;
- `treadmill=true` is accepted only for the head-fixed profile;
- freely-moving treadmill configuration fails before BCM13/16 claims;
- required startup failure prevents session preparation;
- partial `InputService` construction failure leaves no treadmill process, IPC,
  or GPIO claim even though owner assignment never completed;
- optional startup failure remains loud and inspectable;
- an enabled facade without initial state returns a failed health report and
  raises the typed unavailable error from `snapshot()`/`require_healthy()`;
- another recording owner does not restart or re-zero the logger;
- final-owner stop finalizes treadmill artifacts;
- a later newly opened recording starts a new logger and establishes one new
  zero without restarting acquisition;
- enabled optional startup failure creates header-only state plus explicit
  failed metadata, diagnostics, and summary artifacts;
- recording-directory open failure follows logger criticality and rolls back a
  newly opened shared recording when required;
- pre-existing treadmill artifact paths are never overwritten or appended and
  follow the same visible logger-failure handling;
- recording-origin zero failure is atomic and follows treadmill required versus
  optional behavior without emitting mislabeled samples;
- pre-recording acquisition/integrity failure rolls back required treadmill
  recording start but produces failed optional treadmill artifacts;
- non-treadmill lick, poke, trigger, output, and recording tests remain
  unchanged and passing.
- runtime polling surfaces one warning per treadmill health/code transition
  without changing task state or warning continuously.

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
- current speed is `None` after first/re-baseline motion until a safe interval,
  then becomes zero only after timeout;
- initial `None` speed/timestamp values and zero directions;
- direction changes;
- timeout to zero;
- retained last encoder direction after current locomotion direction times out;
- retained last-edge speed after timeout;
- rejected/regressing events follow the documented last-edge/last-motion
  timestamp rules;
- no dependence on Python processing delay or wall time.

### 18.4 Failure-injection tests

Inject:

- global and per-line sequence gaps;
- duplicate/inconsistent rising and falling events;
- timestamp regression and nonpositive intervals;
- out-of-order sequences;
- malformed channel/edge identifiers;
- backend read failures;
- full bounded control, acknowledgement, logger-status, and diagnostic-event
  channels.

Verify counters, failure matrix behavior, latched integrity, health, preserved
diagnostic history, global-only missing-event totals without per-line double
counting, one-time forward-gap accounting without backward baseline movement,
deterministic primary/all-code reporting, speed-baseline reset,
unavailable task-facing physical fields after continuity loss, and absence of
invented position corrections.

### 18.5 GPIO adapter and synchronization tests

With fake binding objects and synthetic chip inventories, test:

- required libgpiod capabilities;
- package/BehavBox import and disabled operation when `gpiod` is absent;
- Pi 5 RP1 resolution under different gpiochip numbering;
- missing, ambiguous, claimed, and out-of-range lines;
- joint A/B requests with both edges, pull-up, zero debounce, monotonic clock,
  and requested buffer size;
- exact event adaptation and order preservation;
- multi-batch draining;
- an injected event at every startup synchronization boundary;
- startup excluded-event baselines, initial sequence convention, and a gap
  before the first admitted event;
- cleanup after partial startup failure.

### 18.6 Shared-state and lifecycle tests

Test that:

- concurrent readers never receive torn snapshots;
- a stalled reader cannot stop acquisition progress;
- unavailable publication slots cause diagnosed skips, not blocking;
- behavior read timeout raises the typed error while health reporting remains
  nonthrowing and non-healthy;
- startup waits for usable health and reports staged failures;
- spawn-mode worker targets and injected sources serialize without import-time
  process or GPIO side effects;
- treadmill process creation leaves the application's global multiprocessing
  start-method configuration unchanged;
- acquisition/logger processes and all IPC primitives use the same selected
  multiprocessing context;
- heartbeat updates without motion;
- stale heartbeat or a killed worker becomes `FAILED`;
- zero acknowledgements match command and state version;
- an unacknowledged zero leaves canonical offsets unchanged;
- zero rejects unavailable/integrity-invalid acquisition but remains available
  for degradation that leaves trajectory continuity intact;
- close drains pending fake events and is idempotent;
- forced cleanup is bounded and diagnosed;
- forced treadmill cleanup does not prevent the enclosing BehavBox cleanup
  from reaching later unrelated resources;
- repeated construct/start/close cycles leave no live child or owned IPC
  resource behind;
- post-close snapshots use the cached final state, do not touch released IPC,
  preserve clean `STOPPED` versus forced `FAILED` shutdown, and commands follow
  the documented facade lifecycle.

### 18.7 Logger and artifact tests

Using deterministic clocks and temporary directories, test:

- deterministic deadline scheduling at 50, 100, and 200 Hz plus the selected
  configured rate;
- monotonic and UTC timestamp fields;
- late/missed deadline accounting without duplicate catch-up rows;
- shared-state read failures skip rows without timestamping stale copies and
  appear in diagnostics/summary;
- stable/versioned TSV and JSON schemas;
- bounded buffering and periodic flush;
- complete normal shutdown;
- useful partial files after interruption where practical;
- logger failure never blocks acquisition;
- acquisition death cannot leave later logger samples marked healthy;
- an optional recording failure remains visible until the shared recording
  ends and does not invalidate intact acquisition trajectory;
- metadata and summary contain units, calibration, versions, health, and
  integrity;
- startup/pre-recording diagnostic context reaches a later logger, and a full
  diagnostic channel increments the published/summary drop counter without
  blocking acquisition;
- handled-failure ring dumps are reconstructable and abrupt-loss summaries do
  not claim a dump exists;
- repeated close does not overwrite a completed recording;
- requested-but-failed and disabled treadmill recordings remain
  distinguishable from their artifact sets and metadata;
- failed recordings without an acknowledged origin use null window counters
  while preserving any known lifetime counters.

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
- heartbeat and health reporting;
- start, snapshot, zero, recording, and close lifecycle.

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
- best-effort handled-failure ring dump;
- application-log fallback for logger failure.

### 20.4 Tools and documentation

- calibration/sign/rate diagnostic utility;
- synthetic and process stress utility;
- setup and dependency instructions for Pi 5 Trixie;
- calibration procedure;
- health/integrity interpretation guide;
- hardware rate-sweep and soak-test procedure;
- migration note containing modern and legacy calibration values.
- one tracked `docs/treadmill_validation.md` record linking the target API,
  timing, calibration, stress, rate-sweep, soak, commands, commits, and external
  artifact locations used for acceptance.

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
after a separate explicit user instruction; documentation review or approval
does not authorize it. Plan-ledger status is evidence of execution progress,
not a replacement for committed tests and measured results.

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
