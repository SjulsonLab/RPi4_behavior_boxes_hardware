#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "../quadrature_timing.h"

typedef struct {
    uint32_t quadrature_hz;
    uint32_t expected_pio_cycles_per_quarter;
    uint32_t expected_delay_counter;
} required_timing_case_t;

static void test_required_frequency_pairs(void)
{
    static const required_timing_case_t cases[] = {
        {10u, 500000u, 499996u},
        {100u, 50000u, 49996u},
        {1000u, 5000u, 4996u},
        {10000u, 500u, 496u},
        {100000u, 50u, 46u},
        {1000000u, 5u, 1u},
    };

    for (size_t index = 0u; index < (sizeof(cases) / sizeof(cases[0])); ++index) {
        quadrature_timing_t timing = {0u, 0u};
        const bool valid = quadrature_calculate_timing(
            120000000u,
            6u,
            cases[index].quadrature_hz,
            &timing
        );

        assert(valid);
        assert(timing.pio_cycles_per_quarter == cases[index].expected_pio_cycles_per_quarter);
        assert(timing.delay_counter == cases[index].expected_delay_counter);
    }
}

static void test_timing_boundaries_and_invalid_inputs(void)
{
    quadrature_timing_t timing = {0u, 0u};

    assert(quadrature_calculate_timing(120000000u, 6u, 1250000u, &timing));
    assert(timing.pio_cycles_per_quarter == 4u);
    assert(timing.delay_counter == 0u);

    assert(!quadrature_calculate_timing(0u, 6u, 1000u, &timing));
    assert(!quadrature_calculate_timing(120000000u, 0u, 1000u, &timing));
    assert(!quadrature_calculate_timing(120000000u, 6u, 0u, &timing));
    assert(!quadrature_calculate_timing(120000000u, 6u, 997u, &timing));
    assert(!quadrature_calculate_timing(120000000u, 6u, 2500000u, &timing));
    assert(!quadrature_calculate_timing(120000000u, 6u, 1000u, NULL));
}

static void test_cycle_counter_encoding(void)
{
    uint32_t encoded_count = 0u;

    assert(quadrature_encode_cycle_count(UINT64_C(1), &encoded_count));
    assert(encoded_count == 0u);
    assert(quadrature_encode_cycle_count(UINT64_C(2), &encoded_count));
    assert(encoded_count == 1u);
    assert(quadrature_encode_cycle_count(UINT64_C(3), &encoded_count));
    assert(encoded_count == 2u);
    assert(quadrature_encode_cycle_count(UINT32_MAX, &encoded_count));
    assert(encoded_count == (UINT32_MAX - 1u));

    assert(!quadrature_encode_cycle_count(UINT64_C(0), &encoded_count));
    assert(!quadrature_encode_cycle_count(UINT64_C(1) + UINT32_MAX, &encoded_count));
    assert(!quadrature_encode_cycle_count(UINT64_C(1), NULL));
}

int main(void)
{
    test_required_frequency_pairs();
    test_timing_boundaries_and_invalid_inputs();
    test_cycle_counter_encoding();
    return 0;
}
