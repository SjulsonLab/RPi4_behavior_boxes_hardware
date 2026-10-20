#include <stdbool.h>
#include <stdint.h>

#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pio.h"
#include "pico/stdlib.h"

#include "quadrature_burst.pio.h"
#include "quadrature_timing.h"

/* Routine users should edit only these two settings. */
#define QUADRATURE_HZ UINT32_C(1000)
#define QUADRATURE_CYCLES UINT64_C(1000)

/* Hardware and usability settings normally remain unchanged. */
#define START_DELAY_MS UINT32_C(1000)
#define BUTTON_DEBOUNCE_MS UINT32_C(20)
#define A_PIN 2u
#define B_PIN 3u
#define START_BUTTON_PIN 15u

#define SYSTEM_CLOCK_HZ UINT32_C(120000000)
#define PIO_CLOCK_DIVIDER 6u
#define COMPLETION_IRQ_INDEX 0u
#define OUTPUT_PIN_MASK ((UINT32_C(1) << A_PIN) | (UINT32_C(1) << B_PIN))

_Static_assert(
    QUADRATURE_PIN_CONFIGURATION_IS_VALID(
        A_PIN,
        B_PIN,
        START_BUTTON_PIN,
        PICO_DEFAULT_LED_PIN,
        NUM_BANK0_GPIOS
    ),
    "Quadrature, START, and LED pins must be valid, distinct, and adjacent where required"
);
_Static_assert(SYSTEM_CLOCK_HZ > 0u, "SYSTEM_CLOCK_HZ must be positive");
_Static_assert(PIO_CLOCK_DIVIDER > 0u, "PIO_CLOCK_DIVIDER must be positive");
_Static_assert(PIO_CLOCK_DIVIDER <= UINT16_MAX, "PIO divider exceeds the integer divider field");
_Static_assert(BUTTON_DEBOUNCE_MS > 0u, "BUTTON_DEBOUNCE_MS must be positive");
_Static_assert(QUADRATURE_HZ > 0u, "QUADRATURE_HZ must be positive");
_Static_assert(QUADRATURE_CYCLES > 0u, "QUADRATURE_CYCLES must be positive");
_Static_assert(QUADRATURE_CYCLES <= UINT32_MAX, "QUADRATURE_CYCLES exceeds the PIO counter");

typedef struct {
    PIO pio;
    uint state_machine;
} quadrature_generator_t;

/**
 * Drive both waveform GPIOs high using SIO before PIO takes ownership.
 *
 * Inputs: none. Outputs: A_PIN and B_PIN become 3.3 V digital outputs at the
 * logical idle state A=1, B=1. Returns no value.
 */
static void initialize_idle_outputs(void)
{
    gpio_init(A_PIN);
    gpio_put(A_PIN, true);
    gpio_set_dir(A_PIN, GPIO_OUT);

    gpio_init(B_PIN);
    gpio_put(B_PIN, true);
    gpio_set_dir(B_PIN, GPIO_OUT);
}

/**
 * Initialize the active-low START input and the ready indicator.
 *
 * Inputs: none. Outputs: START_BUTTON_PIN is a pulled-up input and the onboard
 * LED is a digital output initially off. Returns no value.
 */
static void initialize_control_gpio(void)
{
    gpio_init(START_BUTTON_PIN);
    gpio_set_dir(START_BUTTON_PIN, GPIO_IN);
    gpio_pull_up(START_BUTTON_PIN);

    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    gpio_put(PICO_DEFAULT_LED_PIN, false);
}

/**
 * Set whether the generator is ready to accept a fresh press.
 *
 * Input ready: true turns the onboard LED on; false turns it off. This is a
 * logical indicator with no timing guarantee. Returns no value.
 */
static void set_ready_indicator(bool ready)
{
    gpio_put(PICO_DEFAULT_LED_PIN, ready);
}

/**
 * Stop forever with the waveform outputs left at their current idle state.
 *
 * Inputs: none. Returns: never. This is used only when configuration or
 * initialization validation fails, before any burst can start.
 */
static _Noreturn void halt_safely(void)
{
    set_ready_indicator(false);
    while (true) {
        tight_loop_contents();
    }
}

/**
 * Configure and verify the 120 MHz system clock.
 *
 * Inputs: none. Returns true only when clk_sys is configured to exactly
 * SYSTEM_CLOCK_HZ hertz; returns false otherwise.
 */
static bool configure_system_clock(void)
{
    if (!set_sys_clock_hz(SYSTEM_CLOCK_HZ, false)) {
        return false;
    }
    return clock_get_hz(clk_sys) == SYSTEM_CLOCK_HZ;
}

/**
 * Wait until the sampled START level is stable for the debounce interval.
 *
 * Input expected_high: true waits for released/high; false waits for
 * pressed/low. BUTTON_DEBOUNCE_MS is measured in milliseconds using 1 ms
 * polling samples. Returns after the requested level has remained sampled
 * continuously for the configured interval.
 */
