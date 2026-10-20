# Arduino Due Known-Count Quadrature Burst Generator

## Adaptation basis and priority

Adapt the user-supplied `pico2_quadrature_generator_spec_v2.md`, retaining its operating sequence, six presets, direction, idle state, count contract, button behavior, and small-project scope. This document replaces the Pico-specific implementation requirements; it does not change the Pico project.

The user's additional requirement is authoritative:

> Exact cycle count is more important than exact frequency or exact burst duration. Prefer a small, understandable implementation which emits every required transition over a precisely clocked waveform with an uncertain stopping count.

The Due-specific architecture below is an engineering recommendation based on the official hardware/core references at the end. It is not something established by the Pico source document, and it is not evidence of completed firmware or hardware validation.

## 1. Purpose and scope

Implement a small, standalone **Arduino Due / SAM3X8E** sketch using the official **Arduino SAM Boards (32-bits ARM Cortex-M3)** package. Use normal `setup()` / `loop()` organization and the standard 84 MHz configuration. Prefer the Programming Port for uploading and power. Do not target an Uno, Mega, or SAMD board. [1, 8]

The firmware has one job:

> Wait for a deliberate START-button press, wait one second, emit exactly N complete quadrature cycles, and remain idle until another deliberate press.

The Due is an independent signal source. Do not change the receiver, reset the receiver, or add communication commands to it.

### Fixed architecture

Use **a CPU-counted burst with direct, masked GPIO register writes**, paced with the Cortex-M3 hardware cycle counter. Every complete iteration produces exactly four transitions. A countdown of completed cycles—not elapsed time—determines completion.

This deliberately differs from the Pico implementation: the Due CPU generates the transitions, rather than a programmable PIO state machine. SAM3X **PIO means Parallel Input/Output**, not the Pico's programmable waveform engine. [4]

Use this priority order:

1. Exact count, correct state ordering, and no additional transitions.
2. Clean electrical signals and repeatable quarter-cycle spacing.
3. Frequency as close to the requested preset as practical.
4. Minimal code and development burden; do not pursue exact timing with an elaborate peripheral architecture.

A slower but correctly counted burst is usable at its **measured actual rate**. It is not proof of receiver performance at the requested higher rate. Preserve all six targets and report the achieved results rather than quietly omitting or relabeling a target.

## 2. Wiring and pin ownership

Use these fixed defaults. D-numbers are **Arduino Due digital-pin labels**, not SAM3X bit numbers or Raspberry Pi BCM numbers.

| Purpose | Due connection | Configuration |
|---|---|---|
| Quadrature A | **D34 = PIOC bit 2 / PC2** | Actively driven output |
| Quadrature B | **D35 = PIOC bit 3 / PC3** | Actively driven output |
| START | **D22 → normally-open button → GND** | `INPUT_PULLUP`, active low |
| RESET | **Onboard RESET button** | Restarts firmware; not START |
| Optional external RESET | RESET header signal → button → GND | Duplicates reset, not ERASE |
| Reference | Due GND → receiver GND | Required common ground |
| Power/programming | Due **Programming USB port** | No open serial terminal required |

The D34/D35 mapping is defined in the official Due variant. Both outputs use one port so the burst can use fixed masks and avoid repeated pin lookup. [2]

Use 3.3 V-compatible connections, short signal/ground wiring, and a common ground. The Due is a 3.3 V board; do not apply 5 V logic to its GPIO. Disconnect the real encoder before driving the receiver inputs, configure those receiver pins as inputs, and power both boards before testing. Do not intentionally drive an unpowered receiver. [9]

The short-jumper START circuit uses the internal pull-up; no added button series resistor is required for this input-to-ground arrangement. Use RESET, **not ERASE**, for ordinary restarts. [3, 9]

Do not copy Pico GP2/GP3 numbers into this sketch. Keep A/B pins fixed, or require an explicit update and validation of their port masks if changed. Arbitrary pin-routing support is out of scope.

## 3. Easily editable settings

Place one clearly marked block near the top of the sketch:

