"""Shared session configuration helpers for operator- and CLI-launched go/no-go runs."""

from __future__ import annotations

import time
from pathlib import Path

from box_runtime.behavior.gpio_backend import is_raspberry_pi


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
    repo_root = Path(__file__).resolve().parents[2]
    visual_root = repo_root / "box_runtime" / "visual_stimuli"
    visual_backend = "drm" if is_raspberry_pi() else "fake"
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
        "visual_stimulus": True,
        "vis_gratings": [
            str(visual_root / "go_grating.yaml"),
            str(visual_root / "nogo_grating.yaml"),
        ],
        "visual_display_backend": visual_backend,
        "visual_display_connector": "HDMI-A-2",
        "visual_display_refresh_hz": 60.0,
        "visual_display_degrees_subtended": 80.0,
        "gray_level": 127,
        "camera_enabled": True,
        "camera_ids": ["camera0"],
        "camera_recording_enabled": False,
        "camera_preview_modes": {"camera0": "qt_local"},
        "camera_preview_connector": "HDMI-A-1",
        "camera_preview_max_hz": 15.0,
        "treadmill": False,
        "box_profile": "head_fixed",
        "mock_audio": True,
    }
