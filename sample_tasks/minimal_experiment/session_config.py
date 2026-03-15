"""Session-configuration helpers for the minimal experiment tutorial.

Data contracts:
- ``output_root``: ``pathlib.Path`` directory under which one session directory
  is created
- ``session_tag``: filesystem-safe basename for the session directory
- return value: JSON-serializable ``session_info`` mapping consumed by
  ``BehavBox``
"""

from __future__ import annotations

import time
from pathlib import Path


def _base_session_info(output_root: Path, session_tag: str, *, mock_audio: bool) -> dict[str, object]:
    """Build one minimal session configuration shared by mock and Pi runners.

    Args:
    - ``output_root``: directory root for session outputs
    - ``session_tag``: basename for the session directory
    - ``mock_audio``: whether the audio runtime should stay in mock mode

    Returns:
    - ``session_info``: JSON-serializable mapping used by ``BehavBox``
    """

    timestamp = time.strftime("%Y-%m-%d_%H%M%S")
    session_dir = output_root / session_tag
    return {
        "external_storage": str(output_root),
        "basename": session_tag,
        "dir_name": str(session_dir),
        "mouse_name": "tutorial_mouse",
        "datetime": timestamp,
        "box_name": "minimal_experiment",
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
        "mock_audio": bool(mock_audio),
    }


def build_mock_session_info(output_root: Path, session_tag: str) -> dict[str, object]:
    """Build session configuration for local mock-mode tutorial runs.

    Args:
    - ``output_root``: directory root for session outputs
    - ``session_tag``: basename for the session directory

    Returns:
    - ``session_info``: JSON-serializable mapping with mock audio enabled
    """

    return _base_session_info(Path(output_root).resolve(), session_tag, mock_audio=True)


def build_headless_pi_session_info(output_root: Path, session_tag: str) -> dict[str, object]:
    """Build session configuration for real headless Raspberry Pi tutorial runs.

    Args:
    - ``output_root``: directory root for session outputs
    - ``session_tag``: basename for the session directory

    Returns:
    - ``session_info``: JSON-serializable mapping with real audio enabled
    """

    return _base_session_info(Path(output_root).resolve(), session_tag, mock_audio=False)