| Setting | Default | Meaning |
|---|---:|---|
| `QUADRATURE_HZ` | `1000` | Requested complete cycles per second, not combined edge rate |
| `QUADRATURE_CYCLES` | `1000` | Exact complete cycles per burst |
| `START_DELAY_MS` | `1000` | Delay after accepting the debounced press |
| `BUTTON_DEBOUNCE_MS` | `20` | Stable level required for press/release acceptance |
| `A_PIN` | `34` | Fixed Due pin; mapping must agree with A mask |
| `B_PIN` | `35` | Fixed Due pin; mapping must agree with B mask |
| `START_BUTTON_PIN` | `22` | Active-low START input |

Ordinary users edit **frequency and cycle count**, rebuild, and upload. There is no menu, parser, persistent configuration, serial command protocol, or frequency button.

### Required requested presets

| Requested frequency | Exact cycles | Nominal duration `N/f` | Exact total A/B transitions |
|---|---:|---:|---:|
| 10 Hz | 10 | 1 s | 40 |
| 100 Hz | 100 | 1 s | 400 |
| **1 kHz — default** | **1,000** | **1 s** | **4,000** |
| 10 kHz | 10,000 | 1 s | 40,000 |
| 100 kHz | 100,000 | 1 s | 400,000 |
| 1 MHz | 1,000,000 | 1 s | 4,000,000 |

The one-second START delay is separate from the nominal one-second burst. Under this count-first adaptation, actual burst duration may exceed the nominal value if output is slower.

Keep frequency and count independently editable for small-count tests. Support exactly the six requested frequency values in the table above; reject zero and every other frequency before generation. Require a positive integer N from 1 through `UINT32_MAX`. Use a full-cycle countdown or another loop whose boundary cannot wrap incorrectly at the maximum. Use 64-bit arithmetic for `4 * N` and configuration calculations which could overflow.

Do not shorten a requested burst to make it finish in one second. Large independent count settings may keep the instrument busy for a very long time with normal maskable interrupts disabled; for example, `UINT32_MAX` cycles at 10 Hz would take approximately 13.6 years. Document `N/f` and the RESET interruption behavior rather than silently capping N. The full numeric range is an interface requirement, not a practical validated operating range.

## 4. Waveform definition and exact counts

### Logical sequence

Idle is **A=1, B=1**. Emit this fixed sequence:

```text
11 → 10 → 00 → 01 → 11
```

The labels mean **A then B**. One full cycle consists of:

| Step | Operation | Resulting AB |
|---|---|---|
| Start | Already idle | `11` |
| 1 | B low | `10` |
| 2 | A low | `00` |
| 3 | B high | `01` |
| 4 | A high | `11` |

Repeat those four operations N times. Never change both signals together during the running sequence.

Preserve the Pico spec's encoder-positive ordering. Do not call it animal-forward motion. Reverse-direction testing can use swapped A/B connections while idle; no direction interface is required.

### Phase and count contract

The target waveform has equal quarter-cycle intervals, 50% running duty cycle on each channel, and a quarter-period phase offset. Small measured timing errors are permissible; wrong ordering, omitted transitions, and extra transitions are not.

For N completed cycles:

```text
A rising edges:          N
A falling edges:         N
B rising edges:          N
B falling edges:         N
Combined transitions:  4N
Final state:            11
```

A legacy A-rising decoder should count N; an x4 decoder should count 4N, with receiver-dependent sign.

The count guarantee applies to an uninterrupted, electrically validated run. Reset, power loss, a processor fault, or incorrect wiring invalidates that run. Initial settling after power/reset is outside the burst; the receiver must establish its baseline after the source is ready.

## 5. Due generation and timing requirements

### 5.1 Direct GPIO interface

Use the SAM3X PIO **set and clear output registers**, not repeated `digitalWrite()` calls, in the waveform loop. `digitalWrite()` includes pin/state handling which is unnecessary for these fixed outputs. [3, 4]

For the selected pins:

```text
A mask: PIO_PC2 / (1u << 2)
B mask: PIO_PC3 / (1u << 3)
Port:   PIOC

B low:  write B mask to PIOC->PIO_CODR
A low:  write A mask to PIOC->PIO_CODR
B high: write B mask to PIOC->PIO_SODR
A high: write A mask to PIOC->PIO_SODR
```

These are register-operation requirements, not a complete firmware implementation.

