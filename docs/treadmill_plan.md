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

Status as of 2026-10-06:

| Item | State |
|---|---|
| Specification and plan documentation | Prepared for review |
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
   coherent shared state and heartbeat.
10. Recording uses the TSV/JSON artifacts defined in specification Section 16.
11. Archived and external legacy treadmill code remains untouched and
    unimported.
12. Sol coordinates bounded Terra worker assignments using Section 8.

## 3. Current codebase and intended delta

The active integration points are:

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
- enabled freely-moving sessions fail before BCM13/16 claims;
- acquisition begins during `prepare_session()`;
- first-owner/final-owner recording semantics remain unchanged;
- recording delegates to the treadmill facade instead of sampling gpiozero;
- acquisition remains live after recording stops and closes with
  `InputService`;
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
| `acquisition.py` | Spawn-safe worker, heartbeat, lag diagnostics, bounded diagnostic emission, `ZERO`/`STOP`, and orderly GPIO cleanup |
| `recording.py` | Logger process, deadline scheduling, artifact writers, logger diagnostics, summaries, and fallback failure record |
| `runtime.py` | Composition root and public facade; owns child lifecycle and effective health |
| `__init__.py` | Intentional public exports only |

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
- Effective snapshots apply motion timeout and heartbeat timeout at read time.
  The facade and logger use the same pure materialization function.
- Do not generalize these mechanisms for unrelated subsystems.

## 5. Integration scope

### 5.1 `InputService`

The final change remains localized to treadmill-specific code. Expected work:

1. remove the active `RotaryEncoder` dependency and old sampling fields;
2. keep one `self.treadmill` facade reference;
3. construct/start it only for enabled head-fixed sessions using manifest pins;
4. assign `owner.treadmill`, or `None` when disabled;
5. reject enabled freely-moving sessions before BCM13/16 can be claimed as
   treadmill or poke inputs;
6. locally unwind partially constructed inputs/treadmill resources before a
   constructor/startup error escapes;
7. expose a cheap warning-only health poll used by `BehavBox.poll_runtime()`;
8. delegate first-owner recording start, final-owner recording stop, and close;
9. delete the old sampler thread and direct treadmill TSV writer.

The prior 40-70 changed-line estimate is directional only. Correct localized
behavior and unchanged non-treadmill paths are the acceptance criteria.

### 5.2 `BehavBox`, manifest, and recorder

BehavBox needs only targeted integration:

- initialize `self.treadmill = None`;
- retain current `InputService` construction and cleanup ordering;
- ensure required logger-start failure rolls back a newly opened shared
  recording;
- expose the facade without adding automatic task pause/abort behavior;
- call the nonthrowing treadmill health poll from the existing runtime poll
  path without changing task state.

The manifest remains the sole BCM13/16 authority. `SharedIoRecorder` ownership
semantics remain unchanged. Add a recorder rollback mechanism only if the
tests demonstrate it is required for atomic failure after `started_now=True`.
Rollback restores ownership flags and closes handles; it need not erase the
selected directory or failure evidence already written there.

### 5.3 Recording edge cases

Directory existence, artifact opening, and writability are checked during
facade recording start, after `SharedIoRecorder` selects/creates the directory.
Static config loading does not validate a future directory.

For `treadmill=false`, create no treadmill artifacts.

For enabled optional acquisition that failed during preparation, recording
creates header-only state, failed metadata, and diagnostics artifacts and adds
the failed summary at finalization. If the logger itself cannot create normal
artifacts, the parent emits an application-log error and may write only the
clearly incomplete `treadmill_failure.json` fallback.

## 6. Environment and evidence prerequisites

### 6.1 Pi 5 target evidence

Before adapter code, record from the target Pi 5:

- OS release, architecture, Python, kernel, libgpiod, tools, and binding
  versions;
- whether the expected Trixie `python3-libgpiod` package provides the binding
  on the Raspberry Pi OS image;
- installed Python signatures/event fields and new-request sequence-number
  convention required by specification Section 9;
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
- processing-lag warning threshold.

The actual logger does not exist in `TW-0`, so that package must not build a
throwaway logger benchmark. Kernel buffer 8192, read batch 256, diagnostic ring
8192, diagnostic-event queue 256, and logger rate 200 Hz remain implementation
candidates. `TW-6` compares
the real logger at 50, 100, and 200 Hz under representative load and selects
the lowest rate meeting documented behavioral/offline needs. Later physical
maximum-rate measurements must justify the final buffer and diagnostic-history
sizes.

