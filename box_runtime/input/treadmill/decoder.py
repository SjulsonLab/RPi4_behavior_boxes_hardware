"""Pure ordered-edge x4 decoding with bounded failure history and integer totals."""

from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, replace
from itertools import islice

from .config import TreadmillConfig
from .state import HEALTH_CODES, TreadmillState, materialize_state, ordered_codes


LAG_BIN_UPPER_BOUNDS_NS = (
    50_000,
    100_000,
    250_000,
    500_000,
    1_000_000,
    2_000_000,
    5_000_000,
    10_000_000,
    25_000_000,
    50_000_000,
)
TRANSITIONS = {
    (0, 1): 1,
    (1, 3): 1,
    (3, 2): 1,
    (2, 0): 1,
    (1, 0): -1,
    (3, 1): -1,
    (2, 3): -1,
    (0, 2): -1,
}
COUNTER_NAMES = (
    "edge_event_count",
    "valid_transition_count",
    "positive_transition_count",
    "negative_transition_count",
    "direction_reversal_count",
    "global_sequence_gap_count",
    "a_line_sequence_gap_count",
    "b_line_sequence_gap_count",
    "estimated_missing_event_count",
    "event_inconsistency_count",
    "publication_skip_count",
    "diagnostic_event_drop_count",
    "processing_lag_sample_count",
    "processing_lag_sum_ns",
    "processing_lag_warning_count",
)
FAILURE_PRECEDING_EVENTS = 8
FAILURE_FOLLOWING_EVENTS = 4