Use **plain assignment**, not `|=` or another read-modify-write operation, on these write-only command registers. Preserve volatile register access and touch only the selected masks. Do not replace the whole port value or alter unrelated pins. No per-edge `pinMode()`, peripheral remuxing, or pin-table lookup is allowed. [4]

Precompute masks, register addresses, and timing parameters. A few inlined operations are preferable to a generic GPIO abstraction. Ordinary Arduino calls remain suitable for the button and LED outside the burst.

### 5.2 Counted CPU loop owns completion

Use one full-cycle loop. Decrement its remaining-cycle count only after the four specified transitions have been emitted.

After cycle N, perform no further waveform write which changes either output. Leave both pins actively high. Complete one final quarter-period hold at `11` before restoring the saved interrupt mask; the hold must not create another edge.

**Forbidden:** start a continuous timer/PWM waveform, wait approximately `N/f`, then disable it. Counting timer interrupts is also not sufficient unless each physical output cycle and the terminal boundary are independently guaranteed.

The selected direct-write approach has a useful property: delayed CPU execution normally stretches a state instead of allowing an autonomous output to run for extra cycles. It still requires electrical validation; counting stores is not a measurement of pulses at the receiver.

Do not use time-derived phase selection, drop states to catch up, or emit compressed catch-up transitions to force a one-second duration.

### 5.3 Cycle-counter pacing

Use the hardware **DWT CYCCNT** counter as the fine-grained time reference while the CPU runs. Enable trace access and cycle counting during setup, then verify that the counter advances without a debugger attached. [5]

The normal 84 MHz clock gives these arithmetic budgets:

| Requested quadrature rate | Target spacing between adjacent transitions | CPU clock ticks per spacing |
|---|---:|---:|
| 10 Hz | 25 ms | 2,100,000 |
| 100 Hz | 2.5 ms | 210,000 |
| 1 kHz | 250 µs | 21,000 |
| 10 kHz | 25 µs | 2,100 |
| 100 kHz | 2.5 µs | 210 |
| 1 MHz | 250 ns | 21 |

The formula is:

```text
quarter_period_ticks = CPU_CLOCK_HZ / (4 * QUADRATURE_HZ)
```

All six nominal values divide exactly at 84 MHz. **That does not prove the software meets them:** counter reads, branches, GPIO bus writes, and the cycle-loop boundary all consume part of the budget.

Use `F_CPU` and verify agreement with the configured system clock. Do not copy the Pico's proposed 120 MHz clock or overclock the Due. [8]

Prefer a compact, inlined cycle-counter wait with local, wrap-safe interval arithmetic. Its exact placement is an implementation detail, but the timing calculation must include surrounding overhead. A relative wait which yields slightly slower output is acceptable for this count-first instrument.

If scheduling is late, preserve the next state and let time slip. Do not advance a global schedule by issuing adjacent state changes with no suitable intervening dwell. Any deadline-based implementation must avoid that catch-up behavior explicitly.

At the highest rate, a full 21-tick wait **plus** nonzero loop overhead will be slower than 1 MHz. Do not label it exact based on the wait alone. Account for measured overhead or report the slower result.

### 5.4 Counter access and rollover

Prefer CMSIS names when available. Check the actual installed SAM core: the older Cortex-M3 header in ArduinoCore-sam does not expose all modern DWT definitions. Do not replace the core's entire CMSIS package to obtain a few register names. [5, 6]

If necessary, use a few clearly named, local volatile register definitions verified against Arm documentation:

```text
DEMCR:      0xE000EDFC; trace-enable bit 24
DWT_CTRL:   0xE0001000; cycle-counter-enable bit 0
DWT_CYCCNT: 0xE0001004
```

Preserve unrelated control bits. Verify counter operation before accepting START; failure must prevent generation, not select an uncalibrated fallback. [5]

CYCCNT is 32-bit. At 84 MHz it wraps approximately every 51.13 seconds. Use unsigned subtraction over short local intervals so a burst can span rollover. Do not compare entire arbitrarily long bursts using a single 32-bit elapsed difference. A small review or test starting the counter near wrap is sufficient; a new timing framework is unnecessary.

### 5.5 Reaching the high rates without overbuilding

Begin with direct masked writes and inlined counter pacing. Keep one count engine and no per-edge interrupts, dynamic allocation, floating-point calculations, serial output, or GPIO readback in the running loop.

