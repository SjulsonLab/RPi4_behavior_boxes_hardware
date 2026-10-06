# Treadmill Acquisition: Pi 5 / Trixie Implementation Plan

## 1. Purpose and status

This document translates `docs/treadmill_spec.md` into a test-first execution
plan for the active `RPi4_behavior_boxes_hardware` codebase. The specification
is authoritative when the two documents differ.

This plan targets only:

- Raspberry Pi 5;
- 64-bit Raspberry Pi OS / Debian 13 Trixie;
- system Python 3.13;
- the official libgpiod v2 Python API.

Raspberry Pi 4B support is deferred until the Pi 5 implementation has passed
production acceptance. Do not add Pi 4 compatibility branches, provisioning,
tests, or abstractions during the initial implementation.

Plan state as of 2026-10-06:

| Item | State |
|---|---|
| Specification | Updated for the modern codebase and Pi 5-only target |
| Plan rewrite | Complete in this document; awaiting user review |
| Runtime implementation | Not started |
| Treadmill rewrite tests | Not started |
| Dependency changes | Not started |
| Hardware calibration | Required after the framework is runnable |
| Production acceptance | Not started |

Updating these documents does not authorize runtime implementation. Before the
first implementation work package, the Sol coordinator must confirm that the
user has approved this plan and record that approval in Section 20.

### 1.1 Resume procedure

A fresh implementation chat must:

1. read `AGENTS.md`, `docs/SoftwareDesign.md`, `docs/treadmill_spec.md`, and this
   plan in full;
2. inspect `git status` and preserve unrelated changes;
3. inspect the current files and callers named by the next work package;
4. verify that prerequisite tests, commits, and evidence exist;
5. update the ledger before assigning or beginning work;
6. obtain approval if a requirement, public interface, dependency, or package
   boundary would materially change;
7. execute only the approved work package and its required verification.

Chat history and worker reports are supporting information, not substitutes
for repository state, committed tests, or reproducible command output.

## 2. Approved decisions

The following decisions are fixed for the first implementation:

1. **Platform:** Pi 5, Trixie, and Python 3.13 only.
2. **Enablement:** `session_info["treadmill"]` is authoritative.
3. **Public API:** the modern facade is exposed as `box.treadmill`.
   `box.treadmill_encoder` is not reused for the new runtime.
4. **Pins:** the head-fixed manifest remains the sole owner of BCM13 and BCM16.
   Pin numbers are not duplicated in treadmill YAML.
5. **Backend:** treadmill GPIO uses libgpiod v2, both edges, kernel monotonic
   timestamps, and event sequence numbers. There is no fallback decoder.
6. **Calibration:** the starting value is the explicitly unverified modern
   nominal value `0.39269908169872414 mm/encoder cycle`. The legacy
   `0.41095 mm/encoder cycle` value is documentation and migration evidence,
   not the default.
7. **Configuration:** readable defaults live in
   `box_runtime/input/treadmill/defaults.yaml`; session-specific overrides live
   under `session_info["treadmill_config"]`.
8. **Recording:** normal output uses versioned TSV and JSON artifacts in the
   active `SharedIoRecorder` directory. SQLite is not part of this work.
9. **Lifecycle:** acquisition starts during session preparation; recording
   starts only when shared recording first opens and stops only when its final
   owner stops.
10. **Failure behavior:** unverified calibration is a prominent allowed
    `DEGRADED` state. Runtime policies are `warn`, `pause`, and `abort`, with
    `warn` as the default.
11. **Legacy code:** archived code and the older external behavior repository
    remain untouched and unimported.
12. **Execution:** a Sol coordinator may delegate bounded tasks to Terra
    workers using the simplified protocol in Section 14.

## 3. Modern baseline and required delta

The active implementation is concentrated in:

```text
box_runtime/input/service.py
box_runtime/behavior/behavbox.py
box_runtime/io_manifest.py
box_runtime/io_recording.py
box_runtime/behavior/gpio_backend.py
environment/rpi5_trixie_manifest.json
environment/rpi5_trixie.py
environment/rpi5_trixie_verifier.py
deploy/ansible/pi5_trixie.yml
```

Current behavior that must change:

- every head-fixed `InputService` constructs `gpiozero.RotaryEncoder`, even
  when `session_info["treadmill"]` is false;
- speed is derived by sampling `.steps` in a thread, normally at 30 Hz;
- the old artifact is a two-column `treadmill_speed.tsv` in cm/s;
- the runtime has no event history, sequence-loss detection, worker heartbeat,
  physical position API, or integrity latch;
- `box.treadmill_encoder` exposes the low-level gpiozero device;
- Pi 5 provisioning does not yet declare or verify the required libgpiod v2
  Python capability set.

Modern behavior that must remain intact:

- the manifest owns profile-specific pin assignments;
- head-fixed and freely-moving profiles remain distinct;
- user-owned and task-owned recordings may overlap;
- the first recording owner opens one shared directory;
- another owner reuses that recording;
- the final owner closes it;
- non-treadmill inputs and outputs continue through the existing gpiozero /
  rpi-lgpio backend;
