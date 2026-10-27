# Treadmill implementation validation

This index records measured evidence, not production acceptance. Work is
authorized through TW-3; full-stack tests and production acceptance are later
gates. User bench results are recorded below; production acceptance is pending.

## TW-0 target evidence, 2026-10-26

- Starting local commit: `88ed91dc7d697d32a6a22c9f14f3bbf7cddd5913` (`dev2`).
- User-selected target: `pi@10.42.84.10`, hostname `raspberrypi`, Raspberry Pi 5
  Model B Rev 1.0, aarch64, Debian 13.4 Trixie.
- Kernel: `6.12.75+rpt-rpi-2712`, Debian build
  `1:6.12.75-1+rpt1 (2026-03-11)`; system Python 3.13.5.
- Apt binding package `python3-libgpiod` and separate CLI package `gpiod`:
  both `2.2.1-2+deb13u1`, Debian Trixie main arm64 origin.
- Python reports `gpiod.__version__ == "2.2.0"` at
  `/usr/lib/python3/dist-packages/gpiod/__init__.py`. Preserve this distinction
  from the apt package version. PyYAML 6.0.2 and pytest 8.3.5 are available.
- RP1: `/dev/gpiochip0`, label `pinctrl-rp1`, 54 lines. BCM13/16 have line
  names `GPIO13`/`GPIO16`, offsets 13/16, initially unused. `/dev/gpiochip4`
  aliases that same device; resolution must deduplicate aliases. Other chips
  are `gpiochip10` through `gpiochip13` with `gpio-brcmstb` labels.
- User `pi` belongs to `gpio`; device access is `root:gpio`, mode 0660.
  An input-only joint request succeeded without sudo, with both edges,
  pull-up, zero debounce, monotonic clock, and event buffer 8192. Both values
  were active, no edge was pending during the probe, and the request released.
  This is API/permission evidence, not proof of physical edge capture.

Installed Python source was inspected with `inspect.getsource` for
`request_lines`, `LineSettings`, `Chip`, `LineRequest`, and `EdgeEvent`.
Verified request keywords are `consumer`, `config`, and `event_buffer_size`;
`wait_edge_events(timeout)` accepts seconds and `read_edge_events(max_events)`
returns ordered events with `timestamp_ns`, `line_offset`, `event_type`,
`global_seqno`, and `line_seqno`. `get_values([a, b])` returns enum values;
`release()` ends ownership. No open request is passed across processes.