At 1 MHz, 21 clock ticks per state is a tight software budget. Inspect the compiled loop and measure before claiming that rate. A **small, local fast-path helper**, including short documented inline assembly or calibrated instruction delays, is allowed if necessary. Its four transition writes and cycle countdown must remain explicit and auditable. Include the cycle-loop branch in phase balancing.

Do not add a general timer framework, DMA descriptor machinery, output buffers proportional to N, external gating hardware, or a second generator backend just to hit a round frequency.

If a compact implementation cannot reach a requested high rate, preserve the requested count, document its achieved rate, and report the timing limitation. Do not substitute a smaller count or falsely claim a 1 MHz test. A receiver tested at a lower measured rate has only been tested at that rate.

If the default path needs timing constants derived from measurement, keep them together, explain them, and identify the compiler/core version. Do not hide unexplained per-preset magic numbers.

### 5.6 Interrupts during a burst

For this standalone instrument, save the interrupt mask and disable normal maskable interrupts **only during the waveform section**, then restore the saved mask after the final output write and hold. Use the core's CMSIS interrupt primitives and appropriate compiler/memory ordering. Do not blindly enable interrupts if they were previously disabled. [7]

The pre-burst delay and button debounce run with interrupts enabled. Do not use `delay()`, `millis()`, `micros()`, interrupt-dependent communication, or `yield()` in the masked waveform section.

Arduino's timekeeping uses SysTick-related state. A long masked burst can cause that bookkeeping to miss elapsed ticks; restoring interrupts does not reconstruct all missed milliseconds. Use the independent cycle counter for waveform timing, and fresh time baselines for later button/debounce/delay handling. Do not attempt to repair Arduino global timekeeping. [10]

A typical preset occupies the CPU for about a second, possibly longer. USB handling and serial work are intentionally unavailable during it. Verify the selected build's watchdog configuration cannot unexpectedly reset a valid burst. Do not add watchdog-service work to every edge. Hardware RESET remains the interruption method; it invalidates the burst.

### 5.7 Why not the native Gray-code peripherals first?

The SAM3X TC and PWM peripherals do provide Gray-code waveform generation. That is useful hardware capability, but it is not by itself an arbitrary-N burst-count guarantee. [4]

For this small interim project, do not make autonomous Gray/PWM output the baseline and then solve count precision with a timing-sensitive stop interrupt. Use the explicit counted register sequence. A future hardware-counted alternative would need a separately reviewed exact final-cycle mechanism; it is not an additional deliverable here.

### 5.8 Timing descriptions and burst boundaries

Keep **requested frequency**, **calculated timing**, and **measured output frequency** distinct.

For ideal equal spacing, first changed edge to last changed edge is `(4N - 1)/(4f)`. Including one final quarter-period at idle gives `N/f`. Actual software-paced duration must be measured, not inferred solely from the requested rate.

Measure recurring same-channel periods for frequency and adjacent A/B intervals for phase uniformity. Do not describe an approximate, CPU-paced output as a calibrated frequency standard or as equivalent to the Pico's autonomous timing.

## 6. Startup, START button, and reset behavior

### Startup/reset

Establish idle `11` without intentionally driving a low-going initialization pulse. Preload the selected output latches high before enabling their output drivers, claim PIO ownership, and keep the outputs push-pull. Do not allow a later generic initialization call to undo that state. The SAM3X supports establishing output data before enabling the drive. [4]

Configure START with its pull-up, initialize/verify the time reference, validate the constants, and wait. No burst runs automatically at power-up, upload, or RESET. START held at boot requires a stable release followed by a fresh press.

Firmware does not guarantee glitch-free electrical behavior during reset or power loss. Start receiver counting after the generator has settled. Do not reset the generator between routine bursts.

### Accepted press

A press is accepted only while ready and after START has stayed low for `BUTTON_DEBOUNCE_MS`.

```text
accept debounced START
    → hold 11 for START_DELAY_MS, with interrupts enabled
    → run exactly one CPU-counted burst
    → leave 11 and restore the prior interrupt mask
    → require a fresh stable-release observation
    → arm for a new press
```

The one-second delay begins after debounce acceptance, not after release. With the defaults, physical press to first edge is approximately 1.02 seconds plus small setup overhead.