- BehavBox preparation and close paths clean up partially initialized
  resources.

Existing tests that assert a head-fixed encoder exists while `treadmill=false`
describe the behavior being replaced and must be rewritten through the normal
RED/GREEN process. Tests for unrelated lick, poke, trigger, output, camera, and
recording behavior remain regression requirements.

## 4. Architecture

```text
Manifest BCM13/BCM16
        |
        v
libgpiod v2 joint line request
(RP1 resolution, both edges, kernel timestamps and sequence numbers)
        |
        v
Dedicated acquisition process
        |-- batch event draining
        |-- pure x4 decoder
        |-- position, distance, direction, and speed
        |-- integrity, lag, health, and heartbeat
        |-- bounded recent-event ring
        |
        v
Single-writer coherent shared state
        |                              |
        v                              v
box.treadmill facade             Logger process while recording
snapshot/zero/health             treadmill_state.tsv
                                 metadata/diagnostics/summary JSON
```

The acquisition process is the only writer of canonical decoder state. The
BehavBox process and logger process are bounded readers. Behavior work and disk
latency must not create a blocking path back into event draining.

## 5. Proposed package and module contracts

Create the package incrementally:

```text
box_runtime/input/treadmill/
    __init__.py
    defaults.yaml
    config.py
    decoder.py
    state.py
    gpio_backend.py
    shared_state.py
    diagnostics.py
    acquisition.py
    recording.py
    runtime.py
```

Focused tests live under:

```text
tests/treadmill/
```

Do not create empty placeholder modules. Each module is added only after its
tests require it.

### 5.1 Module responsibilities

| Module | Responsibility | Must not do |
|---|---|---|
| `config.py` | Load YAML defaults, merge session overrides, validate types/units/policies, and produce an immutable config | Own pins, import GPIO, start processes, or write artifacts |
| `decoder.py` | Define the generic edge/internal decoder state and apply deterministic x4, speed, zero, and integrity rules | Import gpiod, multiprocessing, files, or wall-clock time |
| `state.py` | Define immutable public state, health/policy records, and explicit shared-field conversions | Decode edges or own synchronization primitives |
| `gpio_backend.py` | Probe the installed gpiod v2 API, resolve the Pi 5 RP1 controller, claim lines, synchronize input state, and adapt event batches | Decode motion, start processes, or write files |
| `shared_state.py` | Publish and read coherent fixed-layout state without blocking the acquisition writer | Own GPIO, decoder policy, or recording |
| `diagnostics.py` | Maintain bounded lag aggregates, recent-event records, stable event codes, and summary structures | Perform disk I/O in the acquisition hot path |
| `acquisition.py` | Run the spawn-safe acquisition worker, controls, heartbeat, state publication, and orderly GPIO shutdown | Perform behavior callbacks or normal disk writes |
| `recording.py` | Run fixed-rate logger scheduling and incrementally write/version TSV and JSON artifacts | Receive every GPIO edge or block acquisition |
| `runtime.py` | Compose processes and IPC, implement the behavior-facing facade, and evaluate effective health/policy | Contain quadrature tables or direct task-state mutations |
| `__init__.py` | Export only the intentionally public facade/state/config/error types | Re-export internal worker or gpiod details |

### 5.2 Design constraints

- Prefer deterministic module-level functions for decoding, validation,
  scheduling, serialization, and policy evaluation.
- Use dataclasses for configuration and typed records.
- Use classes only for cohesive resources with lifecycle or owned state.
- Keep creation in `runtime.py`; pass narrow dependencies to lower layers.
- Add a `Protocol` only where production and test implementations genuinely
  share a behavioral boundary. The initial justified boundary is the edge
  source used by the acquisition worker.
- Do not pass full `session_info` below the configuration/integration boundary.
- Every function and method documents input types, units, returns, side
  effects, and lifecycle constraints.
- Avoid generic `models.py`, `utils.py`, or `helpers.py` modules.

## 6. Configuration and public contracts

### 6.1 Configuration precedence

Effective settings are assembled in this order:

1. BCM pins from the selected `BoxProfileManifest`;
2. tracked values from `defaults.yaml`;
3. documented keys from `session_info["treadmill_config"]`;
4. validated immutable runtime configuration;
5. a serialized effective configuration in session metadata.

The YAML file must not contain A/B pin numbers. A session override cannot
silently replace manifest pins.

Initial readable defaults match the specification, including:

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

Heartbeat, lag, startup, shutdown, and expected maximum rate defaults are
selected only after Pi 5 timing measurements. They must still be explicit and
validated before production acceptance.

### 6.2 Public state

`TreadmillState` is immutable and includes the physical fields, raw diagnostic
fields, monotonic timestamps, counters, lag values, health, integrity,
failure code, and state version required by the specification.

Physical units are:

- position and distance: millimetres;
- speed: millimetres per second;
- event and heartbeat time: monotonic nanoseconds;
- sample UTC alignment: POSIX seconds in the TSV and paired timebase anchors
  in metadata/summary.

