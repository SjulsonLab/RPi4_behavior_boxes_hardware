from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest


def _session_info(base_dir: str, **overrides) -> dict[str, object]:
    """Build one isolated BehavBox session configuration for plotting tests.

    Args:
        base_dir: Temporary directory root string.
        **overrides: Session fields overriding the defaults.

    Returns:
        dict[str, object]: Session configuration mapping.
    """

    info: dict[str, object] = {
        "external_storage": base_dir,
        "basename": "plotting_session",
        "dir_name": str(Path(base_dir) / "run"),
        "mouse_name": "mouseA",
        "datetime": "2026-04-08_120000",
        "box_name": "plotting_box",
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
        "camera_enabled": False,
        "mock_audio": True,
    }
    info.update(overrides)
    return info


def test_prepare_session_fails_cleanly_when_plotting_required_without_desktop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required plotting should fail with a desktop-session-specific error.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace plotting checks.
    """

    if sys.version_info < (3, 10):
        pytest.skip("BehavBox imports currently require Python 3.10+ syntax in this repo.")

    from box_runtime.behavior import behavbox as behavbox_module
    from box_runtime.behavior.behavbox import BehavBox
    from box_runtime.behavior.plotting_support import DesktopSessionStatus, PlottingDependencyStatus

    monkeypatch.setattr(
        behavbox_module,
        "detect_plotting_dependencies",
        lambda: PlottingDependencyStatus(ok=True, missing_modules=(), reason="ok"),
    )
    monkeypatch.setattr(
        behavbox_module,
        "detect_desktop_session",
        lambda env=None: DesktopSessionStatus(
            ok=False,
            display_env=None,
            reason="desktop plotting requires DISPLAY or WAYLAND_DISPLAY",
        ),
    )

    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp, plotting_required=True))
        with pytest.raises(RuntimeError, match="DISPLAY or WAYLAND_DISPLAY"):
            box.prepare_session()


def test_prepare_session_skips_plotting_when_not_required_without_desktop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional plotting should degrade cleanly when no desktop session exists.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace plotting checks.
    """

    if sys.version_info < (3, 10):
        pytest.skip("BehavBox imports currently require Python 3.10+ syntax in this repo.")

    from box_runtime.behavior import behavbox as behavbox_module
    from box_runtime.behavior.behavbox import BehavBox
    from box_runtime.behavior.plotting_support import DesktopSessionStatus, PlottingDependencyStatus

    monkeypatch.setattr(
        behavbox_module,
        "detect_plotting_dependencies",
        lambda: PlottingDependencyStatus(ok=True, missing_modules=(), reason="ok"),
    )
    monkeypatch.setattr(
        behavbox_module,
        "detect_desktop_session",
        lambda env=None: DesktopSessionStatus(
            ok=False,
            display_env=None,
            reason="desktop plotting requires DISPLAY or WAYLAND_DISPLAY",
        ),
    )

    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp, plotting_required=False))
        box.prepare_session()

        assert box.keyboard_active is False
        assert box.plotting_status["failure_reason"] == "desktop plotting requires DISPLAY or WAYLAND_DISPLAY"
        box.close()
