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
it. Here, fake-backend means injected test doubles for the libgpiod boundary,
not an enabled runtime treadmill simulator.

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
- a C extension, Cython, Numba, or another premature optimization;
- reader generations, leases, abandoned-thread recovery, or another parent
  concurrency layer beyond the lifecycle `RLock`;
- a second parent diagnostic buffer, competing live consumer, or exact-once
  recovery protocol for an already failed logger.

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

The final `InputService` change must be localized to its treadmill and shared-
recording lifecycle integration. It must not redesign lick, poke, trigger,
profile, or general IO recording behavior beyond the close gate required
below.

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
8. Route shared-recording ownership transitions through the Section 10.6
   recording/zero/close guard, including when treadmill is disabled.
9. During `InputService.close()`, set the close gate, clean the shared recorder,
   and close the treadmill facade idempotently under that guard.
10. Remove the existing treadmill-only sampler thread, step-difference speed
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

### 5.4 Real and mock execution

The initial enabled treadmill runtime targets the real Pi 5/libgpiod path only.
It does not preserve the current mock `RotaryEncoder` as a second runtime
backend. Synthetic edge sources remain test and explicit diagnostic inputs;
they are not selected silently from production session configuration.

The head-fixed hardware-stress configuration must therefore be mode-aware:

- on an identified real Pi 5, default to real-hardware mode and set
  `treadmill = true`, `treadmill_required = true`, and
  `continuous_logger_required = true` so a full-stack acceptance run cannot
  pass without acquisition and artifacts;
- on a known non-ARM host, or when a valid true `BEHAVBOX_FORCE_MOCK` value was
  set before process imports, select mock mode, set `treadmill = false`, and
  omit treadmill artifacts rather than pretending to exercise the hardware
  path;
- on another Raspberry Pi model, fail with an unsupported-platform error unless
  mock mode was explicitly forced. Pi 4B is not silently treated as either an
  accepted Pi 5 treadmill host or an off-Pi development host. Perform this
  validation before creating output directories, changing display-service
  state, starting the mock server, or constructing BehavBox.

The process-wide real/mock choice is made once near the start of
`box_runtime.behavior.gpio_backend`, before that module conditionally imports
the real or mock GPIO classes. `BEHAVBOX_FORCE_MOCK` must therefore already be
set when the first transitive import of that module occurs. Unset means no
override. If set, case-insensitive `1`, `true`, `yes`, and `on` mean true;
`0`, `false`, `no`, and `off` mean false. An empty or any other value is a
configuration error rather than false.

Host detection produces one explicit immutable result: known non-ARM host,
identified Raspberry Pi 5, identified unsupported Raspberry Pi, or unknown ARM
host. On ARM, unreadable, empty, malformed, or unrecognized device-tree model
data yields unknown rather than off-Pi. A valid true force-mock override selects
mock mode for any host. Without a valid true override, hardware-stress selects
mock only on a known non-ARM host, selects real hardware only on an identified
Pi 5, and fails on unsupported or unknown ARM hardware. Both stress entrypoints
consume these same process facts for session configuration and mock-server
startup; they must not set or clear `BEHAVBOX_FORCE_MOCK` afterward. No
separate real-hardware flag is required: an identified Pi 5 defaults to real
mode, and valid force-mock is the explicit override.

This strict eligibility rule is specific to the Pi 5 treadmill hardware-stress
path. Exposing the shared host classification must not by itself remove any
existing non-stress GPIO support for another identified Raspberry Pi model.
The display launcher's existing `--dry-run` remains a non-session planning
path: it may print planned display-service actions before backend selection,
but must not import/construct hardware, create output, or report hardware
acceptance.

Any other session which explicitly requests `treadmill = true` without the
real backend follows the normal required/optional startup rules and must never
fall back to a simulated or gpiozero encoder.

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
- `treadmill`, `calibration_verified`, `treadmill_required`, or
  `continuous_logger_required` values which are not actual Booleans;
- a `calibration_source` or `calibration_note` which is not a nonempty string
  after surrounding whitespace is removed;
- unknown `treadmill_config` keys;
- retired top-level treadmill settings in an enabled session;
- non-finite numeric values, nonpositive calibration/configured
  timeouts/configured rates/buffer sizes, or non-integer count/size fields;
- a non-integer or nonpositive non-null `processing_lag_warning_ns`;
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

### 7.3 Internal bounded-operation constants

The following implementation constants are deliberately not user-facing YAML
settings:

```text
COHERENT_STATE_READ_TIMEOUT_S
CONTROL_RESULT_TIMEOUT_S
LOGGER_START_TIMEOUT_S
INITIAL_SYNC_QUIET_PERIOD_S
```

`TW-0` must select positive finite values on the target Pi 5 and record them in
`docs/treadmill_validation.md` before the tests which depend on them are
written. Tests may inject shorter values. A coherent-state read may retry only
within `COHERENT_STATE_READ_TIMEOUT_S`; a control operation includes enqueue,
direct acknowledgement, and state reconciliation within
`CONTROL_RESULT_TIMEOUT_S`; and logger startup includes process creation,
artifact opening, and its ready response within `LOGGER_START_TIMEOUT_S`.
`CONTROL_RESULT_TIMEOUT_S` must exceed `COHERENT_STATE_READ_TIMEOUT_S`. A zero
command's worker deadline reserves the final coherent-read interval from the
overall control deadline:

```text
worker_deadline = operation_start
    + CONTROL_RESULT_TIMEOUT_S
    - COHERENT_STATE_READ_TIMEOUT_S
```

This leaves a bounded state-reconciliation opportunity after a late or lost
direct acknowledgement.
Initial synchronization repeats the quiet-period algorithm until it succeeds
or the configured `startup_timeout_s` expires; it has no independent unbounded
retry count.

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

Debian 13 currently provides the binding as `python3-libgpiod`. The `gpiod`
command-line tools are packaged separately and are a required validation and
troubleshooting dependency, although the production Python runtime must not
shell out to them. The exact Raspberry Pi OS package origins, installed
binding/library/tool versions, and Python signatures must still be verified
from the target Pi and official package source before adapter code is written.
Provisioning, environment verification, and installation docs must then be
updated as part of implementation.

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

Use `INITIAL_SYNC_QUIET_PERIOD_S` for the bounded quiet-period/retry algorithm
described in Section 7.3. Deterministic tests must prove that an event at any
synchronization boundary is either:

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

`zero()` submits an idempotent `ZERO` command with a unique monotonically
increasing command ID and a worker-visible monotonic deadline. The facade
allows at most one `ZERO` command in flight. The worker drains already-pending
edges, resolves the command at that event boundary, and never applies the same
command ID twice.

Public zeroing requires live acquisition and valid trajectory integrity. It is
allowed for calibration-only or lag-only degradation when those conditions
leave acquisition continuity intact. Unavailable or
integrity-invalid acquisition raises a typed health error and does not send or
apply a zero command. The worker rechecks integrity at the drained event
boundary so a failure racing the caller's precheck cannot establish an origin.

Public `zero()` is also rejected with a typed lifecycle error while a shared
recording is active; it does not allocate or enqueue a command. The facade's
private recording-start sequence may issue the origin zero after logger
readiness and before the first state sample. Mid-recording zero segmentation is
not part of the initial artifact schema, so no caller may silently create a
position discontinuity inside one recording.

`InputService` owns one parent-process `threading.RLock` and one one-way
close-requested event. Both are injected into the facade but are not exposed
through `box.treadmill`; neither is shared with the acquisition or logger
process. Parent-facing `snapshot()`, `health_report()`, `require_healthy()`,
`zero()`, every shared-recorder ownership transition, and `close()` use this
same lock. The reentrant form permits a recording transition to call the
facade's locked internal operations without another concurrency protocol.

Each external operation checks the close event before acquiring the lock and
again immediately after acquiring it. If close was requested, it follows the
cached lifecycle behavior below without touching the recorder, control channel,
diagnostic channel, or treadmill IPC. An operation which already holds the lock
when close is requested finishes or unwinds within its existing bounded
operation deadline; internal steps of that admitted operation do not repeat the
external close check. A genuinely hung parent thread is outside the supported
operating envelope; no reader-generation or abandoned-thread recovery protocol
is added.

Public `zero()` holds the lock from its authoritative recording-state check
through the resolved or timed-out control result. BehavBox delegates each
shared-recorder ownership transition through `InputService`, which holds the
same lock. On first-owner start, `InputService` makes the recorder active before
logger work begins. That state remains true through optional logger/origin
failure and clears only after a required rollback or final recorder
finalization. On final-owner stop, the lock remains held through logger and
shared-recorder finalization.

