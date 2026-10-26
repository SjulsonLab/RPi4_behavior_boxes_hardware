"""Count raw GPIO edges without quadrature decoding, shared state or worker queues."""

from __future__ import annotations

from dataclasses import dataclass
import signal
import time
from types import FrameType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from box_runtime.input.treadmill.decoder import EdgeEvent
    from box_runtime.input.treadmill.gpio_backend import InitialState

try:
    from debug.treadmill_options import parse_counter_options
except ModuleNotFoundError:
    from treadmill_options import parse_counter_options


@dataclass(frozen=True)
class RawAnomaly:
    """One received event with count index, monotonic ns, prior sequences and flags.

    event_number is one-based after startup. Sequences are dimensionless uint32;
    line_sequence_before is None for an unmapped channel. Records are never sorted.
    """

    event_number: int
    event: EdgeEvent
    processing_monotonic_ns: int
    global_sequence_before: int
    line_sequence_before: int | None
    flags: tuple[str, ...]


class RawEdgeCounter:
    """Minimal received-order statistics; never repairs or decodes motion."""

    def __init__(self, initial: InitialState) -> None:
        """Initialize from accepted AB bits and uint32 startup sequence baselines.

        Args:
            initial: Source's quiet startup boundary, with A as bit 1, B as bit 0.

        Returns:
            None. edge_counts is a (2, 2) nested integer list: rows A/B, columns
            rising/falling. All counts are dimensionless; timestamps and lag are
            monotonic ns. Only the first eight anomalous events are retained.
        """
        self.edge_event_count = 0
        self.edge_counts = [[0, 0], [0, 0]]
        self.global_sequence = initial.global_sequence_number
        self.line_sequences = list(initial.line_sequence_numbers)
        self.levels = [int(bool(initial.ab_state & 2)), initial.ab_state & 1]
        self.previous_timestamp_ns = None
        self.line_timestamp_ns: list[int | None] = [None, None]
        self.maximum_processing_lag_ns: int | None = None
        self.anomaly_counts: dict[str, int] = {}
        self.anomalies: list[RawAnomaly] = []

    def process_event(self, event: EdgeEvent, processing_monotonic_ns: int) -> None:
        """Count one raw event, preserving receipt order and continuing on anomalies.

        Args:
            event: Source event with integer monotonic timestamp ns, uint32
                sequences, A/B channel and rising/falling edge labels.
            processing_monotonic_ns: Integer time when this event is processed, ns.

        Returns:
            None. Updates integer totals, anomaly observations and maximum lag.
            Sequence/timestamp comparisons use the previous received event (or
            previous event on that line), including reordered/duplicate events.
            Thus forward-gap observations are not an estimate of lost edges.
        """
        self.edge_event_count += 1
        flags = []
        global_before = self.global_sequence
        issue = _sequence_issue(global_before, event.global_sequence_number)
        if issue:
            flags.append(f"GLOBAL_SEQUENCE_{issue}")
        self.global_sequence = event.global_sequence_number

        timestamp_ns = event.timestamp_ns
        if (
            self.previous_timestamp_ns is not None
            and timestamp_ns <= self.previous_timestamp_ns
        ):
            flags.append("TIMESTAMP_ORDER_INVALID")
        self.previous_timestamp_ns = timestamp_ns
        if 0 <= timestamp_ns <= processing_monotonic_ns:
            lag_ns = processing_monotonic_ns - timestamp_ns
            if (
                self.maximum_processing_lag_ns is None
                or lag_ns > self.maximum_processing_lag_ns
            ):
                self.maximum_processing_lag_ns = lag_ns
        else:
            flags.append("EVENT_TIMESTAMP_INVALID")

        channel = 0 if event.channel == "A" else 1 if event.channel == "B" else None
        line_before = None
        if channel is not None:
            line_before = self.line_sequences[channel]
            issue = _sequence_issue(line_before, event.line_sequence_number)
            if issue:
                flags.append(f"{event.channel}_LINE_SEQUENCE_{issue}")
            self.line_sequences[channel] = event.line_sequence_number
            previous_ns = self.line_timestamp_ns[channel]
            if previous_ns is not None and timestamp_ns <= previous_ns:
                flags.append(f"{event.channel}_TIMESTAMP_ORDER_INVALID")
            self.line_timestamp_ns[channel] = timestamp_ns

        if channel is None or event.edge_type not in ("rising", "falling"):
            flags.append("EVENT_FORMAT_INVALID")
        else:
            rising = event.edge_type == "rising"
            self.edge_counts[channel][0 if rising else 1] += 1
            if self.levels[channel] == int(rising):
                flags.append(f"{event.channel}_EDGE_LEVEL_REPEATED")
            self.levels[channel] = int(rising)

        for flag in flags:
            self.anomaly_counts[flag] = self.anomaly_counts.get(flag, 0) + 1
        if flags and len(self.anomalies) < 8:
            self.anomalies.append(
                RawAnomaly(
                    self.edge_event_count,
                    event,
                    processing_monotonic_ns,
                    global_before,
                    line_before,
                    tuple(flags),
                )
            )


