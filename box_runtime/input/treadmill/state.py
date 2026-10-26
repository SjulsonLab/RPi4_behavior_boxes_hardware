"""Immutable public state and pure effective health/timeout interpretation."""

from dataclasses import dataclass, replace

from .config import TreadmillConfig


# The order is the public primary-code priority from specification Section 14.1.
HEALTH_CODES = (
    "SHUTDOWN_INCOMPLETE", "ACQUISITION_PROCESS_EXITED", "HEARTBEAT_STALE",
    "ACQUISITION_START_FAILED", "BACKEND_CAPABILITY_MISSING", "GPIO_RESOLUTION_FAILED",
    "GPIO_LINE_CLAIM_FAILED", "INITIAL_SYNC_FAILED", "BACKEND_IO_FAILED",
    "GLOBAL_SEQUENCE_GAP", "LINE_SEQUENCE_GAP", "SEQUENCE_METADATA_INCONSISTENT",
    "SEQUENCE_ORDER_INVALID", "STATE_RESYNC_AMBIGUOUS", "EVENT_FORMAT_INVALID",
    "EDGE_STATE_INCONSISTENT", "REQUIRED_LOGGER_FAILED", "STATE_READ_TIMEOUT",
    "ZERO_RESULT_INDETERMINATE", "ZERO_NOT_APPLIED", "LOGGER_FAILED",
    "STATE_PUBLICATION_SKIPPED", "SEQUENCE_DUPLICATE", "EVENT_TIMESTAMP_INVALID",
    "PROCESSING_LAG_HIGH", "DIAGNOSTIC_EVENTS_DROPPED", "CALIBRATION_UNVERIFIED",
)
FAILED_CODES = frozenset(HEALTH_CODES[:17])


@dataclass(frozen=True, slots=True)
class TreadmillState:
    """Coherent scalar state in mm, mm/s, ns, Hz and dimensionless counts.

    Optional values are None when no estimate exists or acquisition is untrusted.
    Histogram counts are 11 bins, ascending lag upper bounds then overflow.
    Code times are a fixed tuple in HEALTH_CODES order; -1 means never observed.
    The latter is internal transport context, not an additional history queue.
    """

    position_mm: float | None = 0.0
    distance_travelled_mm: float | None = 0.0
    speed_mm_s: float | None = 0.0
    last_edge_speed_mm_s: float | None = None
    last_encoder_direction: int = 0
    locomotion_direction: int | None = 0
    last_edge_monotonic_ns: int | None = None
    last_motion_monotonic_ns: int | None = None
    last_state_update_monotonic_ns: int = 0
    last_heartbeat_monotonic_ns: int = 0
    raw_transition_position: int = 0
    zeroed_transition_position: int = 0
    equivalent_encoder_cycles: float = 0.0
    edge_event_count: int = 0
    valid_transition_count: int = 0
    positive_transition_count: int = 0
    negative_transition_count: int = 0
    direction_reversal_count: int = 0
    global_sequence_gap_count: int = 0
    a_line_sequence_gap_count: int = 0
    b_line_sequence_gap_count: int = 0
    estimated_missing_event_count: int = 0
    event_inconsistency_count: int = 0
    publication_skip_count: int = 0
    diagnostic_event_drop_count: int = 0
    latest_processing_lag_ns: int | None = None
    maximum_processing_lag_ns_lifetime: int | None = None
    processing_lag_sample_count: int = 0
    processing_lag_sum_ns: int = 0
    processing_lag_histogram_counts: tuple[int, ...] = (0,) * 11
    processing_lag_warning_count: int = 0
    maximum_observed_transition_rate_hz_lifetime: float = 0.0
    last_resolved_zero_command_id: int | None = None
    last_applied_zero_command_id: int | None = None
    last_zero_result: str | None = None
    zero_resolved_monotonic_ns: int | None = None
    health: str = "STARTING"
    integrity_valid: bool = True
    acquisition_available: bool = True
    failure_code: str | None = None
    observed_acquisition_health_codes: tuple[str, ...] = ()
    active_acquisition_health_codes: tuple[str, ...] = ()
    observed_acquisition_code_times: tuple[int, ...] = (-1,) * len(HEALTH_CODES)
    state_version: int = 0


def ordered_codes(codes) -> tuple[str, ...]:
    """Return known string codes in public priority order; no physical units."""
    present = set(codes)
    return tuple(code for code in HEALTH_CODES if code in present)


def materialize_state(state: TreadmillState, now_ns: int, config: TreadmillConfig,
                      *, worker_alive: bool = True, stopped: bool = False,
                      extra_codes: tuple[str, ...] = ()) -> TreadmillState:
    """Return immutable effective state using monotonic time and child liveness.

    Args:
        state: Coherent canonical publication in mm, seconds-derived speed, ns.
        now_ns: Current monotonic nanoseconds in the kernel event clock domain.
        config: Validated scalar settings, timeouts in seconds.
        worker_alive: Whether the acquisition child is currently alive.
        stopped: Whether orderly requested cleanup completed.
        extra_codes: Parent-owned active code strings, in any order.

    Returns:
        TreadmillState: New immutable copy with effective health and physical
        availability; canonical diagnostic counters and timestamps are preserved.
    """
    codes = set(state.active_acquisition_health_codes) | set(extra_codes)
    if not stopped:
        if not worker_alive:
            codes.add("ACQUISITION_PROCESS_EXITED")
        elif now_ns - state.last_heartbeat_monotonic_ns > config.heartbeat_failure_timeout_s * 1e9:
            codes.add("HEARTBEAT_STALE")
    ordered = ordered_codes(codes)
    failed = bool(codes & FAILED_CODES)
    health = "STOPPED" if stopped else ("FAILED" if failed else "DEGRADED" if codes else "READY")
    acquisition_failed = bool(codes & (FAILED_CODES - {"REQUIRED_LOGGER_FAILED"}))
    available = worker_alive and not stopped and not acquisition_failed
    physical_valid = state.integrity_valid and (stopped or not acquisition_failed)
    speed, direction = state.speed_mm_s, state.locomotion_direction
    if state.last_motion_monotonic_ns is None or now_ns - state.last_motion_monotonic_ns >= config.speed_timeout_s*1e9:
        speed, direction = 0.0, 0
    return replace(state, health=health, acquisition_available=available,
                   failure_code=None if stopped else (ordered[0] if ordered else None),
                   position_mm=state.position_mm if physical_valid else None,
                   distance_travelled_mm=state.distance_travelled_mm if physical_valid else None,
                   speed_mm_s=speed if physical_valid else None,
                   locomotion_direction=direction if physical_valid else None)