Raw transition counts are diagnostic and must not become the default task API.

### 6.3 BehavBox facade

The stable modern boundary is:

```python
box.treadmill
```

Semantics:

- `None` when `session_info["treadmill"]` is false;
- a `TreadmillRuntime` facade when treadmill acquisition is enabled;
- for optional startup failure, a facade exposing visible `FAILED` health
  rather than a silent gpiozero fallback;
- required startup failure propagates out of `prepare_session()` after cleanup.

The facade provides operations equivalent to:

```text
start
snapshot
zero
health_report
require_healthy
start_recording
stop_recording
stop
close
```

Do not alias the facade to `box.treadmill_encoder` and do not emulate the old
`.steps` interface.

## 7. Decoder and GPIO implementation design

### 7.1 Generic event and decoder

Production and synthetic sources use one small ordered event containing:

```text
timestamp_ns
channel
edge_type
global_sequence_number
line_sequence_number
```

The decoder uses the explicit positive sequence:

```text
00 -> 01 -> 11 -> 10 -> 00
```

and its reverse. Each valid single-bit transition changes signed transition
position by one. It never invents an intermediate state after loss.

Position and distance are derived from integer transition totals:

```text
mm_per_transition = mm_per_encoder_cycle / 4

position_mm =
    (raw_transition_position - zero_transition_offset)
    * mm_per_transition
    * locomotion_sign

distance_travelled_mm =
    (absolute_transition_total - zero_absolute_offset)
    * mm_per_transition
```

Speed uses consecutive valid transition timestamps supplied by the kernel.
The first transition has no interval-derived estimate. Public speed becomes
zero after `speed_timeout_s`, while the last-edge estimate remains available.

The tested failure matrix determines whether every anomaly changes state,
increments counters, resynchronizes A/B, degrades/fails health, or latches
integrity false. Sequence loss or ambiguous trajectory loss always invalidates
integrity and never produces a guessed correction.

### 7.2 Pi 5 GPIO backend

The backend must verify the installed official binding rather than assuming an
API from memory. Required capabilities include:

- one joint request for both lines;
- both rising and falling edge detection;
- pull-up bias and explicit zero debounce;
- monotonic event clock;
- configurable kernel event buffer;
- waiting and batched reads;
- line offset, edge type, timestamp, global sequence, and line sequence.

RP1 resolution must enumerate gpiochips and use chip/line metadata. It must not
assume `/dev/gpiochip0`, because numbering can change with kernel/device-tree
configuration. It records all candidates and fails on missing, ambiguous,
claimed, or invalid lines.

### 7.3 Startup synchronization

Request both lines with edge detection active, then use a bounded
drain/read/quiet-period procedure. Deterministic boundary-injection tests must
prove that motion around synchronization is either included exactly once after
the accepted initial state or explicitly classified as pre-acquisition motion.

The runtime reaches `READY` only after capability checks, controller
resolution, the joint claim, coherent initial A/B state, initial publication,
and heartbeat have all succeeded.

### 7.4 Acquisition hot loop

The loop:

1. waits no longer than the next heartbeat/control deadline;
2. drains available events in bounded batches and kernel order;
3. decodes all drained events;
4. publishes an appropriate coherent snapshot;
5. updates heartbeat on schedule even while stationary;
6. services a bounded amount of lower-priority control work;
7. immediately returns to draining if more edges are pending.

It performs no console output, normal file I/O, network work, GUI work,
behavior callbacks, or unbounded allocation.

## 8. Processes, shared state, health, and lifecycle

### 8.1 Process ownership

The facade owns:

- one acquisition process while treadmill acquisition is active;
- zero or one logger process while shared recording is active;
- coherent shared-state storage;
- bounded commands, acknowledgements, and status channels;
- startup, heartbeat, and shutdown monitoring.

Use an explicitly selected Python 3.13 multiprocessing start method. Start with
`spawn` unless Pi 5 tests produce a documented reason to choose differently.
The child opens and owns the gpiod request; the parent never passes an open GPIO
request across the process boundary.

### 8.2 Shared state

Implement a measured coherent publication design, initially two fixed-layout
slots with bounded reader locking and nonblocking writer acquisition.

Required properties:

- readers never observe torn field combinations;
- a reader stalled on one slot cannot stop writer progress through the other;
- if both slots are temporarily unavailable, publication is skipped and
  diagnosed rather than blocking edge draining;
- reader retry/timeout is bounded;
- the acquisition process retains canonical state even if publication skips.

### 8.3 Controls and health

Controls include `ZERO`, `START_RECORDING`, `STOP_RECORDING`, `STATUS`, and
`STOP`. They use bounded communication and matched acknowledgements. Edge
events never travel through the control queue.

Health states are:

```text
STARTING
READY
DEGRADED
FAILED
STOPPED
```

