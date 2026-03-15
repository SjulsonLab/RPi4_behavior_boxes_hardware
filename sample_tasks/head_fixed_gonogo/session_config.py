"""Shared session configuration helpers for operator- and CLI-launched go/no-go runs."""

from __future__ import annotations

import time
from pathlib import Path


def build_session_info(output_root: Path, session_tag: str) -> dict[str, object]:
    """Build one runnable session configuration for ``head_fixed_gonogo``.

    Args:
        output_root: Directory under which the session directory is created.
        session_tag: Human-readable session basename.

    Returns:
        dict[str, object]: Session configuration mapping consumed by ``BehavBox``.
    """

    timestamp = time.strftime("%Y-%m-%d_%H%M%S")
    session_dir = output_root / session_tag
    return {
        "external_storage": str(output_root),
        "basename": session_tag,
        "dir_name": str(session_dir),
        "mouse_name": "mock_mouse",
        "datetime": timestamp,
        "box_name": "head_fixed_gonogo",
        "reward_size": 50,
        "key_reward_amount": 50,
        "calibration_coefficient": {
            "1": [0.0, 0.01],
            "2": [0.0, 0.01],
            "3": [0.0, 0.01],
            "4": [0.0, 0.01],
        },
        "air_duration": 0.01,
        "vacuum_duration": 0.01,
        "visual_stimulus": False,
        "treadmill": False,
        "box_profile": "head_fixed",
        "mock_audio": True,
    }