The [kernel event API](https://docs.kernel.org/userspace-api/gpio/gpio-v2-line-event-read.html)
documents ordered capture and overflow detection. The
[Pi 6.12 kernel source](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/drivers/gpio/gpiolib-cdev.c)
uses zero-initialized request/line counters, increments before reporting, and
unsigned 32-bit event sequence fields. New-request decoder baselines are zero;
the first event on each line has line sequence 1. Physical confirmation with
the external generator remains a TW-2 hardware gate.

## Provisional timing values

Ten spawn-to-ready pipe handshakes using the target system Python measured
36.54-51.20 ms. Fifty requested 1 ms sleeps measured 1.052-1.060 ms. These are
idle bring-up observations, not load benchmarks. The initial values below are
conservative development choices; later loaded measurements may revise them
through tests-first changes.

| Setting | Initial value |
|---|---:|
| `heartbeat_interval_s` | 0.05 |
| `heartbeat_failure_timeout_s` | 0.5 |
| `startup_timeout_s` | 5.0 |
| `shutdown_timeout_s` | 3.0 |
| `processing_lag_warning_ns` | 50000000 |
| `COHERENT_STATE_READ_TIMEOUT_S` | 0.02 |
| `CONTROL_RESULT_TIMEOUT_S` | 1.0 |
| `LOGGER_START_TIMEOUT_S` | 5.0 |
| `INITIAL_SYNC_QUIET_PERIOD_S` | 0.001 |

The quiet period requires a stationary interval during startup. Start the
receiver, wait for its ready message, then start the generator. Kernel buffer
8192, batch 256, ring 8192, diagnostic queue 256, and logger 200 Hz remain
unbenchmarked candidates. Physical calibration and maximum rate remain unknown.

## Reproduction commands and software gates

Local baseline (uv 0.10.6), 22 passed:

```bash
uv run --offline --with pytest --with pyyaml python -m pytest tests/test_rpi5_trixie_provisioning.py tests/test_treadmill_step_counter.py tests/test_treadmill_legacy_step_counter.py tests/test_treadmill_decoder_legacy.py -q
```

Verified Pi command preserves apt modules without creating another environment:

```bash
cd /home/pi/RPi4_behavior_boxes_hardware
/home/pi/.local/bin/uv run --offline --no-project --python /usr/bin/python3 python -c 'import sys, gpiod, pytest, yaml; print(sys.executable, gpiod.__version__, pytest.__version__, yaml.__version__)'
```

Result: `/usr/bin/python3 2.2.0 8.3.5 6.0.2`. Production uses
`/usr/bin/python3`; Pi tests use the same uv prefix followed by `python -m pytest`.
TW-0 RED commit `6131cd8`: 5 intended failures for missing manifest/probe
requirements, 9 passes. After the manifest update, the focused baseline command
reports 27 passes. No new provisioning mechanism was required: Ansible and the
verifier already consume the manifest. Packages were already present on target.

The new manifest was supplied over SSH stdin to the target's existing
`environment.rpi5_trixie_verifier.verify_host` with runtime checks and 15 s
per-probe limits. All imports, commands, and runtime probes passed
(`FAILURE_COUNT 0`). The target checkout was not overwritten. GPIO edge smoke,
ARM coherent-state tests, and standalone diagnostic checks are pending.

## TW-1 through TW-3 software evidence

Pure-core RED commits: `11d943c`, `efa740f`; GREEN: `cf50432`.
GPIO adapter RED: `1eaf754`; GREEN: `cf378fd`.
Process/standalone RED commits: `484a3d7`, `a7a34bd`, `91b8ef5`;
GREEN: `05cfd69`.

Local development tests use Python 3.12. They verify software contracts but are
not evidence of ARM shared-state behavior or real GPIO acquisition. This
focused command passed 206 tests:

```bash
uv run --offline --with pytest --with pyyaml --with flask --with numpy --with scipy --with colorama --with pillow python -m pytest tests/treadmill tests/test_rpi5_trixie_provisioning.py tests/test_treadmill_step_counter.py tests/test_treadmill_legacy_step_counter.py tests/test_treadmill_decoder_legacy.py tests/test_input_service.py tests/test_input_gpio_compat.py tests/test_mock_hardware.py -q
```

Ruff 0.15.4 formatting and static checks passed for the new treadmill package,
standalone entry point, and tests. All existing legacy counter/decoder files
and the old RotaryEncoder compatibility wrapper remain unchanged.

## Next Pi checks after pulling dev2

Deployment is by Git only: the user will pull `dev2` onto the target. No files
have been copied to the Pi by this work. The following commands are the next
checks; they have not yet been run on the target implementation.

From `/home/pi/RPi4_behavior_boxes_hardware`, first run the software tests on
ARM with its apt-installed Python modules:

```bash
uv run python -m pytest tests/treadmill tests/test_rpi5_trixie_provisioning.py -q
```

Plain `uv run` was verified on this Pi to select `/usr/bin/python` and import
the apt-installed gpiod 2.2.0 binding from
`/usr/lib/python3/dist-packages/gpiod`. The explicit interpreter/project flags
used in the earlier environment probe are unnecessary for these commands.

The software tests inject sources and do not claim GPIO. Next, with BCM13/16
available, run the production GPIO source for a short stationary check:

```bash
uv run debug/treadmill_counter.py --duration-s 5
```

Run until Ctrl-C instead:

```bash
uv run debug/treadmill_counter.py
```

For temporary A=BCM23 and B=BCM24 wiring, select pins on the command line:

```bash
uv run debug/treadmill_counter.py --a-pin 23 --b-pin 24
```

The same flags work with `--duration-s 5`. Each omitted channel uses its
head-fixed manifest default (currently A=13, B=16). These are BCM GPIO numbers,
not physical header numbers. Selections must be distinct integers in 0..27
and cannot use manifest-reserved pins such as GPIO9. Invalid selections fail
before runtime construction; the backend still rejects lines already owned.
The standalone override does not change BehavBox's manifest or treadmill YAML.
Remove any previous hardcoded `settings = replace(settings, a_bcm_pin=23,
b_bcm_pin=24)` line added to the counter locally; the CLI now supplies that
override. The ready message and failure context display the selected pins.

The command prints readiness, warns about unverified physical calibration,
then reports final edge/x4 counts, signed and positive/negative transitions,
reversals, sequence gaps, estimated missing events, inconsistencies, integrity,
rate, lag, publication/diagnostic drops, and retained health codes. Counts are
dimensionless x4 transitions, not full quadrature cycles. An ordinary Ctrl-C is
a normal stop; acquisition or cleanup failure returns nonzero. Run one counter
at a time because the old and new tools claim the same lines.

For external-generator testing, start the receiver first and wait for
readiness before starting an independently counted generator burst. Stop the
generator and allow pending input to drain before stopping the receiver.
Compare the receiver's valid-transition total and signed raw position with the
generator's independently recorded totals; expected counts are not receiver
arguments. Physical electrical compatibility and both-edge/count verification
remain pending. Zero counts with idle inputs validate lifecycle only.

## Bench follow-up and first-failure diagnostics

The user subsequently connected the Pico directly to Pi header GPIO after the
breakout-board path failed an electrical-level check. Idle runs of 30 and 60 s
reported no anomaly. A 10 kHz/50,000-cycle burst produced 200,001 delivered
events, 200,000 accepted positive transitions, zero sequence gaps and one
`EDGE_STATE_INCONSISTENT` event. This remains a failed-integrity run; exact
accepted totals alone do not establish a clean waveform or valid capture.
The user's 100 kHz run also failed integrity and is not an accepted rate.

The counter now automatically prints the first integrity-failure context after
shutdown. Invocation is unchanged:

```bash
uv run debug/treadmill_counter.py
```

Capture is one immutable window of up to eight preceding records, the offending
event and four subsequent records. Short windows are labeled; shutdown does
not wait for more edges. The report includes admitted event number, pre-failure
accepted count/signed position, previous global/A/B sequence baselines, channel
and configured BCM pin, edge type, remembered AB states, raw monotonic-ns
timestamps, adjacent kernel-time differences, lag, sequences, delta and flags.
Records stay in decoder-admission order (after bounded delivery correction).
Remembered AB values are decoder history, not
new electrical measurements. The first window survives later ring wraparound.

The existing STOP result carries this bounded report with final state; the
facade caches it before releasing IPC. `first_failure_diagnostic` is a read-only
property that uses that cache. Missing context after an interrupted/forced or
otherwise undelivered result is explicitly reported as unavailable. Integrity
and count rules are unchanged; there is no new logger, IPC channel, config
setting, dependency, per-edge transport, or acquisition console/file output.
The ready message uses effective config pins, including a local bench override.

Tests-first diagnostic commits: RED `7d7357e` (22 intended failures), follow-up
forced-cleanup test `1d2a7cf`; GREEN `f2258b8`. The same focused software command
listed above now reports 229 passed, including 23 new diagnostic tests. Ruff
checks and `git diff --check` pass. New Pi-side diagnostic behavior still needs
a run after the user pulls the branch.

Seven alternating local synthetic runs of 50,000 events compared the baseline
decoder at `6b406e2` with the new code. Median elapsed times: 0.142458 s before,
0.146681 s after, ratio 1.0296 (about 3% extra time). Both decoded all 50,000
events exactly. This Python 3.12.12 measurement excludes GPIO and shared-state
publication and must not be presented as Pi or full-process performance.

Separate known sizing correction: the inspected
[Pi 6.12 kernel source](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/drivers/gpio/gpiolib-cdev.c#L1689-L1693)
caps the requested event FIFO at `GPIO_V2_LINES_MAX * 16`, or 1,024 events.
The configured 8,192 value is a request, not evidence of 8,192 allocated slots.
Final buffer/headroom acceptance must account for that cap.

## Adjustable standalone pins: software evidence

Tests-only RED `72a9265`: 17 intended failures for absent pin arguments/help
and validation, 2 existing-default behaviors passed. GREEN `12d5ae3`: all 19
pin-selection tests pass. Cases include manifest-derived defaults, separate
or paired overrides, BCM range/type errors, duplicate/reserved selections,
actual-pin labels in failure context, and unchanged cached manifest/normal
session config after a standalone override. No GPIO or child process is used
by these new argument-boundary tests.

The focused software command above now reports 248 passed. Ruff checks,
`git diff --check`, and `uv run --offline python debug/treadmill_counter.py
--help` pass. There are no new dependencies or per-event processing changes.
Pi verification follows the existing Git pull workflow; no files were copied.

## STOP starvation correction

The next user run reported 125,982 delivered events, 125,981 valid positive
transitions, one inconsistency, no sequence gaps, forced termination and
unavailable first-failure context. The user confirmed one finite 5 s,
50,000-cycle burst. Those are aborted-run counts, not the final burst total.

A continuously nonempty synthetic source reproduced the forced-cleanup errors:
the worker stayed in event draining without checking STOP. A separate finite
200,001-event synthetic run on the Pi completed its STOP report, so this is a
confirmed shutdown defect, not a proven cause of that particular GPIO run.

Tests-first RED `b2a9595`: 7 intended failures, 2 passes. Follow-up RED
`9156f52` proves that losing the STOP reply must not turn partial counts into
clean shutdown. Ten new process/CLI tests cover continuous healthy/failing
input, a finite 200,000-transition backlog plus one extra event, complete
finite draining, full result queues, lost replies, ZERO expiration and drained
application, and final diagnostic output. The large synthetic source uses
5 s worth of 10 kHz-cycle timestamps and delayed reads to maintain backlog;
it is not a real-time GPIO throughput measurement.

STOP is checked after every bounded batch. A nonblocking final drain ends at
an empty read or an absolute cutoff; it reserves time for GPIO release and
report delivery before the parent's reply/join/forced-cleanup deadlines. The
existing STOP report includes whether draining completed. An incomplete drain
still returns captured first-failure context, reports final counts as partial,
and retains `SHUTDOWN_INCOMPLETE`, including through the existing status path
if the STOP reply is lost. A genuinely blocked backend still uses forced
cleanup and may leave context unavailable. No new dependencies, configuration
fields, public shared-state fields, or IPC channels were added.

The focused software command above reports 258 passed on the development host.
Ruff static/format checks and `git diff --check` pass. These results establish
software behavior with injected sources, not Pi GPIO throughput or acceptance.

After pulling `dev2`, repeat the single 10 kHz/50,000-cycle burst using the
existing counter invocation and selected BCM pins. Verify prompt shutdown and
save the first-failure context if the inconsistency recurs. That electrical
or capture anomaly remains unresolved; this change does not waive integrity
checks or establish an accepted physical count/rate.

## Raw-source comparison and unused-history suppression

The next BCM23/24 report returned first-failure context at admitted event
21,027: another A falling event following A falling, with consecutive global
and A-line sequences. Following rows included global 21,029 before 21,028,
with timestamps reversed in the same received order. Final counts were partial
because STOP's drain cutoff expired during ongoing input; forced termination
was no longer reported. This evidence does not identify the first anomaly as
electrical noise, generator error, or a kernel-capture limitation.

A read-only Pi serialization benchmark used 8,192 synthetic EventRecords:
five-run median pickle time was 57.021 ms for the 408,989-byte history versus
0.075 ms for 13 records (750 bytes). This measures serialization, not the
exact pause imposed by the multiprocessing feeder. It makes the unused history
transfer a plausible contributor to later loss; it cannot explain the first
anomaly, which precedes that transfer. At 40,000 delivered events/s, a
1,024-event FIFO represents 25.6 ms of buffering.

`debug/treadmill_counter.py` now disables only the unused large-history
channel. It retains the normal decoder, shared-state/worker architecture and
small first-failure STOP result. `TreadmillRuntime` keeps its existing history
default for other callers. Shared `debug/treadmill_options.py` supplies both
diagnostics with identical BCM validation, manifest defaults and optional
duration, without changing normal session configuration.

The raw comparison reads the same production GPIO source directly in one
process. It creates no decoder, shared-state transport or diagnostic queues.
Run it on the current bench pins:

```bash
uv run debug/treadmill_raw_counter.py --a-pin 23 --b-pin 24
```

It runs until Ctrl-C, or accepts `--duration-s` like the normal counter. Start
the receiver before triggering the Pico, let the full burst finish, then stop
the receiver. Duration is measured from receiver readiness, not generator
start. SIGINT/duration finishes the current admitted batch; there is no final
drain, and unread queued events are outside the printed totals. Prefer Ctrl-C
after the finite burst for complete-count comparisons.

The raw counter continues through anomalies. It reports total events,
A/B rising/falling counts, maximum processing lag and per-code anomaly
observations, retaining at most eight anomalous events in receipt order.
Sequence comparisons use the previous received event, including reordered
events; gap observations are not an exact lost-edge count. Repeated target
levels are observations and can also follow gaps/replays. Exit status is
nonzero for any raw anomaly or source/cleanup error. Zero anomalies alone do
not prove all physical edges were captured, and the probe does not compute
quadrature position, distance, speed or physical calibration.

For the first comparison, use separate 10 kHz, 50,000-cycle bursts with the raw
and normal counters, three trials each. Full raw totals should be 200,000
events: 50,000 rising and 50,000 falling on each channel. Expected totals stay
on the generator/test worksheet; they are not receiver arguments. The normal
counter still stops on failed integrity, so an aborted total is not a complete
burst comparison. If raw capture passes while normal acquisition fails,
investigate decoder/publication cost. If raw also fails, inspect the common
GPIO backend/kernel and the waveform at the Pi header, then repeat at lower
rates. Neither outcome by itself proves an electrical or kernel cause.

Tests-first RED `7907312`: the raw module was absent, 3 intended history
failures and 1 existing-default pass. Follow-up test-isolation correction:
`640e3cf`. The focused development-host command above now reports 284 passed,
including 26 new raw/history cases. These tests use injected sources and do
not establish real Pi GPIO throughput. Ruff static/format checks, diff checks,
and both simple `uv run <file> --help` invocations pass, including launching
the raw counter from `debug/`. Pi comparison remains pending the user's Git
pull; no implementation files have been copied to the target.

## Coalesced state publication

After moving Pico outputs to GP4/GP5 and separating the breadboard leads, the
user reports at least three fully clean raw bursts: 200,000 events, 50,000
rising/falling on each channel, and zero raw anomalies. Normal acquisition
still intermittently reports an extra same-level falling event. In the latest
report, event 103,142 was another A falling event with consecutive global/line
sequences and 0.134056 ms processing lag; the next B falling event followed
0.797 us later in kernel timestamp order. Final counts were partial because
the bounded STOP drain ended while the finite burst was still active. These
observations do not prove an electrical, kernel, or decoder-load cause.

A read-only Pi CPU benchmark and cProfile run identified substantial repeated
snapshot/packing cost at small batch sizes. For 10,000 synthetic perfect
events and batch size 8, the profiled run spent 0.195 s cumulatively in
`_publish` versus 0.156 s in `Decoder.process_event`. Profile instrumentation
adds overhead; the unprofiled paired benchmark below measures throughput.

Routine publication is now coalesced with
`STATE_PUBLICATION_INTERVAL_NS = 1000000` in `acquisition.py`. Decoder updates
and bounded control checks still occur for every event/batch respectively.
New health conditions and startup/ZERO/backend/heartbeat/STOP updates bypass
the cadence. Pending tail state shortens the next read timeout, and failed
nonblocking writes retain pending state for a cadence-limited retry.
Intentional coalescing is not counted as a publication skip. There are no new
dependencies, public configuration fields, or shared-state transport fields.

Ten deterministic worker tests use a controllable ns clock, real packed-state
slots, and in-process source/channel doubles. RED `76fef12`: 9 intended
failures, 1 stationary-heartbeat pass. `68a319f` corrects a test assumption
that a heartbeat due at STOP requires a redundant live snapshot. `ce6078f`
makes the existing forced-cleanup test wait for its deliberately broken source
to signal a stall; a tail pending behind that blocked backend is not promised
to be published. Other spawned-process/legacy tests remain regression gates.

On the Pi, three alternating trials per strategy used the same installed
Decoder/SharedState implementations with 10,000 events, startup and final
copies, and no parent readers. The comparison benchmark applied either
per-batch publication or the approved 1 ms strategy; it did not copy or execute
the revised production worker on the Pi.

| Events/read batch | Per-batch events/s | 1 ms events/s | Median publications, per-batch / 1 ms |
|---:|---:|---:|---:|
| 1 | 12,870 | 101,743 | 10,002 / 98 |
| 4 | 38,691 | 107,856 | 2,502 / 93 |
| 8 | 58,286 | 109,106 | 1,252 / 89 |
| 32 | 94,272 | 111,156 | 315 / 81 |
| 256 | 114,428 | 114,572 | 42 / 41 |

Every trial retained exactly 10,000 valid transitions with no publication
skips. This isolates decoder/shared-state CPU cost and excludes GPIO waits,
event adaptation, worker control polling and reader contention. Actual GPIO
batch sizes were not measured. It demonstrates the benefit of coalescing for
small batches, not accepted physical throughput or resolution of the extra
falling-event anomaly.

The focused software command above reports 294 passed on the development
host. Ruff static/format checks and `git diff --check` pass. The production
change is confined to acquisition publication bookkeeping; source adaptation,
per-event decoder rules and public configuration/transport interfaces remain
as before. The user's Pico firmware/build/editor changes are not part of
this implementation commit.

After the user pulls `dev2`, repeat the normal-counter 10 kHz/50,000-cycle
bursts with GP4/GP5 and the current Pi wiring unchanged. Preserve complete
failure output if an anomaly recurs. Deployment remains Git-only; the Pi
checkout and GPIO were not modified by the benchmark.

## IRQ-to-pin-read timing diagnostic

`debug/treadmill_irq_latency.py` attaches to either already running standalone
counter. It does not claim GPIO, change the counter, or change IRQ affinity or
priority. Start the counter in terminal 1 with the Pico generator idle:

```bash
uv run debug/treadmill_counter.py --a-pin 23 --b-pin 24
```

The raw counter can be used instead, with the same pin flags. Wait for the
counter's ready message, then start the tracer in terminal 2:

```bash
uv run debug/treadmill_irq_latency.py --a-pin 23 --b-pin 24
```

The tracer requests sudo for tracefs access, reusing the Python interpreter
selected by uv. No package installation or kernel rebuild is needed on the
verified target. It resolves the current GPIO IRQ numbers and IRQ-thread IDs
from `/proc`, so the counter must remain running while the tracer attaches.
Wait for **IRQ latency tracer ready**, then start one Pico burst. After the
burst ends, press Ctrl-C in terminal 2 to save/report the trace; stop terminal 1
as usual. `--duration-s 15` can replace Ctrl-C in terminal 2, provided the whole
burst fits inside that window. Each omitted pin uses the manifest default.

The four recording points are:

| Point | Recorded information |
|---|---|
| `irq:irq_handler_entry` | Selected GPIO IRQ, timestamp, task and CPU |
| `edge_irq_thread` entry | IRQ-thread start timestamp and IRQ |
| `rp1_gpio_get` entry | Start of the pin-read bracket and BCM pin |
| `rp1_gpio_get` return | End of the bracket, BCM pin and sampled raw level |

The RP1 register read occurs between the last two points. Delays start at
software IRQ-handler entry, after GPIO-bank dispatch; they do not include
physical-edge-to-handler latency. All points use ftrace's `mono` clock. The
saved text timestamps have 1 us resolution and are converted without float
rounding of large boot-time values. Each sample requires an unambiguous
IRQ/thread/read pairing. Overlapping interrupts, missing partners and read
errors are reported separately, not repaired. Ambiguous intervals may contain
the longest delays, so statistics for matched reads alone cannot exonerate
kernel scheduling or edge capture.

The report gives per-pin median, nearest-rank p95/p99, maximum and >=50 us
counts for thread start and both ends of the read bracket. The 50 us reference
is relevant to the current 10 kHz waveform's same-pin edge spacing. It also
prints up to ten longest matched read examples per pin, with monotonic IRQ
timestamps for comparison with the counter's first-failure context.

Recording uses a private tracefs instance, selected IRQ/pin/task filters and
64 MiB per CPU (approximately 256 MiB on this four-core Pi). No per-event Python
processing, console output or file writes occur during the burst. After
recording stops, its events are disabled and trace text, metadata and summary
are saved under the already ignored
`tmp_operator_runs/treadmill_irq_latency/<UTC timestamp>/`. Metadata records the
kernel, Git revision, IRQs, thread IDs, clock, buffer size and losses; failed
captures retain `status: incomplete`. Captures do not overwrite previous runs.
Only this invocation's probe definitions and tracing instance are removed.

`Trace capture complete: False` means a ring-buffer overrun/drop or missed
probe was observed; do not interpret it as complete-burst evidence. Failure to
read the owned probe statistics is an error, not an assumed zero. Tracing adds
overhead even when its buffer does not lose events. Compare identical bursts
with tracing off/on/off, keeping wiring unchanged, first at low frequency and
then at 10 kHz. Run both counter variants. Traced failures alone do not prove
the untraced acquisition has the same timing problem.

Development-host tests follow RED/GREEN: `851aed8` specifies pairing and
cleanup, `b9b60a9` incorporates the actual Pi trace/profile formats, and
`e18a052` specifies capture shutdown and incomplete metadata. All 27 diagnostic
tests and the documented focused regression command pass (321 tests total).
The Pi accepted the entry/return probe syntax, including saved function
arguments at return. A private temporary trace of the **existing** raw counter
for 0.2 s captured eight startup pin-read records, with valid BCM23/24 arguments
and returned levels; the counter saw zero input edges. This verified the live
text format and cleanup, not IRQ-to-thread latency under a signal burst. The
installed kernel's probe profile prints bare event names, so this tracer gives
every probe a unique name and requires statistics for all three probes.

The new tracer's end-to-end Pi run and generator bursts remain pending the
user's Git pull. No implementation files were copied to the Pi. Legacy
counters, production acquisition and the user's Pico changes remain unchanged.

### Tracefs control-write correction

The first user run after pulling `99de47b` failed with `EINVAL` before creating
any probes. The failure was in Python's buffered append-mode file open, which
seeks to the end of `dynamic_events`; tracefs rejects that seek. The earlier
shell-redirection checks did not exercise Python's append-open behavior.
On the Pi, `Path.open("a")` and an explicit end-seek reproduced `EINVAL`, while
`os.open(..., O_WRONLY | O_APPEND)` succeeded without seeking.

Regression commit `e3829d2` adds three initially failing tests for nonseekable
control files, preservation of existing probe definitions, contextual errors,
descriptor cleanup and short writes. Control commands now use a single direct
`os.write`, close the descriptor in `finally`, and include the path and command
in any error. The entry command and tracing/pairing behavior are unchanged.

The installed Pi tracer was exercised for 0.2 s with only its control writer
temporarily replaced in memory. The complete setup, 64 MiB/CPU buffer,
IRQ/thread filters, capture export, loss checks, report and cleanup succeeded.
There were zero IRQ entries and zero trace/probe losses; this is a stationary
lifecycle check, not a waveform/latency result. No source files were copied or
edited on the Pi. The three new tests raise the diagnostic total to 30 and the
focused regression total to 324. After pulling the correction, repeat the same
tracer command with the counter running and start the generator only after
the tracer's ready message.

## Bounded delivery ordering and excluded-read reporting

The raw/trace capture `20261027_024359_953297` confirmed cross-channel delivery
reordering and an overlapping same-pin read. The runtime now corrects delivery
order with one pure `EventOrderBuffer` before strict decoding, also reused in
startup synchronization. Initial bounds are 1 ms and 256 retained records.
Contiguous input has no deliberate wait. Timed-out/capacity-limited holes,
same-level events and invalid kernel metadata still invalidate acquisition;
extra B notifications are not removed. ZERO/STOP/backend-error tests prove
that an empty raw read cannot hide pending records or discard admitted counts.

The normal counter adds `Corrected delivery reorders`, counting successfully
resolved buffering episodes. No warning/failure code is added for a corrected
episode. Shared state carries the new dimensionless counter. Failure tables
are labeled decoder-admission order. Kernel timestamps and physical units are
unchanged; processing lag includes the ordering wait. The raw counter's
post-startup received-order behavior and all legacy scripts remain unchanged.

The tracer adds up to eight excluded contexts, using existing records only.
Each displays relevant IRQ/thread/read points, sampled level, task/CPU, and
up to eight records before/after exclusion. Known thread-to-read delay and an
ambiguous range of candidate IRQ-to-read delays are reported separately from
matched-read statistics. Capture and probe loss checks retain their meaning.

To reanalyze the existing saved burst after pulling dev2, no counter, generator
or sudo is needed:

```bash
uv run debug/treadmill_irq_latency.py --analyze-capture tmp_operator_runs/treadmill_irq_latency/20261027_024359_953297
```

Offline mode reads the saved metadata and trace and writes no capture files.
The source recording was read from the Pi into an ignored development-host
folder; no implementation files were copied onto the Pi. The new report
preserved the original matched statistics and excluded counts and displayed
`Thread-to-read: 53.0 us` and candidate IRQ-to-read range `[3.0, 55.0] us`, with
the original interleaved CPU/task records and both low-level reads.

RED ordering commit `7051463`: 5 failures, 13 missing-module errors, 2 existing
behavior passes. RED report commit `3c7acb7`: 5 failures, 30 existing cases
passing. A further RED case verifies that ordering expiry exactly at the STOP
cutoff cannot be reported as a complete drain. The focused regression command
now passes 350 tests, including 21 ordering/lifecycle cases and 5 additional
report/replay cases.

A development-host synthetic comparison, with three alternating 100,000-event
trials per path and batch size 256, retained exact valid position/counts.
Median decoder-only time was 0.3172 s (315,269 events/s); adding ordering took
0.3325 s (300,766 events/s), about 4.8% more CPU time. This excludes GPIO, IPC
and publication and is not a Pi throughput or hardware-acceptance result.

After pulling dev2, restart the normal counter so its child and shared-state
layout both use the updated code. Pi runtime tests and new signal bursts remain
pending that pull. 10 kHz per channel is a stress target, not a stated production
requirement. No affinity, kernel, electrical or Pico firmware changes are part
of this correction.