Heartbeat age and child liveness affect reader-side effective health. A dead
worker must not look like healthy stationary speed zero. Integrity remains a
separate latch so logger-only failure does not falsely imply lost encoder
transitions.

Policy evaluation returns a typed `none`, `warn`, `pause_requested`, or
`abort_requested` outcome. The treadmill subsystem never directly mutates task
or presenter state.

### 8.4 Lifecycle

During `BehavBox.prepare_session()`:

1. validate profile, manifest pins, config, and calibration state;
2. construct `box.treadmill` only when enabled;
3. start acquisition and complete the startup handshake;
4. return only after an allowed usable state.

When the first shared recording owner starts:

1. start and validate the logger for the selected directory;
2. acknowledge one recording-origin zero after pending edges are decoded;
3. write metadata/timebase information;
4. begin fixed-rate sampling from the acknowledged state version.

Additional owners do not restart the logger or zero again. The final owner
stops and finalizes recording, but acquisition remains active until
`InputService.close()`.

Close is idempotent and bounded. Acquisition drains already available events,
publishes final state, releases only its lines, and joins. Forced termination
is a diagnosed last resort.

## 9. Recording artifacts

Normal recordings create these files in the active shared recording directory:

```text
treadmill_state.tsv
treadmill_metadata.json
treadmill_diagnostics.jsonl
treadmill_summary.json
```

`treadmill_state.tsv` is a versioned fixed-rate physical-state record. It does
not reuse the name or schema of the old `treadmill_speed.tsv`.

The initial logger rate is 200 Hz. It uses monotonic deadline advancement:

```text
next_deadline += period
```

If late, it records one sample at the real time, increments late/missed counts,
and advances to the next future deadline. It never writes duplicate catch-up
rows.

Writes are buffered and flushed at bounded intervals. A complete session is
never held in RAM, and one physical synchronization per sample is forbidden.

Metadata records effective config, unverified/verified calibration, platform,
software versions, GPIO resolution, timebase, and schema. Diagnostics use
stable codes and structured context. The final summary includes counts, gaps,
integrity, health, lag, logger timing, final position/distance, calibration,
and shutdown outcome.

The acquisition process retains only a bounded recent-event ring during normal
operation. Integrity failure preserves a copy outside the hot path. Full raw
edge recording is explicit debug mode and off by default.

## 10. Modern codebase integration

### 10.1 `InputService`

The expected change is intentionally localized. It should modify roughly
40-70 lines and remove the old treadmill sampler methods; exact diff size is
not an acceptance criterion.

Required changes:

1. Remove the active treadmill dependency on `RotaryEncoder`.
2. Remove old treadmill sampling/calibration fields, file handle, thread, stop
   event, step baseline, and sample timestamp.
3. Keep one `self.treadmill` facade reference.
4. For `treadmill=false`, do not claim BCM13/16 and set `owner.treadmill=None`.
5. For enabled head-fixed sessions, read `treadmill_1` and `treadmill_2` from
   the manifest, construct/start the facade, and assign `owner.treadmill`.
6. Reject enabled freely-moving sessions before any treadmill line claim.
7. Delegate recording-start and recording-stop hooks to the facade.
8. Delegate close idempotently.
9. Delete the old sampler start/stop/loop/write methods.

Do not redesign button construction, lick/poke callbacks, trigger handling,
user-configurable GPIO, event recording, or safe-close behavior.

### 10.2 `BehavBox`

Expected changes are small:

- initialize `self.treadmill = None`;
- continue constructing `InputService` during `prepare_session()`;
- preserve the existing cleanup-on-preparation-failure path;
- ensure a required logger-start failure rolls back a newly opened shared
  recording rather than leaving `SharedIoRecorder` active;
- expose the facade without adding task-specific treadmill polling.

No generic behavior loop automatically pauses or aborts a task. Task code may
inspect typed policy outcomes when it elects to depend on treadmill state.

### 10.3 Manifest and recorder

`box_runtime/io_manifest.py` remains authoritative for BCM13/16. The plan does
not move pin assignments or create a second allocation table.

`SharedIoRecorder` ownership semantics remain unchanged. A small rollback hook
may be added only if tests show it is required for atomic failure of a newly
opened recording. The treadmill subsystem otherwise integrates through the
existing `started_now` and final-owner stop boundaries.

### 10.4 Legacy and archive boundary

Do not modify or import runtime code from:

```text
box_runtime/old_hardware/treadmill/
/home/matt/EinsteinMed Dropbox/Matthew Chin/LabComputerShare/RPi4_behavior_boxes/
```

Those files remain calibration and historical references only.

## 11. Dependencies and Pi 5/Trixie environment

### 11.1 Dependency policy

Use:

- the existing PyYAML dependency for readable configuration;
- the official Trixie/libgpiod v2 Python binding for treadmill GPIO;
- Python standard-library multiprocessing, dataclasses, JSON, CSV/TSV, and
  timing primitives;
- pytest for tests.

Do not add SQLite, pandas, HDF5, PyArrow, Numba, Cython, a C extension, or a
real-time framework for this subsystem.

