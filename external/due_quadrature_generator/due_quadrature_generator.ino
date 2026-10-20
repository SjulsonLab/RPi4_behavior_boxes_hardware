#include <Arduino.h>

#include "quadrature_timing.h"

/* Routine users should edit only these two settings. */
#ifndef QUADRATURE_HZ
#define QUADRATURE_HZ UINT32_C(1000)
#endif

#ifndef QUADRATURE_CYCLES
#define QUADRATURE_CYCLES UINT64_C(1000)
#endif

/* Hardware and usability settings normally remain unchanged. */
#define START_DELAY_MS UINT32_C(1000)
#define BUTTON_DEBOUNCE_MS UINT32_C(20)
#define A_PIN 34u
#define B_PIN 35u
#define START_BUTTON_PIN 22u

#define A_OUTPUT_MASK PIO_PC2
#define B_OUTPUT_MASK PIO_PC3
#define OUTPUT_MASK (A_OUTPUT_MASK | B_OUTPUT_MASK)

/* ArduinoCore-sam 1.6.12 lacks modern DWT names, so use Arm-defined addresses. */
#define DWT_CONTROL_REGISTER \
    (*reinterpret_cast<volatile uint32_t *>(UINT32_C(0xE0001000)))
#define DWT_CYCLE_COUNTER_REGISTER \
    (*reinterpret_cast<volatile uint32_t *>(UINT32_C(0xE0001004)))
#define DWT_CYCLE_COUNTER_ENABLE_MASK UINT32_C(1)

static_assert(
    DUE_QUADRATURE_PIN_CONFIGURATION_IS_VALID(A_PIN, B_PIN, START_BUTTON_PIN),
    "A, B, and START must retain the reviewed Arduino Due pin mapping"
);
static_assert(A_OUTPUT_MASK == (UINT32_C(1) << 2), "D34 must map to PIOC bit 2");
static_assert(B_OUTPUT_MASK == (UINT32_C(1) << 3), "D35 must map to PIOC bit 3");
static_assert(F_CPU == DUE_REQUIRED_CPU_CLOCK_HZ, "The Due build must use 84 MHz");
static_assert(
    DUE_QUADRATURE_FREQUENCY_IS_SUPPORTED(QUADRATURE_HZ),
    "QUADRATURE_HZ must be one of the six supported presets"
);
static_assert(QUADRATURE_CYCLES > 0u, "QUADRATURE_CYCLES must be positive");
static_assert(
    QUADRATURE_CYCLES <= UINT32_MAX,
    "QUADRATURE_CYCLES must fit in the 32-bit cycle countdown"
);
static_assert(BUTTON_DEBOUNCE_MS > 0u, "BUTTON_DEBOUNCE_MS must be positive");

/**
 * Drive both quadrature outputs high before enabling their output drivers.
 *
 * Inputs: none. A_PIN and B_PIN are fixed Arduino Due D-numbers. On success,
 * both corresponding 3.3 V push-pull outputs are under PIOC control at A/B=11.
 * Returns true only when the relevant control, direction, and data status bits
 * confirm that state.
 */
static bool initialize_idle_outputs(void)
{
    const uint32_t configured = PIO_Configure(PIOC, PIO_OUTPUT_1, OUTPUT_MASK, 0u);

    return
        configured != 0u &&
        (PIOC->PIO_PSR & OUTPUT_MASK) == OUTPUT_MASK &&
        (PIOC->PIO_OSR & OUTPUT_MASK) == OUTPUT_MASK &&
        (PIOC->PIO_ODSR & OUTPUT_MASK) == OUTPUT_MASK;
}

/**
 * Initialize the active-low START input and onboard ready LED.
 *
 * Inputs: none. START_BUTTON_PIN becomes a pulled-up input and LED_BUILTIN
 * becomes an output initially off. Returns no value.
 */
static void initialize_control_pins(void)
{
    pinMode(START_BUTTON_PIN, INPUT_PULLUP);
    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, LOW);
}

/**
 * Set the logical ready indication outside the timing-critical section.
 *
 * Input ready is true to illuminate LED_BUILTIN and false to turn it off.
 * Returns no value and does not touch either quadrature output.
 */
static void set_ready_indicator(bool ready)
{
    digitalWrite(LED_BUILTIN, ready ? HIGH : LOW);
}

/**
 * Stop firmware operation while retaining the initialized idle outputs.
 *
 * Inputs: none. Returns: never. The ready LED is held off and no burst can be
 * generated after configuration or hardware initialization fails.
 */
static void halt_safely(void) __attribute__((noreturn));
static void halt_safely(void)
{
    set_ready_indicator(false);
    while (true) {
        __NOP();
    }
}

/**
 * Enable and verify the Cortex-M3 DWT cycle counter.
 *
 * Inputs: none. The processor must be running at the configured 84 MHz clock.
 * Returns true only when the dimensionless 32-bit counter advances during a
 * short standalone instruction sequence; no debugger is required.
 */
