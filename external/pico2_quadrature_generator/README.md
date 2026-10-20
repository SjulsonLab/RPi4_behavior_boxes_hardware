# Pico 2 known-count quadrature burst generator

This standalone Raspberry Pi Pico 2 firmware emits an exact, finite burst of
quadrature transitions for testing a separate Pi or Teensy decoder. It does
not require a serial terminal or a host computer after flashing.

The firmware has passed host timing tests, PIO static review, and a Pico 2
build only when recorded as such in the repository handoff. Electrical
behavior must not be described as validated until the hardware checks below
have been completed with a Pico 2 and a scope or logic analyzer.

## Wiring

GPIO names and physical header-pin numbers are different:

| Purpose | Pico 2 GPIO | Physical header pin | Connection |
|---|---:|---:|---|
| Quadrature A | GP2 | 4 | Receiver encoder A input |
| Quadrature B | GP3 | 5 | Receiver encoder B input |
| START | GP15 | 20 | Normally-open button to GND |
| RESET | RUN | 30 | Normally-open button to GND |
| Ground | GND | 3, 8, 13, 18, 23, 28, or 38 | Receiver ground and buttons |
| Power/programming | USB | USB connector | 5 V USB source |

Disconnect the real encoder before connecting A and B. Configure the receiver
pins as inputs, use only 3.3 V-compatible inputs, join the board grounds, and
do not intentionally drive an unpowered receiver.

## Routine configuration

Routine users should edit exactly two constants near the top of `main.c`:

```c
#define QUADRATURE_HZ UINT32_C(1000)
#define QUADRATURE_CYCLES UINT64_C(1000)
```

The defaults emit 1,000 cycles at 1 kHz. The required one-second combinations
are:

| `QUADRATURE_HZ` | `QUADRATURE_CYCLES` | Combined transitions |
|---:|---:|---:|
| 10 | 10 | 40 |
| 100 | 100 | 400 |
| **1,000 (default)** | **1,000 (default)** | **4,000** |
| 10,000 | 10,000 | 40,000 |
| 100,000 | 100,000 | 400,000 |
| 1,000,000 | 1,000,000 | 4,000,000 |

Unsupported frequencies fail validation and cannot start a burst. Valid cycle
counts range from 1 through `UINT32_MAX`.

## Build and flash

The initial build validation uses the official Raspberry Pi Pico C/C++ SDK
2.1.1, CMake 3.28.3, and Arm GNU Toolchain 13.2.1. A newer compatible SDK may
also work, but should be recorded separately rather than silently replacing
the validated version.

Command-line build:

```sh
export PICO_SDK_PATH=/path/to/pico-sdk
cmake -S . -B build -DPICO_BOARD=pico2
cmake --build build
```

The result is `build/pico2_quadrature_generator.uf2`. Hold BOOTSEL while
connecting the Pico 2, then copy that UF2 file to the mounted `RP2350` drive.

The same directory can be opened with the Raspberry Pi Pico VS Code extension.
Select the non-wireless `pico2` board before building.

## Operation

1. After startup, release START. The onboard LED turns on when ready.
2. Press START and hold it low for the 20 ms debounce interval.
3. The LED turns off and A/B remain at idle `11` for 1,000 ms.
4. PIO emits exactly the configured number of cycles.
5. PIO stops at `11`. Release START before another press can be accepted.

A release during the delay does not cancel the burst. Presses during the delay
or burst are ignored and are not queued. Holding START low cannot repeat a
burst. Pressing RESET restarts the firmware and invalidates any active test.

## Waveform and timing

The logical A/B sequence is:

```text
11 -> 10 -> 00 -> 01 -> 11
```

A is the least-significant PIO output pin, so the corresponding PIO `SET`
values are `1, 0, 2, 3`. A legacy A-rising decoder should count N; an x4
decoder should count 4N transitions, subject to its configured sign.

The system clock is verified at 120 MHz. A fixed integer PIO divider of 6 gives
a 20 MHz PIO instruction clock. Each quarter-period contains four fixed PIO
instructions plus a counted delay loop:

```text
quadrature_hz = 120,000,000 / (4 * 6 * PIO_cycles_per_quarter)
delay_counter = PIO_cycles_per_quarter - 4
```

The fourth phase uses the one-cycle `JMP X--` burst branch where the preceding
phases use one-cycle `NOP` padding. Therefore the branch between cycles does
not stretch the final quarter-period.

For N=1, X starts at 0 and the first failed `JMP X--` ends the burst. For N=2
and N=3, X starts at 1 and 2 respectively and produces exactly that many full
passes through all four states. The final `IRQ WAIT 0` reports completion only
after the remaining quarter-period at `11`; clearing it returns PIO to a
blocking `PULL` without another output transition.

The visible interval from the first changed edge to the last changed edge is
`(4N - 1)/(4f)`. Since the final edge already returns the output to idle `11`,
there is no separate edge at the nominal `N/f` endpoint. Add one measured
quarter-period to the first-to-last edge interval when checking nominal burst
duration.

## Host tests

The timing and count encoding helpers can be tested without Pico hardware:

```sh
cmake -S tests -B tests/build
cmake --build tests/build
ctest --test-dir tests/build --output-on-failure
```

These tests cover all six required rates, invalid timing settings, pin
relationships, and cycle counts from 1 through `UINT32_MAX`.

## Pending hardware validation

With a suitable scope or logic analyzer:

- Check N=1, 2, 3, and 10 for exact state order, edge totals, idle `11`, and no
  extra transition.
- Exercise repeated presses, a held START button, busy-period presses, and
  START held during reset.
- Verify the configured pre-burst delay independently.
- Test all six required frequency/count pairs for exact counts, channel
  frequency, phase, duty cycle, and uniform adjacent-transition spacing.
- Compare against the target receiver only after the electrical waveform has
  been verified independently.
