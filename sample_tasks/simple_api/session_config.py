"""Session-info helpers for the simple task launcher."""

from __future__ import annotations

import time
from pathlib import Path


def build_session_info(
    output_root: Path,
    session_tag: str,
    *,
    protocol_name: str,
    box_profile: str,
    mock_audio: bool,
) -> dict[str, object]:
    """Build one session-info mapping for the simple task launcher.

    Args:
    - ``output_root``: root directory for run outputs
    - ``session_tag``: basename for the session directory
    - ``protocol_name``: task protocol name string
    - ``box_profile``: semantic box profile string
    - ``mock_audio``: whether the audio runtime should stay mocked

    Returns:
    - ``session_info``: JSON-serializable mapping consumed by ``BehavBox``
    """

    timestamp = time.strftime("%Y-%m-%d_%H%M%S")
    session_dir = output_root / session_tag
    return {
        "external_storage": str(output_root),
        "basename": session_tag,
        "dir_name": str(session_dir),
        "mouse_name": "tutorial_mouse",
        "datetime": timestamp,
        "box_name": str(protocol_name),
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
        "box_profile": str(box_profile),
        "mock_audio": bool(mock_audio),
    }
