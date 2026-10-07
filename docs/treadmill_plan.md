# Treadmill Acquisition: Pi 5 / Trixie Implementation Plan

## 1. Purpose, authority, and status

This document sequences implementation of `docs/treadmill_spec.md` in the
active `RPi4_behavior_boxes_hardware` codebase. The specification owns detailed
requirements, data contracts, tests, and acceptance criteria. This plan owns
work order, repository boundaries, Sol/Terra assignments, and evidence gates.

If the documents differ, the specification is authoritative.

Initial target:

- Raspberry Pi 5;
- 64-bit Raspberry Pi OS / Debian 13 Trixie;
- system Python 3.13;
- official libgpiod v2 Python binding.

Raspberry Pi 4B is deferred until the Pi 5 implementation has passed production
acceptance.

Status as of 2026-10-07:

| Item | State |
|---|---|
| Specification and plan documentation | Revised after implementation-readiness review |
| Runtime implementation | Not started and not authorized by this document |
| Rewrite tests | Not started |
| Dependency/provisioning changes | Not started |
| Hardware calibration and acceptance | Not started |

Documentation approval is not implementation approval. No Sol coordinator may
assign a `TW-*` implementation activity until the user gives a separate,
explicit instruction to begin implementation.

### 1.1 Resume procedure

Before any future work, the Sol coordinator must:

1. read `AGENTS.md`, `docs/SoftwareDesign.md`, `docs/treadmill_spec.md`, and this
   plan in full;
2. inspect `git status` and preserve unrelated changes;
3. inspect the current files and callers in the assigned package;
4. confirm prerequisite commits/evidence and explicit user authorization;
5. update Section 11 before assigning work;
6. stop for approval if a requirement, dependency, public API, or package
   boundary must change.

## 2. Fixed decisions

The first implementation uses these approved boundaries:

1. `session_info["treadmill"]` is authoritative.
2. The public behavior API is `box.treadmill`; the old
   `box.treadmill_encoder` API is retired.
3. BCM13 and BCM16 remain owned by the head-fixed manifest and are not copied
   into treadmill YAML.
4. Enabled freely-moving treadmill configuration is invalid because those pins
   are poke inputs in that profile.
5. Treadmill acquisition uses libgpiod v2 with both edges, kernel monotonic
   timestamps, and sequence numbers. There is no fallback decoder.
6. The modern nominal calibration begins explicitly unverified. Legacy
   calibration is retained only as migration documentation.
7. Readable defaults live in
   `box_runtime/input/treadmill/defaults.yaml`; documented session overrides
   live under `session_info["treadmill_config"]`.
8. Acquisition and fixed-rate logging use separate processes. The parent facade
   owns both; logger lifecycle is not routed through the acquisition worker.
9. Acquisition controls are limited to `ZERO` and `STOP`. Status comes from
   coherent shared state and heartbeat. `ZERO` uses idempotent command IDs,
   worker deadlines, and shared-state reconciliation rather than assuming a
   channel acknowledgement is atomic with state publication.
10. One parent-side `InputService` `RLock` and one close-requested flag
    serialize facade reads, public zero, shared-recorder ownership transitions,
    logger/origin work, final logger stop, and close. Close sets the flag before
    acquiring the lock; queued operations recheck it after acquisition and exit
    without touching resources, while an operation already holding the lock
    finishes within its existing bounded deadline. Close holds the lock through
    recorder/treadmill cleanup and attempts every cleanup step before reporting
    accumulated errors. Public zero is rejected for the entire shared
    recording; the private origin zero occurs after logger readiness and before
    the first sample.
11. Recording uses the TSV/JSON artifacts defined in specification Section 16.
12. Archived and external legacy treadmill code remains untouched and
    unimported.
13. The first implementation has no runtime mock treadmill backend. Synthetic
    edge sources are test/diagnostic inputs only; off-Pi and force-mock
    hardware-stress sessions disable treadmill.
14. Hardware-stress mode is selected once before `gpio_backend` conditionally
    imports real/mock GPIO classes. Force-mock parsing is strict; Pi 5 defaults
    to real mode and requires treadmill acquisition plus its continuous logger;
    unsupported or unknown ARM hardware fails unless mock mode was validly
    forced. The mock server starts only in mock mode.
15. A real Pi 5 stress run stops and returns nonzero for every treadmill
    degradation except the explicit calibration-only bring-up warning, and it
    passes only after facade-lifetime retained-code/artifact validation and
    mandatory cleanup. Mock hardware acceptance remains `not_applicable` even
    when a separately reported cleanup failure makes the process nonzero.
