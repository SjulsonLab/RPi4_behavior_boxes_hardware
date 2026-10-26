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
Records stay in received order. Remembered AB values are decoder history, not
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
