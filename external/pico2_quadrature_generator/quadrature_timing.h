#ifndef QUADRATURE_TIMING_H
#define QUADRATURE_TIMING_H

#include <stdbool.h>
#include <stdint.h>

#define QUADRATURE_PHASES_PER_CYCLE UINT32_C(4)
#define QUADRATURE_FIXED_PIO_CYCLES_PER_PHASE UINT32_C(4)

/*
 * Validate dimensionless GPIO numbers as a constant expression. All arguments
 * must be non-negative integer values; gpio_count is the exclusive upper pin
 * bound. Outputs must be adjacent and all three signal GPIOs must be distinct.
 */
#define QUADRATURE_SIGNAL_PIN_CONFIGURATION_IS_VALID(a_pin, b_pin, start_pin, gpio_count) \
    (                                                                                     \
        (a_pin) < (gpio_count) &&                                                         \
        (b_pin) < (gpio_count) &&                                                         \
        (start_pin) < (gpio_count) &&                                                     \
        (b_pin) > (a_pin) &&                                                              \
        ((b_pin) - (a_pin)) == 1u &&                                                      \
        (start_pin) != (a_pin) &&                                                         \
        (start_pin) != (b_pin)                                                            \
    )

/** Timing values loaded into the PIO generator for one configured frequency. */
typedef struct {
    uint32_t pio_cycles_per_quarter;
    uint32_t delay_counter;
} quadrature_timing_t;

/**
 * Calculate exact integer PIO timing for one quadrature frequency.
 *
 * Inputs:
 * - system_clock_hz: positive system clock frequency in hertz.
 * - pio_clock_divider: positive integer PIO clock divider, dimensionless.
 * - quadrature_hz: positive full quadrature cycles per second in hertz.
 * - timing: non-NULL output pointer.
 *
 * Returns true and writes dimensionless PIO cycle counts when every quarter
 * period is exactly representable and has enough cycles for the PIO phase
 * loop. Returns false without modifying *timing otherwise.
 */
static inline bool quadrature_calculate_timing(
    uint32_t system_clock_hz,
    uint32_t pio_clock_divider,
    uint32_t quadrature_hz,
    quadrature_timing_t *timing
)
{
    uint64_t transition_rate_hz;
    uint64_t timing_denominator;
    uint64_t pio_cycles_per_quarter;

    if (
        system_clock_hz == 0u ||
        pio_clock_divider == 0u ||
        quadrature_hz == 0u ||
        timing == NULL
    ) {
        return false;
    }

    transition_rate_hz = (uint64_t)quadrature_hz * QUADRATURE_PHASES_PER_CYCLE;
    if (transition_rate_hz > (UINT64_MAX / pio_clock_divider)) {
        return false;
    }
    timing_denominator = transition_rate_hz * pio_clock_divider;

    if (((uint64_t)system_clock_hz % timing_denominator) != 0u) {
        return false;
    }

    pio_cycles_per_quarter = (uint64_t)system_clock_hz / timing_denominator;
    if (
        pio_cycles_per_quarter < QUADRATURE_FIXED_PIO_CYCLES_PER_PHASE ||
        pio_cycles_per_quarter > UINT32_MAX
    ) {
        return false;
    }

    timing->pio_cycles_per_quarter = (uint32_t)pio_cycles_per_quarter;
    timing->delay_counter = (uint32_t)(
        pio_cycles_per_quarter - QUADRATURE_FIXED_PIO_CYCLES_PER_PHASE
    );
    return true;
}

/**
 * Encode a requested burst length for the PIO JMP X-- loop.
 *
 * Inputs:
 * - quadrature_cycles: requested complete cycles, dimensionless, in the range
 *   1 through UINT32_MAX.
 * - encoded_count: non-NULL output pointer for the 32-bit N-minus-one value.
 *
 * Returns true and writes the encoded counter for a valid cycle count.
 * Returns false without modifying *encoded_count otherwise.
 */
static inline bool quadrature_encode_cycle_count(
    uint64_t quadrature_cycles,
    uint32_t *encoded_count
)
{
    if (
        quadrature_cycles == 0u ||
        quadrature_cycles > UINT32_MAX ||
        encoded_count == NULL
    ) {
        return false;
    }

    *encoded_count = (uint32_t)(quadrature_cycles - 1u);
    return true;
}

#endif
