# Treadmill implementation validation

This index records measured evidence, not production acceptance. Work is
authorized through TW-3; full-stack tests and production acceptance are later
gates. No external quadrature generator has been exercised yet.

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
/home/pi/.local/bin/uv run --offline --no-project --python /usr/bin/python3 python -m pytest tests/treadmill tests/test_rpi5_trixie_provisioning.py -q
```

The software tests inject sources and do not claim GPIO. Next, with BCM13/16
available, run the production GPIO source for a short stationary check:

```bash
/home/pi/.local/bin/uv run --offline --no-project --python /usr/bin/python3 python debug/treadmill_counter.py --duration-s 5
```

Run until Ctrl-C instead:

```bash
/home/pi/.local/bin/uv run --offline --no-project --python /usr/bin/python3 python debug/treadmill_counter.py
```

The command prints readiness, warns about unverified physical calibration,
then reports final edge/x4 counts, signed and positive/negative transitions,
reversals, sequence gaps, estimated missing events, inconsistencies, integrity,
rate, lag, publication/diagnostic drops, and retained health codes. Counts are
dimensionless x4 transitions, not full quadrature cycles. An ordinary Ctrl-C is
a normal stop; acquisition or cleanup failure returns nonzero. Run one counter
at a time because the old and new tools claim the same lines.

The microcontroller is not yet attached. Start the receiver first and wait for
readiness before starting an independently counted generator burst. Stop the
generator and allow pending input to drain before stopping the receiver.
Compare the receiver's valid-transition total and signed raw position with the
generator's independently recorded totals; expected counts are not receiver
arguments. Physical electrical compatibility and both-edge/count verification
remain pending. Zero counts with idle inputs validate lifecycle only.