Joining or leaving an already active multi-owner recording is serialized by the
same mutex but does not restart or stop the logger. Thus a public zero either
finishes before a new recording begins or observes the recording state and is
rejected; it cannot pass a check and apply during recording startup or
finalization. It is still rejected when optional treadmill failure has stopped
the logger but the shared recorder remains active. The mutex is a parent-side
lifecycle guard, not a lock held by the acquisition process. A public zero's
mutex wait is included in
`CONTROL_RESULT_TIMEOUT_S` and may wait no later than the already defined
worker deadline, preserving the final coherent-read interval. Failure to
acquire it by then raises a typed recording-transition lifecycle error without
allocating or enqueueing a command.

The coordinated close path sets the close event before acquiring the `RLock`.
This prevents a waiting operation from entering IPC after the current lock
owner finishes. Close then holds the lock while it finalizes any active
recording, closes the shared recorder, stops and joins children, captures final
state, releases parent-owned treadmill IPC, and publishes the authoritative
cache. Because every parent IPC reader uses the same lock, close cannot release
IPC underneath a reader.

After close is requested but before the final cache exists, `snapshot()` raises
a typed closing lifecycle error and `health_report()` returns cached
close-in-progress health without IPC: an already `FAILED` state remains
`FAILED`, otherwise health is `CLOSING`. After close completes, both use the
final cached behavior defined in Section 13.2. New zero or recording ownership
transitions raise a typed lifecycle error without changing owner flags or
touching resources. These rules also apply when treadmill is disabled because
the same lock protects shared-recorder transitions.

Zeroing must not reset:

- lifetime event/transition totals;
- sequence gaps;
- inconsistency counters;
- lag metrics;
- health history;
- a latched integrity failure.

Before changing canonical offsets, the worker must reserve a writable coherent
publication slot without blocking edge acquisition. If no slot is immediately
available, it leaves the command pending, continues normal bounded edge/control
work, and retries only until the worker deadline. The resulting publication
records at least the resolved command ID, whether it was applied or rejected,
the last applied zero command ID, the worker resolution timestamp, and the
resulting state version. If the deadline expires or integrity becomes invalid
before reservation, the worker rejects the command without changing offsets.
The worker retains only the in-flight command and the most recently resolved
command/result. A repeat of either is coalesced or replays that retained result
without applying another zero. An ID older than the retained resolved ID is
rejected as a stale internal-protocol request without applying a zero and
without claiming whether that ID was applied historically; the retained
last-resolved ID/result is not replaced by the stale request. The facade's
normal monotonic, one-in-flight protocol never sends such an older ID. This
bounded rule is the complete idempotency guarantee; it does not require an
unbounded history of command results.

The direct acknowledgement channel is an optimization, not the authority: an
acknowledgement can be lost after a successful shared-state publication.
`zero()` waits only through `CONTROL_RESULT_TIMEOUT_S` and reconciles any
missing direct acknowledgement against coherent shared state. Its result has
exactly one of these outcomes:

- `applied`: the requested command ID is published as applied; this is the only
  successful return and includes the command ID, worker timestamp, and state
  version;
- `confirmed_not_applied`: enqueue failed before acceptance or the worker
  published a rejection for that command ID; offsets are unchanged and a typed
  error carries the result;
- `indeterminate`: neither application nor rejection can be established before
  the bounded deadline, including when coherent state itself cannot be read; a
  typed error carries the command ID and known context.

Thus loss of a channel acknowledgement does not imply that offsets are
unchanged. A recording-origin operation may proceed only with `applied`; it
must abort and follow required/optional rollback policy for either other
outcome. An indeterminate attempt is recorded prominently because a later
snapshot may show that its zero was applied even though no recording claimed
it as an origin.

## 11. Integrity and diagnostics

### 11.1 Sequence tracking

Track global and per-line sequence continuity when supplied by libgpiod.
Startup synchronization supplies the baselines described in Section 9.4;
events explicitly excluded from motion totals still advance those baselines.

Treat the kernel's global and per-line sequence values as unsigned 32-bit
serial numbers. For each tracker compute
`delta = (observed - baseline) mod 2^32`:

- `delta == 0` is a duplicate;
- `1 <= delta < 2^31` is forward, with `delta == 1` contiguous and a larger
  value missing exactly `delta - 1` events;
- `delta == 2^31` is ambiguous and is
  `SEQUENCE_METADATA_INCONSISTENT`; it does not advance the baseline;
- `2^31 < delta < 2^32` is older/out of order and is
  `SEQUENCE_ORDER_INVALID`.

These comparisons apply independently to the global and relevant per-line
trackers and make `0xffffffff -> 0` an ordinary contiguous step. Raw sequence
values remain unsigned in diagnostics and artifacts.

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

Track the following decoder/adapter conditions separately; facade/lifecycle
conditions use the stable codes in Section 14.1. `edge_event_count`
increments for every delivered event handed to the decoder, including rejected
events. "Reject" means no transition, distance, direction, or current-speed
update. Every row is covered directly by deterministic tests before decoder
implementation.

| Condition and stable code | A/B and position action | Additional accounting | Health and integrity |
|---|---|---|---|
| Global forward sequence gap: `GLOBAL_SEQUENCE_GAP` | Reject the transition, clear the speed baseline, and, if the channel/edge is valid, set that remembered line to the reported level for subsequent diagnostic decoding. | Increment global gap occurrences and global-only estimated missing events; also increment the relevant per-line gap when observed there. | Latch `FAILED`; latch integrity false. |
| Per-line forward gap without a global jump: `LINE_SEQUENCE_GAP` plus `SEQUENCE_METADATA_INCONSISTENT` | Same reject/resynchronize action as a global gap. | Increment that line's gap and the inconsistency counter; do not add to estimated missing events. | Latch `FAILED`; latch integrity false. |
| Impossible global/per-line sequence relation not otherwise covered: `SEQUENCE_METADATA_INCONSISTENT` | Reject the event and clear the speed baseline; update a valid reported line level only for diagnostic continuation. | Increment the inconsistency counter without inventing missing-event counts. | Latch `FAILED`; latch integrity false. |
| Global and relevant per-line sequence values consistently repeat their accepted baselines: `SEQUENCE_DUPLICATE` | Reject without changing remembered A/B. | Increment the inconsistency counter. | Latch `DEGRADED`; integrity remains true. |
| Global or relevant per-line sequence value is older than its accepted baseline: `SEQUENCE_ORDER_INVALID` | Reject without changing remembered A/B and clear the speed baseline. | Increment the inconsistency counter; never move a baseline backward. | Latch `FAILED`; latch integrity false. |
| New sequence but edge type already matches the remembered line level: `EDGE_STATE_INCONSISTENT` | Reject; the reported level causes no A/B change; clear the speed baseline. | Increment the inconsistency counter. | Latch `FAILED`; latch integrity false. |
| Regressing/equal timestamp, or timestamp later than processing time, on an otherwise valid ordered transition: `EVENT_TIMESTAMP_INVALID` | Apply the quadrature position transition in sequence order, do not accept the invalid timestamp into public timestamp fields, and clear the speed baseline. | Increment the inconsistency counter; never compute speed across the event. | Latch `DEGRADED`; integrity remains true. |
| Malformed channel or edge identifier: `EVENT_FORMAT_INVALID` | Reject because no safe A/B update is possible; clear the speed baseline. | Increment the inconsistency counter and preserve the raw diagnostic. | Latch `FAILED`; latch integrity false. |
| Simultaneous physical-level check cannot establish one unambiguous boundary: `STATE_RESYNC_AMBIGUOUS` | Do not add motion. Adopt a new A/B baseline only when a later synchronization attempt succeeds. | Increment the inconsistency counter and preserve observed levels/events. | Startup fails, or runtime latches `FAILED`; integrity is false. |
| Backend wait/read/request API error: `BACKEND_IO_FAILED` | Stop normal decoding and make acquisition unavailable; do not invent a transition or gap. A normal bounded wait which reports no edge is not an error. | Preserve the backend error in diagnostics. | Effective health is `FAILED`; retain the last integrity value unless a separate ambiguity/gap invalidated it. |
| Processing lag exceeds its threshold: `PROCESSING_LAG_HIGH` | Process the event normally according to any higher-priority row. | Update lag aggregates and increment the exceedance count once for this event. | Latch at least `DEGRADED`; integrity is unchanged. |
| Coherent publication slot unavailable: `STATE_PUBLICATION_SKIPPED` | Canonical decoding continues; no reader-visible partial update is written. | Increment publication skips in the next successful publication. | Latch `DEGRADED`; integrity is unchanged. |
| Diagnostic-event queue full: `DIAGNOSTIC_EVENTS_DROPPED` | Canonical decoding and publication continue. | Increment diagnostic drops; retain the bounded in-process ring. | Latch `DEGRADED`; integrity is unchanged. |

When more than one row applies, perform the safest stated event action, update
all applicable counters/codes, and use the most severe health/integrity result.
The primary `failure_code` is then selected by Section 14.1; no test may invent
a different precedence. Acquisition-process, heartbeat, logger, zero-control,
and shutdown failures are facade/lifecycle conditions covered by the same
stable-code table rather than decoder events.