The rest of BehavBox continues using gpiozero and rpi-lgpio. Do not remove or
replace those dependencies as part of treadmill work.

### 11.2 Target verification before adapter code

The first environment task must inspect the target Pi 5 and record:

- exact Trixie release and architecture;
- Python version;
- kernel version;
- installed libgpiod library, command-line tools, and Python-binding versions;
- the Trixie apt package providing the official Python binding;
- actual Python method signatures and event fields;
- RP1 gpiochip names, labels, paths, line counts, and BCM13/16 line metadata;
- permissions required to request those lines.

Verify the API against official documentation and installed package source.
Do not write an adapter around an assumed package name or remembered signature.

After verification:

- add the confirmed runtime package to
  `environment/rpi5_trixie_manifest.json`;
- add a gpiod module/capability probe to the existing verifier;
- update the Pi 5 Ansible path if installation or group configuration is
  required;
- add tests for those manifest/verifier changes first.

This work does not introduce Pi 4 manifests or generalize the provisioning
framework. Shared Pi 4/Pi 5 helpers may be considered only in a later,
separately approved compatibility effort.

### 11.3 Python command policy

Use `uv run` for local Python and pytest commands as required by repository
policy. Do not turn this work into a repository-wide `pyproject.toml` or
lockfile migration. Pi hardware packages may remain managed through the
existing Trixie apt provisioning path.

## 12. Tests written before implementation

Each implementation work package follows RED -> GREEN -> REFACTOR. Tests are
committed before implementation in a separate commit. The RED result must fail
for the intended missing behavior, not because of syntax, import, fixture, or
environment mistakes.

### 12.1 Configuration and state tests

Write tests for:

- exact YAML defaults and units;
- session override precedence;
- absence of pins from YAML;
- manifest-supplied distinct BCM13/16;
- invalid profile, calibration, sign, bias, rates, buffers, timeouts, and
  policies;
- unverified calibration warning/metadata and allowed `DEGRADED` state;
- immutable state and shared-layout conversions;
- effective health and typed policy outcomes.

### 12.2 Decoder and diagnostics tests

Write tests for:

- forward/reverse complete cycles and partial cycles;
- all four initial A/B states;
- long motion and immediate/repeated reversals;
- seeded randomized valid traces;
- exact net and absolute transition totals;
- modern and legacy calibration conversions;
- locomotion sign and animal-forward public sign;
- stationary/moving/repeated zeroing;
- first-edge speed, known speed, timeout, and reversal;
- global/per-line gaps, duplicate edges, two-bit jumps, malformed events,
  timestamp regression, and out-of-order sequence values;
- declared failure-matrix behavior and latched integrity;
- bounded recent-event ring and lag aggregates.

### 12.3 GPIO backend tests

Using fake binding objects and synthetic chip inventories, test:

- every required libgpiod capability;
- Pi 5 RP1 resolution across different gpiochip numbering;
- missing, ambiguous, claimed, and out-of-range lines;
- a joint request with both edges, pull-up, zero debounce, monotonic clock, and
  configured kernel buffer;
- exact event adaptation and ordering;
- multi-batch draining;
- events injected at every synchronization boundary;
- cleanup after partial startup failure.

The marked hardware smoke test then confirms actual capabilities and event
fields on the target Pi 5.

### 12.4 Shared-state and lifecycle tests

Write tests proving:

- no torn snapshots under concurrent publication;
- a stalled reader cannot block acquisition;
- both slots unavailable causes a diagnosed skip;
- startup waits for an allowed state and preserves staged error context;
- heartbeat updates without motion;
- stale heartbeat and worker death become `FAILED`;
- matched zero acknowledgements and bounded queue failure;
- graceful draining, final publication, and idempotent close;
- forced cleanup is bounded and diagnosed;
- logger-only degradation and integrity failure remain distinct.

### 12.5 Recording tests

Using deterministic clocks and temporary directories, test:

- 200 Hz deadline scheduling;
- real timestamps and no catch-up duplicates;
- stable/versioned TSV and JSON schemas;
- monotonic/UTC fields and timebase anchors;
- bounded buffering and periodic flush;
- complete orderly shutdown and useful partial output where practical;
- metadata, diagnostics, summary, and calibration warning content;
- reconstructable failure-ring dumps;
- optional raw-edge mode;
- logger failure never blocks acquisition and reaches the application log;
- repeated close does not overwrite a completed recording.

### 12.6 Modern integration tests

Update/add tests proving:

- `treadmill=false` creates no runtime, claims no BCM13/16, and creates no
  treadmill artifacts;
- enabled head-fixed sessions use manifest BCM13/16 and expose
  `box.treadmill`;
- enabled freely-moving configuration fails before claims;
- `box.treadmill_encoder` is not the modern API;
- required startup failure aborts preparation;
- optional startup failure remains visible and inspectable;
- first recording owner starts/zeros once;
- another owner does not restart or re-zero;
- final owner stops/finalizes recording;
- required logger-start failure rolls back a newly opened recording;
- acquisition remains live between recording stop and runtime close;
- all unaffected input/output/recording tests still pass.

