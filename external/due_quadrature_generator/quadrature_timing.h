#ifndef DUE_QUADRATURE_TIMING_H
#define DUE_QUADRATURE_TIMING_H

#include <stddef.h>
#include <stdint.h>

#define DUE_REQUIRED_CPU_CLOCK_HZ UINT32_C(84000000)
#define QUADRATURE_PHASES_PER_CYCLE UINT32_C(4)

/* The first release deliberately supports only the six validated presets. */
#define DUE_QUADRATURE_FREQUENCY_IS_SUPPORTED(quadrature_hz) \
    (                                                          \
        (quadrature_hz) == UINT32_C(10) ||                     \
        (quadrature_hz) == UINT32_C(100) ||                    \
        (quadrature_hz) == UINT32_C(1000) ||                   \
        (quadrature_hz) == UINT32_C(10000) ||                  \
        (quadrature_hz) == UINT32_C(100000) ||                 \
        (quadrature_hz) == UINT32_C(1000000)                   \
    )

/* D-numbers must retain the fixed PIOC mask mapping used by the sketch. */
#define DUE_QUADRATURE_PIN_CONFIGURATION_IS_VALID(a_pin, b_pin, start_pin) \
    ((a_pin) == 34u && (b_pin) == 35u && (start_pin) == 22u)

/** Timing derived for one supported quadrature rate. */
typedef struct {
    uint32_t quarter_period_ticks;
} quadrature_timing_t;

/**
 * Calculate the cycle-counter interval for one supported rate.
 *
 * Inputs:
 * - cpu_clock_hz: processor clock frequency in hertz; must be exactly 84 MHz.
 * - quadrature_hz: complete quadrature cycles per second; must be one of the
 *   six supported positive integer presets.
 * - timing: non-NULL output pointer.
 *
 * Returns true and writes a positive, dimensionless CPU-tick count when the
 * quarter period is exactly representable. Returns false without modifying
 * *timing otherwise.
 */
static inline bool quadrature_calculate_timing(
    uint32_t cpu_clock_hz,
    uint32_t quadrature_hz,
    quadrature_timing_t *timing
)
{
    uint64_t timing_denominator;
    uint64_t quarter_period_ticks;

    if (
        cpu_clock_hz != DUE_REQUIRED_CPU_CLOCK_HZ ||
        !DUE_QUADRATURE_FREQUENCY_IS_SUPPORTED(quadrature_hz) ||
        timing == NULL
    ) {
        return false;
    }

    timing_denominator =
        (uint64_t)quadrature_hz * (uint64_t)QUADRATURE_PHASES_PER_CYCLE;
    if (((uint64_t)cpu_clock_hz % timing_denominator) != 0u) {
        return false;
    }

    quarter_period_ticks = (uint64_t)cpu_clock_hz / timing_denominator;
    if (quarter_period_ticks == 0u || quarter_period_ticks > UINT32_MAX) {
        return false;
    }

    timing->quarter_period_ticks = (uint32_t)quarter_period_ticks;
    return true;
}

/**
 * Calculate the number of state-changing writes in a requested burst.
 *
 * Inputs:
 * - quadrature_cycles: dimensionless complete-cycle count in [1, UINT32_MAX].
 * - transition_count: non-NULL output pointer.
 *
 * Returns true and writes the dimensionless value 4 * quadrature_cycles using
 * 64-bit arithmetic. Returns false without modifying *transition_count for an
 * invalid count or pointer.
 */
static inline bool quadrature_calculate_transition_count(
    uint64_t quadrature_cycles,
    uint64_t *transition_count
)
{
    if (
        quadrature_cycles == 0u ||
        quadrature_cycles > UINT32_MAX ||
        transition_count == NULL
    ) {
        return false;
    }

    *transition_count = quadrature_cycles * QUADRATURE_PHASES_PER_CYCLE;
    return true;
}

/**
 * Return elapsed 32-bit counter ticks using modulo arithmetic.
 *
 * Inputs start_tick and current_tick are dimensionless samples from the same
 * monotonically increasing 32-bit counter. Returns elapsed ticks modulo 2^32.
 */
static inline uint32_t quadrature_elapsed_ticks(
    uint32_t start_tick,
    uint32_t current_tick
)
{
    return current_tick - start_tick;
}

/**
 * Test whether a short local counter interval has elapsed.
 *
 * Inputs start_tick and current_tick are dimensionless 32-bit counter samples.
 * interval_ticks is a positive duration in CPU ticks shorter than one complete
 * counter wrap. Returns true once at least interval_ticks have elapsed,
 * including when the counter wrapped between the two samples.
 */
static inline bool quadrature_interval_has_elapsed(
    uint32_t start_tick,
    uint32_t current_tick,
    uint32_t interval_ticks
)
{
    return quadrature_elapsed_ticks(start_tick, current_tick) >= interval_ticks;
}

#endif