Validate sequence metadata before decoding the event payload. A new or forward
global sequence advances its baseline even if the event is later rejected for
timestamp, channel, edge-type, or line-state reasons. Advance the per-line
baseline likewise only when the line is identifiable. Duplicate/out-of-order
values never move a baseline. This prevents one malformed delivered event from
creating a second, artificial gap on the next event.

A duplicate or out-of-order sequence is rejected before edge-level or timestamp
consistency checks, so replaying an already represented edge does not also
create an `EDGE_STATE_INCONSISTENT` failure. After a forward gap, use a valid
channel/edge only for the matrix's diagnostic resynchronization action; do not
classify its relationship to the now-untrusted prior A/B state as a second
edge-state failure. A malformed payload still records `EVENT_FORMAT_INVALID`.

Because the internal event identifies one channel and one edge, applying one
well-formed event can change only one remembered A/B bit. A two-bit jump is not
a separate decoder input or failure class. Any mismatch found while explicitly
checking or resynchronizing physical line levels is instead recorded as a
state-resynchronization inconsistency. Sequence gaps and any ambiguity which
can change exact position must always invalidate integrity.

### 11.3 Processing lag and observed transition rate

For each event:

```text
processing_lag_ns = processing_monotonic_ns - event.timestamp_ns
```

Maintain bounded lifetime count, sum, latest, exact lifetime maximum, and
histogram counts. Events with invalid timestamps follow the matrix in Section
11.2 and do not enter lag or transition-rate aggregates.
Use these processing-lag bin upper bounds in nanoseconds:

```text
50_000
100_000
250_000
500_000
1_000_000
2_000_000
5_000_000
10_000_000
25_000_000
50_000_000
overflow
```

Summary percentiles are histogram estimates, not exact order statistics. For
each of p50, p95, and p99, report the upper bound of the first bin whose
cumulative count reaches `ceil(percentile * count)`. Field names and metadata
must include `approximate`, and the metadata repeats the bin edges. For an
overflow result, keep the value numeric at `50_000_000` and set its companion
`_is_lower_bound` Boolean true; otherwise that flag is false. Recording-window
histogram counts and sums are final lifetime values minus the baseline captured
with the applied recording origin. The recording-window approximate maximum is
the upper bound of the highest nonempty difference bin and uses the same
lower-bound flag. Values and flags are `null` when no origin was established.
They are also `null` when an applied-origin recording contains zero valid lag
samples; its lag count and sum are still zero.

Define `maximum_observed_transition_rate_hz_lifetime` deterministically from
accepted position-changing transitions with valid non-regressing event
timestamps. Assign each transition to the aligned 100 ms monotonic bin
`floor(timestamp_ns / 100_000_000)`, count transitions per bin, divide the
largest observed bin count by `0.1 s`, and retain the maximum for the
acquisition lifetime. This fixed-bin diagnostic is a boundary-dependent
headroom indicator, not an instantaneous electrical edge-rate claim. It is
explicitly labeled lifetime in public state and summaries; no unsupported
recording-window maximum is inferred by subtracting scalar maxima.

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
nonblocking channel; they never share the raw-edge path. While acquisition is
live, records remain in that channel until the logger starts. The logger is the
only live consumer. `health_report()` does not drain diagnostic records; it
uses shared health state and parent-maintained process/logger status.

If the worker-to-consumer channel is full, acquisition increments
`diagnostic_event_drop_count`, activates `DIAGNOSTIC_EVENTS_DROPPED`, and
continues; it never waits for diagnostic delivery. The worker's retained health
bitset makes that loss visible even if its diagnostic record could not be
queued.

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

The facade's active `STATE_READ_TIMEOUT` condition clears after its next
successful coherent read, while diagnostics preserve the occurrence. The
Section 10.6 lifecycle lock prevents a coherent read from overlapping IPC
teardown. Logger read failures remain recording counters rather than changing
canonical acquisition health.

### 12.3 Control channel

The acquisition control channel is deliberately small:

```text
ZERO
STOP
```

Controls must use bounded communication and must not carry individual GPIO
events. The acquisition process drains pending edges before lower-priority
controls. `ZERO` follows the command-ID, worker-deadline, idempotency, and
state-reconciliation contract in Section 10.6. `STOP` also uses a matched
bounded result, but shutdown completion is finally determined by child exit and
resource cleanup. Status is read from coherent shared state and heartbeat. The
parent facade, not the acquisition worker, owns logger-process start and stop.

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
maximum_processing_lag_ns_lifetime
processing_lag_sample_count
processing_lag_sum_ns
processing_lag_histogram_counts
processing_lag_warning_count
maximum_observed_transition_rate_hz_lifetime

last_resolved_zero_command_id
last_applied_zero_command_id
last_zero_result

health
integrity_valid
acquisition_available
failure_code
observed_acquisition_health_codes
state_version
```

Convenience fields such as time since last motion may be derived from a
caller-supplied current monotonic time.

`last_edge_monotonic_ns` is the newest valid, non-regressing kernel timestamp
not later than its processing time from an identifiable A/B edge with a new or
forward sequence, even if that edge is later rejected for a line-state
inconsistency. `last_motion_monotonic_ns` advances only for an accepted
position-changing x4 transition with a valid timestamp. Duplicate/out-of-order
sequences, malformed identifiers, and invalid timestamps still increment their
diagnostic/event counters but must not alter either public timestamp.

Before the first corresponding event:

- `last_edge_speed_mm_s` is `None`;
- `last_edge_monotonic_ns` and `last_motion_monotonic_ns` are `None`;
- `latest_processing_lag_ns` and `maximum_processing_lag_ns_lifetime` are
  `None`;
- `processing_lag_sample_count` and `processing_lag_sum_ns` are zero;
- `processing_lag_histogram_counts` is an eleven-element tuple of zeros ordered
  by the ten finite bin upper bounds followed by overflow;
- `processing_lag_warning_count` is zero;
- `maximum_observed_transition_rate_hz_lifetime` is `0.0`;
- the zero-command identity/result fields are `None`;
- `last_encoder_direction` and `locomotion_direction` are zero;
- `position_mm`, `distance_travelled_mm`, and `speed_mm_s` are `0.0`.

`failure_code` is the deterministic primary stable code and is `None` when no
current or latched warning/failure applies. If several codes apply, choose by
health severity and then the Section 14.1 priority table covered by the
failure-matrix tests; do not join free-form strings. `health_report()` returns
the complete ordered active-code set, the facade-lifetime observed-code set,
and human-readable context, while diagnostics preserve chronology.

The acquisition worker stores a fixed bitset plus first-observation monotonic
times keyed by the stable Section 14.1 code order for every
acquisition/calibration code it has ever activated. It sets a code's bit and
time before that condition may clear and never clears them during the facade
lifetime. Coherent readers expose the derived ordered tuple
`observed_acquisition_health_codes`; the numeric mask and timestamp array are
not public schema fields. This bounded state makes a transient worker-owned
code visible and orderable even if its diagnostic record is delivered late.
Parent-, logger-, zero-, and shutdown-owned codes are merged by the bounded
facade/logger accumulators in Section 14.3.

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

`last_zero_result` is `applied` or `rejected` for
`last_resolved_zero_command_id`; it is never `indeterminate`, because that is a
facade conclusion when no worker result can be observed. The last applied ID
remains unchanged by a rejection.

`last_state_update_monotonic_ns` is the acquisition time at which decoder,
control, or health content last changed; a routine heartbeat alone does not
change it. `last_heartbeat_monotonic_ns` is refreshed on the heartbeat schedule.
Effective `acquisition_available` is true only while the worker is live and its
heartbeat is current; it is false for startup failure, unexpected loss, and
orderly `STOPPED` state. It is a reader-materialized public field, not a claim
that an abruptly dead worker could write into canonical shared state.
`state_version` increases for each coherent publication, including heartbeat
and applied or rejected zero-result publications; readers must not interpret it
as an edge count. Motion/heartbeat timeout materialization can change effective
speed, direction, availability, and health without changing `state_version`,
because the underlying canonical publication has not changed.

The public immutable record uses optional types for fields which can be
unavailable initially or after acquisition continuity is lost. Fixed-width
shared state uses explicit validity flags, not magic float values. TSV encodes
an unavailable optional value as an empty field; JSON encodes it as `null`.

### 13.2 Behavior-facing facade

The facade surface is conceptually equivalent to:

```python
treadmill.start()
state = treadmill.snapshot()
zero_result = treadmill.zero()
report = treadmill.health_report()
treadmill.require_healthy()
treadmill.close()
```

Exact names may follow established repository conventions. Behavior code must
not need to understand gpiochips, libgpiod objects, transition tables, shared
memory, or logger processes.

Routine behavior code uses the read, health, and zero operations. `start()` and
`close()` are lifecycle operations owned by `InputService`; task and user code
must close the enclosing BehavBox rather than shutting down acquisition
directly while shared recording may still be active.

On success, `zero()` returns the typed `applied` result from Section 10.6.
`confirmed_not_applied` and `indeterminate` are typed exceptions carrying the
same result fields and available context.

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
`CLOSING`/`STOPPED`. Callers which intentionally allow an unverified-calibration
`DEGRADED` state use `health_report()` and make that choice explicitly.
The report distinguishes currently active ordered codes from the bounded
facade-lifetime observed-code history defined in Section 14.3.

`start()` is used once by `InputService`; it is not a task-level way to restart
a failed acquisition. The integration-owned `close()` sends the internal
`STOP`, joins owned children, and is idempotent. After close,
`health_report()` reports `STOPPED` only after orderly cleanup; forced or
incomplete child cleanup reports `FAILED`, unavailable, with a stable shutdown
failure code. `snapshot()` returns the corresponding final cached state when
one exists, and commands such as `zero()` raise a typed lifecycle error. The
facade captures final worker state before unlinking shared resources, then
publishes the immutable authoritative cache after cleanup outcomes are known.
Post-close reads do not access released IPC. A restart requires a new facade.

The facade also has integration-only logger/origin start and logger stop
operations used by `InputService` while that service holds the parent lifecycle
`RLock`. `InputService` owns the surrounding shared-recorder update; these
facade operations are not alternate public BehavBox
recording APIs. Task and user code continue through the existing BehavBox
recording methods.

## 14. Health and failure reporting

### 14.1 Health states

Use explicit states:

```text
STARTING
READY
DEGRADED
FAILED
CLOSING
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
- `CLOSING`: new parent operations no longer access IPC, but final cleanup and
  final-state caching have not completed;
