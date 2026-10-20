# Pico 2 Known-Count Quadrature Burst Generator

## 1. Purpose and scope

Implement a small, standalone firmware project for a **Raspberry Pi Pico 2 (non-wireless)** using the **official Pico C/C++ SDK**, buildable with the Raspberry Pi Pico VS Code extension or the SDK command-line tools.

The firmware has one job:

> Wait for a START-button press, wait one second, generate exactly N quadrature cycles on two output pins at a configured frequency, then hold the outputs idle until another deliberate press.

The Pico is an independent test-signal source for an existing Pi or Teensy decoder. It does not configure, reset, or communicate commands to the receiver. Do not modify the receiver code as part of this project.

Use one CPU core for setup/button handling and **one PIO state machine controlling both waveform pins**. The PIO program must generate and terminate the counted burst independently of CPU timing.

## 2. Wiring and pin ownership

Use these defaults, with GPIO numbers clearly distinguished from physical header-pin numbers:

| Purpose | Pico connection | Configuration |
|---|---|---|
| Quadrature A | GP2 | Actively driven digital output |
| Quadrature B | GP3 | Actively driven digital output |
| START | GP15 to normally-open button to GND | Input, internal pull-up, active low |
| RESET | RUN to normally-open button to GND | Hardware reset; not a software GPIO input |
| Reference ground | Pico GND to receiver GND | Required common ground |
| Power/programming | USB | No connected serial terminal required |

The START input uses the SDK's internal pull-up; normal short-jumper button wiring does not require an added series resistor. RUN is the dedicated hardware reset connection, not the START input.

Connect A and B to the receiver's configured encoder inputs. Use 3.3 V-compatible connections and short signal wires. Disconnect the real encoder before the generator drives those inputs, and configure the receiver pins as inputs. Power both boards before running a test; do not intentionally drive an unpowered receiver.

Use adjacent output GPIOs. Keep B derived from A + 1, or validate adjacency explicitly. Do not add arbitrary pin-routing support.

## 3. Easily editable settings

Put a clearly marked configuration block near the top of `main.c` (or `main.cpp`). Do not add a configuration-file parser, menu, or serial-command protocol.

| Setting | Default | Meaning |
|---|---:|---|
| `QUADRATURE_HZ` | 1,000 | Full quadrature cycles per second; also each channel's square-wave frequency |
| `QUADRATURE_CYCLES` | 1,000 | Exact number of complete cycles in one burst |
| `START_DELAY_MS` | 1,000 | Delay after a debounced START press is accepted |
| `BUTTON_DEBOUNCE_MS` | 20 | Stable button level required for press/release acceptance |
| `A_PIN` | 2 | Pico GPIO number |
| `B_PIN` | 3 | Pico GPIO number; adjacent to A |
| `START_BUTTON_PIN` | 15 | Pico GPIO number |

Changing frequency or cycle count means editing a constant, rebuilding, and flashing. Each subsequent press repeats the same configured test.

The default is **1,000 cycles at 1 kHz**, giving a nominal one-second burst. All six frequency/count pairs below are required to work:

| Quadrature frequency (`QUADRATURE_HZ`) | Cycles per burst (`QUADRATURE_CYCLES`) | Nominal burst duration (`N / f`) | Combined A/B transitions (`4N`) |
|---|---:|---:|---:|
| 10 Hz | 10 | 1 s | 40 |
| 100 Hz | 100 | 1 s | 400 |
| **1 kHz (default)** | **1,000** | **1 s** | **4,000** |
| 10 kHz | 10,000 | 1 s | 40,000 |
| 100 kHz | 100,000 | 1 s | 400,000 |
| 1 MHz | 1,000,000 | 1 s | 4,000,000 |

The default **one-second pre-burst delay is separate from the one-second burst**. Each of these settings follows the same sequence: accept a debounced press, wait `START_DELAY_MS` at idle, emit the configured counted burst, then remain idle.

Keep frequency and cycle count independently editable; the table defines required one-second test combinations, not a restriction that every burst must last one second. Other frequencies need not be supported unless the same simple timing configuration represents them correctly. Reject unsupported settings instead of silently changing their meaning.

Accept cycle counts from 1 through `UINT32_MAX`, inclusive. Validate the range before loading the counter, including any N-minus-one encoding. Reject zero. Use 64-bit arithmetic for derived totals such as `4 * N`, so diagnostic calculations cannot overflow a 32-bit count.

The README must identify exactly which two constants a routine user edits: frequency and cycle count. Include the six required one-second settings above, with 1,000 cycles at 1 kHz clearly marked as the default.

## 4. Waveform definition and exact counts

### Logical A/B sequence

Idle is **A=1, B=1**. Use this fixed sequence for one complete cycle:

```text
11 -> 10 -> 00 -> 01 -> 11
```

The labels above mean **A then B**, not the numeric bit order of a PIO register. With A on GP2 and B on GP3, a two-pin PIO value uses A as its least-significant bit. Document this mapping so the waveform is not accidentally reversed.