16. Sol coordinates bounded Terra worker assignments using Section 8.

## 3. Current codebase and intended delta

The active integration points are:

```text
box_runtime/input/service.py
box_runtime/behavior/behavbox.py
box_runtime/io_manifest.py
box_runtime/io_recording.py
box_runtime/behavior/gpio_backend.py
sample_tasks/head_fixed_hardware_stress/run.py
sample_tasks/head_fixed_hardware_stress/session_config.py
sample_tasks/head_fixed_hardware_stress/task.py
scripts/run_head_fixed_hardware_stress_mode.py
environment/rpi5_trixie_manifest.json
environment/rpi5_trixie.py
environment/rpi5_trixie_verifier.py
deploy/ansible/pi5_trixie.yml
```

Current treadmill behavior:

- every head-fixed `InputService` creates `gpiozero.RotaryEncoder`, even when
  the treadmill flag is false;
- a thread samples `.steps`, normally at 30 Hz;
- speed is written to the old two-column `treadmill_speed.tsv`;
- there is no edge history, sequence-loss detection, heartbeat, integrity
  latch, or physical position API.

Required integration outcome:

- disabled head-fixed sessions do not claim BCM13/16, no disabled session
  creates treadmill resources/artifacts, and freely-moving poke ownership is
  unchanged;
- enabled head-fixed sessions expose `box.treadmill`;
- off-Pi/forced-mock hardware-stress sessions remain runnable with treadmill
  disabled, while real Pi 5 stress sessions fail if acquisition or its logger
  is unavailable;
- both stress entrypoints consume the same immutable import-time backend mode,
  strictly validate force-mock/platform identity, never mutate force-mock after
  hardware imports, and reject unsupported/unknown ARM hardware instead of
  silently selecting a different path;
- enabled freely-moving sessions fail before BCM13/16 claims;
- acquisition begins during `prepare_session()`;
- first-owner/final-owner recording semantics remain unchanged;
- recording delegates to the treadmill facade instead of sampling gpiozero;
- acquisition remains live after recording stops and closes with
  `InputService`;
- real hardware-stress runtime failure stops the task, both launchers always
  close constructed runtime resources, and exit zero requires final treadmill
  health/artifact acceptance;
- lick, poke, trigger, user GPIO, output, camera, audio, and general recording
  behavior is not redesigned.

Existing tests which expect `treadmill_encoder` while `treadmill=false` describe
the behavior being replaced. All unrelated tests remain regression gates.

## 4. Minimal architecture and package layout

```text
Manifest BCM13/BCM16
        |
        v
libgpiod v2 joint request on Pi 5 RP1
        |
        v
Acquisition process
(batch drain -> x4 decode -> health/integrity -> shared state)
        |                                      |
        v                                      v
box.treadmill facade                    Logger process while recording
                                        TSV/JSON artifacts
```

The acquisition process is the only canonical decoder-state writer. Behavior
and logger readers cannot block its edge-draining loop.

Create modules only when their tests require them:

```text
box_runtime/input/treadmill/
    __init__.py
    defaults.yaml
    config.py
    decoder.py
    gpio_backend.py
    state.py
    acquisition.py
    recording.py
    runtime.py
```

Focused tests live under `tests/treadmill/`.

### 4.1 Module ownership

| Module | Cohesive responsibility |
|---|---|
| `config.py` | Load/merge/validate readable settings; no GPIO, files, or processes |
| `decoder.py` | Generic edge, x4 state, speed/zero/integrity rules, decoder counters, and recent decoder events |
| `gpio_backend.py` | Enabled-only verified gpiod API boundary, RP1 resolution, line ownership, synchronization, and ordered batch adaptation |
| `state.py` | Immutable public state plus the treadmill-specific coherent double-slot publication/reading implementation |
| `acquisition.py` | Spawn-safe worker, heartbeat, lag/rate diagnostics, bounded diagnostic emission, idempotent `ZERO`/bounded `STOP`, and orderly GPIO cleanup |
| `recording.py` | Logger process, deadline scheduling, artifact writers, logger diagnostics, summaries, and fallback failure record |
| `runtime.py` | Composition root and public facade; owns child lifecycle and effective health |
| `__init__.py` | Intentional public exports only |

Keep the four internal bounded-operation constants with the module that owns
the operation (`state.py`, `gpio_backend.py`, or `runtime.py`) and pass shorter
values explicitly in tests. Do not add a general constants/settings module.