Before that `TW-6` selection, the user/lab must state the maximum acceptable
fixed-state sample interval or equivalent offline-analysis requirement. If it
is still unknown, `TW-6` reports the measurements for all candidates and stops
for that choice instead of inventing a criterion.

`expected_max_transition_rate_hz` remains `null` until physical calibration and
maximum-speed measurement, then becomes mandatory for production acceptance.

### 6.3 Provisioning changes

After the package/API evidence exists:

- add the confirmed binding package to the Pi 5 Trixie manifest;
- add module and capability probes to the existing verifier;
- update the Ansible path only for verified installation/permission needs;
- write provisioning tests before these changes.

Do not add Pi 4 manifests or refactor provisioning for hypothetical reuse.

### 6.4 Dependencies and contracts

The only new runtime dependency is the target's official `python3-libgpiod`
binding. Reuse the already provisioned `python3-yaml`; use the standard library
for multiprocessing, dataclasses, TSV/JSON, timing, and bounded collections.
Tests use the existing pytest path. Do not add another GPIO, serialization, IPC,
or scientific package for this work without a separately reviewed need.

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
- select provisional runtime values and record, but do not benchmark, the
  initial logger-rate candidate;
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
  effective direction/speed timeout, x4 decoding, zeroing, integrity matrix,
  decoder counters, and bounded recent decoder history;
- no GPIO or multiprocessing dependency.

Exit gate: exhaustive and seeded randomized traces are exact; initial/timeout
state is unambiguous; memory is bounded; pure tests pass off-Pi.

### `TW-2`: Pi 5 GPIO backend

Scope:

- fake-binding and synthetic-inventory tests first;
- off-Pi treadmill-package imports remain independent of `gpiod`;
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
  `ZERO`/`STOP`, and facade lifecycle without changing the application's global
  multiprocessing method;
- bounded pre-recording diagnostic retention and nonblocking diagnostic
  delivery/drop accounting;
- process/facade behavior from specification Sections 13.2 and 14, excluding
  logger-specific and BehavBox integration cases owned by later packages;
- explicit spawn, worker-death, and stalled-reader tests.

Exit gate: readers see no torn state, cannot block acquisition, and worker
death cannot appear as healthy stationary data. Start, snapshot, zero, and
close operations are bounded, and close is idempotent as specified.

### `TW-4`: Logger and recording artifacts

Scope:

- deterministic scheduler/schema/failure tests first;
- logger process and all specification Section 16 artifacts;
- active-logger contributions to effective health from specification Section
  14;
- header-only failed-acquisition recording, heartbeat-aware health samples,
  pre-recording diagnostic handoff, bounded flush, best-effort handled-failure
  ring dump, and fallback failure record.

Exit gate: artifacts are versioned, bounded, incremental, and distinguish
disabled, failed, degraded, and successful acquisition. Logger failure cannot
block acquisition; facade recording start/stop is bounded, and an
unacknowledged origin never produces samples.

### `TW-5`: Modern BehavBox integration

Scope:

- authoritative flag/profile/ownership tests first;
- disabled BehavBox sessions remain independent of `gpiod`;
- localized `InputService` replacement, `box.treadmill`, shared-recording
  delegation/rollback, rejection of retired enabled-session treadmill keys,
  and deletion of old sampler/encoder exposure;
- required/optional preparation and shared-recording outcomes from
  specification Sections 14 and 15;
- complete non-hardware regression suite.

Exit gate: disabled sessions create no treadmill resources, freely-moving poke
claims remain intact, shared owners start/zero/finalize exactly once,
acquisition outlives recording, and unrelated runtime behavior is unchanged.

### `TW-6`: Synthetic and full-process validation

Scope:

- millions-event seeded validation;
- real acquisition/shared-state/logger processes under representative CPU,
  disk, camera-like, GUI-like, console, and scheduling load;
- worker/logger failure injection and saved resource/storage metrics;
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
- automatic task pause/resume/abort wiring and unused policy states;
- alternative persistence formats;
- reusable concurrency frameworks;
- unmeasured low-level optimization.

Required before the corresponding gate:

| Fact | Required before |
|---|---|
| Exact gpiod package/API/RP1 mapping and permissions | `TW-2` implementation |
| Provisional heartbeat/lag/startup/shutdown values | `TW-1` config implementation |
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
- Timing values, logger rate, buffer sizing, gpiod API, calibration, and hardware
  facts require the evidence assigned above.
- Exact next action: user reviews the documentation. Any implementation requires
  a separate future instruction.