This sequence is the modern treadmill decoder's positive encoder ordering, rotated to start at 11. Do not label it animal-forward locomotion; that depends on receiver wiring/sign configuration.

A fixed direction is sufficient. Reverse-direction testing can be done by swapping the two receiver signal connections while idle. Do not add a direction-selection button or menu.

### Phase and frequency

Both channels must have the same frequency and a 50% duty cycle over the conceptual running interval from the first transition through one final quarter-period after the last transition, with a quarter-period phase offset. Exactly one signal changes at each transition.

For quadrature frequency f:

```text
channel A frequency = f
channel B frequency = f
combined transition rate = 4f
time between successive A/B transitions = 1 / (4f)
```

At 1 MHz, that means 250 ns between adjacent transitions and 4 million total transitions per second. This describes the generator, not a promised receiver capability.

### Count contract

For N complete cycles, generate exactly:

```text
A rising edges:          N
A falling edges:         N
B rising edges:          N
B falling edges:         N
Combined transitions:  4N
Final state:            11
```

Initial configuration to the idle state is not a counted cycle. There must be no extra start pulse, cleanup pulse, partial cycle, or spontaneous repeat between bursts.

A legacy A-rising-only decoder should therefore observe N counted edges; an x4 decoder should observe 4N transitions, subject to the receiver's sign convention.

## 5. PIO generation and timing requirements

The CPU supplies a count and starts the PIO operation. PIO owns every waveform transition and the exact stopping point.

**Do not generate a continuous waveform and have the CPU stop it after N/f seconds.** That makes the edge count dependent on CPU stop latency.

Use a short counted PIO loop. Account for all instructions, delays, and loop branches when calculating phase durations. The branch between cycles must not stretch one quarter-cycle relative to the others. The last cycle must terminate cleanly at 11 without beginning another cycle.

The entire burst must run from a count loaded before its first transition. It must not depend on the CPU feeding one FIFO word per edge/cycle. Do not use DMA or a buffer proportional to the requested burst length.

Use a PIO completion indication, such as a completion flag polled by the CPU. CPU polling does not need to be fast: the waveform must already have stopped safely at 11 before completion is handled. Do not use FIFO-empty status alone as proof of completion.

### Clock choice

Prefer one fixed, SDK-supported **120 MHz system clock**, verified during initialization. This is a proposed implementation setting, not a claim that firmware has already been tested on the board. It permits 30 system-clock ticks per quarter-cycle at 1 MHz. Verify PIO divider and instruction-timing limits across the entire required **10 Hz to 1 MHz** range, including the two lowest frequencies; do not assume one unchanged loop timing plus a divider alone covers every required setting.

Balance the PIO instruction timing and select integer dividers so the required frequencies have uniform quarter-cycle intervals. Do not silently introduce fractional-divider timing variation to achieve only the correct average frequency.

Document the actual formula, for example:

```text
quadrature_hz = system_clock_hz / (4 * PIO_cycles_per_quarter * divider)
```

Verify that the SDK accepts the selected clock, and calculate settings from the configured clock rather than assuming an unrelated default. Keep one clock configuration; do not implement an automatic clock-search/tuning subsystem.

Frequency is nominal relative to the board oscillator. Exact cycle count is the primary guarantee; the board is not a calibrated frequency standard.

### Burst boundaries

Hold 11 indefinitely before the first transition and after the final return to 11. The extended high intervals at the outside boundaries are idle time, not malformed extra cycles.

Avoid a misleading duration check: the interval from the first changed edge to the last changed edge is `(4N - 1)/(4f)`. N/f is the nominal duration of N complete periods when the remaining quarter-period is included. Document which endpoints any duration measurement uses.

## 6. Startup, START button, and reset behavior

### Startup/reset

Initialize the output latches and PIO ownership carefully so firmware does not intentionally generate a low-going glitch while establishing idle 11. Configure START with its pull-up, initialize the counted PIO generator, validate settings, and wait.

Do not automatically emit a burst at power-up or after reset. If START is already held low at boot, require a stable release followed by a new press before accepting a test.

No firmware guarantee is made about electrical transitions during power loss or hardware reset. Start receiver counting only after the generator has initialized and settled. Reset during a test invalidates that test.

### Accepted START press

Accept a press only while ready and after GP15 has remained low for the debounce interval. Then:

1. Keep A/B at 11 and wait `START_DELAY_MS` (**1,000 ms by default**).
2. Start exactly one counted PIO burst.
3. Wait for PIO completion while PIO maintains waveform timing.
4. Leave A/B at 11.
5. Require a stable released button state before accepting a fresh press.

The configured delay starts when the press is **accepted after debounce**, not when the button is released. Thus a physical press normally precedes the burst by roughly 1.02 seconds with the default delay and debounce settings. The delay is a usability interval, not a precision synchronization output; ordinary small firmware overhead is acceptable.