### 12.7 Volume, stress, and hardware tests

Write or prepare tests/utilities for:

- millions of seeded transitions with exact generated/decoded totals;
- the real process architecture under CPU, disk, camera-like, GUI-like,
  console, network-like, and sleep load;
- injected gaps, worker death, and logger death;
- recorded CPU, memory, event throughput, lag, publication skips, backlog, and
  storage MB/hour;
- external deterministic quadrature rate sweeps;
- physical calibration, direction, reversal, start/stop, and soak acceptance.

## 13. Implementation phases and work packages

### Phase 0: target evidence and dependency path

#### `TW-0A` Pi 5/Trixie gpiod inventory

- Read-only inspection of the target Pi 5.
- Record the exact package/API/controller evidence from Section 11.2.
- Confirm BCM13/16 wiring availability and no active owner.
- No production adapter code.

#### `TW-0B` Provisioning RED/GREEN

- Tests first for the new manifest dependency and verifier capability probe.
- Update only the Pi 5/Trixie provisioning files and related tests.
- Run the existing provisioning tests and the verifier on the Pi.

Phase exit: the official binding and required API are proven on the target Pi,
and the existing provisioning path installs/verifies them reproducibly.

### Phase 1: configuration, state, decoder, and diagnostics

#### `TW-1A` Configuration and public-state RED/GREEN

- Add tests from Section 12.1.
- Implement `defaults.yaml`, `config.py`, and `state.py` only.

#### `TW-1B` Nominal decoder RED/GREEN

- Add nominal x4, position, distance, speed, direction, and zero tests.
- Implement the deterministic core in `decoder.py`.

#### `TW-1C` Integrity and diagnostics RED/GREEN

- Add the complete failure matrix and bounded diagnostic tests.
- Implement integrity rules and `diagnostics.py`.

Phase exit: all pure tests pass without GPIO or multiprocessing; randomized
totals are exact; memory remains bounded.

### Phase 2: Pi 5 GPIO backend

#### `TW-2A` Capability and RP1 resolution RED/GREEN

- Add fake-binding and synthetic-inventory tests.
- Implement verified v2 capability checks and dynamic RP1 resolution.

#### `TW-2B` Request, synchronization, and batching RED/GREEN

- Add request-shape, boundary-race, event-adaptation, and drain tests.
- Implement joint line ownership, synchronization, batched reads, and cleanup.

Phase exit: fake tests pass and the marked Pi smoke test observes both edge
types, kernel timestamps, and sequence fields on BCM13/16.

### Phase 3: shared state, acquisition, and facade

#### `TW-3A` Shared-state RED/GREEN

- Add coherence, stalled-reader, timeout, and publication-skip tests.
- Implement `shared_state.py`.

#### `TW-3B` Acquisition lifecycle RED/GREEN

- Add spawn/startup/heartbeat/control/death/shutdown tests.
- Implement `acquisition.py`.

#### `TW-3C` Public runtime RED/GREEN

- Add facade, effective-health, and policy tests.
- Implement `runtime.py` and intentional exports.

Phase exit: start/snapshot/zero/health/stop/close are bounded and deterministic;
worker death is detected; readers cannot block acquisition.

### Phase 4: recording and artifacts

#### `TW-4A` Artifact writers RED/GREEN

- Add schema, metadata, diagnostic, summary, and bounded-flush tests.
- Implement pure/versioned TSV and JSON writing primitives.

#### `TW-4B` Logger process RED/GREEN

- Add deterministic scheduling, lateness, process, and failure tests.
- Implement the independent logger process in `recording.py`.

#### `TW-4C` Failure dump and fallback RED/GREEN

- Add ring-dump, raw-debug, and failed-logger application-log tests.
- Complete diagnostic/fallback integration without adding acquisition I/O.

Phase exit: artifacts are versioned, incremental, bounded, and recoverable
enough for interrupted experimental use; logger failure never blocks edges.

### Phase 5: modern BehavBox integration

#### `TW-5A` InputService enablement RED/GREEN

- Add authoritative flag/profile/manifest/public-facade tests.
- Replace only the treadmill-specific construction and close path.

#### `TW-5B` Shared recording lifecycle RED/GREEN

- Add first-owner/join/final-owner/rollback/liveness tests.
- Replace the old sampler hooks with facade recording delegation.

#### `TW-5C` Regression and API cleanup

- Remove old treadmill sampler code and `treadmill_encoder` exposure.
- Run focused integration tests and the complete non-hardware suite.
- Review the diff to confirm unrelated InputService behavior is unchanged.

Phase exit: `box.treadmill` is the only modern public treadmill boundary;
disabled sessions claim nothing; shared ownership is preserved; regressions
pass.

### Phase 6: synthetic performance and full-process stress

#### `TW-6A` High-volume deterministic validation

- Run millions-event traces with saved seed/config/version evidence.
- Measure throughput, memory, and lag before optimizing.