Keep data and diagnostics beside the component that interprets them. Do not add
a catch-all diagnostics/models/helpers module. Use deterministic module-level
functions where lifecycle ownership is unnecessary, dataclasses for typed
records, and one narrow edge-source `Protocol` only because production and test
sources both need the boundary.

### 4.2 Concurrency decisions

- The acquisition process owns the gpiod request and decoder.
- The logger process reads copied state and owns all treadmill artifact handles.
- The facade starts/stops the logger directly.
- A treadmill-specific double-slot shared state prevents a descheduled reader
  from holding the only writer lock. The writer never blocks: if both slots are
  unavailable, it diagnoses and skips that publication while retaining
  canonical internal state.
- A zero is applied only after reserving a publication slot. The publication
  carries its command/result identity, so a lost direct acknowledgement is
  reconciled without reapplying the command.
- The worker retains only the in-flight and most recently resolved zero result;
  older IDs are rejected stale, keeping idempotency state bounded.
- One parent-process `RLock`, owned by `InputService` and injected into the
  facade, serializes `snapshot()`, `health_report()`, `require_healthy()`, public
  zero, shared-recorder ownership changes, logger/origin work, and close. Each
  external operation checks the close flag before and after lock acquisition.
  Close sets the flag first and holds the lock through recorder cleanup, child
  shutdown, final-state capture, and IPC release. This prevents teardown from
  racing parent readers without another concurrency protocol.
- The logger is the diagnostic channel's only live consumer. The worker's
  retained observed-health bitset supplies preflight and acceptance state, so
  health reads never drain diagnostic records. A stopped-worker startup-failure
  path and a confirmed-dead logger-failure path may each make one bounded
  fallback drain; logger failure already invalidates acceptance, so no
  exact-once recovery protocol is required.
- Effective snapshots apply motion timeout and heartbeat timeout at read time.
  The facade and logger use the same pure materialization function.
- Do not generalize these mechanisms for unrelated subsystems.

## 5. Integration scope

### 5.1 `InputService`

The change remains localized to the existing InputService/BehavBox recording
integration and the new treadmill package. The close gate also applies when
treadmill is disabled because those sessions use the same shared recorder;
other recording semantics are unchanged. Expected work:

1. remove the active `RotaryEncoder` dependency and old sampling fields;
2. keep one `self.treadmill` facade reference;
3. construct/start it only for enabled head-fixed sessions using manifest pins;
4. assign `owner.treadmill`, or `None` when disabled;
5. reject enabled freely-moving sessions before BCM13/16 can be claimed as
   treadmill or poke inputs;
6. locally unwind partially constructed inputs/treadmill resources before a
   constructor/startup error escapes;
7. expose a cheap warning-only health poll used by `BehavBox.poll_runtime()`;
8. own/inject the lifecycle `RLock`, close-requested flag, and authoritative
   recording-state callback; perform shared-recorder ownership and facade
   logger/origin work under that guard;
9. expose the immutable effective configuration for session finalization;
10. reject external recording transitions and facade reads after close is
    requested, and serialize exception-safe shared-recorder plus facade/IPC
    cleanup under the guard;
11. delete the old sampler thread and direct treadmill TSV writer.

The prior 40-70 changed-line estimate is directional only. Correct localized
behavior and unchanged non-treadmill paths are the acceptance criteria.

### 5.2 `BehavBox`, manifest, and recorder

BehavBox needs only targeted integration:

- initialize `self.treadmill = None`;
- retain current `InputService` construction and cleanup ordering;
- delegate shared-recording start/stop through the corresponding `InputService`
  integration methods so that service can hold its lifecycle `RLock`;
- have coordinated InputService close hold that lock while it rejects new
  ownership transitions, finalizes the logger, closes/resets the shared
  recorder, and shuts down the facade; the existing later recorder close stays
  idempotent;
- attempt each recorder/treadmill cleanup action despite earlier failures,
  retain ordered cleanup errors, latch `SHUTDOWN_INCOMPLETE`, and let
  `BehavBox.close()` finish all unrelated subsystem cleanup before it reports
  an aggregate failure;
- roll back a newly opened shared recording after required
  treadmill logger/origin failure;
- make `SharedIoRecorder.start_recording()` restore prior owner flags and close
  partial handles if its own directory/artifact opening fails;
- expose the facade without adding automatic task pause/abort behavior;
- call the nonthrowing treadmill health poll from the existing runtime poll
  path before the prepared-state early return, without changing task state;
- add the explicit enabled/effective treadmill object to
  `BehavBox.finalize_session()` metadata.

