"""Pure treadmill contracts; all synthetic times are monotonic nanoseconds."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import random
import subprocess
import sys

import pytest

from box_runtime.io_manifest import load_box_profile
from box_runtime.input.treadmill.config import ConfigurationError, load_config
from box_runtime.input.treadmill.decoder import Decoder, EdgeEvent
from box_runtime.input.treadmill.state import materialize_state


def config(**overrides):
    """Return enabled head-fixed configuration with explicit test overrides."""
    return load_config(
        {"treadmill": True, "treadmill_config": overrides},
        load_box_profile("head_fixed"),
    )


def trace(
    deltas,
    initial=0,
    start_ns=1_000_000_000,
    period_ns=1_000_000,
    global_baseline=0,
    line_baselines=(0, 0),
):
    """Build exact Gray-code events for signed +/-1 transitions; return events."""
    cycle = (0, 1, 3, 2)
    index = cycle.index(initial)
    counters = list(line_baselines)
    events = []
    for number, delta in enumerate(deltas, 1):
        before = cycle[index]
        index = (index + delta) % 4
        after = cycle[index]
        channel_index = 0 if before ^ after == 2 else 1
        counters[channel_index] = (counters[channel_index] + 1) % 2**32
        bit = 2 if channel_index == 0 else 1
        events.append(
            EdgeEvent(
                start_ns + number * period_ns,
                "AB"[channel_index],
                "rising" if after & bit else "falling",
                (global_baseline + number) % 2**32,
                counters[channel_index],
            )
        )
    return events


def decode(deltas, *, initial=0, **overrides):
    """Return a decoder after applying the requested dimensionless transitions."""
    decoder = Decoder(config(**overrides), initial_state=initial)
    for event in trace(deltas, initial):
        decoder.process_event(event, event.timestamp_ns + 10_000)
    return decoder


def test_configuration_defaults_manifest_and_disable_contract():
    settings = config()
    assert (settings.a_bcm_pin, settings.b_bcm_pin) == (13, 16)
    assert settings.mm_per_encoder_cycle == pytest.approx(0.39269908169872414)
    assert settings.heartbeat_failure_timeout_s > settings.heartbeat_interval_s > 0
    assert settings.startup_timeout_s > 0 and settings.shutdown_timeout_s > 0
    assert settings.processing_lag_warning_ns > 0
    assert settings.expected_max_transition_rate_hz is None
    assert settings.calibration_verified is False
    assert "pin" not in Path("box_runtime/input/treadmill/defaults.yaml").read_text()
    assert (
        load_config(
            {"treadmill": False, "treadmill_config": {"nonsense": 1}},
            load_box_profile("freely_moving"),
        )
        is None
    )
    with pytest.raises(ConfigurationError):
        load_config({"treadmill": True}, load_box_profile("freely_moving"))
    with pytest.raises(FrozenInstanceError):
        settings.locomotion_sign = -1


@pytest.mark.parametrize(
    "key,value",
    [
        ("unknown", 1),
        ("mm_per_encoder_cycle", 0),
        ("mm_per_encoder_cycle", float("nan")),
        ("mm_per_encoder_cycle", float("inf")),
        ("mm_per_encoder_cycle", True),
        ("locomotion_sign", 0),
        ("locomotion_sign", True),
        ("locomotion_sign", 1.0),
        ("gpio_bias", "automatic"),
        ("debounce_period_us", -1),
        ("debounce_period_us", 0.5),
        ("read_batch_size", 1.5),
        ("read_batch_size", True),
        ("kernel_event_buffer_size", 0),
        ("speed_timeout_s", 0),
        ("heartbeat_interval_s", None),
        ("heartbeat_failure_timeout_s", 0.01),
        ("processing_lag_warning_ns", None),
        ("processing_lag_warning_ns", 2.5),
        ("diagnostic_ring_buffer_size", -1),
        ("continuous_log_rate_hz", float("inf")),
        ("continuous_log_flush_interval_s", 0),
        ("startup_timeout_s", 0),
        ("shutdown_timeout_s", -1),
        ("expected_max_transition_rate_hz", 0),
        ("calibration_verified", 1),
        ("treadmill_required", "yes"),
        ("continuous_logger_required", 0),
        ("calibration_source", " "),
        ("calibration_note", None),
    ],
)
def test_invalid_configuration(key, value):
    with pytest.raises(ConfigurationError):
        config(**{key: value})


@pytest.mark.parametrize(
    "key",
    [
        "treadmill_speed_hz",
        "treadmill_wheel_diameter_cm",
        "treadmill_pulses_per_rotation",
    ],
)
def test_retired_settings_give_migration_guidance(key):
    with pytest.raises(
        ConfigurationError, match="migrat|mm_per_encoder_cycle|continuous_log_rate_hz"
    ):
        load_config({"treadmill": True, key: 30}, load_box_profile("head_fixed"))


@pytest.mark.parametrize("value", [0, 1, None, "true"])
def test_enablement_is_a_real_boolean(value):
    with pytest.raises(ConfigurationError):
        load_config({"treadmill": value}, load_box_profile("head_fixed"))


@pytest.mark.parametrize("initial", range(4))
@pytest.mark.parametrize(
    "deltas", [[1], [-1], [1] * 12, [-1] * 12, [1, -1] * 8, [1, 1, -1, -1, 1]]
)
@pytest.mark.parametrize("sign", [-1, 1])
def test_exact_transition_totals_and_physical_units(initial, deltas, sign):
    decoder = decode(deltas, initial=initial, locomotion_sign=sign)
    state = decoder.snapshot(1_100_000_000)
    assert state.raw_transition_position == sum(deltas)
    assert state.valid_transition_count == state.edge_event_count == len(deltas)
    assert state.positive_transition_count == deltas.count(1)
    assert state.negative_transition_count == deltas.count(-1)
    assert state.direction_reversal_count == sum(
        a != b for a, b in zip(deltas, deltas[1:])
    )
    assert state.position_mm == pytest.approx(
        sum(deltas) * config().mm_per_encoder_cycle / 4 * sign
    )
    assert state.distance_travelled_mm == pytest.approx(
        len(deltas) * config().mm_per_encoder_cycle / 4
    )
    assert state.integrity_valid


def test_seeded_trace_is_exact_and_history_is_bounded():
    rng = random.Random(9217)
    deltas = [rng.choice((-1, 1)) for _ in range(20_000)]
    decoder = decode(deltas, diagnostic_ring_buffer_size=32)
    state = decoder.snapshot(22_000_000_000)
    assert state.raw_transition_position == sum(deltas)
    assert state.valid_transition_count == len(deltas)
    assert len(decoder.recent_events) == 32
    assert state.maximum_observed_transition_rate_hz_lifetime == 1000


def test_speed_timeout_baseline_and_initial_optionals():
    decoder = Decoder(config(calibration_verified=True))
    initial = decoder.snapshot(0)
    assert initial.speed_mm_s == 0 and initial.last_edge_speed_mm_s is None
    assert initial.last_edge_monotonic_ns is None
    assert initial.maximum_processing_lag_ns_lifetime is None
    assert initial.processing_lag_histogram_counts == (0,) * 11
    assert initial.health == "READY" and initial.failure_code is None
    events = trace([1, 1, -1])
    decoder.process_event(events[0], events[0].timestamp_ns + 500)
    assert decoder.snapshot(events[0].timestamp_ns).speed_mm_s is None
    decoder.process_event(events[1], events[1].timestamp_ns + 500)
    speed = config().mm_per_encoder_cycle / 4 / 0.001
    assert decoder.snapshot(events[1].timestamp_ns).speed_mm_s == pytest.approx(speed)
    decoder.process_event(events[2], events[2].timestamp_ns + 500)
    state = decoder.snapshot(events[2].timestamp_ns + 51_000_000)
    assert state.speed_mm_s == 0 and state.locomotion_direction == 0
    assert state.last_encoder_direction == -1
    assert state.last_edge_speed_mm_s == pytest.approx(-speed)


def test_zero_preserves_lifetime_counts_and_integrity():
    decoder = decode([1] * 7)
    decoder.apply_zero(1, 1_008_000_000)
    state = decoder.snapshot(1_008_000_000)
    assert state.position_mm == state.distance_travelled_mm == 0
    assert state.raw_transition_position == state.valid_transition_count == 7
    assert state.zeroed_transition_position == state.equivalent_encoder_cycles == 0
    assert state.last_applied_zero_command_id == 1
    decoder.process_event(trace([1] * 9)[-1], 1_010_000_000)
    assert not decoder.snapshot(1_010_000_000).integrity_valid
    with pytest.raises(ValueError):
        decoder.apply_zero(2, 1_011_000_000)


@pytest.mark.parametrize(
    "kind,code,failed",
    [
        ("global_gap", "GLOBAL_SEQUENCE_GAP", True),
        ("line_gap", "LINE_SEQUENCE_GAP", True),
        ("half_range", "SEQUENCE_METADATA_INCONSISTENT", True),
        ("order", "SEQUENCE_ORDER_INVALID", True),
        ("duplicate", "SEQUENCE_DUPLICATE", False),
        ("edge", "EDGE_STATE_INCONSISTENT", True),
        ("channel", "EVENT_FORMAT_INVALID", True),
        ("edge_type", "EVENT_FORMAT_INVALID", True),
        ("timestamp", "EVENT_TIMESTAMP_INVALID", False),
        ("future", "EVENT_TIMESTAMP_INVALID", False),
    ],
)
def test_failure_matrix(kind, code, failed):
    decoder = Decoder(config(calibration_verified=True))
    first, second = trace([1, 1])
    decoder.process_event(first, first.timestamp_ns + 1000)
    changes = {
        "global_gap": dict(global_sequence_number=3),
        "line_gap": dict(line_sequence_number=2),
        "half_range": dict(global_sequence_number=1 + 2**31),
        "order": dict(global_sequence_number=0),
        "duplicate": dict(
            channel=first.channel,
            edge_type=first.edge_type,
            global_sequence_number=1,
            line_sequence_number=1,
        ),
        "edge": dict(channel="B", edge_type="rising", line_sequence_number=2),
        "channel": dict(channel="C"),
        "edge_type": dict(edge_type="invalid"),
        "timestamp": dict(timestamp_ns=first.timestamp_ns),
        "future": dict(timestamp_ns=second.timestamp_ns + 2000),
    }
    event = replace(second, **changes[kind])
    decoder.process_event(event, second.timestamp_ns + 1000)
    state = decoder.snapshot(second.timestamp_ns + 1000)
    assert code in state.observed_acquisition_health_codes
    assert state.integrity_valid is not failed
    assert state.health == ("FAILED" if failed else "DEGRADED")
    assert state.edge_event_count == 2
    assert state.raw_transition_position == (
        2 if kind in ("timestamp", "future") else 1
    )
    if failed:
        assert (
            state.position_mm is state.distance_travelled_mm is state.speed_mm_s is None
        )
    if kind == "global_gap":
        assert (
            state.global_sequence_gap_count == state.estimated_missing_event_count == 1
        )
    if kind == "duplicate":
        assert "EDGE_STATE_INCONSISTENT" not in state.observed_acquisition_health_codes


def test_sequence_wrap_and_speed_not_computed_across_bad_timestamp():
    decoder = Decoder(
        config(),
        global_sequence_number=2**32 - 2,
        line_sequence_numbers=(2**32 - 1, 2**32 - 1),
    )
    events = trace(
        [1] * 4, global_baseline=2**32 - 2, line_baselines=(2**32 - 1, 2**32 - 1)
    )
    for event in events:
        decoder.process_event(event, event.timestamp_ns + 100)
    assert decoder.snapshot(1_004_000_000).raw_transition_position == 4
    assert decoder.snapshot(1_004_000_000).integrity_valid
    fifth, sixth = trace(
        [1] * 6, global_baseline=2**32 - 2, line_baselines=(2**32 - 1, 2**32 - 1)
    )[-2:]
    decoder.process_event(replace(fifth, timestamp_ns=1), 1_005_000_000)
    decoder.process_event(sixth, 1_006_000_100)
    assert decoder.snapshot(1_006_000_100).speed_mm_s is None


def test_lag_histogram_and_health_materialization():
    decoder = decode([1, 1], processing_lag_warning_ns=100)
    state = decoder.snapshot(1_002_010_000)
    assert state.processing_lag_sample_count == state.processing_lag_warning_count == 2
    assert state.processing_lag_sum_ns == 20000
    assert state.processing_lag_histogram_counts[0] == 2
    assert state.failure_code == "PROCESSING_LAG_HIGH"
    dead = materialize_state(state, 2_000_000_000, config(), worker_alive=False)
    assert dead.health == "FAILED" and dead.position_mm is None
    assert dead.failure_code == "ACQUISITION_PROCESS_EXITED"
    stopped = materialize_state(state, 2_000_000_000, config(), stopped=True)
    assert stopped.health == "STOPPED" and stopped.position_mm is not None


def test_package_import_does_not_import_hardware():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import box_runtime.input.treadmill; "
            "assert 'gpiod' not in sys.modules; assert 'gpiozero' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_required_logger_failure_does_not_mask_valid_acquisition_values():
    """Logger criticality changes subsystem health, not edge-capture integrity."""
    state = decode([1] * 4).snapshot(1_004_010_000)
    effective = materialize_state(
        state, 1_004_010_000, config(), extra_codes=("REQUIRED_LOGGER_FAILED",)
    )
    assert effective.health == "FAILED"
    assert effective.acquisition_available
    assert effective.position_mm == state.position_mm


@pytest.mark.parametrize(
    "lag,bin_index", [(50_000, 0), (50_001, 1), (50_000_000, 9), (50_000_001, 10)]
)
def test_exact_lag_histogram_boundaries(lag, bin_index):
    decoder = Decoder(config())
    event = trace([1])[0]
    decoder.process_event(event, event.timestamp_ns + lag)
    state = decoder.snapshot(event.timestamp_ns + lag)
    assert state.processing_lag_histogram_counts[bin_index] == 1
    assert sum(state.processing_lag_histogram_counts) == 1


def test_gap_is_accounted_once_and_next_valid_motion_remains_untrusted():
    decoder = Decoder(config(calibration_verified=True))
    events = trace([1] * 8)
    for event in (events[0], *events[3:]):
        decoder.process_event(event, event.timestamp_ns + 100)
    state = decoder.snapshot(1_008_000_100)
    assert state.global_sequence_gap_count == 1
    assert state.estimated_missing_event_count == 2
    assert state.a_line_sequence_gap_count == state.b_line_sequence_gap_count == 1
    assert not state.integrity_valid and state.position_mm is None
    assert state.failure_code == "GLOBAL_SEQUENCE_GAP"


def test_sequence_duplicate_does_not_clear_speed_or_double_apply_position():
    decoder = decode([1, 1])
    before = decoder.snapshot(1_002_010_000)
    event = trace([1, 1])[-1]
    decoder.process_event(event, event.timestamp_ns + 20000)
    after = decoder.snapshot(event.timestamp_ns + 20000)
    assert after.raw_transition_position == before.raw_transition_position
    assert after.speed_mm_s == before.speed_mm_s
    assert after.event_inconsistency_count == 1


def test_legacy_calibration_remains_an_explicit_conversion_option():
    decoder = decode([1] * 4, mm_per_encoder_cycle=0.41095)
    assert decoder.snapshot(1_004_010_000).position_mm == pytest.approx(0.41095)