Releasing during the delay does not cancel the burst. Ignore, rather than queue, presses during the delay/burst. A held button cannot retrigger. A low input already present at completion is not a new press.

Simple blocking waits and polling are appropriate. No button interrupt, event scheduler, cancellation protocol, or background task is needed.

### Reset

Use the onboard **RESET** button, or an external duplicate on the RESET signal. Do not use ERASE as a start/reset control. Reset restarts initialization and never resumes an interrupted test. [9]

## 7. Minimal feedback and failure handling

Use `LED_BUILTIN` as a simple ready indicator: on when armed for a fresh press; off during delay, burst, and release wait. Set it outside the timing-critical section.

USB serial is optional and disabled by default. No host connection or terminal is required. Development messages, if added, occur outside the burst and must not block waiting for a host.

Invalid board selection, pin mapping, count, frequency, clock, or non-running cycle counter prevents generation. Prefer compile-time checks for constants and a small initialization check for the time reference. Leave outputs at `11` and the ready LED off on initialization failure. No automatic restart, fallback generator, elaborate health state machine, or extra status protocol is required.

Do not print an internal loop count as though it were an independently measured electrical count.

## 8. Project size, build, and deliverables

Target approximately:

```text
due_quadrature_generator/
    due_quadrature_generator.ino
    README.md
```

One extra local source/header is acceptable only if genuinely needed for the short high-rate helper; default to one sketch. Do not build classes, abstract backends, a reusable waveform library, or a separate host application.

Use the official Arduino SAM board package, not SAMD. The intended Programming Port target is `arduino:sam:arduino_due_x_dbg`; verify it against the installed package. Record the tested package/compiler versions and provide the actual build/upload instructions. No code or build steps are supplied by this specification itself. [1, 8]

The README must cover wiring, the two routine configuration constants, all six presets, normal operation, count expectations, measured rate limitations, interrupt/timekeeping caveats, and RESET versus ERASE. Keep measurement results in a compact README table, not a new reporting system.

## 9. Proportionate validation

### Build and count review

Build for the Due target. Review N=1, 2, and 3 explicitly: exactly four state-changing writes per cycle, correct order, and no write beginning cycle N+1. Check the full allowed counter range without attempting a billions-cycle hardware test.

Review volatile writes, mask ownership, arithmetic width, counter rollover, and interrupt restoration. Inspect optimized output where high-rate timing depends on it. No large simulator or new automated testing framework is required.

### Independent electrical count check

Use a logic analyzer, oscilloscope with suitable counting capability, or independent hardware counter. Check N=1, 2, 3, and 10; repeat after switching to any optimized high-rate path.

Verify N rising/falling edges on each channel, the specified ordering, final `11`, no extra edge afterward, and repeatability across button presses. Check START held at boot, held throughout a burst, pressed while busy, and released normally. Check the pre-burst delay independently.

### All six requested presets

For every preset, record:

| Requested Hz | Requested N | Independently measured A/B counts | Measured Hz | Phase-spacing range / skew | Actual duration | Qualification |
|---|---|---|---|---|---|---|

Separate **count correctness** from **frequency accuracy**. Correct count is mandatory. Record average frequency and phase-spacing variation rather than inventing a precise frequency tolerance the user has not selected.

A result such as “1,000,000 cycles, measured 0.92 MHz” is a valid count-first diagnostic at 0.92 MHz, not evidence of reception at 1 MHz. This is an illustration of reporting, not a prediction for the Due implementation.

At high rates, a complete long logic trace is not mandatory if an independent hardware counter establishes full-burst totals and a sufficiently fast scope/analyzer separately verifies ordering and spacing. The combined evidence must cover both channels and the stop boundary. No output count may be inferred from a nominal one-second duration alone.

Test standalone without an attached debugger. Identify unmeasured high-rate presets as unvalidated, not passed. If changes affect timing, repeat affected electrical checks.

### Receiver before/after comparison

Start and baseline the receiver while the generator is idle, press START, wait for output and receiver backlog to finish, then compare N or 4N as appropriate. Preserve generator firmware/settings and wiring across the receiver comparison. Use measured generator rate when interpreting receiver capacity.