#### `TW-6B` Representative process stress

- Exercise acquisition, shared state, logger, and BehavBox-like load together.
- Require exact totals, zero unexplained gaps, valid integrity, bounded memory,
  and interpretable artifacts.

Phase exit: synthetic acceptance passes with documented resource and storage
results on Pi 5.

### Phase 7: calibration, hardware acceptance, and documentation

#### `TW-7A` Calibration/diagnostic utility

- Implement only after the runtime contracts are stable and tests are written.
- Guide measured-distance calibration, locomotion sign, electrical facts, and
  expected maximum transition-rate calculation.

#### `TW-7B` Rate sweep and full-stack soak

- Use an external deterministic quadrature source.
- Test 0.25x, 0.5x, 1x, and 2x expected maximum rate under representative load.
- Run the actual behavior stack and the required soak duration.

#### `TW-7C` Documentation and final acceptance

- Document setup, configuration, calibration, operation, health, artifact
  interpretation, recovery, rate tests, and the legacy calibration comparison.
- Complete the production checklist with saved evidence.

Phase exit: calibration is verified for the rig, 2x rate acceptance and the
soak pass, and another lab member can operate and interpret the system.

## 14. Sol coordinator and Terra worker protocol

The orchestration model is intentional but kept compact.

### 14.1 Sol coordinator responsibilities

The Sol coordinator:

1. confirms prerequisites and user approval;
2. assigns one bounded `TW-*` activity with explicit editable files;
3. reviews every worker diff and reproduces required commands;
4. commits tests before authorizing implementation;
5. prevents overlapping edits and integrates completed packages;
6. updates Section 20 after every RED, GREEN, blocker, or phase transition.

Sol remains responsible for architecture, public-interface decisions, user
communication, repository-wide regression review, and final acceptance. A
Terra report is not proof until Sol checks the repository and evidence.

### 14.2 Terra worker responsibilities

Each Terra assignment states:

- package ID and objective;
- prerequisite commits/evidence;
- files allowed for inspection and files allowed for editing;
- contracts that must remain unchanged;
- RED tests or GREEN implementation scope;
- required focused/regression commands;
- performance/boundedness constraints;
- expected handoff evidence.

A RED assignment writes tests only and demonstrates the intended failure. After
Sol review and a tests-only commit, a GREEN assignment implements only enough
to satisfy the approved tests and specification, then refactors with tests
passing. The same Terra worker may perform both assignments, but only as two
separately authorized turns.

Terra workers stop and report a blocker on unexpected worktree changes,
unsupported library behavior, ambiguous requirements, or a needed public API
change. They do not invent hardware observations.

Parallel Terra tasks are allowed only when dependencies are complete and edit
ownership is disjoint. The ledger is edited by Sol to avoid conflicts.

## 15. Performance strategy

Correctness and observability precede optimization.

Initial choices:

- dedicated acquisition and logger interpreters;
- one joint A/B request;
- batch event reads;
- integer transition accumulation;
- explicit-width shared fields;
- publication after batches/heartbeat deadlines rather than per-edge public
  object creation;
- bounded queues, buffers, rings, and histograms;
- buffered fixed-rate state logging;
- no normal raw-edge disk stream.

Measure:

- decoded transitions per second;
- latest/maximum/aggregate processing lag;
- sequence continuity and exact displacement;
- publication skips and reader retry behavior;
- logger late/missed samples;
- CPU and memory by process;
- kernel buffer headroom and backlog;
- artifact storage MB/hour.

If acceptance fails, tune in this order:

1. remove blocking work and unnecessary allocation;
2. improve batch draining;
3. size the kernel buffer and read batch from evidence;
4. reduce publication frequency/cost without hiding state;
5. improve logger buffering;
6. profile again;
7. request approval before affinity, priority changes, C extensions, Cython, or
   another major optimization.

## 16. Calibration and hardware acceptance

Framework development may proceed with `calibration_verified=false`. Every
startup and recording must make that state prominent.

After the framework is runnable, the user/lab must:

1. record encoder model, output type/voltage, external pulls, wiring, and roller
   dimensions;
2. move through a measured distance over multiple rotations;
3. calculate `mm_per_encoder_cycle` from decoded cycles/transitions;
4. repeat in both directions and assess slip/asymmetry;
5. set `locomotion_sign` from animal-forward motion;
6. record rig, date, method, operator, and result;
7. set `calibration_verified=true` only after review;
8. measure maximum plausible speed and calculate expected transition rate.

Production rate acceptance uses an independent deterministic quadrature source.
At 2x expected maximum rate under representative behavior load:

```text
generated signed transitions == decoded signed transitions
generated absolute total      == decoded absolute total
global sequence gaps          == 0
unexplained per-line gaps      == 0
event inconsistencies         == 0
integrity_valid               == true
```

The soak duration is `max(2 hours, 1.5 * longest expected session)` after the
lab records the expected session length.

## 17. Documentation deliverables

Before production acceptance, documentation must cover:

