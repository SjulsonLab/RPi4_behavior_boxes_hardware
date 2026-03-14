"""Helpers for standardized sample-task output artifacts."""

from __future__ import annotations

import json
from pathlib import Path


def write_final_task_state(session_dir: Path, final_task_state: dict) -> Path:
    """Write the final serialized task-state snapshot.

    Args:
        session_dir: Session output directory.
        final_task_state: JSON-serializable dictionary.

    Returns:
        Path to the written ``final_task_state.json`` file.
    """

    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "final_task_state.json"
    path.write_text(json.dumps(final_task_state, indent=2, sort_keys=True), encoding="utf-8")
    return path


def write_task_events(session_dir: Path, task_events: list[dict]) -> Path:
    """Write task and lifecycle events as newline-delimited JSON.

    Args:
        session_dir: Session output directory.
        task_events: Sequence of JSON-serializable event dictionaries.

    Returns:
        Path to the written ``task_events.jsonl`` file.
    """

    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "task_events.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for event in task_events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    return path
