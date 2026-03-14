import os
import tempfile
import time
from pathlib import Path

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox
from sample_tasks.common.mock_inputs import MockInputInjector
from sample_tasks.head_fixed_gonogo import task as gonogo_task


def _session_info(base_dir: str) -> dict:
    """Build one isolated mock-session configuration for go/no-go tests.

    Args:
        base_dir: Temporary directory root.

    Returns:
        Session configuration mapping with explicit filesystem paths.
    """

    return {
        "external_storage": base_dir,
        "basename": "gonogo_session",
        "dir_name": str(Path(base_dir) / "run"),
        "mouse_name": "mouseA",
        "datetime": "2026-03-13_220000",
        "box_name": "test_box",
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
        "input_profile": "head_fixed",
    }


def _drain_events(box: BehavBox, task_state: dict) -> None:
    """Deliver all queued box events into the task handler.

    Args:
        box: Prepared and started BehavBox runtime.
        task_state: Mutable task state returned by ``prepare_task``.
    """

    while box.event_list:
        event = box.event_list.popleft()
        gonogo_task.handle_event(box, task_state, event)


def _advance_task(box: BehavBox, task_state: dict, steps: int = 3) -> None:
    """Advance the time-driven FSM enough to reach the response phase.

    Args:
        box: Prepared and started BehavBox runtime.
        task_state: Mutable task state dictionary.
        steps: Number of update cycles to run.
    """

    for _ in range(int(steps)):
        gonogo_task.update_task(box, task_state, now_s=time.time())
        _drain_events(box, task_state)
        time.sleep(0.01)


def test_go_trial_center_response_yields_hit_and_reward():
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))
        box.prepare_session()
        box.start_session()
        task_state = gonogo_task.prepare_task(
            box,
            {
                "trial_sequence": ["go"],
                "go_cue_duration_s": 0.02,
                "nogo_cue_duration_s": 0.02,
                "iti_s": 0.0,
                "response_window_s": 0.25,
                "reward_output": "3",
                "reward_size_ul": 50,
                "max_trials": 1,
            },
        )
        gonogo_task.start_task(box, task_state)
        _advance_task(box, task_state)

        box.center_entry()
        _drain_events(box, task_state)
        _advance_task(box, task_state, steps=2)

        assert task_state["counters"]["hits"] == 1
        assert task_state["counters"]["completed_trials"] == 1
        assert any(name == "pump3_reward" for name, _ in box.pump.reward_list)

        box.stop_session()
        box.finalize_session()
        box.close()


def test_nogo_trial_center_response_yields_false_alarm_and_timeout():
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))
        box.prepare_session()
        box.start_session()
        task_state = gonogo_task.prepare_task(
            box,
            {
                "trial_sequence": ["nogo"],
                "go_cue_duration_s": 0.02,
                "nogo_cue_duration_s": 0.02,
                "iti_s": 0.0,
                "response_window_s": 0.25,
                "timeout_s": 0.02,
                "max_trials": 1,
            },
        )
        gonogo_task.start_task(box, task_state)
        _advance_task(box, task_state)

        box.center_entry()
        _drain_events(box, task_state)
        _advance_task(box, task_state, steps=3)

        assert task_state["counters"]["false_alarms"] == 1
        assert task_state["phase"] in {"timeout", "inter_trial_cleanup", "iti"}

        box.stop_session()
        box.finalize_session()
        box.close()


def test_mock_input_injector_drives_same_response_path():
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))
        box.prepare_session()
        box.start_session()
        injector = MockInputInjector()
        task_state = gonogo_task.prepare_task(
            box,
            {
                "trial_sequence": ["go"],
                "go_cue_duration_s": 0.02,
                "nogo_cue_duration_s": 0.02,
                "iti_s": 0.0,
                "response_window_s": 0.25,
                "max_trials": 1,
            },
        )
        gonogo_task.start_task(box, task_state)
        _advance_task(box, task_state)

        injector.pulse("lick_3", duration_ms=10)
        time.sleep(0.05)
        _drain_events(box, task_state)
        _advance_task(box, task_state, steps=2)

        assert task_state["counters"]["hits"] == 1

        box.stop_session()
        box.finalize_session()
        box.close()
