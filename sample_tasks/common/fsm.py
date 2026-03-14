"""Finite-state-machine helpers for reference tasks."""

from __future__ import annotations

from typing import Optional


def append_task_event(task_state: dict, name: str, timestamp: float, **payload) -> dict:
    """Append one JSON-serializable task event.

    Args:
        task_state: Mutable task-state dictionary containing ``task_events``.
        name: Canonical event name string.
        timestamp: POSIX seconds as ``float``.
        payload: Additional JSON-serializable event fields.

    Returns:
        The appended event dictionary.
    """

    event = {"name": str(name), "timestamp": float(timestamp)}
    event.update(payload)
    task_state.setdefault("task_events", []).append(event)
    return event


def enter_phase(
    task_state: dict,
    phase: str,
    now_s: float,
    duration_s: Optional[float] = None,
    **payload,
) -> dict:
    """Update the current FSM phase and record a phase-transition event.

    Args:
        task_state: Mutable task-state dictionary.
        phase: New phase name.
        now_s: POSIX seconds as ``float``.
        duration_s: Optional phase duration in seconds. ``None`` means no
            deadline.
        payload: Additional JSON-serializable fields to store in the event.

    Returns:
        The emitted phase-transition event dictionary.
    """

    task_state["phase"] = str(phase)
    task_state["phase_started_s"] = float(now_s)
    task_state["phase_deadline_s"] = None if duration_s is None else float(now_s) + float(duration_s)
    return append_task_event(
        task_state,
        "phase_changed",
        float(now_s),
        phase=str(phase),
        duration_s=None if duration_s is None else float(duration_s),
        **payload,
    )