- `STOPPED`: orderly requested shutdown completed.

Integrity loss remains latched until an explicit acquisition restart. Zeroing
must not clear it. The first processing-lag threshold exceedance also latches
acquisition health at least `DEGRADED` for that run while leaving
`integrity_valid` unchanged; the summary preserves when and how often it
occurred.

The shared acquisition record contains acquisition/calibration health. The
parent facade materializes effective subsystem health by also checking child
liveness, heartbeat age, and the active logger's status. The logger applies the
same acquisition heartbeat rule to each sample. Logger state is not written
back through the acquisition process, preserving single-writer decoder state.
Effective-health precedence is `STOPPED` after orderly close; while close is in
progress, an existing `FAILED` state remains `FAILED`, otherwise the
parent-only cached report is `CLOSING`; before close, precedence is `FAILED`
over `DEGRADED` over `READY`. `STARTING` applies only before the first usable
publication. `CLOSING` by itself has no failure code. Integrity-invalid
acquisition failure remains latched. Transient heartbeat failure may recover
only if the same worker resumes with continuous sequences; every failure and
recovery remains in diagnostics.

Within the effective health severity, the first active code in the following
table is the primary `failure_code`. `health_report()` returns every active code
in this same order. Codes in the decoder matrix retain the behavior defined
there; this table only resolves presentation priority.

| Priority | Stable code | Severity and source |
|---:|---|---|
| 1 | `SHUTDOWN_INCOMPLETE` | `FAILED`: forced or incomplete close |
| 2 | `ACQUISITION_PROCESS_EXITED` | `FAILED`: unexpected worker exit |
| 3 | `HEARTBEAT_STALE` | `FAILED`: live worker heartbeat deadline exceeded |
| 4 | `ACQUISITION_START_FAILED` | `FAILED`: process creation or startup handshake failed |
| 5 | `BACKEND_CAPABILITY_MISSING` | `FAILED`: required libgpiod v2 feature absent |
| 6 | `GPIO_RESOLUTION_FAILED` | `FAILED`: missing or ambiguous RP1/header mapping |
| 7 | `GPIO_LINE_CLAIM_FAILED` | `FAILED`: unavailable or unclaimable configured line |
| 8 | `INITIAL_SYNC_FAILED` | `FAILED`: no clean initial boundary before startup deadline |
| 9 | `BACKEND_IO_FAILED` | `FAILED`: libgpiod request, wait, or read API error |
| 10 | `GLOBAL_SEQUENCE_GAP` | `FAILED`: authoritative request sequence gap |
| 11 | `LINE_SEQUENCE_GAP` | `FAILED`: per-line sequence gap |
| 12 | `SEQUENCE_METADATA_INCONSISTENT` | `FAILED`: impossible global/per-line relationship |
| 13 | `SEQUENCE_ORDER_INVALID` | `FAILED`: sequence older than accepted baseline |
| 14 | `STATE_RESYNC_AMBIGUOUS` | `FAILED`: no unambiguous physical A/B boundary |
| 15 | `EVENT_FORMAT_INVALID` | `FAILED`: unknown channel or edge type |
| 16 | `EDGE_STATE_INCONSISTENT` | `FAILED`: new event contradicts remembered line level |
| 17 | `REQUIRED_LOGGER_FAILED` | `FAILED`: required logger start or runtime failure |
| 18 | `STATE_READ_TIMEOUT` | `DEGRADED`: reader could not obtain a coherent copy |
| 19 | `ZERO_RESULT_INDETERMINATE` | `DEGRADED`: zero application could not be reconciled |
| 20 | `ZERO_NOT_APPLIED` | `DEGRADED`: zero could not be enqueued or was confirmed rejected |
| 21 | `LOGGER_FAILED` | `DEGRADED`: non-required logger failure |
| 22 | `STATE_PUBLICATION_SKIPPED` | `DEGRADED`: canonical update could not be published immediately |
| 23 | `SEQUENCE_DUPLICATE` | `DEGRADED`: repeated accepted sequence value |
| 24 | `EVENT_TIMESTAMP_INVALID` | `DEGRADED`: unusable event timestamp |
| 25 | `PROCESSING_LAG_HIGH` | `DEGRADED`: configured lag threshold crossed |
| 26 | `DIAGNOSTIC_EVENTS_DROPPED` | `DEGRADED`: bounded worker diagnostic channel overflow |
| 27 | `CALIBRATION_UNVERIFIED` | `DEGRADED`: calibration warning required |

Configuration errors raised before a facade becomes usable are typed
exceptions, not synthetic health codes. Orderly `STOPPED` has no failure code.
`ACQUISITION_PROCESS_EXITED` applies only to an unexpected exit; a worker which
exits as the documented cleanup consequence of a published startup or backend
failure retains the causal code without adding a misleading exit code.
Likewise, `ACQUISITION_START_FAILED` is emitted only when process creation or a
startup handshake fails without a more specific published cause. If startup
publishes any applicable causal failure at priorities 5 through 16, retain
that actionable code and do not add the generic startup code.
Adding or renaming a stable code is a public schema change and requires fixture
and artifact-schema review.

### 14.2 Required versus optional treadmill

`treadmill_required` controls acquisition startup and recording-origin
establishment behavior:

- if true, inability to reach `READY` or an allowed calibration-only
  `DEGRADED` state prevents session startup;
- if false, preparation may also complete with a fully cleaned-up,
  materialized failure facade: partial child/GPIO resources are gone, while a
  cached unavailable `FAILED` health report and diagnostics remain available
  for warnings, metadata, and later header-only recording artifacts.

Before releasing startup IPC, the optional-failure path copies everything
needed for those later artifacts into one immutable JSON-serializable failure
context: effective configuration and calibration, platform/backend identity,
cached health and ordered diagnostics, failure timestamps/codes, and any
last-known public state represented only by primitive values. It contains no
shared-memory view, queue, lock, process handle, or live backend object. A
later header-only logger receives this context as serialized startup input and
must not require acquisition IPC to write and finalize the failure artifact
set.

If an otherwise usable acquisition cannot establish the recording-origin zero
as `applied`, a required treadmill causes that newly opened shared
recording start to fail and roll back its ownership/handles.
An optional treadmill leaves the other shared IO recording active but
finalizes failed, zero-sample treadmill artifacts. Rollback does not promise to
delete the selected directory or already created failure evidence.

Rollback is mandatory for two concrete failure boundaries:

1. `SharedIoRecorder.start_recording()` must restore the caller's prior owner
   flag and prior in-memory recording state, close any partially opened shared
   artifact handles, and leave `is_recording = false` if selecting/creating the
   directory or opening either shared artifact fails. A directory already
   created on disk may remain.
2. If the shared recorder returned `started_now = true` but a required
   treadmill logger/origin step then fails, the BehavBox integration must clear
   the owner demand asserted by that call, close the newly opened shared
   handles, and restore the prior in-memory recording state. It must not
   disturb a recording which was already active for another owner.

The same required/optional rule applies if acquisition is already unavailable
or integrity-invalid when a new shared recording begins. It does not
retroactively roll back a recording which was established successfully and
then experiences a runtime acquisition failure.

An unverified calibration is currently an allowed, prominent `DEGRADED` state
and does not block startup.

### 14.3 Runtime failure reporting