Do not use the suspect receiver as the sole evidence that the source is correct.

If hardware is unavailable, deliver the build/review result and list electrical tests as pending. Do not claim a measured maximum, exact frequency, or verified physical count without evidence.

## 10. Definition of done

A lab member can edit frequency/count, upload the sketch, and repeatedly obtain:

```text
ready at 11
    → deliberate debounced START
    → one-second delay
    → exactly N complete quadrature cycles
    → idle 11, awaiting release and a fresh press
```

All six requested configurations must have an explicit count result and timing-qualification status. An interim handoff may identify high-rate timing as unmet or awaiting measurement; it must not claim the entire requested range has been validated.

**Non-negotiable:** exact completed-cycle count, correct quadrature ordering, no extra transition, no automatic repetition, no elapsed-time stopping, and no host-computer dependency.

**Allowed compromise:** documented frequency/duration error, or an explicitly reported lower achieved rate, while keeping the exact requested count and clean waveform. Prefer that over an uncertain count or a substantially larger codebase.

## Reference basis

[P] User-supplied `pico2_quadrature_generator_spec_v2.md`, especially Sections 1–4 and 6–10. This is the basis for preserved behavior and settings. Due architecture, pin selection, timing approach, and count-first qualifications are explicit adaptations.

[1] Arduino, [Installing the SAM core for the Arduino Due](https://docs.arduino.cc/software/ide-v1/tutorials/getting-started/cores/arduino-sam). Build-environment basis.

[2] ArduinoCore-sam, [Due variant pin definitions](https://github.com/arduino/ArduinoCore-sam/blob/master/variants/arduino_due_x/variant.cpp). D34/PC2, D35/PC3, and D22/PB26 mappings.

[3] ArduinoCore-sam, [wiring_digital.c](https://github.com/arduino/ArduinoCore-sam/blob/master/cores/arduino/wiring_digital.c). `INPUT_PULLUP`, `digitalWrite()`, and Arduino pin-state handling.

[4] Microchip/Atmel, [SAM3X/SAM3A datasheet](https://ww1.microchip.com/downloads/en/DeviceDoc/Atmel-11057-32-bit-Cortex-M3-Microcontroller-SAM3X-SAM3A_Datasheet.pdf), revision 11057C. Sections 31.5.4–31.5.7 and 31.7.10–31.7.11 cover PIO output control and set/clear registers; Section 11.5.5 covers DWT; Sections 36.6.15 and 38.6.2.3 cover native Gray-code modes. Use the actual output-control semantics, not an assumption that autonomous waveform support proves exact finite-burst stopping.

[5] Arm CMSIS, [Cortex-M3 core register definitions](https://github.com/ARM-software/CMSIS_5/blob/develop/CMSIS/Core/Include/core_cm3.h) and [DWT register structure](https://arm-software.github.io/CMSIS_6/latest/Core/structDWT__Type.html). Cycle-counter layout and enable definitions.

[6] ArduinoCore-sam, [bundled Cortex-M3 header](https://github.com/arduino/ArduinoCore-sam/blob/master/system/CMSIS/CMSIS/Include/core_cm3.h). Check installed-header compatibility before using modern DWT names.

[7] ArduinoCore-sam/CMSIS, [core_cmFunc.h](https://github.com/arduino/ArduinoCore-sam/blob/master/system/CMSIS/CMSIS/Include/core_cmFunc.h). Interrupt-mask access and interrupt disable/restore primitives.

[8] ArduinoCore-sam, [boards.txt](https://github.com/arduino/ArduinoCore-sam/blob/master/boards.txt). Programming Port board identifier, SAM3X target, and 84 MHz build clock.

[9] Arduino, [Due board documentation](https://docs.arduino.cc/hardware/due/) and its [board datasheet](https://docs.arduino.cc/resources/datasheets/A000062-datasheet.pdf). Board electrical interface, Programming Port, RESET, and ERASE.

[10] ArduinoCore-sam, [wiring.c](https://github.com/arduino/ArduinoCore-sam/blob/master/cores/arduino/wiring.c). Arduino `millis()`, `micros()`, and `delay()` timebase dependencies.

Hardware/API facts should be checked against the installed core and official references during implementation. These references do not establish measured timing of a sketch which has not yet been built and electrically tested.
