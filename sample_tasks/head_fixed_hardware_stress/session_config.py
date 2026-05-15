"""Session configuration helpers for the head-fixed hardware stress task."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Mapping

from box_runtime.behavior.gpio_backend import is_raspberry_pi

DEFAULT_USB_AUDIO_DEVICE = "plughw:CARD=Device,DEV=0"


def resolve_audio_session_settings(
    *,
    is_raspberry_pi_fn: Callable[[], bool] = is_raspberry_pi,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Resolve mock-versus-real audio settings for the stress task.

    Data contracts:
    - ``is_raspberry_pi_fn``: zero-argument callable answering whether the host
      is a Raspberry Pi.
    - ``env``: optional mapping of environment variables used to override the
      selected ALSA device name.
    - Returns ``dict[str, object]`` containing:
      - ``mock_audio``: ``bool`` selecting the recording backend off-Pi.
      - ``audio_device``: ``str | None`` ALSA playback device name.
    """

    environment = os.environ if env is None else env
    if not bool(is_raspberry_pi_fn()):
        return {
            "mock_audio": True,
            "audio_device": None,
        }
    audio_device = str(environment.get("BEHAVBOX_AUDIO_DEVICE", DEFAULT_USB_AUDIO_DEVICE))
    return {
        "mock_audio": False,
        "audio_device": audio_device,
    }


def build_session_info(output_root: Path, session_tag: str) -> dict[str, object]:
    """Build one runnable session configuration for hardware-stress sessions.

    Data contracts:
    - ``output_root``: ``Path`` naming the parent directory for the session.
    - ``session_tag``: ``str`` basename for the session directory.
    - Returns ``dict[str, object]`` consumed by ``BehavBox``. The mapping keeps
      the go/no-go monitor topology but enables recording and treadmill logging.
    """

    timestamp = time.strftime("%Y-%m-%d_%H%M%S")
    session_dir = Path(output_root) / str(session_tag)
    repo_root = Path(__file__).resolve().parents[2]
    visual_root = repo_root / "box_runtime" / "visual_stimuli"
    visual_backend = "drm" if is_raspberry_pi() else "fake"
    audio_settings = resolve_audio_session_settings()
    return {
        "external_storage": str(output_root),
        "basename": str(session_tag),
        "dir_name": str(session_dir),
        "mouse_name": "mock_mouse",
        "datetime": timestamp,
        "box_name": "head_fixed_hardware_stress",
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
        "camera_ids": ["camera0", "camera1"],
        "camera_recording_enabled": True,
        "camera_preview_modes": {"camera0": "qt_local", "camera1": "off"},
        "camera_preview_connector": "HDMI-A-1",
        "camera_preview_max_hz": 15.0,
        "treadmill": True,
        "box_profile": "head_fixed",
        **audio_settings,
    }
