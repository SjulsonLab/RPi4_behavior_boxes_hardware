from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.input import service as input_service_module


def _session_info(base_dir: str, **overrides) -> dict[str, object]:
    """Build one BehavBox session mapping for GPIO compatibility tests.

    Args:
        base_dir: Temporary directory used for session artifacts.
        **overrides: Session fields overriding the defaults.

    Returns:
        dict[str, object]: Session configuration mapping consumed by BehavBox.
    """

    info: dict[str, object] = {
        "external_storage": base_dir,
        "basename": "gpio_compat_session",
        "dir_name": str(Path(base_dir) / "run"),
        "mouse_name": "mouseA",
        "datetime": "2026-04-09_120000",
        "box_name": "gpio_compat_box",
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
        "mock_audio": True,
        "camera_enabled": False,
        "box_profile": "head_fixed",
    }
    info.update(overrides)
    return info


def test_prepare_session_uses_gpiozero_compatible_button_keywords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BehavBox input setup should use keyword-only Button arguments.

    Args:
        monkeypatch: Fixture used to replace input-service GPIO classes.
    """

    button_calls: list[dict[str, object]] = []

    class StrictButton:
        """Test double matching current gpiozero Button keyword contract."""

        def __init__(
            self,
            pin: int | None = None,
            *,
            pull_up: bool = True,
            active_state: bool | None = None,
            bounce_time: float | None = None,
            hold_time: float = 1,
            hold_repeat: bool = False,
            pin_factory=None,
        ) -> None:
            del bounce_time, hold_time, hold_repeat, pin_factory
            self.pin = pin
            self.pull_up = pull_up
            self.active_state = active_state
            self.when_pressed = None
            self.when_released = None
            button_calls.append(
                {
                    "pin": pin,
                    "pull_up": pull_up,
                    "active_state": active_state,
                }
            )

        def close(self) -> None:
            return None

    class FakeRotaryEncoder:
        """Small encoder stand-in used to isolate Button construction."""

        def __init__(self, a: int, b: int, *args, **kwargs) -> None:
            del args, kwargs
            self.a = type("PinRef", (), {"pin": a})()
            self.b = type("PinRef", (), {"pin": b})()
            self.steps = 0

        def close(self) -> None:
            return None

    monkeypatch.setattr(input_service_module, "Button", StrictButton)
    monkeypatch.setattr(input_service_module, "RotaryEncoder", FakeRotaryEncoder)

    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp, box_profile="head_fixed"))
        box.prepare_session()
        box.close()

    assert button_calls
    assert all(call["pull_up"] is None for call in button_calls)
    assert all(call["active_state"] is True for call in button_calls)