The manifest remains the sole BCM13/16 authority. `SharedIoRecorder` ownership
semantics remain unchanged. The two rollback paths above are required and
tests-first; they restore ownership state and close handles but need not erase
the selected directory or failure evidence already written there.

### 5.3 Recording edge cases

Directory existence, artifact opening, and writability are checked during
facade recording start, after `SharedIoRecorder` selects/creates the directory.
Static config loading does not validate a future directory.

For `treadmill=false`, create no treadmill artifacts.

Update `sample_tasks/head_fixed_hardware_stress/run.py`,
`sample_tasks/head_fixed_hardware_stress/session_config.py`, and
`scripts/run_head_fixed_hardware_stress_mode.py`, with the existing
`box_runtime/behavior/gpio_backend.py` as the single process-wide real/mock
authority. Extend its existing import-time decision only as needed to expose
the immutable selected mode and four-way host classification before it
conditionally imports real/mock GPIO classes. Strictly parse the documented
true/false force-mock literals and reject invalid values. Both stress
entrypoints consume the same facts: known non-ARM or validly forced mock
sessions use `treadmill=false` and start the mock server; a real Pi 5 defaults
to `treadmill=true` with `treadmill_required=true` and
`continuous_logger_required=true` and does not start that server. An unforced
unsupported/unknown ARM host fails before output-directory creation, LightDM
changes, mock-server startup, or BehavBox construction. Do not let stress
session configuration re-query the mutable environment via
`is_raspberry_pi()`, and do not add a separate real-mode flag, a simulated
facade, or the mock `RotaryEncoder` exposure. This stress eligibility check
must not otherwise redefine existing non-stress GPIO support on an identified
non-Pi-5 Raspberry Pi. Preserve the display launcher's `--dry-run` as a
side-effect-free planning path before hardware imports or host eligibility; it
does not produce a hardware-acceptance result.

For enabled optional acquisition that failed during preparation, recording
creates and immediately finalizes header-only state, failed metadata,
diagnostics, and summary artifacts from an immutable serialized failure context
after acquisition IPC is gone, then leaves no logger process or open treadmill
handle. If the logger itself cannot create normal artifacts, the parent emits
an application-log error and may write only the clearly incomplete
`treadmill_failure.json` fallback.

Logger readiness is established before any recording-origin zero. Required
logger failure rolls back; optional logger failure leaves shared IO recording
active with visible failure/fallback evidence and performs no zero or treadmill
sampling. If the ready logger is followed by a non-applied origin zero, stop
and join it, immediately finalize/close the header-only failure artifacts, and
clear logger/sampling-active facade state before required rollback or optional
shared-recording continuation.

Update `sample_tasks/head_fixed_hardware_stress/task.py` with a task-specific
health/acceptance check rather than adding generic BehavBox task control. Real
Pi 5 stress permits only `READY` or calibration-only `DEGRADED`, stops as an
error on every other report, and validates the finalized normal treadmill
summary's retained `observed_health_codes` before success. Reuse the same
task-specific evaluator in the display launcher immediately before audio
preflight and again before task start. Both gates check active and
facade-lifetime retained codes: failure at the first gate skips all preflight
audio, while a failure observed at either gate prevents task start. Mock output
labels hardware acceptance `not_applicable`. Both launchers use
explicit `try`/`finally` cleanup after box construction, return nonzero for
interruption or any runtime, artifact, or BehavBox/treadmill cleanup failure,
retain the facade for its cached post-close health, and replace any provisional
`pending_cleanup` final-task status only after the bounded close attempt and
cached-health plus retained-observed-code evaluation. When possible, incomplete
cleanup replaces pending results rather than leaving provisional state. Real
cleanup failure makes hardware acceptance `failed`; mock hardware acceptance
remains `not_applicable`, while the separate `behavbox_cleanup` result becomes
`failed` and process exit is nonzero. The display launcher closes BehavBox before
display-service restoration. Keep the common `TaskRunner` API unchanged unless
implementation proves a separate general bug.

## 6. Environment and evidence prerequisites

### 6.1 Pi 5 target evidence

Before adapter code, record from the target Pi 5:

- OS release, architecture, Python, kernel, libgpiod, tools, and binding
  versions;
- whether the expected Trixie `python3-libgpiod` package provides the binding
  on the Raspberry Pi OS image;
- installed Python signatures/event fields, unsigned sequence width, and
  new-request sequence-number convention required by specification Sections 9
  and 11;
- RP1 gpiochip names, labels, paths, line counts, and BCM13/16 metadata;
- line permissions and absence of an active owner;
- actual commands for development tests, hardware tests, verifier execution,
  and production launch.