def main(
    argv: list[str] | None = None,
    *,
    source_factory=None,
    clock_ns_fn=time.monotonic_ns,
    output_fn=print,
) -> int:
    """Read the production GPIO source directly until Ctrl-C or a duration.

    Args:
        argv: CLI strings, or None for process arguments; same pins/duration as
            treadmill_counter.py. No expected-count or decoder settings are added.
        source_factory: Optional callable(config) -> EdgeSource for software tests.
        clock_ns_fn: Zero-argument monotonic integer-ns clock, normally the OS clock.
        output_fn: Callable accepting one terminal-output string.

    Returns:
        int: Zero for no observed raw anomalies or I/O errors; one otherwise.
        Anomalies never end acquisition early. Ctrl-C/duration finishes the current
        batch and releases GPIO; queued unread events are outside these totals.
        Stop after the generator finishes when comparing a complete burst.
        Zero anomalies alone cannot prove all physical edges were captured.
    """
    args, settings = parse_counter_options(argv, __doc__)
    from box_runtime.input.treadmill.gpio_backend import GpiodSource

    source = (source_factory or GpiodSource)(settings)
    counter = None
    started_ns = None
    error = None
    previous_handler = None
    stop_requested = False

    def request_stop(signum: int, frame: FrameType | None) -> None:
        """Accept SIGINT number/frame; request a stop after this batch, return None."""
        nonlocal stop_requested
        stop_requested = True

    try:
        initial = source.open(clock_ns_fn() + int(settings.startup_timeout_s * 1e9))
        counter = RawEdgeCounter(initial)
        previous_handler = signal.signal(signal.SIGINT, request_stop)
        output_fn(
            f"Raw counter ready on BCM{settings.a_bcm_pin}/{settings.b_bcm_pin}. "
            "Start the generator now; Ctrl-C stops acquisition."
        )
        output_fn(
            "Anomalies do not stop this counter. Stop after the burst for complete-burst counts."
        )
        output_fn(
            f"Kernel buffer requested: {settings.kernel_event_buffer_size} events; "
            f"read batch: {settings.read_batch_size} events; "
            f"startup-excluded events: {initial.excluded_event_count}"
        )
        started_ns = clock_ns_fn()
        deadline_ns = (
            None if args.duration_s is None else started_ns + int(args.duration_s * 1e9)
        )
        while not stop_requested:
            now_ns = clock_ns_fn()
            if deadline_ns is not None and now_ns >= deadline_ns:
                break
            wait_s = 0.05
            if deadline_ns is not None:
                wait_s = min(wait_s, (deadline_ns - now_ns) / 1e9)
            batch = source.read_batch(wait_s)
            for event in batch:
                counter.process_event(event, clock_ns_fn())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        error = str(exc)
    finally:
        stopped_ns = clock_ns_fn()
        try:
            source.close()
        except Exception as exc:
            error = f"{error + '; ' if error else ''}Cleanup failed: {exc}"
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)

    if started_ns is not None:
        output_fn(f"Elapsed acquisition time: {(stopped_ns - started_ns) / 1e9:.6f} s")
    if counter is not None:
        for line in _format_summary(counter):
            output_fn(line)
    if error:
        output_fn(f"ERROR: {error}")
    return int(bool(error) or counter is None or bool(counter.anomaly_counts))


def _sequence_issue(previous: int, current: int) -> str | None:
    """Compare received uint32 sequences; return DUPLICATE/GAP/ORDER_INVALID/None."""
    difference = (current - previous) % 2**32
    if difference == 0:
        return "DUPLICATE"
    if difference >= 2**31:
        return "ORDER_INVALID"
    return "GAP" if difference > 1 else None


def _format_summary(counter: RawEdgeCounter) -> list[str]:
    """Return terminal lines from raw counts/ns; convert lag to milliseconds.

    counter is the stopped in-process accumulator. Formatting never reads GPIO,
    emits individual good events, or claims a quadrature position/integrity result.
    """
    lag_ns = counter.maximum_processing_lag_ns
    lag_text = "unavailable" if lag_ns is None else f"{lag_ns / 1e6:.6f} ms"
    lines = [
        f"Kernel edge events: {counter.edge_event_count}",
        f"A rising / falling: {counter.edge_counts[0][0]} / {counter.edge_counts[0][1]}",
        f"B rising / falling: {counter.edge_counts[1][0]} / {counter.edge_counts[1][1]}",
        f"Maximum processing lag: {lag_text}",
        f"Raw anomaly observations: {sum(counter.anomaly_counts.values())}",
    ]
    for flag, count in sorted(counter.anomaly_counts.items()):
        lines.append(f"  {flag}: {count}")
    lines.append(
        "Observed gaps can include reordered events; they are not an exact lost-edge count."
    )
    if counter.anomalies:
        lines.append(
            f"First anomalous events (at most 8; received order): {len(counter.anomalies)}"
        )
        lines.append(
            "  event# kernel_ns processing_ns ch edge global_before->observed line_before->observed flags"
        )
        for item in counter.anomalies:
            event = item.event
            lines.append(
                f"  {item.event_number} {event.timestamp_ns} {item.processing_monotonic_ns} "
                f"{event.channel} {event.edge_type} "
                f"{item.global_sequence_before}->{event.global_sequence_number} "
                f"{item.line_sequence_before}->{event.line_sequence_number} {','.join(item.flags)}"
            )
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
