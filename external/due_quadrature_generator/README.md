# Arduino Due known-count quadrature generator

This standalone Arduino Due sketch emits a finite, count-first quadrature burst
for testing the Raspberry Pi treadmill inputs. It prioritizes exact transition
count and ordering over exact frequency.

## Routine configuration

Edit these two definitions near the top of `due_quadrature_generator.ino`:

```cpp
#define QUADRATURE_HZ UINT32_C(1000)
#define QUADRATURE_CYCLES UINT64_C(1000)
```

`QUADRATURE_HZ` is the complete quadrature-cycle rate, not the combined edge
rate. It must be one of these six values:

| Requested rate | Typical one-second count | Quarter-period CPU ticks |
|---:|---:|---:|
| 10 Hz | 10 | 2,100,000 |
| 100 Hz | 100 | 210,000 |
| 1 kHz | 1,000 | 21,000 |
| 10 kHz | 10,000 | 2,100 |
| 100 kHz | 100,000 | 210 |
| 1 MHz | 1,000,000 | 21 |

`QUADRATURE_CYCLES` may be any integer from 1 through `UINT32_MAX`. Large
values can keep the processor busy with normal interrupts disabled for an
impractical length of time. For example, `UINT32_MAX` cycles at 10 Hz would
take about 13.6 years. RESET interrupts a run but invalidates its count.

## Wiring

Disconnect the real encoder before attaching the generator. Configure the
Raspberry Pi receiver pins as inputs and power both boards before testing.

| Purpose | Arduino Due connection |
|---|---|
| Quadrature A | D34 / PC2 |
| Quadrature B | D35 / PC3 |
| START button | D22 through a normally-open button to GND |
| Reference | Due GND to Raspberry Pi GND |
| Programming/power | Due Programming USB port |

The Due uses 3.3 V GPIO. Do not apply 5 V to its pins, do not drive an
unpowered receiver, and do not connect the Due outputs while the real encoder
is also driving the same receiver inputs.

## Operation

Idle is A/B=`11`. The onboard LED is on only while the generator is ready for
a fresh START press.

1. Press and hold START long enough to pass the 20 ms debounce.
2. The ready LED turns off and the sketch waits one second.
3. The generator repeats `11 -> 10 -> 00 -> 01 -> 11` exactly N times.
4. It completes the final quarter-period hold at `11` and waits for START to be
   released before rearming.

A held button cannot retrigger. Releasing during the one-second delay does not
cancel the accepted burst. Use RESET, not ERASE, to restart or interrupt the
instrument.

For N completed cycles, each channel has N rising and N falling edges. The
combined A/B transition total is 4N. An A-rising decoder should report N and an
x4 decoder should report 4N, with sign determined by receiver conventions.

## Timing limitations

The sketch uses direct SAM3X PIOC set/clear writes and the Cortex-M3 DWT cycle
counter. Each quarter-period wait enforces a minimum dwell, so instruction and
bus overhead can make the output slower than requested. The 1 MHz preset has
only 21 CPU ticks per transition and must not be treated as an achieved rate
until measured on hardware.

Normal maskable interrupts are disabled only during the burst. Arduino
`millis()`, `micros()`, USB, and serial bookkeeping can miss elapsed work during
that interval. The sketch does not repair the global Arduino timebase.

## Build and validation status

Target the Programming Port board definition:

```text
arduino:sam:arduino_due_x_dbg
```

The implementation is developed against Arduino IDE 1.8.19, Arduino SAM Boards
1.6.12, and ARM GCC 4.8.3. Host contract tests cover configuration arithmetic
and DWT rollover. Optimized ARM output must be inspected for the four explicit
writes and the terminal loop boundary.

Run the host tests in a temporary build directory:

```text
cmake -S tests -B /tmp/due-quadrature-host-tests
cmake --build /tmp/due-quadrature-host-tests
ctest --test-dir /tmp/due-quadrature-host-tests --output-on-failure
```

For a normal Arduino IDE upload, select **Arduino Due (Programming Port)**,
select the Programming Port device, open `due_quadrature_generator.ino`, and
use Verify followed by Upload. The equivalent command-line forms are:

```text
arduino --verify --board arduino:sam:arduino_due_x_dbg due_quadrature_generator.ino
arduino --upload --board arduino:sam:arduino_due_x_dbg --port /dev/ttyACM0 due_quadrature_generator.ino
```

Replace `/dev/ttyACM0` with the actual Programming Port device. Upload has not
yet been exercised because hardware validation is intentionally pending.

Build-only validation completed for this implementation:

| Requested rate | Quarter ticks | Due build | Electrical counts | Measured rate/phase |
|---:|---:|---|---|---|
| 10 Hz | 2,100,000 | Pass | Pending | Pending |
| 100 Hz | 210,000 | Pass | Pending | Pending |
| 1 kHz | 21,000 | Pass | Pending | Pending |
| 10 kHz | 2,100 | Pass | Pending | Pending |
| 100 kHz | 210 | Pass | Pending | Pending |
| 1 MHz | 21 | Pass | Pending | Pending |

The default build uses 11,124 bytes of flash according to Arduino's build
summary. A separate `UINT32_MAX` cycle-count configuration also builds. In the
optimized 1 MHz output, the burst loop contains exactly these four PIOC writes:
B clear, A clear, B set, and A set. Each is followed by a cycle-counter wait;
the countdown decrement and loop branch occur after the final wait. The saved
interrupt mask is restored only after loop completion. This establishes the
compiled control structure, not physical timing or edge counts. The selected
Arduino SAM core's default startup also disables the watchdog before `setup()`.

Electrical edge counts, measured rates, phase spacing, and receiver comparisons
remain pending until tested with the Arduino Due and independent measurement
equipment. An internal software loop count is not independent evidence of the
physical output count.

## Pending hardware validation

1. With the receiver disconnected, confirm idle A/B=`11` and verify that START
   produces no edge before the one-second delay expires.
2. At a low rate, run N=1, 2, 3, and 10. Independently count N rising and N
   falling edges on each channel, verify the four-state order, and check that no
   edge follows the terminal `11` state.
3. Exercise START held at boot, held through a burst, pressed while busy, and
   released normally. Each accepted fresh press must produce only one burst.
4. For every preset, record measured same-channel frequency, adjacent-edge
   spacing, total duration, and independently measured edge totals. Treat any
   rate below the requested value as the achieved test rate.
5. Only after the generator is independently verified, connect the Raspberry Pi
   receiver and compare its count with N or 4N according to the decoder mode.