Create one tracked `docs/treadmill_validation.md` evidence index in `TW-0` and
update it through later packages. Large generated artifacts stay outside the
repository; the index records their paths or durable locations, relevant
identifiers, commands, dates, and software commits.

The test command must not accidentally hide apt-installed Pi modules. Verify a
uv-managed environment based on `/usr/bin/python3` with system site packages,
or document another tested `uv run` approach. Production continues through the
existing system-Python/apt provisioning path; this project does not introduce a
repository-wide packaging migration.

### 6.2 Provisional runtime values

The same initial target work selects conservative positive provisional values
for:

- heartbeat interval and failure timeout;
- startup and shutdown timeout;
- processing-lag warning threshold;
- internal coherent-state copy, control-result, logger-start, and initial-sync
  quiet-period constants from specification Section 7.3.

The actual logger does not exist in `TW-0`, so that package must not build a
throwaway logger benchmark. Kernel buffer 8192, read batch 256, diagnostic ring
8192, diagnostic-event queue 256, and logger rate 200 Hz remain implementation
candidates. `TW-6` compares the real logger at 50, 100, and 200 Hz under
representative load and selects the lowest rate meeting documented
behavioral/offline needs. Later physical
maximum-rate measurements must justify the final buffer and diagnostic-history
sizes.

The four internal operation constants selected in `TW-0` are conservative
provisional values. Any change uses a tests-first bounded revision and is
recorded in the validation index.

Before that `TW-6` selection, the user/lab must state the maximum acceptable
fixed-state sample interval or equivalent offline-analysis requirement. If it
is still unknown, `TW-6` reports the measurements for all candidates and stops
for that choice instead of inventing a criterion.

`expected_max_transition_rate_hz` remains `null` until physical calibration and
maximum-speed measurement, then becomes mandatory for production acceptance.

### 6.3 Provisioning changes

After the package/API evidence exists:

- add the confirmed `python3-libgpiod` binding package and the separately
  packaged `gpiod` validation/troubleshooting tools to the Pi 5 Trixie
  manifest;
- add module and capability probes to the existing verifier;
- update the Ansible path only for verified installation/permission needs;
- write provisioning tests before these changes.

Do not add Pi 4 manifests or refactor provisioning for hypothetical reuse.

### 6.4 Dependencies and contracts

The only new imported runtime dependency is the target's official
`python3-libgpiod` binding. The separate `gpiod` package supplies command-line
validation/troubleshooting tools and is not invoked by production Python.
Reuse the already provisioned `python3-yaml`; use the standard library for
multiprocessing, dataclasses, TSV/JSON, timing, and bounded collections. Tests
use the existing pytest path. Do not add another GPIO, serialization, IPC, or
scientific package for this work without a separately reviewed need.

Every new function/method documents input and return types, physical units,
optional/error semantics, side effects, and lifecycle constraints. Array-shape
contracts are not applicable unless a later approved utility introduces array
inputs.

## 7. Eight work packages and gates

Detailed required tests come from specification Section 18. Each software
package uses separate tests-only RED and implementation GREEN assignments and
commits. A RED result must fail for the intended missing behavior rather than a
broken test environment.

### `TW-0`: Pi 5 environment, timing, and provisioning

Scope:

- collect plan Section 6 target/API evidence without production treadmill code;
- select provisional runtime/configuration deadlines and the four internal
  bounded-operation constants, and record, but do not benchmark, the initial
  logger-rate candidate;
- add tests, then update Pi 5 provisioning/verifier files;
- verify the resulting environment on the target Pi.

Exit gate: official v2 capabilities, RP1 mapping, test/production commands, and
required provisional values are documented and reproducible.

### `TW-1`: Configuration, state, and pure decoder

Scope:

- tests for specification Sections 7, 8, 10, and decoder-owned Section 11
  behavior, plus the immutable state contract in Section 13.1 and pure
  timeout/health materialization rules from Section 14;
- implement defaults/config validation, public optional-value semantics,
  effective direction/speed timeout, x4 decoding, zero offsets, the normative
  failure matrix and causal stable-code ordering, unsigned 32-bit modular
  sequence comparison, decoder counters, aligned 100 ms lifetime rate
  measurement, fixed lag histogram, and bounded recent decoder history;
- no GPIO or multiprocessing dependency.

Exit gate: exhaustive and seeded randomized traces are exact; initial/timeout
state is unambiguous; memory is bounded; pure tests pass off-Pi.

### `TW-2`: Pi 5 GPIO backend