static bool initialize_cycle_counter(void)
{
    uint32_t start_tick;

    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT_CONTROL_REGISTER &= ~DWT_CYCLE_COUNTER_ENABLE_MASK;
    DWT_CYCLE_COUNTER_REGISTER = 0u;
    DWT_CONTROL_REGISTER |= DWT_CYCLE_COUNTER_ENABLE_MASK;
    __DSB();
    __ISB();

    start_tick = DWT_CYCLE_COUNTER_REGISTER;
    for (uint32_t iteration = 0u; iteration < 64u; ++iteration) {
        __NOP();
    }
    return DWT_CYCLE_COUNTER_REGISTER != start_tick;
}

/**
 * Wait for one minimum quarter-period using the free-running DWT counter.
 *
 * Input interval_ticks is a positive duration in 84 MHz CPU ticks and must be
 * shorter than one counter wrap. Returns after at least that many ticks have
 * elapsed. Loop overhead may make the physical dwell slightly longer.
 */
static inline __attribute__((always_inline)) void wait_quarter_period(
    uint32_t interval_ticks
)
{
    const uint32_t start_tick = DWT_CYCLE_COUNTER_REGISTER;

    while (!quadrature_interval_has_elapsed(
        start_tick,
        DWT_CYCLE_COUNTER_REGISTER,
        interval_ticks
    )) {
        /* A volatile counter read in the condition keeps this busy wait live. */
    }
}

/**
 * Emit exactly one finite quadrature burst while normal interrupts are masked.
 *
 * Inputs:
 * - cycle_count: dimensionless complete-cycle count in [1, UINT32_MAX].
 * - quarter_period_ticks: positive minimum dwell in 84 MHz CPU ticks.
 *
 * Returns after exactly four state-changing writes per cycle plus the final
 * quarter-period hold. A/B finish at 11. The prior PRIMASK value is restored.
 */
static void run_quadrature_burst(
    uint32_t cycle_count,
    uint32_t quarter_period_ticks
)
{
    const uint32_t saved_primask = __get_PRIMASK();
    uint32_t remaining_cycles = cycle_count;

    __disable_irq();
    __DSB();
    __ISB();

    while (remaining_cycles != 0u) {
        PIOC->PIO_CODR = B_OUTPUT_MASK;
        wait_quarter_period(quarter_period_ticks);

        PIOC->PIO_CODR = A_OUTPUT_MASK;
        wait_quarter_period(quarter_period_ticks);

        PIOC->PIO_SODR = B_OUTPUT_MASK;
        wait_quarter_period(quarter_period_ticks);

        PIOC->PIO_SODR = A_OUTPUT_MASK;
        wait_quarter_period(quarter_period_ticks);

        --remaining_cycles;
    }

    __DSB();
    __set_PRIMASK(saved_primask);
    __ISB();
}

/**
 * Wait until START remains continuously at the requested logical level.
 *
 * Input expected_high is true for released/high and false for pressed/low.
 * BUTTON_DEBOUNCE_MS is interpreted as milliseconds using 1 ms samples.
 * Returns after that many consecutive matching samples. Interrupts must be on.
 */
static void wait_for_debounced_button_level(bool expected_high)
{
    uint32_t stable_time_ms = 0u;

    while (stable_time_ms < BUTTON_DEBOUNCE_MS) {
        const bool sampled_high = digitalRead(START_BUTTON_PIN) == HIGH;

        if (sampled_high == expected_high) {
            ++stable_time_ms;
        } else {
            stable_time_ms = 0u;
        }
        delay(1u);
    }
}

/**
 * Initialize the generator and process deliberate START button presses.
 *
 * Inputs: none. Returns no value; normal operation remains in the polling loop
 * indefinitely. Frequency is in complete cycles/s, count is dimensionless,
 * millisecond constants are wall-clock delays, and DWT timing is in CPU ticks.
 */
void setup(void)
{
    quadrature_timing_t timing;
    uint64_t transition_count;

    initialize_control_pins();
    if (!initialize_idle_outputs()) {
        halt_safely();
    }
    if (SystemCoreClock != F_CPU) {
        halt_safely();
    }
    if (!quadrature_calculate_timing(F_CPU, QUADRATURE_HZ, &timing)) {
        halt_safely();
    }
    if (!quadrature_calculate_transition_count(
        QUADRATURE_CYCLES,
        &transition_count
    )) {
        halt_safely();
    }
    (void)transition_count;
    if (!initialize_cycle_counter()) {
        halt_safely();
    }

    /* A low button at boot must be released before the first ready state. */
    wait_for_debounced_button_level(true);

    while (true) {
        set_ready_indicator(true);
        wait_for_debounced_button_level(false);
        set_ready_indicator(false);

        delay(START_DELAY_MS);
        run_quadrature_burst(
            (uint32_t)QUADRATURE_CYCLES,
            timing.quarter_period_ticks
        );

        /* A busy-period press cannot arm another burst without a new release. */
        wait_for_debounced_button_level(true);
    }
}

/** Inputs: none. Returns no value; setup() owns the permanent service loop. */
void loop(void)
{
}
