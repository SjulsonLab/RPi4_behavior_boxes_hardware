import json
import os
import tempfile
import time
from pathlib import Path

import pytest

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.io_manifest import load_box_profile
from box_runtime.mock_hw.registry import REGISTRY


def _session_info(base_dir: str, **overrides) -> dict:
    """Build one isolated mock-session configuration for output-service tests.

    Args:
    - ``base_dir``: Temporary directory root.
    - ``overrides``: Session-info overrides.

    Returns:
    - ``session_info``: mapping consumed by ``BehavBox``.
    """

    info = {
        "external_storage": base_dir,
        "basename": "output_session",
        "dir_name": str(Path(base_dir) / "run"),
        "mouse_name": "mouseA",
        "datetime": "2026-03-14_120000",
        "box_name": "test_box",
        "reward_size": 50,
        "key_reward_amount": 50,
        "calibration_coefficient": {
            "1": [0.0, 0.01],
            "2": [0.0, 0.02],
            "3": [0.0, 0.03],
            "4": [0.0, 0.04],
        },
        "air_duration": 0.01,
        "vacuum_duration": 0.01,
        "visual_stimulus": False,
        "treadmill": False,
        "box_profile": "head_fixed",
    }
    info.update(overrides)
    return info


def test_load_box_profile_uses_fixed_python_mappings_and_profile_specific_outputs():
    head_fixed = load_box_profile("head_fixed")
    freely_moving = load_box_profile("freely_moving")

    assert not hasattr(head_fixed, "source_csv")
    assert head_fixed.profile_name == "head_fixed"
    assert freely_moving.profile_name == "freely_moving"
    assert head_fixed.inputs["trigger_in"].pin == 23
    assert head_fixed.outputs["trigger_out"].pin == 24
    assert head_fixed.outputs["cue_led_5"].pin == 10
    assert head_fixed.outputs["cue_led_6"].pin == 11
    assert head_fixed.user_configurable["user_configurable"].pin == 4
    assert 9 in head_fixed.reserved

    assert "airpuff" in head_fixed.outputs
    assert "reward_5" not in head_fixed.outputs
    assert "reward_5" in freely_moving.outputs
    assert "airpuff" not in freely_moving.outputs
    assert freely_moving.inputs["poke_left"].pin == 5
    assert freely_moving.inputs["poke_right"].pin == 6
    assert freely_moving.inputs["poke_center"].pin == 12
    assert freely_moving.inputs["poke_extra1"].pin == 13
    assert freely_moving.inputs["poke_extra2"].pin == 16


def test_freely_moving_profile_exposes_reward_5_not_airpuff():
    REGISTRY.reset()
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp, box_profile="freely_moving"))
        box.prepare_session()

        assert "reward_5" in box.output_service.outputs
        with pytest.raises(KeyError):
            box.pulse_output("airpuff")


def test_deliver_reward_and_generic_outputs_share_recording_files():
    REGISTRY.reset()
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))
        box.prepare_session()
        recording_dir = Path(box.start_recording())

        box.deliver_reward("reward_left", 100)
        box.pulse_output("vacuum", duration_s=0.01)
        box.pulse_output("trigger_out", duration_s=0.01)
        time.sleep(0.08)
        box.stop_recording()

        jsonl_rows = [
            json.loads(line)
            for line in (recording_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        event_names = [row["name"] for row in jsonl_rows]
        assert "reward_left_pulse" in event_names
        assert "vacuum_pulse" in event_names
        assert "trigger_out_pulse" in event_names

        log_text = (recording_dir / "input_events.log").read_text(encoding="utf-8")
        assert "reward_left_pulse" in log_text
        assert "vacuum_pulse" in log_text
        assert "trigger_out_pulse" in log_text


def test_configure_user_output_claims_gpio4_without_touching_trigger_pins():
    REGISTRY.reset()
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))
        box.prepare_session()

        user_output = box.configure_user_output(label="ttl_output")

        assert user_output.pin == 4
        assert box.trigger_in.pin == 23
        assert box.output_service.outputs["trigger_out"].pin == 24

        state = REGISTRY.get_state()
        gpio4 = next(pin for pin in state["pins"] if pin["pin"] == 4)
        assert gpio4["direction"] == "output"
        assert gpio4["label"] == "ttl_output"