Scope:

- fake-binding and synthetic-inventory tests first;
- off-Pi treadmill-package imports remain independent of `gpiod`;
- no off-Pi or forced-mock enabled session falls back to a runtime simulated
  encoder;
- verified v2 capability checks, dynamic RP1 resolution, joint line request,
  initial synchronization, ordered batching, and partial-startup cleanup;
- marked Pi smoke test after fake tests pass.

Exit gate: ambiguity/ownership failures close safely, and target hardware
observes both edge types, kernel timestamps, and sequence fields on BCM13/16.

### `TW-3`: Shared state, acquisition, and public facade

Scope:

- concurrency/lifecycle tests first;
- treadmill-specific double-slot state, shared effective-state materialization,
  treadmill-local spawn context, acquisition process, heartbeat, lag,
  idempotent command-ID/deadline/reconciliation `ZERO`, bounded `STOP`, and
  facade lifecycle without changing the application's global multiprocessing
  method;
- bounded zero idempotency storage plus facade use of an injected
  lifecycle `RLock`, close flag, and state callbacks, with deterministic
  facade-level zero/recording/close serialization tests;
- bounded facade first-observation health-code accumulation for parent/logger
  statuses;
- fixed shared acquisition-code bitset/first-observation times so cleared
  worker conditions do not depend on diagnostic delivery timing;
- one parent lifecycle lock/close-flag protocol, cached `CLOSING` health, and
  immutable final shutdown fields;
- health reads never consume the diagnostic channel; logger-consumer activation
  is completed in `TW-4`;
- process/facade behavior from specification Sections 13.2 and 14, excluding
  logger-specific and BehavBox integration cases owned by later packages;
- explicit spawn, worker-death, and stalled-reader tests.

Exit gate: readers see no torn state, cannot block acquisition, and worker
death cannot appear as healthy stationary data. Start, snapshot, zero, and
close operations are bounded, direct-ack loss has an unambiguous reconciled
outcome whenever state is readable, shared-recording state remains
authoritative after logger failure, and close is idempotent without racing a
parent read, zero, or recording transition against recorder cleanup or released
IPC.

### `TW-4`: Logger and recording artifacts

Scope:

- deterministic scheduler/schema/failure tests first;
- logger process and all specification Section 16 artifacts;
- active-logger contributions to effective health from specification Section
  14;
- header-only failed-acquisition recording, heartbeat-aware health samples,
  queued pre-recording diagnostics, bounded flush, best-effort handled-failure
  ring dump, fallback failure record, lifetime rate field, and recording-window
  lag histogram/approximate percentiles;
- bounded logger cleanup and immediate header-only artifact finalization
  whenever no recording origin can be established, with null recording-window
  duration/end fields and a distinct artifact-finalization timestamp;
- no-acquisition-IPC header-only logging from immutable failure context, exact
  state-row count, normal monotonic origin-to-stop duration, and ordered
  retained health codes from startup through final diagnostic drain;
- facade-lifetime retained-code scope with no recording baseline, so later
  recordings conservatively repeat earlier codes until a new facade is made;
  the existing logger-finalization result returns its bounded merged
  first-observation accumulator to the facade;
- logger-only live diagnostic consumption, including records queued before
  recording, worker-channel drop reporting, and one bounded best-effort drain
  after confirmed worker/logger death for failure context;

Exit gate: artifacts are versioned, bounded, incremental, and distinguish
disabled, failed, degraded, and successful acquisition. Logger failure cannot
block acquisition; facade recording start/stop is bounded, and an
origin not confirmed `applied` never produces samples. Header-only output
works after acquisition IPC removal, row counts are exact, and duration never
mislabels failed/incomplete coverage. A transient code cannot disappear from
the final acceptance input merely because final health recovered. Logger
activation has no competing parent diagnostic consumer. A failed logger may
lose or duplicate fallback diagnostics, but its artifacts are already
ineligible for hardware acceptance.

### `TW-5`: Modern BehavBox integration

Scope:

- authoritative flag/profile/ownership tests first;
- disabled BehavBox sessions remain independent of `gpiod`;
- subprocess import-order/strict-mode/unknown-platform tests for both
  hardware-stress entrypoints before in-process integration tests;
- localized `InputService` replacement, `box.treadmill`, shared-recording
  delegation and mandatory rollback at both specified failure boundaries,
  rejection of retired enabled-session treadmill keys, and deletion of old
  sampler/encoder exposure;