The initial implementation reports and deduplicates prominent runtime
warnings; it does not add unused pause/abort policy states or mutate task or
presenter state. Generic automatic pause/abort wiring remains deferred; the
hardware-stress task has the explicit acceptance policy in Section 14.4.
Tasks which require valid live treadmill data must call `require_healthy()` at
their chosen control boundary or explicitly inspect `health_report()`.
`treadmill_required` does not by itself add hidden task-control behavior.

For baseline visibility, the existing `BehavBox.poll_runtime()` path performs a
cheap nonthrowing treadmill health check when enabled and emits deduplicated
application warnings on health/code transitions. This check reads coherent
shared health and directly maintained parent/logger status; it never consumes
the diagnostic channel. It does not service edges, wait for new diagnostics,
block acquisition, or alter task state. It runs before the current
`prepared`-state early return so a failure between preparation and session
start is visible.

While recording is active, a non-required logger failure causes effective
`DEGRADED` health but does not invalidate otherwise continuous encoder
position. If `continuous_logger_required` is true, logger start failure rolls
back a newly opened shared recording and runtime logger failure makes effective
health `FAILED`, without falsely claiming an encoder sequence gap. After a
clean final-owner logger stop, logger status no longer degrades live acquisition
health. Any optional treadmill recording failure, including a
`confirmed_not_applied` or `indeterminate` origin zero, remains latched at
least `DEGRADED` until that shared recording ends even if its logger has
already exited. Its summary preserves the failure.
No warning or logger policy may make failed acquisition appear healthy.

The parent facade maintains each stable health code's first-observation
monotonic time for its lifetime. Its maximum size is the finite Section 14.1
code table, so this is bounded state rather than a general event history.
Every parent-side effective-health materialization, including stress/preflight
checks and logger-status handling, contributes its active codes. Every coherent
read also merges the worker-published
`observed_acquisition_health_codes` bitset and first-observation timestamps.
Consequently preflight and final acceptance do not depend on diagnostic-record
delivery.

The bounded worker diagnostic channel retains records until a logger starts.
The logger is the only live diagnostic-channel consumer and drains any queued
pre-recording records before continuing with new records. `snapshot()`,
`health_report()`, and `BehavBox.poll_runtime()` never drain this channel.
Channel overflow remains explicit: the worker increments
`diagnostic_event_drop_count` and publishes `DIAGNOSTIC_EVENTS_DROPPED` in its
retained health history.

If acquisition startup fails before any logger exists, the parent first stops
and joins the worker, then performs one bounded channel drain into the immutable
failure context before releasing IPC. If a live logger fails, the parent first
confirms that child is dead and then performs one bounded best-effort drain for
fallback/application logging. That artifact is already incomplete and cannot
pass hardware acceptance, so this recovery path does not promise exact-once
diagnostic delivery.

The health report returns ordered active codes and the current ordered
facade-lifetime observed codes separately. At recording finalization, the
facade supplies its accumulator with the logger stop request; the logger merges
it with the worker's retained codes, its own effective-health samples, and
drained health-transition diagnostics to produce Section 16.4
`observed_health_codes`. The matched logger-finalization result returns the
same bounded first-observation accumulator, and the facade merges it before the
final-owner stop returns. Thus codes observed only by recording 1's logger are
still present when recording 2 starts, without adding another channel.

This history is intentionally facade-lifetime, not recording-window state. It
has no per-recording baseline: a code first observed during recording 1 remains
in recording 2's summary. A new facade/acquisition lifetime resets it. This is
the conservative hardware-stress policy and is distinct from explicitly named
recording-window counters.

Outside an active recording, a later coherent read may replace
`ZERO_RESULT_INDETERMINATE` with the resolved `applied` or
`ZERO_NOT_APPLIED` outcome. A resolved `applied` result, or a later successfully
applied zero, clears the active zero-control code; diagnostics retain every
earlier outcome. The recording-origin latch above does not clear early.

### 14.4 Hardware-stress acceptance policy

This policy applies only to an identified Pi 5 hardware-stress run with the
required treadmill and logger enabled. It does not add hidden control behavior
to other tasks.

After each normal runtime poll, the stress task explicitly inspects
`box.treadmill.health_report()`. During framework bring-up it may continue only
when acquisition is available, integrity is valid, current health is either
`READY` or `DEGRADED` solely because `CALIBRATION_UNVERIFIED` is active, and
the retained facade-lifetime code list contains no other code. Any other active
or previously observed code, unavailable acquisition, or invalid integrity
ends the task as an error before further stimulus or reward output. The
calibration-only exception permits infrastructure validation but remains
prominent and does not satisfy production calibration acceptance.

When the display launcher enables audio preflight, it invokes the same
task-specific health evaluator immediately after `runner.prepare()` and before
calling the preflight function. A disallowed report prevents all preflight
audio output and enters the normal nonzero cleanup path. It evaluates health
again after preflight and before `runner.start()`. Because the evaluator checks
both active and retained codes, a disallowed transient represented in retained
startup diagnostics prevents task start even if it has cleared. Mock mode
remains `not_applicable`; the preflight function does not add its own treadmill
policy or polling loop.

After final-owner recording stop has finalized the treadmill artifacts, the
stress task performs a final acceptance check before reporting success. It
requires a readable schema-valid normal artifact set and summary, an applied
recording origin, at least one state row, valid final recording integrity, no
required acquisition/logger failure or forced/incomplete cleanup, and no
disallowed code observed during the run. It checks the summary's retained
`observed_health_codes` rather than attempting to infer cleared conditions from
final health or reparsing diagnostics. This catches a late transient failure
after the last ordinary task poll even if that code is no longer active at
logger finalization. The post-close check separately covers shutdown codes
which occur after the recording summary is complete by inspecting cached
post-close health and the facade-lifetime observed-code history.

Both stress launchers return zero only after ordinary task completion and that
final acceptance check. A detected treadmill failure, task/runtime exception,
startup/origin failure, artifact/finalization failure, or BehavBox/treadmill
cleanup failure returns nonzero after best-effort finalization and mandatory
close. User interruption is not a passed stress run and returns a conventional
nonzero interrupt status. A mock-mode run may return zero for its non-hardware
functional checks, but its final task state must label treadmill hardware
acceptance as `not_applicable`; it is never evidence of Pi 5 treadmill
acceptance. A mock BehavBox cleanup failure still returns nonzero, but it does
not turn a hardware test which never ran into `failed` hardware acceptance;
the separate cleanup result records that failure.

When `final_task_state.json` can be written, it includes a
`treadmill_hardware_acceptance` object with `status`, ordered `reasons`, and the
summary path or `null`, plus a separate `behavbox_cleanup` object with `status`
and ordered `reasons`. Task finalization may write real hardware acceptance as
`pending_cleanup`; mock hardware acceptance is already `not_applicable`.
`behavbox_cleanup.status` begins as `pending` in either mode. The launcher
retains the facade reference, runs mandatory close, checks cached post-close
health, facade-lifetime observed codes, and any aggregate cleanup error, then
writes both final objects. A file whose cleanup status remains `pending` is not
a successful run.

Final hardware-acceptance status is `passed`,
`passed_with_calibration_warning`, `failed`, or `not_applicable`; cleanup status
is `complete` or `failed`. Real-mode incomplete cleanup makes both hardware
acceptance and cleanup `failed`. Mock-mode incomplete cleanup leaves hardware
acceptance `not_applicable`, makes cleanup `failed`, and returns nonzero. Only
real `passed` is eligible to support production acceptance; the warning status
may still return zero for the explicitly allowed bring-up run. Failure to write
the final post-close state is itself a nonzero result.

## 15. Modern BehavBox lifecycle

### 15.1 Preparation

During `BehavBox.prepare_session()` and `InputService` construction:

1. Validate profile, manifest ownership, calibration, and runtime settings.
2. Construct the treadmill facade only when enabled.
3. Start the acquisition process.
4. Validate backend capabilities and resolve the header GPIO controller.
5. Claim BCM13 and BCM16 together.
6. Synchronize initial A/B state.
7. On success, publish initial state and heartbeat. On optional failure, clean
   partial resources and cache the unavailable failure report and diagnostics.
8. Return only after one of those two outcomes reaches the state defined in
   Section 14.2.

Required startup failure propagates through the existing `prepare_session()`
cleanup path. Because `BehavBox.input_service = InputService(...)` is assigned
only after construction returns, `InputService` must also clean any facade,
process, IPC, or input device it created if its own construction/startup raises;
it must not rely on the outer BehavBox reference already existing.

### 15.2 Recording start

When `SharedIoRecorder` first opens a recording directory:

1. Confirm the directory exists.
2. Start the independent treadmill logger in live-state mode for usable
   acquisition, or in header-only failure mode from the immutable Section 14.2
   context when startup was materialized as failed. Header-only mode receives
   no acquisition shared memory or control/diagnostic queue. The logger opens
   the required artifacts and reports successful startup within
   `LOGGER_START_TIMEOUT_S`. Treat a timeout or open/write failure according
   to `continuous_logger_required`. A successful live-state logger becomes the
   diagnostic channel's sole consumer and first drains records already queued.