A button release during the delay does not cancel the scheduled burst. Ignore additional presses during the delay or burst; do not queue them. A held button must never cause repeated bursts. Inputs pressed while busy must not become a delayed extra test after completion.

Simple polling and blocking waits are acceptable here because this firmware has no concurrent task responsibilities. Do not add a general event scheduler or interrupt-driven button framework.

### RESET

The physical RUN-to-GND button restarts the firmware. It does not count as START and is not a graceful end-of-burst control. Do not attempt to resume an interrupted burst after reset.

## 7. Minimal user feedback

Use the onboard LED if helpful: on when ready for a fresh START press, off during the delay/burst and while waiting for release. Keep this to one simple indication rather than a blinking status system.

USB serial is not required for normal use. The device must work from USB power without an attached computer or open terminal. Do not add interactive serial controls or continuous logging. Optional development messages must remain outside the timed output and must not gate operation on a host connection.

Invalid configuration must prevent a burst. Prefer compile-time checks for constants and straightforward initialization checks for hardware configuration; no automatic fallback frequency or recovery framework.

## 8. Project size and deliverables

Use approximately this project structure:

```text
pico2_quadrature_generator/
    main.c                  # or main.cpp; one application source
    quadrature_burst.pio    # short counted waveform program
    CMakeLists.txt
    pico_sdk_import.cmake   # standard SDK file, if needed
    README.md
```

Use the SDK's PIO assembler to generate its header. Target `PICO_BOARD=pico2` and produce a `.uf2` file. Do not require Arduino libraries, MicroPython, an RTOS, networking, multicore execution, a custom host application, or a reusable waveform-generation framework.

Prefer a few plainly named functions over classes, abstract backends, or extra packages. Comments should explain the pin/bit mapping, PIO loop count, timing calculation, button rearming, and safe idle behavior.

The README should cover wiring, editable constants, SDK/VS Code build and UF2 upload, operating sequence, expected counts, and the checks below. Include the SDK version used to build. Standard generated build files need not be treated as application complexity.

## 9. Proportionate validation

Do enough checking to trust the diagnostic source without creating another large testing project.

**Build and static review.** Build for Pico 2. Explain the PIO instruction timing and counter initialization for N=1, 2, and 3. Check that every quarter-cycle, including the loop boundary, has the intended duration. Keep this explanation beside the PIO code or in the README; no separate simulator is required.

**Small-count hardware check.** With a scope or logic analyzer, check N=1, 2, 3, and 10 at a required frequency. Confirm 4N total transitions, N rising edges on each channel, the specified ordering, idle 11, and no extra transition after completion. Observe repeated button presses, a held button, presses during the delay/burst, and START held at reset. Confirm the configured pre-burst delay after press acceptance.

**Required one-second burst checks.** Verify all six frequency/count pairs in Section 3: 10 cycles at 10 Hz, 100 at 100 Hz, 1,000 at 1 kHz, 10,000 at 10 kHz, 100,000 at 100 kHz, and 1,000,000 at 1 MHz. For each pair, check exact cycle/transition totals, channel frequency, phase relationship, duty cycle, regular adjacent-transition spacing, and clean return to idle. Measure the first-to-last transition interval as `(4N - 1)/(4f)`, measure one quarter-period as `1/(4f)`, and add them to confirm the nominal duration `N/f`; there is no separate A/B transition at the end of the final quarter-period because the output is already at idle 11. Measure the configured pre-burst delay independently. Use an instrument capable of resolving the highest-rate transitions; a software-generated expected count is not independent electrical verification.

**Receiver comparison.** Start the receiver, establish its baseline while the generator is idle, press START, allow the burst and receiver backlog processing to finish, and compare final count with the appropriate N or 4N expectation. Repeat before/after receiver changes without changing the generator settings. Do not use the suspect receiver as the only evidence that the generator is correct.

If hardware is unavailable during implementation, report what compiled/reviewed successfully and list the hardware checks as not yet performed. Do not describe the generator as electrically validated until those checks are done.

## 10. Definition of done

The firmware defaults to **1,000 cycles at 1 kHz**, and all six one-second frequency/count pairs in Section 3 work. A lab member can edit frequency/count, build and flash the firmware, then repeatedly obtain:

```text
ready at 11
    -> one debounced START press
    -> configured delay at 11 (one second by default)
    -> exactly N hardware-timed quadrature cycles
    -> idle at 11, awaiting a fresh press
```

There are no extra cycles, no automatic repetition, no CPU-timed stopping, and no dependence on a host computer during a burst. The implementation remains a small dedicated instrument, not a new behavior-control subsystem.

## Reference basis

Hardware/API facts should be checked against the official Raspberry Pi Pico SDK documentation (`hardware_pio`, `hardware_clocks`, and `hardware_gpio`), the Pico 2 board documentation, and the official Pico C/C++ build/UF2 documentation. The logical positive sequence matches the supplied treadmill specification, Section 10.2. All proposed defaults and scope restrictions above are requirements for this generator, not evidence of completed implementation or hardware validation.