- `InputService` ownership/injection of the lifecycle `RLock`, close flag, and
  state callbacks plus deterministic first-owner/final-owner, facade-read,
  zero, and recording-transition versus close tests through BehavBox;
- exception-safe recorder/InputService/BehavBox cleanup tests which inject each
  recorder flush/close failure and verify all later cleanup plus the aggregate
  `SHUTDOWN_INCOMPLETE` result;
- one immutable hardware-stress mode across GPIO imports, session config, and
  conditional mock-server startup; prepared-state health polling; and explicit
  effective configuration in session metadata;
- required/optional preparation and shared-recording outcomes from
  specification Sections 14 and 15;
- task-specific real-stress health/summary acceptance, nonzero exit semantics,
  active-plus-retained pre/post-audio-preflight health gates, and unconditional
  launcher cleanup across injected lifecycle failures;
- final-state separation of treadmill hardware acceptance from BehavBox cleanup
  status in real and mock modes;
- complete non-hardware regression suite.

Exit gate: disabled sessions create no treadmill resources, freely-moving poke
claims remain intact, shared owners start/zero/finalize exactly once,
acquisition outlives recording, mock stress runs omit treadmill, real Pi 5
stress runs require it and its logger, unsupported Pi models fail unless mock
was forced, unknown ARM and invalid override inputs fail closed, metadata
exposes effective settings, zero cannot race recording or close transitions,
new recording transitions cannot pass a close request or race recorder cleanup,
recorder close errors cannot skip child/IPC/unrelated-resource cleanup,
readers cannot race IPC release, admitted lifecycle operations finish before
close acquires the lock, mock cleanup failure remains hardware
`not_applicable` but exits nonzero, stress failures cannot exit zero or leak
children, and unrelated runtime behavior is unchanged.

### `TW-6`: Synthetic and full-process validation

Scope:

- millions-event seeded validation;
- real acquisition/shared-state/logger processes under representative CPU,
  disk, camera-like, GUI-like, console, and scheduling load;
- worker/logger failure injection and saved resource/storage metrics;
- real-stress acquisition/logger/finalization failure injection with verified
  error stop, bounded cleanup, final-summary rejection of persistent and
  cleared transient codes, preflight rejection from retained codes, and
  nonzero exit;
- actual logger comparison at 50, 100, and 200 Hz with selection of the lowest
  rate that meets the documented timing and offline-analysis need;
- profile before any optimization.

If the selected rate differs from the implemented candidate, reopen `TW-1` for
the tests-first default/config change, then rerun affected logger, integration,
and stress gates. Apply the same feedback rule in `TW-7` for measured
buffer/ring settings; rig-specific calibration and maximum rate remain explicit
rig/session configuration rather than an unjustified global default.

Exit gate: generated and decoded totals are exact, integrity is valid, gaps are
zero unless deliberately injected, memory remains bounded, and artifacts are
interpretable.

### `TW-7`: Calibration, hardware acceptance, and documentation

Scope:

- tested calibration/sign/rate utility;
- user/lab physical calibration and electrical record;
- expected maximum transition rate and accepted buffer/ring/logger settings;
- external generator sweep through 2x expected rate;
- actual behavior stack and required soak;
- setup, operation, health, artifact, recovery, and migration documentation;
- completed `docs/treadmill_validation.md` evidence index.

Exit gate: every specification Section 21 checklist item has saved evidence.
Pi 4 support remains deferred.

If stress, calibration, or hardware acceptance exposes a software defect or an
invalid earlier assumption, reopen the responsible `TW-*` package, add a
tests-only RED commit, make the bounded correction, and repeat every affected
downstream gate. Acceptance work must not patch runtime code ad hoc.

## 8. Sol coordinator and Terra worker protocol

The Sol/Terra model is intentional.

Sol:

1. confirms user authorization, prerequisites, and editable files;
2. assigns one bounded RED or GREEN activity;
3. reviews diffs and reproduces commands;
4. commits tests before authorizing implementation;
5. prevents overlapping edits and updates Section 11;
6. owns architecture, integration, user communication, and package gates.

Each Terra assignment states its package, objective, prerequisite commit,
editable files, preserved contracts, required commands, and handoff evidence.
A RED assignment changes tests only. After Sol review and a tests-only commit, a
separate GREEN assignment implements the approved behavior. The same worker may
perform both turns.

Terra stops on unexpected worktree changes, unsupported APIs, ambiguous
requirements, or a needed public-interface change. Hardware observations must
come from saved target evidence. Parallel workers are allowed only for complete
prerequisites and disjoint edit ownership. Sol alone edits the ledger.

## 9. Performance and acceptance discipline