static void wait_for_debounced_button_level(bool expected_high)
{
    uint32_t stable_time_ms = 0u;

    while (stable_time_ms < BUTTON_DEBOUNCE_MS) {
        const bool sampled_high = gpio_get(START_BUTTON_PIN) != 0;
        if (sampled_high == expected_high) {
            ++stable_time_ms;
        } else {
            stable_time_ms = 0u;
        }
        sleep_ms(1u);
    }
}

/**
 * Claim and initialize one PIO state machine while preserving idle A/B=11.
 *
 * Input generator: non-NULL output object. The function loads the fixed PIO
 * program into pio0, sets a dimensionless integer clock divider, prepares the
 * PIO output latch and direction at A/B=11, then transfers GPIO ownership.
 * Returns true on success and writes the claimed PIO/state-machine handles;
 * returns false on allocation or state-machine initialization failure.
 */
static bool initialize_quadrature_generator(quadrature_generator_t *generator)
{
    const PIO pio = pio0;
    const int claimed_state_machine = pio_claim_unused_sm(pio, false);
    int program_offset;
    pio_sm_config configuration;
    uint state_machine;

    if (generator == NULL || claimed_state_machine < 0) {
        return false;
    }
    state_machine = (uint)claimed_state_machine;

    program_offset = pio_add_program(pio, &quadrature_burst_program);
    if (program_offset < 0) {
        return false;
    }

    configuration = quadrature_burst_program_get_default_config((uint)program_offset);
    sm_config_set_set_pins(&configuration, A_PIN, 2u);
    sm_config_set_clkdiv_int_frac8(&configuration, PIO_CLOCK_DIVIDER, 0u);
    if (pio_sm_init(pio, state_machine, (uint)program_offset, &configuration) != PICO_OK) {
        return false;
    }

    /* Prepare PIO's output latch and enable state before changing the mux. */
    pio_sm_set_pins_with_mask(pio, state_machine, OUTPUT_PIN_MASK, OUTPUT_PIN_MASK);
    pio_sm_set_pindirs_with_mask(pio, state_machine, OUTPUT_PIN_MASK, OUTPUT_PIN_MASK);
    pio_gpio_init(pio, A_PIN);
    pio_gpio_init(pio, B_PIN);

    pio_interrupt_clear(pio, COMPLETION_IRQ_INDEX);
    pio_sm_clear_fifos(pio, state_machine);
    pio_sm_clkdiv_restart(pio, state_machine);
    pio_sm_set_enabled(pio, state_machine, true);

    generator->pio = pio;
    generator->state_machine = state_machine;
    return true;
}

/**
 * Run one complete hardware-timed burst and wait for its completion flag.
 *
 * Inputs:
 * - generator: initialized PIO/state-machine handle.
 * - encoded_cycle_count: dimensionless N-minus-one PIO X preload.
 * - delay_counter: dimensionless PIO Y preload for each quarter-period.
 *
 * Returns after PIO has emitted exactly N cycles, completed the final quarter
 * interval at A/B=11, and raised IRQ flag 0. CPU polling latency cannot change
 * the waveform. The IRQ is cleared so PIO returns to its blocking input state.
 */
static void run_quadrature_burst(
    const quadrature_generator_t *generator,
    uint32_t encoded_cycle_count,
    uint32_t delay_counter
)
{
    pio_interrupt_clear(generator->pio, COMPLETION_IRQ_INDEX);
    pio_sm_put_blocking(generator->pio, generator->state_machine, encoded_cycle_count);
    pio_sm_put_blocking(generator->pio, generator->state_machine, delay_counter);

    while (!pio_interrupt_get(generator->pio, COMPLETION_IRQ_INDEX)) {
        tight_loop_contents();
    }
    pio_interrupt_clear(generator->pio, COMPLETION_IRQ_INDEX);
}

/**
 * Initialize the generator and service deliberate START button presses.
 *
 * Inputs: none. Returns 0 only by C signature convention; normal firmware
 * operation is an infinite polling loop. All waveform counts are
 * dimensionless and all configured delays are in milliseconds.
 */
int main(void)
{
    quadrature_timing_t timing;
    quadrature_generator_t generator;
    uint32_t encoded_cycle_count;

    initialize_idle_outputs();
    initialize_control_gpio();

    if (!configure_system_clock()) {
        halt_safely();
    }
    if (!quadrature_calculate_timing(
            clock_get_hz(clk_sys),
            PIO_CLOCK_DIVIDER,
            QUADRATURE_HZ,
            &timing
        )) {
        halt_safely();
    }
    if (!quadrature_encode_cycle_count(QUADRATURE_CYCLES, &encoded_cycle_count)) {
        halt_safely();
    }
    if (!initialize_quadrature_generator(&generator)) {
        halt_safely();
    }

    /* A low button at boot must be released before the first ready state. */
    wait_for_debounced_button_level(true);

    while (true) {
        set_ready_indicator(true);
        wait_for_debounced_button_level(false);
        set_ready_indicator(false);

        sleep_ms(START_DELAY_MS);
        run_quadrature_burst(&generator, encoded_cycle_count, timing.delay_counter);

        /* Busy-period presses cannot arm a later burst without a new press. */
        wait_for_debounced_button_level(true);
    }
}