3. For usable acquisition, obtain an `applied` recording-origin zero after
   already pending events are decoded. Here, usable means that a state exists,
   the worker and heartbeat are current, and trajectory integrity is valid;
   calibration-only or latched lag `DEGRADED` health is still usable.
4. Write configuration, platform, GPIO mapping, calibration, and timebase
   metadata, including the applied state/counter baseline and how application
   was confirmed.
5. Begin fixed-rate samples from the applied state version.

This sequence holds the Section 10.6 lifecycle `RLock` through logger readiness,
origin-zero reconciliation, and either successful completion or rollback. A
close request arriving after admission therefore waits for the sequence; its
internal reads remain available because they are part of the already admitted
operation. The same rule covers an admitted public zero and final-owner
recording stop.

If logger startup fails or times out, stop this sequence before zeroing. A
required logger triggers the rollback in Section 14.2. A non-required logger
leaves the shared IO recording active, records the visible logger failure (and
best-effort fallback artifact when possible), and creates no recording origin
or treadmill state samples. This branch is controlled by
`continuous_logger_required` even when live acquisition is required: without a
logger there is no treadmill artifact whose origin must be established, while
`treadmill_required` still requires acquisition itself to remain usable.

If another recording owner joins an existing recording, do not start another
logger and do not zero again.

Every newly opened shared recording while acquisition remains live repeats
this sequence with a new logger instance and a new zero. Continuous treadmill
state logging is part of every enabled treadmill recording; the initial
implementation has no configuration branch that silently omits the normal
artifacts.

No samples may be labeled as belonging to an origin that was not confirmed
`applied`. Whenever the logger reached ready but no applied origin can be
established because acquisition is unusable or zero returns
`confirmed_not_applied`/`indeterminate`, request a bounded failure
finalization, write and flush the header-only state plus available metadata,
diagnostics, and summary, close every logger-owned artifact handle, and
stop/join the logger. If orderly finalization fails, use the bounded
forced-cleanup and fallback rules. No logger process, open treadmill handle, or
sampling-active facade state may remain. A required treadmill then rolls back
the newly opened shared recording under Section 14.2. An optional treadmill
leaves shared IO recording active but keeps a recording-scoped treadmill
failure marker until final-owner stop; its treadmill artifacts are already
final and are not reopened or rewritten at that stop.

If optional treadmill acquisition failed during startup or became unusable
before recording began, recording still creates the normal state, metadata,
diagnostics, and summary artifacts under that same immediate finalization rule.
The state TSV contains only its header, while
metadata, diagnostics, and the summary identify the failure, zero samples,
`FAILED` health, unavailable acquisition, and the last known integrity value.
An acquisition which never established state reports integrity as false; a
later heartbeat/process failure does not invent a sequence gap and may retain
the last integrity value as true. No zero command or fixed-rate sampling
occurs. This distinguishes a requested-but-failed treadmill from
`treadmill=false`, which creates no treadmill artifacts.

### 15.3 Recording stop

When the final recording owner stops:

1. If a logger is active, request its final diagnostic drain, sample, and
   summary; otherwise preserve any already finalized pre-origin
   failure/fallback state.
2. Flush any buffered TSV/JSON output.
3. Close any open treadmill recording artifacts.
4. Leave acquisition alive until `InputService.close()` so live state does not
   disappear before overall runtime cleanup.

### 15.4 Runtime close

Normal close must:

1. set the close-requested event before acquiring the Section 10.6 lifecycle
   `RLock`, then acquire it after any already admitted parent operation finishes
   or unwinds;
2. stop/finalize any active logger or preserve any already finalized failure
   set;
3. close the shared IO recorder, clear both owner demands, and close its
   artifact handles;
4. send `STOP` so acquisition drains already available events, publishes final
   state/diagnostics, and releases only its owned GPIO lines;
5. join child processes within bounded timeouts;
6. capture the final worker state, then clean up every parent-owned treadmill
   IPC resource using its supported contract: close/join queue feeder
   resources, close/unlink shared memory, and release synchronization
   resources/references;
7. after every cleanup attempt has reported its outcome, cache clean `STOPPED`
   state or overlay `FAILED` with a shutdown failure code.

The close path holds the same `RLock` through all seven steps, so no parent
reader or lifecycle transition can touch IPC during teardown. A later
idempotent `SharedIoRecorder.close()` call by enclosing BehavBox cleanup is a
no-op; it is not the authoritative first cleanup of recording state.

Close is exception-safe across these steps. Each step is attempted even if an
earlier step fails, and cleanup errors are accumulated in step order rather
than raised immediately. Shared-recorder cleanup attempts flush and close on
each open handle independently; owner flags, handle references, and
`is_recording` are cleared in `finally` paths even when an operation reports an
error. Logger failure cannot skip acquisition `STOP`/join, forced termination
when required, IPC release, or final-state caching, and recorder failure cannot
skip any treadmill or later BehavBox subsystem cleanup.

When an enabled facade exists, any such error latches `SHUTDOWN_INCOMPLETE` in
its cached post-close health. InputService/facade cleanup reports its ordered
errors only after all of its steps have been attempted. Enclosing
`BehavBox.close()` likewise catches deferred component-cleanup errors, attempts
cleanup of every remaining owned subsystem, then reports one typed aggregate
failure. The hardware-stress launcher therefore returns nonzero while its
retained facade still exposes the shutdown code. A repeated close is safe and
retains, rather than clears, the prior failure report.

`close()` must be idempotent. Forced termination is a last resort and must be
diagnosed in the application log and in any still-open or fallback summary.
Do not rewrite a previously completed recording merely because later
acquisition shutdown failed. After exhausting its bounded cleanup path, the
facade records forced/incomplete shutdown in cached health and defers any
cleanup exception until its remaining steps have run, so enclosing
`BehavBox.close()` can continue cleaning unrelated resources.

After constructing BehavBox, each hardware-stress launcher owns an outer
`try`/`finally` which calls public, idempotent `box.close()` on every path,
including prepare, logger/origin start, task runtime, stop, artifact writing,
and finalization exceptions. It may first attempt lifecycle-appropriate stop
and finalization to preserve diagnostics, but those best-effort steps never
replace the unconditional close. A cleanup failure is reported and cannot
convert the exit to success. The display-mode launcher closes BehavBox before
its independent best-effort LightDM restoration. `BehavBox.__del__` is not
part of this correctness contract.

### 15.5 Session metadata

The facade retains an immutable JSON-serializable copy of the validated
effective treadmill configuration. `BehavBox.finalize_session()` writes a
top-level `treadmill` object in `session_metadata.json`: disabled sessions use
`{"enabled": false}`, while enabled sessions use
`{"enabled": true, "effective_config": ...}`. This is an explicit addition to
the existing metadata payload; implementation must not rely on mutating the
caller's original `session_info` mapping as an undocumented side channel.
Recording-specific platform, mapping, origin, and counter details remain in
`treadmill_metadata.json`.

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
maximum_observed_transition_rate_hz_lifetime
health
failure_code
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
- internal bounded-operation constants, processing-lag histogram edges, and
  the 100 ms transition-rate bin width;
- continuous sample rate;
- expected maximum transition rate if known;
- recording-origin result, confirmation source, command identity, monotonic
  timestamp, state version, and counter baselines when established;
- start monotonic/UTC timebase anchor.

### 16.3 Diagnostics

Write event-driven records to:

```text
treadmill_diagnostics.jsonl
```

Record startup stages, GPIO resolution, the first usable state (`READY` or an
explicitly allowed `DEGRADED`), calibration warning, zero direct results and
state reconciliations, health transitions, lag warnings, gaps/inconsistencies,
logger/acquisition errors, and recording/logger shutdown. Acquisition shutdown
is included only when it occurs while the artifact writer remains open; later
close failures remain in the application log. Each record includes monotonic
time, severity, stable event code, message, and structured context.

A successful logger is the channel's only live consumer. It first writes
records already queued before recording and then continues with new records in
channel delivery order. If the logger fails, the artifact is marked incomplete
and the stopped-child fallback drain in Section 14.3 is best effort rather than
an exact-once recovery protocol.

### 16.4 Final summary

Write:

```text
treadmill_summary.json
```

Include at least:

```text
recording_origin_applied
state_sample_count
recording_duration_s
observed_health_codes (facade lifetime)
recording-window edge events
recording-window valid/positive/negative transitions
recording-window direction reversals
recording-window global and per-line sequence gaps
recording-window estimated missing events
recording-window event inconsistencies
maximum_observed_transition_rate_hz_lifetime from aligned 100 ms bins
maximum_processing_lag_ns_lifetime
recording-window lag count/sum
recording-window processing-lag warning count
approximate_maximum_processing_lag_ns
approximate_processing_lag_p50_ns
approximate_processing_lag_p95_ns
approximate_processing_lag_p99_ns
approximate_maximum_processing_lag_ns_is_lower_bound
approximate_processing_lag_p50_ns_is_lower_bound
approximate_processing_lag_p95_ns_is_lower_bound
approximate_processing_lag_p99_ns_is_lower_bound
recording-window publication skips
recording-window diagnostic-event drops
heartbeat/acquisition/logger failure diagnostics and timestamps
logger late/missed samples
logger shared-state read failures
final position and distance travelled
final health, integrity, and acquisition availability
calibration and verification warning
resolved GPIO and software versions
recording_end_monotonic_ns and recording-end UTC anchor (null without an applied origin)
artifact_finalization_monotonic_ns and artifact-finalization UTC time for every outcome
```

The summary repeats the artifact-set schema version. A standalone failure-ring
dump or `treadmill_failure.json` carries its own schema version because it may
need to be interpreted without a complete metadata file.

Failure-diagnostic totals count records actually written into this artifact
set; they are not inferred by subtracting unrelated decoder counters. The
worker-owned `diagnostic_event_drop_count` records channel overflow and
activates the `DIAGNOSTIC_EVENTS_DROPPED` acceptance code.

`observed_health_codes` is the ordered, duplicate-free union defined in
Section 14.3, with ties resolved by first monotonic observation and then
Section 14.1 priority. A code remains in this list after it clears.
Finalization drains health-transition diagnostics already accepted for the
recording before writing the list; if a diagnostic was dropped,
`DIAGNOSTIC_EVENTS_DROPPED` is itself included and prevents real
hardware-stress acceptance. Header-only summaries populate the list from their
immutable failure context. Shutdown codes arising after artifact finalization
remain the launcher's post-close responsibility.

This field deliberately has no recording baseline. Later recordings made by
the same facade repeat earlier codes; consumers must not interpret it as a
recording-window field. This conservative scope is metadata-visible and resets
only with a new facade/acquisition lifetime.

`state_sample_count` is the exact number of data rows successfully written to
`treadmill_state.tsv`; the header is not a sample. It is zero for every
header-only failure artifact and must agree with a direct row count.

For a normally finalized applied-origin recording, the logger captures the
recording-end monotonic time after the final sample attempt at the final-owner
stop boundary and computes:

```text
recording_duration_s =
    (recording_end_monotonic_ns - origin_applied_monotonic_ns) / 1e9
```

It uses monotonic times from the same clock domain. Header-only, failed, or
incomplete recordings set `recording_duration_s` and the recording-end
timebase anchor to `null`; artifact-finalization time remains populated.

Recording-window counters are final lifetime counters minus the baselines saved
with the applied recording origin; zeroing never mutates lifetime
counters. The summary also includes the baseline and final lifetime counters so
the calculation is auditable. If no recording origin was applied, set
`recording_origin_applied=false`, encode recording-window counters as
`null`, and include any last-known lifetime counters without inventing a
baseline or zero-valued recording result. Recording-relative final position and
distance, recording duration, and the recording-end timebase anchor are also
`null` in that case, even if live acquisition still has physical values
relative to an earlier zero. The separate artifact-finalization time records
when the failure set was closed; it must not be mislabeled as the end of the
still-active shared IO recording.

Final physical values are `null` when final trajectory integrity is invalid or
final acquisition availability is false because of an unexpected loss.
Normal completed artifacts retain the health/availability observed when the
final recording owner stopped and are not rewritten during the later
acquisition close. Separately, an orderly cached `STOPPED` public snapshot
retains its last trustworthy physical values as required by Section 10.3. Raw
delivered-event totals remain available as explicitly untrusted diagnostics
after a failure.

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
- non-Boolean enablement/verification/criticality fields, empty or non-string
  calibration provenance fields, and unknown override keys fail clearly;
- retired enabled-session keys fail with their explicit conversion guidance;
- invalid units, signs, rates, buffers, timeouts, and logger-criticality values
  fail clearly;
- `NaN`, infinities, fractional size/count fields, and Boolean-as-integer values
  fail clearly;
- manifest BCM13/16 are used for head-fixed treadmill acquisition;
- pins are not duplicated in YAML;
- `treadmill=false` creates no treadmill claim/process/artifacts while normal
  profile inputs, including freely-moving BCM13/16 pokes, remain unchanged;
- the hardware-stress configuration disables treadmill in off-Pi/forced-mock
  mode and enables required acquisition/logger behavior on a real Pi 5;
- strict force-mock parsing accepts only the documented true/false literals,
  rejects empty/unknown values, and never treats an invalid value as real mode;
- known non-ARM, Pi 5, unsupported Pi, and unknown ARM/model-read-failure cases
  resolve exactly as Section 5.4 requires, including valid force-mock override;
- subprocess entrypoint tests prove the real/mock decision occurs before any
  conditional real/mock GPIO class import, is identical in both stress
  launchers, starts the mock server only in mock mode, and rejects an unforced
  non-Pi-5 Raspberry Pi before launcher side effects;
- display `--dry-run` remains side-effect free, loads no hardware backend, and
  emits no hardware-acceptance result;
- no enabled runtime silently selects the retired mock `RotaryEncoder`;
- `treadmill=true` is accepted only for the head-fixed profile;
- freely-moving treadmill configuration fails before BCM13/16 claims;
- required startup failure prevents session preparation;
- partial `InputService` construction failure leaves no treadmill process, IPC,
  or GPIO claim even though owner assignment never completed;
- optional startup failure remains loud and inspectable;
- optional startup failure returns only after partial worker, GPIO, and IPC
  resources are gone and the cached failed facade is inspectable;
- an enabled facade without initial state returns a failed health report and
  raises the typed unavailable error from `snapshot()`/`require_healthy()`;
- another recording owner does not restart or re-zero the logger;
- final-owner stop finalizes active treadmill artifacts or preserves an
  already-finalized failure set without rewriting it;
- a later newly opened recording starts a new logger and establishes one new
  zero without restarting acquisition;
- enabled optional startup failure immediately finalizes header-only state plus
  explicit failed metadata, diagnostics, and summary artifacts without leaving
  an idle logger or open treadmill handle;
- after optional startup cleanup has removed acquisition IPC, a later
  header-only logger succeeds using only the immutable serialized failure
  context;
- failure while opening either shared-recorder artifact restores prior owner
  flags, closes partial handles, and leaves recording inactive;
- required treadmill logger/origin failure after `started_now=true` rolls back
  only the newly asserted owner and newly opened shared handles;
- pre-existing treadmill artifact paths are never overwritten or appended and
  follow the same visible logger-failure handling;
- recording-origin zero `confirmed_not_applied` and `indeterminate` results
  follow required versus optional behavior without emitting mislabeled samples;
- pre-recording acquisition/integrity failure rolls back required treadmill
  recording start but produces failed optional treadmill artifacts;
- a specific startup cause suppresses generic `ACQUISITION_START_FAILED`, while
  process creation/handshake failure without a specific cause uses it;
- non-treadmill lick, poke, trigger, output, and recording tests remain
  unchanged and passing.
- runtime polling surfaces one warning per treadmill health/code transition
  before the prepared-state early return, without changing task state or
  warning continuously;
- `session_metadata.json` receives the explicit enabled/effective-configuration
  treadmill object without relying on mutation of input `session_info`.
- real Pi 5 stress runs stop on every disallowed treadmill report, accept only
  calibration-only degradation during bring-up, validate the completed summary,
  and return nonzero on treadmill/artifact failure or interruption;
- display-mode real Pi 5 stress evaluates the same health policy immediately
  before audio preflight, skips the preflight on an active or retained failure,
  and rejects an active or cleared-but-retained preflight failure before task
  start;
- mock stress output labels treadmill hardware acceptance `not_applicable`; a
  mock cleanup failure preserves that status, records
  `behavbox_cleanup.status=failed`, and exits nonzero;
- both launchers call `box.close()` after injected preparation/acquisition,
  logger-start, origin-zero, task-runtime, stop, artifact-writing, and
  finalization failures, report cleanup failures, and never rely on `__del__`
  for child cleanup;
- post-close processing finalizes both hardware acceptance and
  `behavbox_cleanup`; real success replaces `pending_cleanup`, while mock stays
  `not_applicable`, and cleanup/final-state-write failure cannot leave a result
  which is interpreted as successful;
- display-mode failure cleanup closes BehavBox before restoring LightDM.

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
- rejected, regressing/equal, and future-timestamp events follow the documented
  last-edge/last-motion timestamp rules;
- no dependence on Python processing delay or wall time.

### 18.4 Failure-injection tests

Inject:

- global and per-line sequence gaps;
- contiguous and gapped global/per-line sequence transitions across the
  unsigned 32-bit wrap boundary, plus the exactly-half-range ambiguous case;
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
- provisioning/verifier checks distinguish the `python3-libgpiod` binding from
  the separately packaged `gpiod` command-line tools;
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
  nonthrowing and non-healthy, then clears its active read-timeout code after a
  successful coherent read while retaining diagnostics;