Correctness and observability precede optimization. Preserve these invariants:

- acquisition performs no normal disk, network, GUI, console, or behavior work;
- all queues, buffers, histories, and control processing are bounded;
- sequence continuity and exact generated/decoded displacement determine
  correctness;
- behavior and logger readers cannot block acquisition;
- logger lateness is measured rather than hidden by duplicate catch-up rows;
- CPU, memory, lag, publication skips, backlog, and MB/hour are recorded.

Tune only after representative profiling, in this order:

1. remove blocking work and unnecessary allocation;
2. improve batch draining and measured buffer sizes;
3. reduce publication/logger cost without hiding state;
4. profile again;
5. request approval before affinity, priority changes, C/Cython/Numba, or
   another major optimization.

Production acceptance requires specification Sections 19 and 21, including
verified calibration/sign, independent 2x-rate generation, full-stack behavior,
and a soak of `max(2 hours, 1.5 * longest expected session)`.

## 10. Deferred scope and known required facts

Deferred:

- Raspberry Pi 4B, Bookworm, and older Python support;
- task-specific movement thresholds or filtering;
- segmented artifacts for intentional mid-recording zero operations;
- automatic task pause/resume/abort wiring and unused policy states;
- alternative persistence formats;
- reusable concurrency frameworks;
- unmeasured low-level optimization.

Required before the corresponding gate:

| Fact | Required before |
|---|---|
| Exact binding/tools packages, API/RP1 mapping, and permissions | `TW-2` implementation |
| Provisional heartbeat/lag/startup/shutdown values and internal bounded-operation constants | `TW-1` config implementation |
| Encoder electrical facts | Physical connection/acceptance |
| Verified calibration and locomotion sign | Production acceptance |
| Maximum plausible speed/rate | Final buffer sizing and rate sweep |
| Maximum acceptable fixed-state sample interval/offline need | `TW-6` logger-rate selection |
| Longest expected session | Final soak |

## 11. Living execution ledger

Allowed states are `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, and `COMPLETE`.
Sol updates this section before assignment and after every RED, GREEN, blocker,
or gate decision.

### 11.1 Package summary

| Package | Status | Evidence/commit | Next gate |
|---|---|---|---|
| `TW-0` Environment/provisioning | `NOT STARTED` | None | Separate user authorization |
| `TW-1` Pure core | `NOT STARTED` | None | `TW-0` provisional config evidence |
| `TW-2` GPIO backend | `NOT STARTED` | None | `TW-0` API evidence and `TW-1` event contract |
| `TW-3` Processes/facade | `NOT STARTED` | None | `TW-1` core and required `TW-2` fake boundary |
| `TW-4` Recording | `NOT STARTED` | None | `TW-3` public/shared-state contract |
| `TW-5` Integration | `NOT STARTED` | None | `TW-3` and `TW-4` complete |
| `TW-6` Stress | `NOT STARTED` | None | Integrated non-hardware system complete |
| `TW-7` Hardware/docs | `NOT STARTED` | None | `TW-6` accepted and hardware available |

### 11.2 Work record template

```markdown
#### TW-<id>: <name>

- Status:
- Sol coordinator/date/chat:
- Terra worker/assignment:
- Starting branch and commit:
- Prerequisites and user authorization:
- Editable files:
- RED tests, command, result, and commit:
- GREEN files, command, result, and commit:
- Regression/performance/hardware evidence:
- Decisions, deviations, or blockers:
- Working-tree state and exact next action:
```

Do not record "tests pass" without the command and summarized result. Do not
mark a package complete without commit IDs or an explicit statement that work
remains uncommitted. Hardware evidence identifies the Pi/rig, wiring,
generator, configuration, duration, software commit, and artifact paths.

### 11.3 Current handoff

- Documentation only; no implementation is authorized or in progress.
- Pi 5/Trixie/Python 3.13 is the sole initial target.
- `box.treadmill`, manifest BCM13/16, TSV/JSON artifacts, separate acquisition
  and logger processes, double-slot state, and Sol/Terra execution are approved
  documentation decisions.
- Timing/internal deadline values, logger rate, buffer sizing, gpiod
  binding/tools API, calibration, and hardware facts require the evidence
  assigned above.
- Do not add reader-generation/lease machinery or a parent diagnostic handoff
  buffer unless an implementation test or hardware result demonstrates a
  concrete failure that the documented `RLock`/single-consumer model cannot
  handle.
- Exact next action: no implementation action until a separate user
  instruction; then begin the `TW-0` evidence/provisioning package.
