"""Live plot-state publication for head-fixed go/no-go runs."""

from __future__ import annotations

from typing import Callable


def _safe_rate(numerator: int, denominator: int) -> float | None:
    if int(denominator) <= 0:
        return None
    return float(numerator) / float(denominator)


def build_plot_payload(task_state: dict, history_limit: int = 64) -> dict:
    """Build one JSON-serializable browser plot payload from task state."""

    counters = dict(task_state.get("counters", {}))
    outcomes = list(task_state.get("trial_outcomes", []))[-max(int(history_limit), 1):]
    hit_denominator = int(counters.get("hits", 0)) + int(counters.get("misses", 0))
    fa_denominator = int(counters.get("false_alarms", 0)) + int(counters.get("correct_rejects", 0))
    return {
        "kind": "gonogo_performance",
        "trial_outcomes": outcomes,
        "counts": {
            "completed_trials": int(counters.get("completed_trials", 0)),
            "hits": int(counters.get("hits", 0)),
            "misses": int(counters.get("misses", 0)),
            "false_alarms": int(counters.get("false_alarms", 0)),
            "correct_rejects": int(counters.get("correct_rejects", 0)),
        },
        "rates": {
            "hit_rate": _safe_rate(int(counters.get("hits", 0)), hit_denominator),
            "false_alarm_rate": _safe_rate(int(counters.get("false_alarms", 0)), fa_denominator),
        },
    }


def build_plot_step_hook(history_limit: int = 64) -> Callable[[object], None]:
    """Build one TaskRunner step hook that republishes live plot state."""

    def _hook(runner) -> None:
        runner.box.publish_runtime_state("plot", **build_plot_payload(runner.task_state, history_limit=history_limit))

    return _hook