- coherent reads, control resolution, logger startup, and synchronization all
  stop at their documented total deadlines;
- startup waits for usable health and reports staged failures;
- spawn-mode worker targets and injected sources serialize without import-time
  process or GPIO side effects;
- treadmill process creation leaves the application's global multiprocessing
  start-method configuration unchanged;
- acquisition/logger processes and all IPC primitives use the same selected
  multiprocessing context;
- heartbeat updates without motion;
- stale heartbeat or a killed worker becomes `FAILED`;
- a worker-owned health code which activates and clears between two reader
  snapshots remains in `observed_acquisition_health_codes` with deterministic
  first-observation ordering even when its diagnostic delivery is delayed;
- zero command IDs are idempotent and direct acknowledgements match the
  published command ID and state version;
- the in-flight and most recently resolved command IDs replay without another
  application, while an older stale ID is rejected without application or an
  unbounded result history;
- loss of a direct zero acknowledgement reconciles to `applied` from coherent
  state when publication succeeded;
- enqueue failure or a published rejection yields `confirmed_not_applied` and
  leaves canonical offsets unchanged;
- unreadable/unresolved state yields `indeterminate`, and a recording never
  uses that command as its origin;
- later readable state resolves a non-recording indeterminate zero without
  reapplying it and follows the documented active-code clearing rules;
- zero rejects unavailable/integrity-invalid acquisition but remains available
  for degradation that leaves trajectory continuity intact;
- public zero is rejected without offset change during active recording, while
  the private pre-sample recording-origin path remains allowed;
- deterministic barriers exercise both orderings between public zero and
  first-owner start, and between public zero and final-owner stop, proving that
  zero resolution cannot interleave logger/origin startup or final sampling;
- the recording-state callback remains true after optional logger/origin
  failure and becomes false only after required rollback or final recorder
  finalization, so public zero remains rejected for the full shared recording;
- public zero's mutex wait respects its total control deadline and timeout does
  not allocate or enqueue a command;
- parameterized before/after-lock barriers cover `snapshot()`,
  `health_report()`, `zero()`, and recording start/stop versus close: an
  operation already holding the lifecycle lock completes or unwinds within its
  existing deadline before cleanup, while one queued behind the close request
  rechecks the flag and touches no IPC, recorder, or logger resource. The
  admitted cases include lost-ack zero reconciliation and logger-ready/origin
  setup;
- coordinated close holds the lifecycle mutex through logger finalization,
  shared-recorder owner/handle cleanup, acquisition shutdown, and treadmill IPC
  release; the later enclosing recorder close is idempotent;
- injected flush and close failures on each shared-recorder handle do not skip
  the other handles, acquisition STOP/join/forced cleanup, final cache update,
  treadmill IPC release, or later BehavBox subsystem cleanup; recorder owner
  flags/references become inactive, errors are aggregated afterward, and cached
  health contains `SHUTDOWN_INCOMPLETE`;
- close drains pending fake events and is idempotent;
- forced cleanup is bounded and diagnosed;
- forced treadmill cleanup does not prevent the enclosing BehavBox cleanup
  from reaching later unrelated resources;
- repeated construct/start/close cycles leave no live child or owned IPC
  resource behind;
- post-close snapshots use the cached final state, do not touch released IPC,
  preserve clean `STOPPED` versus forced `FAILED` shutdown, and commands follow
  the documented facade lifecycle;
- orderly `STOPPED` retains trustworthy final physical values, while an
  unexpected availability loss exposes them as unavailable.

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
- logger-start failure or timeout occurs before any origin zero; required mode
  rolls back and optional mode continues shared IO with visible failure/fallback
  evidence but no treadmill state samples;
- after logger readiness, either non-applied origin outcome stops/joins the
  logger and closes all treadmill handles; required mode then has no active
  recording facade after rollback, while optional mode immediately finalizes
  its header-only failed artifacts and keeps only the recording-scoped failure
  marker until shared recording ends;
- acquisition death cannot leave later logger samples marked healthy;
- an optional recording failure remains visible until the shared recording
  ends and does not invalidate intact acquisition trajectory;
- metadata and summary contain units, calibration, versions, health, and
  integrity;
- `observed_health_codes` preserves first-observation order without duplicates,
  includes startup context and transient health-transition diagnostics, and
  does not remove codes which later clear;
- a second recording on the same facade repeats codes first observed during the
  first recording, including codes seen only by its logger, while a new facade
  starts with a fresh history;
- `state_sample_count` equals the number of TSV data rows, is zero for
  header-only output, and normal duration uses the exact monotonic
  origin-to-final-stop formula while failed/incomplete duration is null;
- aligned 100 ms lifetime transition-rate maxima and recording-window lag
  histogram subtraction/approximate percentile labels follow Section 11.3;
- startup/pre-recording diagnostic context reaches a later logger, and a full
  diagnostic channel increments the published/summary drop counter without
  blocking acquisition;
- pre-recording and active-recording health checks never consume diagnostic
  records, while retained shared health still exposes transient codes to
  preflight;
- a record queued before recording is later written by the successful logger,
  which remains the only live channel consumer;
- worker-channel overflow increments `diagnostic_event_drop_count` and retains
  `DIAGNOSTIC_EVENTS_DROPPED` in shared observed health;
- acquisition startup failure stops the worker before one bounded drain into
  the immutable failure context and releases IPC afterward;
- unexpected logger failure marks the artifact incomplete and, only after the
  child is confirmed dead, performs one bounded best-effort fallback drain
  without claiming exact-once delivery;
- handled-failure ring dumps are reconstructable and abrupt-loss summaries do
  not claim a dump exists;
- repeated close does not overwrite a completed recording;
- requested-but-failed and disabled treadmill recordings remain
  distinguishable from their artifact sets and metadata;
- failed recordings without an applied origin use null window counters
  and null recording duration/end/relative physical values while preserving
  any known lifetime counters and recording the artifact-finalization time.

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

For the real Pi 5 hardware-stress path, inject acquisition death, required
logger death, late finalization failure, a transient disallowed code after the
last ordinary task poll which clears before summary finalization, and
calibration-only degradation. Also inject a disallowed preflight code which
clears before the second gate. Verify immediate error stop for active failures,
no task start for the retained preflight failure, bounded cleanup and nonzero
exit, final-summary rejection of both persistent and cleared late failures, and
a clearly non-production pass for calibration-only bring-up.

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
- close-gated recording ownership transitions and shared-recorder cleanup under
  the single parent lifecycle `RLock`;
- parent reads serialized with close so IPC is never released under a reader;
- one live diagnostic consumer: the logger;
- exception-safe recorder, treadmill, and enclosing BehavBox cleanup with
  aggregate post-cleanup error reporting;
- mandatory rollback for partial shared-artifact and required treadmill-start
  failures;
- effective treadmill configuration in session metadata;
- treadmill-disabled mock stress and treadmill-required real Pi 5 stress
  configuration;
- strict, immutable stress-host/force-mock selection without changing
  unrelated non-stress GPIO support;
- task-specific real-stress health and artifact acceptance, nonzero failure
  exits, and guaranteed bounded launcher cleanup after BehavBox construction;
- separate hardware-acceptance and BehavBox-cleanup results, including mock
  `not_applicable` semantics;
- archived and external legacy code unchanged.

### 20.3 Recording and diagnostics

- fixed-rate `treadmill_state.tsv`;
- `treadmill_metadata.json`;
- `treadmill_diagnostics.jsonl`;
- `treadmill_summary.json`;
- ordered retained `observed_health_codes` for acceptance;
- bounded recent-event ring;
- best-effort handled-failure ring dump;
- application-log fallback for logger failure.

### 20.4 Tools and documentation

- calibration/sign/rate diagnostic utility;
- synthetic and process stress utility;
- setup and dependency instructions for Pi 5 Trixie;
- calibration procedure;
- health/integrity interpretation guide;
- hardware-stress launch instructions covering Pi 5 real default, pre-import
  force-mock use, and unsupported Pi models;
- hardware rate-sweep and soak-test procedure;
- migration note containing modern and legacy calibration values;
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
- [ ] parent readers cannot race close or IPC release;
- [ ] fixed-rate artifacts are bounded, incremental, aligned, and versioned;
- [ ] the final summary retains every observed health code needed for
      acceptance, including cleared transients;
- [ ] summary row count and monotonic origin-to-stop duration agree with the
      recorded state artifact;
- [ ] synthetic million-event tests are exact;
- [ ] full process stress tests are exact under representative load;
- [ ] real Pi 5 stress failure injection cannot exit zero or leave acquisition
      or logger children running;
- [ ] injected recorder flush/close failures still attempt every later cleanup
      and latch `SHUTDOWN_INCOMPLETE`;
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
