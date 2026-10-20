#include <assert.h>
#include <stddef.h>
#include <stdint.h>

#include "../quadrature_timing.h"

typedef struct {
    uint32_t quadrature_hz;
    uint32_t expected_quarter_period_ticks;
} required_timing_case_t;

/** Inputs: none. Returns no value; assertions verify all six required rates. */
static void test_required_frequency_pairs(void)
{
    static const required_timing_case_t cases[] = {
        {10u, 2100000u},
        {100u, 210000u},
        {1000u, 21000u},
        {10000u, 2100u},
        {100000u, 210u},
        {1000000u, 21u},
    };

    for (size_t index = 0u; index < (sizeof(cases) / sizeof(cases[0])); ++index) {
        quadrature_timing_t timing = {0u};
        const bool valid = quadrature_calculate_timing(
            84000000u,
            cases[index].quadrature_hz,
            &timing
        );

        assert(valid);
        assert(timing.quarter_period_ticks == cases[index].expected_quarter_period_ticks);
    }
}

/** Inputs: none. Returns no value; assertions cover rejected timing settings. */
static void test_unsupported_frequency_and_clock_values(void)
{
    quadrature_timing_t timing = {123u};

    assert(!quadrature_calculate_timing(0u, 1000u, &timing));
    assert(!quadrature_calculate_timing(84000000u, 0u, &timing));
    assert(!quadrature_calculate_timing(84000000u, 1u, &timing));
    assert(!quadrature_calculate_timing(84000000u, 999u, &timing));
    assert(!quadrature_calculate_timing(84000000u, 2000000u, &timing));
    assert(!quadrature_calculate_timing(80000000u, 1000u, &timing));
    assert(!quadrature_calculate_timing(84000000u, 1000u, NULL));

    assert(timing.quarter_period_ticks == 123u);
}

/** Inputs: none. Returns no value; assertions cover the complete count range. */
static void test_cycle_count_boundaries_and_transition_totals(void)
{
    uint64_t transition_count = 0u;

    assert(quadrature_calculate_transition_count(UINT64_C(1), &transition_count));
    assert(transition_count == UINT64_C(4));
    assert(quadrature_calculate_transition_count(UINT64_C(2), &transition_count));
    assert(transition_count == UINT64_C(8));
    assert(quadrature_calculate_transition_count(UINT64_C(3), &transition_count));
    assert(transition_count == UINT64_C(12));
    assert(quadrature_calculate_transition_count(UINT32_MAX, &transition_count));
    assert(transition_count == (UINT64_C(4) * UINT32_MAX));

    assert(!quadrature_calculate_transition_count(UINT64_C(0), &transition_count));
    assert(!quadrature_calculate_transition_count(UINT64_C(1) + UINT32_MAX, &transition_count));
    assert(!quadrature_calculate_transition_count(UINT64_C(1), NULL));
}

/** Inputs: none. Returns no value; assertions verify modulo-32-bit elapsed time. */
static void test_wrap_safe_elapsed_ticks(void)
{
    const uint32_t near_wrap = UINT32_MAX - 10u;

    assert(quadrature_elapsed_ticks(100u, 100u) == 0u);
    assert(quadrature_elapsed_ticks(100u, 121u) == 21u);
    assert(quadrature_elapsed_ticks(near_wrap, 9u) == 20u);
    assert(!quadrature_interval_has_elapsed(near_wrap, 8u, 20u));
    assert(quadrature_interval_has_elapsed(near_wrap, 9u, 20u));
}

/** Inputs: none. Returns no value; assertions enforce the fixed Due pin map. */
static void test_pin_configuration_validation(void)
{
    assert(DUE_QUADRATURE_PIN_CONFIGURATION_IS_VALID(34u, 35u, 22u));
    assert(!DUE_QUADRATURE_PIN_CONFIGURATION_IS_VALID(35u, 34u, 22u));
    assert(!DUE_QUADRATURE_PIN_CONFIGURATION_IS_VALID(34u, 36u, 22u));
    assert(!DUE_QUADRATURE_PIN_CONFIGURATION_IS_VALID(34u, 35u, 23u));
}

/** Inputs: none. Returns zero after every host contract assertion passes. */
int main(void)
{
    test_required_frequency_pairs();
    test_unsupported_frequency_and_clock_values();
    test_cycle_count_boundaries_and_transition_totals();
    test_wrap_safe_elapsed_ticks();
    test_pin_configuration_validation();
    return 0;
}
