"""Run production x4 treadmill acquisition until Ctrl-C or an optional duration."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from box_runtime.input.treadmill.config import TreadmillConfig
    from box_runtime.input.treadmill.decoder import FirstFailureDiagnostic

try:
    from debug.treadmill_options import parse_counter_options
except ModuleNotFoundError:
    from treadmill_options import parse_counter_options


def main(
    argv: list[str] | None = None,
    *,
    runtime_factory=None,
    wait_fn=time.sleep,
    output_fn=print,
) -> int:
    """Run acquisition independently and print final count/health diagnostics.

    Args:
        argv: Optional CLI argument strings; None reads the process arguments.
        runtime_factory: Optional test factory taking validated treadmill config.
        wait_fn: Callable(seconds) for bounded operator waits; may raise Ctrl-C.
        output_fn: Callable accepting one terminal-output string.

    Returns:
        int: Zero on ordinary duration/Ctrl-C completion with valid acquisition;
        one on acquisition or cleanup failure. No behavior or recorder is created.
    """
    args, settings = parse_counter_options(argv, __doc__)
    from box_runtime.input.treadmill.runtime import TreadmillRuntime
    from box_runtime.input.treadmill.state import FAILED_CODES

    runtime = (
        runtime_factory(settings)
        if runtime_factory is not None
        else TreadmillRuntime(settings, capture_failure_history=False)
    )
    started = None
    stopped = None
    state = None
    error = None
    try:
        runtime.start()
        started = time.monotonic()
        output_fn(
            f"Treadmill ready on BCM{runtime.config.a_bcm_pin}/{runtime.config.b_bcm_pin}. "
            "Start the generator now; Ctrl-C stops acquisition."
        )
        if not settings.calibration_verified:
            output_fn(
                "WARNING: physical calibration is UNVERIFIED; raw x4 counts remain available."
            )
        try:
            while (
                args.duration_s is None or time.monotonic() - started < args.duration_s
            ):
                remaining = (
                    0.05
                    if args.duration_s is None
                    else min(
                        0.05, max(0, args.duration_s - (time.monotonic() - started))
                    )
                )
                wait_fn(remaining)
                state = runtime.snapshot()
                if state.health == "FAILED":
                    error = f"Acquisition failed: {state.failure_code}"
                    break
        except KeyboardInterrupt:
            pass
    except Exception as exc:
        error = str(exc)
    finally:
        stopped = time.monotonic()
        try:
            runtime.close()
        except Exception as exc:
            error = f"{error + '; ' if error else ''}Cleanup failed: {exc}"
        try:
            state = runtime.snapshot()
        except Exception:
            pass
    report = runtime.health_report()
    if started is not None:
        output_fn(f"Elapsed acquisition time: {stopped - started:.6f} s")
    if state is not None:
        output_fn(f"Kernel edge events: {state.edge_event_count}")
        output_fn(f"Valid x4 quadrature transitions: {state.valid_transition_count}")
        output_fn(f"Signed raw transition position: {state.raw_transition_position}")
        output_fn(
            f"Positive / negative transitions: {state.positive_transition_count} / {state.negative_transition_count}"
        )
        output_fn(f"Direction reversals: {state.direction_reversal_count}")
        output_fn(
            f"Sequence gaps (global / A / B): {state.global_sequence_gap_count} / {state.a_line_sequence_gap_count} / {state.b_line_sequence_gap_count}"
        )
        output_fn(f"Estimated missing events: {state.estimated_missing_event_count}")
        output_fn(f"Event inconsistencies: {state.event_inconsistency_count}")
        output_fn(
            f"Publication skips / diagnostic drops: {state.publication_skip_count} / {state.diagnostic_event_drop_count}"
        )
        output_fn(f"Integrity valid: {state.integrity_valid}")
        output_fn(
            f"Maximum observed transition rate (100 ms bins): {state.maximum_observed_transition_rate_hz_lifetime:.1f} Hz"
        )
        lag = state.maximum_processing_lag_ns_lifetime
        output_fn(
            f"Maximum processing lag: {'unavailable' if lag is None else str(lag / 1e6) + ' ms'}"
        )
    output_fn(f"Health: {report.health}; failure code: {report.failure_code}")
    output_fn(
        f"Observed health codes: {', '.join(report.observed_health_codes) or 'none'}"
    )
    diagnostic = runtime.first_failure_diagnostic
    if diagnostic is not None:
        for line in _format_first_failure(diagnostic, runtime.config):
            output_fn(line)
    elif state is not None and not state.integrity_valid:
        output_fn(
            "First integrity failure context: unavailable (not captured or not delivered before cleanup)."
        )
    if error:
        output_fn(f"ERROR: {error}")
    return int(
        bool(error)
        or not report.integrity_valid
        or bool(set(report.observed_health_codes) & FAILED_CODES)
    )


def _format_first_failure(
    diagnostic: FirstFailureDiagnostic,
    settings: TreadmillConfig,
) -> list[str]:
    """Format a cached bounded report for printing only after acquisition closes.

    Args:
        diagnostic: Frozen first-failure window in received order; timestamps
            are monotonic ns, counts/positions and AB bits are dimensionless.
        settings: Effective config containing the actual A/B BCM pin numbers.

    Returns:
        list[str]: Terminal lines. Raw timestamps remain ns; adjacent kernel
        time differences and lag are displayed in us with nanosecond precision.
        Remembered states are decoded history, never fresh GPIO measurements.
    """
    record = diagnostic.records[diagnostic.preceding_event_count]
    event = record.event
    pins = {"A": settings.a_bcm_pin, "B": settings.b_bcm_pin}
    bit = {"A": 2, "B": 1}.get(event.channel)
    remembered = "unknown" if bit is None else str(int(bool(record.before & bit)))
    target = {"rising": "1", "falling": "0"}.get(event.edge_type, "unknown")
    lines = [
        f"First integrity failure: {', '.join(record.flags)}",
        f"Admitted event: {diagnostic.event_number}",
        f"Accepted transitions before failure: {diagnostic.valid_transition_count_before}",
        f"Signed raw position before failure: {diagnostic.raw_transition_position_before}",
        f"Channel {event.channel} / BCM{pins.get(event.channel, 'unmapped')}: {event.edge_type}",
        f"Remembered {event.channel} level: {remembered}; reported target level: {target}",
        f"AB (decoder-remembered): {record.before:02b} -> {record.after:02b}; decoded delta: {record.delta}",
        f"Kernel timestamp (ns): {event.timestamp_ns}",
        f"Processing timestamp (ns): {record.processing_monotonic_ns}",
        f"Global sequence before: {diagnostic.global_sequence_before}; observed: {event.global_sequence_number}",
        f"A/B line sequences before: {diagnostic.line_sequence_numbers_before[0]} / "
        f"{diagnostic.line_sequence_numbers_before[1]}; observed {event.channel}: {event.line_sequence_number}",
        f"Context: preceding {diagnostic.preceding_event_count}/8; following {diagnostic.following_event_count}/4 "
        "(short windows reflect available history/input)",
        "Received order; '>' marks the offending event. AB values are decoder-remembered.",
        "  event# kernel_ns dt_us ch edge global_seq line_seq AB_before->after delta lag_us flags",
    ]
    previous_ns = None
    first_number = diagnostic.event_number - diagnostic.preceding_event_count
    for index, item in enumerate(diagnostic.records):
        raw = item.event
        marker = ">" if index == diagnostic.preceding_event_count else " "
        dt_us = (
            "-"
            if previous_ns is None
            else f"{(raw.timestamp_ns - previous_ns) / 1000:.3f}"
        )
        lag_us = (
            "-"
            if item.processing_lag_ns is None
            else f"{item.processing_lag_ns / 1000:.3f}"
        )
        lines.append(
            f"{marker} {first_number + index} {raw.timestamp_ns} {dt_us} {raw.channel} {raw.edge_type} "
            f"{raw.global_sequence_number} {raw.line_sequence_number} {item.before:02b}->{item.after:02b} "
            f"{item.delta:+d} {lag_us} {','.join(item.flags) or '-'}"
        )
        previous_ns = raw.timestamp_ns
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
