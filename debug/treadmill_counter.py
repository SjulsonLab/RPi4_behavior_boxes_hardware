"""Run production x4 treadmill acquisition until Ctrl-C or an optional duration."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import time

try:
    from debug.repo_imports import prepare_repo_imports
except ModuleNotFoundError:
    from repo_imports import prepare_repo_imports


def _duration(value: str) -> float:
    """Parse positive finite seconds from CLI text; return float or parser error."""
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be positive finite seconds")
    return seconds


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duration-s", type=_duration, help="otherwise run until Ctrl-C"
    )
    args = parser.parse_args(argv)
    prepare_repo_imports(script_path=Path(__file__))
    from box_runtime.io_manifest import load_box_profile
    from box_runtime.input.treadmill.config import load_config
    from box_runtime.input.treadmill.runtime import TreadmillRuntime
    from box_runtime.input.treadmill.state import FAILED_CODES

    settings = load_config(
        {"treadmill": True, "treadmill_config": {"treadmill_required": True}},
        load_box_profile("head_fixed"),
    )
    runtime = (runtime_factory or TreadmillRuntime)(settings)
    started = None
    stopped = None
    state = None
    error = None
    try:
        runtime.start()
        started = time.monotonic()
        output_fn(
            "Treadmill ready on BCM13/16. Start the generator now; Ctrl-C stops acquisition."
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
    if error:
        output_fn(f"ERROR: {error}")
    return int(
        bool(error)
        or not report.integrity_valid
        or bool(set(report.observed_health_codes) & FAILED_CODES)
    )


if __name__ == "__main__":
    raise SystemExit(main())