@dataclass(frozen=True, slots=True)
class EdgeEvent:
    """One edge: monotonic ns, A/B string, rising/falling string and uint32 sequences."""

    timestamp_ns: int
    channel: str
    edge_type: str
    global_sequence_number: int
    line_sequence_number: int


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Recent raw event plus monotonic processing ns, AB bits, delta and code flags."""

    event: EdgeEvent
    processing_monotonic_ns: int
    before: int
    after: int
    delta: int
    processing_lag_ns: int | None
    flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FirstFailureDiagnostic:
    """One immutable integrity-failure window, with at most 8+1+4 records.

    event_number is one-based admitted-event order after startup synchronization,
    independent of kernel sequence baselines. Counts/positions are dimensionless;
    global and A/B line baselines are uint32 values immediately before failure.
    records stay in received order and retain EventRecord's monotonic-ns units.
    preceding_event_count locates the offending record. Short context is valid
    at startup, with a small configured history, or when input stops afterward.
    """

    event_number: int
    valid_transition_count_before: int
    raw_transition_position_before: int
    global_sequence_before: int
    line_sequence_numbers_before: tuple[int, int]
    preceding_event_count: int
    records: tuple[EventRecord, ...]

    @property
    def following_event_count(self) -> int:
        """Return the dimensionless number of retained records after the failure."""
        return len(self.records) - self.preceding_event_count - 1


class Decoder:
    """Single-writer pure decoder; all mutable state belongs to this instance."""

    def __init__(
        self,
        config: TreadmillConfig,
        initial_state: int = 0,
        global_sequence_number: int = 0,
        line_sequence_numbers: tuple[int, int] = (0, 0),
    ):
        """Initialize from AB bits 0..3 and uint32 startup sequence baselines.

        Args:
            config: Validated calibration and bounded-history scalar settings.
            initial_state: Two bits, A in bit 1 and B in bit 0.
            global_sequence_number: Last startup request sequence, dimensionless.
            line_sequence_numbers: A then B last startup sequences, dimensionless.

        Returns:
            None. No hardware, time reads, file writes or processes are used.
        """
        if initial_state not in range(4):
            raise ValueError("Initial AB state must be 0..3")
        self.config = config
        self.ab_state = initial_state
        self.global_sequence = global_sequence_number
        self.line_sequences = list(line_sequence_numbers)
        self.counters = dict.fromkeys(COUNTER_NAMES, 0)
        self.position = self.zero_position = self.zero_absolute = 0
        self.last_direction = 0
        self.last_edge_ns = self.last_motion_ns = self.speed_baseline_ns = None
        self.last_speed = self.current_speed = None
        self.last_update_ns = 0
        self.latest_lag = self.maximum_lag = None
        self.lag_histogram = [0] * 11
        self.rate_bin = None
        self.rate_bin_count = self.maximum_bin_count = 0
        self.code_times: dict[str, int] = {}
        self.integrity_valid = True
        self.last_resolved_zero_id = self.last_applied_zero_id = None
        self.last_zero_result = self.zero_resolved_ns = None
        self.recent_events = deque(maxlen=config.diagnostic_ring_buffer_size)
        self.first_failure_diagnostic: FirstFailureDiagnostic | None = None
        if not config.calibration_verified:
            self.note_code("CALIBRATION_UNVERIFIED", 0)

    # User-facing pure computation.
    def process_event(self, event: EdgeEvent, processing_monotonic_ns: int) -> None:
        """Admit one ordered event; mutate integer totals and bounded diagnostics.

        Args:
            event: Raw event, timestamps in monotonic ns, sequences uint32.
            processing_monotonic_ns: Caller-supplied processing time in ns.

        Returns:
            None. Invalid events follow the specification failure matrix;
            trajectory-ambiguous events never synthesize replacement motion.
        """
        self.counters["edge_event_count"] += 1
        self.last_update_ns = processing_monotonic_ns
        before = self.ab_state
        global_sequence_before = self.global_sequence
        a_sequence_before, b_sequence_before = self.line_sequences
        valid_count_before = self.counters["valid_transition_count"]
        position_before = self.position
        flags = set()
        channel = {"A": 0, "B": 1}.get(event.channel)
        payload_valid = channel is not None and event.edge_type in ("rising", "falling")
        gd = (event.global_sequence_number - self.global_sequence) % 2**32
        ld = (
            None
            if channel is None
            else (event.line_sequence_number - self.line_sequences[channel]) % 2**32
        )
        for difference in (gd, ld):
            if difference is not None and difference >= 2**31:
                flags.add(
                    "SEQUENCE_METADATA_INCONSISTENT"
                    if difference == 2**31
                    else "SEQUENCE_ORDER_INVALID"
                )
        if gd == 0 and ld == 0:
            flags.add("SEQUENCE_DUPLICATE")
        elif (
            gd == 0
            or ld == 0
            or (ld is not None and ld < 2**31 and gd < 2**31 and ld > gd)
        ):
            flags.add("SEQUENCE_METADATA_INCONSISTENT")
        if 1 < gd < 2**31:
            flags.add("GLOBAL_SEQUENCE_GAP")
            self.counters["global_sequence_gap_count"] += 1
            self.counters["estimated_missing_event_count"] += gd - 1
        if ld is not None and 1 < ld < 2**31:
            flags.add("LINE_SEQUENCE_GAP")
            self.counters[
                "a_line_sequence_gap_count"
                if channel == 0
                else "b_line_sequence_gap_count"
            ] += 1
            if gd == 1:
                flags.add("SEQUENCE_METADATA_INCONSISTENT")
        if 0 < gd < 2**31:
            self.global_sequence = event.global_sequence_number
        if ld is not None and 0 < ld < 2**31:
            self.line_sequences[channel] = event.line_sequence_number
        rejected_sequence = bool(flags)
        sequence_replay = (
            "SEQUENCE_DUPLICATE" in flags or "SEQUENCE_ORDER_INVALID" in flags
        )
        if not payload_valid and not sequence_replay:
            flags.add("EVENT_FORMAT_INVALID")
        timestamp_valid = (
            type(event.timestamp_ns) is int
            and event.timestamp_ns >= 0
            and event.timestamp_ns <= processing_monotonic_ns
            and (self.last_edge_ns is None or event.timestamp_ns > self.last_edge_ns)
        )
        lag = None
        if not sequence_replay and payload_valid and timestamp_valid:
            self.last_edge_ns = event.timestamp_ns
            lag = processing_monotonic_ns - event.timestamp_ns
            self.latest_lag = lag
            self.maximum_lag = (
                lag if self.maximum_lag is None else max(self.maximum_lag, lag)
            )
            self.counters["processing_lag_sample_count"] += 1
            self.counters["processing_lag_sum_ns"] += lag
            self.lag_histogram[bisect_left(LAG_BIN_UPPER_BOUNDS_NS, lag)] += 1
            if lag > self.config.processing_lag_warning_ns:
                flags.add("PROCESSING_LAG_HIGH")
                self.counters["processing_lag_warning_count"] += 1
        delta = 0
        if rejected_sequence:
            if not sequence_replay and payload_valid:
                self._set_level(channel, event.edge_type)
            if "SEQUENCE_DUPLICATE" not in flags:
                self.speed_baseline_ns = self.current_speed = None
        elif payload_valid:
            self._set_level(channel, event.edge_type)
            delta = TRANSITIONS.get((before, self.ab_state), 0)
            if not delta:
                flags.add("EDGE_STATE_INCONSISTENT")
                self.speed_baseline_ns = self.current_speed = None
            else:
                self.position += delta
                self.counters["valid_transition_count"] += 1
                self.counters[
                    "positive_transition_count"
                    if delta > 0
                    else "negative_transition_count"
                ] += 1
                self.counters["direction_reversal_count"] += int(
                    bool(self.last_direction) and self.last_direction != delta
                )
                self.last_direction = delta
                if timestamp_valid:
                    if self.speed_baseline_ns is not None:
                        self.last_speed = (
                            delta
                            * self.config.mm_per_encoder_cycle
                            / 4
                            * self.config.locomotion_sign
                            / ((event.timestamp_ns - self.speed_baseline_ns) / 1e9)
                        )
                        self.current_speed = self.last_speed
                    self.speed_baseline_ns = self.last_motion_ns = event.timestamp_ns
                    self._count_rate(event.timestamp_ns)
                else:
                    flags.add("EVENT_TIMESTAMP_INVALID")
                    self.speed_baseline_ns = self.current_speed = None
        else:
            self.speed_baseline_ns = self.current_speed = None
        inconsistent = flags - {
            "GLOBAL_SEQUENCE_GAP",
            "LINE_SEQUENCE_GAP",
            "PROCESSING_LAG_HIGH",
        }
        if inconsistent:
            self.counters["event_inconsistency_count"] += 1
        integrity_codes = {
            "GLOBAL_SEQUENCE_GAP",
            "LINE_SEQUENCE_GAP",
            "SEQUENCE_METADATA_INCONSISTENT",
            "SEQUENCE_ORDER_INVALID",
            "EVENT_FORMAT_INVALID",
            "EDGE_STATE_INCONSISTENT",
            "STATE_RESYNC_AMBIGUOUS",
        }
        first_integrity_failure = self.integrity_valid and bool(flags & integrity_codes)
        if flags & integrity_codes:
            self.integrity_valid = False
        for code in ordered_codes(flags):
            self.note_code(code, processing_monotonic_ns)
        record = EventRecord(
            event,
            processing_monotonic_ns,
            before,
            self.ab_state,
            delta,
            lag,
            ordered_codes(flags),
        )
        if first_integrity_failure:
            # Copy only the tail needed for this report, before the new record
            # can evict preceding context from a small configured history.
            preceding = tuple(
                reversed(
                    tuple(
                        islice(
                            reversed(self.recent_events),
                            FAILURE_PRECEDING_EVENTS,
                        )
                    )
                )
            )
            self.first_failure_diagnostic = FirstFailureDiagnostic(
                self.counters["edge_event_count"],
                valid_count_before,
                position_before,
                global_sequence_before,
                (a_sequence_before, b_sequence_before),
                len(preceding),
                preceding + (record,),
            )
        elif (
            self.first_failure_diagnostic is not None
            and self.first_failure_diagnostic.following_event_count
            < FAILURE_FOLLOWING_EVENTS
        ):
            self.first_failure_diagnostic = replace(
                self.first_failure_diagnostic,
                records=self.first_failure_diagnostic.records + (record,),
            )
        self.recent_events.append(record)

    def snapshot(
        self, now_ns: int, *, heartbeat_ns: int | None = None, state_version: int = 0
    ) -> TreadmillState:
        """Return immutable state at caller monotonic ns; counts have no units.

        Args:
            now_ns: Effective current monotonic nanoseconds.
            heartbeat_ns: Published heartbeat ns; None uses now_ns for pure tests.
            state_version: Nonnegative coherent-publication version.

        Returns:
            TreadmillState: Physical fields in mm/mm/s with explicit unavailable
            None values; raw integer counts are retained after integrity loss.
        """
        mm = self.config.mm_per_encoder_cycle / 4
        zeroed = self.position - self.zero_position
        state = TreadmillState(
            position_mm=zeroed * mm * self.config.locomotion_sign,
            distance_travelled_mm=(
                self.counters["valid_transition_count"] - self.zero_absolute
            )
            * mm,
            speed_mm_s=self.current_speed,
            last_edge_speed_mm_s=self.last_speed,
            last_encoder_direction=self.last_direction,
            locomotion_direction=self.last_direction * self.config.locomotion_sign,
            last_edge_monotonic_ns=self.last_edge_ns,
            last_motion_monotonic_ns=self.last_motion_ns,
            last_state_update_monotonic_ns=self.last_update_ns,
            last_heartbeat_monotonic_ns=now_ns
            if heartbeat_ns is None
            else heartbeat_ns,
            raw_transition_position=self.position,
            zeroed_transition_position=zeroed,
            equivalent_encoder_cycles=zeroed / 4,
            latest_processing_lag_ns=self.latest_lag,
            maximum_processing_lag_ns_lifetime=self.maximum_lag,
            processing_lag_histogram_counts=tuple(self.lag_histogram),
            maximum_observed_transition_rate_hz_lifetime=self.maximum_bin_count / 0.1,
            last_resolved_zero_command_id=self.last_resolved_zero_id,
            last_applied_zero_command_id=self.last_applied_zero_id,
            last_zero_result=self.last_zero_result,
            zero_resolved_monotonic_ns=self.zero_resolved_ns,
            integrity_valid=self.integrity_valid,
            active_acquisition_health_codes=ordered_codes(self.code_times),
            observed_acquisition_health_codes=tuple(
                code
                for _, _, code in sorted(
                    (timestamp, HEALTH_CODES.index(code), code)
                    for code, timestamp in self.code_times.items()
                )
            ),
            observed_acquisition_code_times=tuple(
                self.code_times.get(code, -1) for code in HEALTH_CODES
            ),
            state_version=state_version,
            **self.counters,
        )
        return materialize_state(state, now_ns, self.config)

    def apply_zero(self, command_id: int, now_ns: int) -> None:
        """Set physical origin at current totals; inputs are integer ID and ns.

        Returns:
            None. Raises ValueError if trajectory integrity is invalid. Lifetime
            counts, direction, timing, failure history and integrity are retained.
        """
        if not self.integrity_valid:
            raise ValueError("Cannot zero an untrusted trajectory")
        self.zero_position = self.position
        self.zero_absolute = self.counters["valid_transition_count"]
        self.last_resolved_zero_id = self.last_applied_zero_id = command_id
        self.last_zero_result = "applied"
        self.zero_resolved_ns = self.last_update_ns = now_ns

    def note_code(self, code: str, now_ns: int) -> None:
        """Latch one known code at its first monotonic ns; return None."""
        if code not in HEALTH_CODES:
            raise ValueError(f"Unknown health code: {code}")
        self.code_times.setdefault(code, now_ns)

    # Helpers: mutate only the decoder-owned line state and fixed rate bin.
    def _set_level(self, channel: int, edge_type: str) -> None:
        """Set A=0/B=1 indexed bit from rising/falling string; return None."""
        bit = 2 if channel == 0 else 1
        self.ab_state = (
            self.ab_state | bit if edge_type == "rising" else self.ab_state & ~bit
        )

    def _count_rate(self, timestamp_ns: int) -> None:
        """Count valid timestamped transitions in aligned 100 ms bins; return None."""
        bin_number = timestamp_ns // 100_000_000
        self.rate_bin_count = (
            self.rate_bin_count + 1 if self.rate_bin == bin_number else 1
        )
        self.rate_bin = bin_number
        self.maximum_bin_count = max(self.maximum_bin_count, self.rate_bin_count)