- Pi 5/Trixie package installation and verification;
- wiring and electrical safety;
- YAML fields, units, precedence, and examples;
- modern and legacy calibration values;
- the mandatory physical calibration procedure;
- `box.treadmill` task-facing examples;
- health, integrity, and failure-policy interpretation;
- artifact schemas and timebase alignment;
- rate-sweep, full-stack, and soak procedures;
- interrupted recording and logger/acquisition failure interpretation;
- explicit statement that Pi 4B is not yet supported.

## 18. Risks and deferred facts

These facts do not block pure-software phases but do block production
acceptance when indicated:

| Item | When required |
|---|---|
| Exact Trixie gpiod package/API/version | Before GPIO adapter implementation |
| RP1 gpiochip metadata and line permissions | Before GPIO adapter implementation |
| Encoder voltage/output stage and external pulls | Before physical connection/acceptance |
| Verified calibration and locomotion sign | Before production acceptance |
| Maximum plausible treadmill speed/rate | Before buffer acceptance and rate sweep |
| Longest expected session | Before final soak |
| Heartbeat, lag, startup, and shutdown defaults | Before production acceptance, from Pi 5 measurements |

Deferred work:

- Raspberry Pi 4B support;
- Bookworm or older Python support;
- task-specific movement thresholds/filtering;
- automatic task pause/resume wiring;
- alternative storage formats;
- low-level optimization without profiling evidence.

## 19. Production acceptance checklist

- [ ] Pi 5/Trixie/Python 3.13 target and official gpiod v2 API verified.
- [ ] BCM13/16 resolve to the RP1 header controller without conflict.
- [ ] Electrical interface is safe for Pi GPIO.
- [ ] Physical calibration and locomotion sign are recorded for the rig.
- [ ] Both channels and both edge types are observed.
- [ ] Kernel timestamps drive speed.
- [ ] Pure decoder and failure-matrix tests pass exactly.
- [ ] Sequence loss and ambiguous trajectory loss latch integrity false.
- [ ] Worker death cannot appear as stationary healthy data.
- [ ] Behavior/logger readers cannot block acquisition.
- [ ] Disabled and freely-moving profile behavior is correct.
- [ ] Shared recording ownership starts/zeros/finalizes exactly once.
- [ ] TSV/JSON artifacts are versioned, bounded, incremental, and interpretable.
- [ ] Millions-event synthetic tests are exact with bounded memory.
- [ ] Full process stress is exact under representative load.
- [ ] External hardware source passes at 2x expected transition rate.
- [ ] Direction, reversal, start/stop, and zero behavior pass physically.
- [ ] Full-stack soak passes with stable resources and no loss/backlog.
- [ ] Documentation is usable by another lab member.

## 20. Living execution ledger

Allowed statuses are `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, and `COMPLETE`.
Sol updates the ledger before assignment and after every meaningful transition.

### 20.1 Phase summary

| Phase | Status | Evidence | Next gate |
|---|---|---|---|
| 0 - Pi 5 environment | `NOT STARTED` | None | Plan approval and target access |
| 1 - Pure core | `NOT STARTED` | None | Phase 0 API facts available where relevant |
| 2 - GPIO backend | `NOT STARTED` | None | `TW-0A/0B` and Phase 1 contracts complete |
| 3 - Processes/facade | `NOT STARTED` | None | Decoder and shared-state prerequisites complete |
| 4 - Recording | `NOT STARTED` | None | Public state/process contracts stable |
| 5 - BehavBox integration | `NOT STARTED` | None | Facade and logger complete |
| 6 - Stress | `NOT STARTED` | None | Integrated non-hardware system complete |
| 7 - Hardware acceptance/docs | `NOT STARTED` | None | Stress accepted and hardware available |

### 20.2 Work-package record template

```markdown
#### TW-<id>: <name>

- Status:
- Sol coordinator/date/chat:
- Terra worker/assignment:
- Starting branch and commit:
- Prerequisites checked:
- Editable files:
- Tests and RED command/result:
- Tests-only commit:
- Implementation files:
- GREEN and regression command/result:
- Performance/hardware evidence:
- Implementation commit:
- Decisions, deviations, or blockers:
- Working-tree state:
- Exact next action:
```

Do not record "tests pass" without the command and summarized result. Do not
record a package complete without commit IDs or an explicit statement that the
work remains uncommitted. Hardware evidence identifies the Pi/rig, wiring,
generator, configuration, duration, software commit, and artifact paths.

### 20.3 Current handoff

- Documentation rewrite only; no treadmill runtime implementation has started.
- Pi 5/Trixie/Python 3.13 is the sole initial target.
- `box.treadmill` is the approved public facade.
- BCM13/16 remain manifest-owned.
- TSV/JSON artifacts and modern nominal calibration are approved.
- Sol/Terra execution is intentional and defined in Section 14.
- Raspberry Pi 4B is explicitly deferred.
- Exact next action: user reviews and approves this rewritten implementation
  plan before Sol assigns `TW-0A` or any tests/implementation activity.
