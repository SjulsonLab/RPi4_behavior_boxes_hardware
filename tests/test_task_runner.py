import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox
from sample_tasks.common.runner import TaskRunner


def _session_info(base_dir: str) -> dict:
    """Build one isolated mock-session configuration for lifecycle tests.

    Args:
        base_dir: Temporary directory root.

    Returns:
        Session configuration mapping with explicit filesystem paths.
    """

    return {
        "external_storage": base_dir,
        "basename": "test_session",
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
        "box_profile": "head_fixed",
    }


class _FakeTask:
    """Small task adapter used to verify runner ordering and cleanup."""

    def __init__(self, should_raise: bool = False) -> None:
        self.should_raise = should_raise
        self.calls: list[str] = []

    def prepare_task(self, box, task_config):
        self.calls.append("prepare_task")
        return {"phase": "idle", "task_config": dict(task_config)}

    def start_task(self, box, task_state):
        self.calls.append("start_task")
        task_state["phase"] = "running"

    def handle_event(self, box, task_state, event):
        self.calls.append(f"handle_event:{box.event_name(event)}")
        if self.should_raise:
            raise RuntimeError("boom")

    def update_task(self, box, task_state, now_s):
        self.calls.append("update_task")
        box._handle_input_event("fake_task_event", record_interaction=False)

    def should_stop(self, box, task_state):
        self.calls.append("should_stop")
        return bool(task_state.get("done", False))

    def stop_task(self, box, task_state, reason):
        self.calls.append(f"stop_task:{reason}")
        task_state["done"] = True
        task_state["stop_reason"] = reason

    def finalize_task(self, box, task_state):
        self.calls.append("finalize_task")
        return {"phase": task_state["phase"], "stop_reason": task_state.get("stop_reason")}


class _PassiveTask(_FakeTask):
    """Task double that never self-terminates, used for hook sequencing tests."""

    def update_task(self, box, task_state, now_s):
        self.calls.append("update_task")

    def should_stop(self, box, task_state):
        self.calls.append("should_stop")
        return False


def test_behavbox_lifecycle_enforces_prepare_before_start():
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))

        with pytest.raises(RuntimeError):
            box.start_session()

        box.prepare_session()
        box.start_session()
        box.stop_session()
        box.finalize_session()
        box.close()
        box.close()


def test_task_runner_writes_final_task_state_and_events():
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))
        task = _FakeTask()
        runner = TaskRunner(box=box, task=task, task_config={"max_duration_s": 0.0})

        runner.prepare()
        runner.start()
        runner.stop(reason="completed")
        result = runner.finalize()

        session_dir = Path(box.session_info["dir_name"])
        final_task_state = session_dir / "final_task_state.json"
        task_events = session_dir / "task_events.jsonl"

        assert result["stop_reason"] == "completed"
        assert final_task_state.exists()
        assert task_events.exists()

        payload = json.loads(final_task_state.read_text(encoding="utf-8"))
        assert payload["stop_reason"] == "completed"
        event_lines = task_events.read_text(encoding="utf-8").strip().splitlines()
        assert any("session_started" in line for line in event_lines)
        assert task.calls[:2] == ["prepare_task", "start_task"]


def test_task_runner_still_cleans_up_on_task_error():
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))
        task = _FakeTask(should_raise=True)
        runner = TaskRunner(box=box, task=task, task_config={})

        runner.prepare()
        runner.start()
        box._handle_input_event("trigger_error")

        with pytest.raises(RuntimeError):
            runner.step()

        final_state = runner.finalize()
        assert final_state["stop_reason"] == "error"
        assert runner.is_closed is True


def test_task_runner_step_hook_runs_only_after_start():
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))
        task = _PassiveTask()
        observed = []

        def step_hook(runner):
            observed.append(
                {
                    "prepared": runner.is_prepared,
                    "started": runner.is_started,
                    "phase": runner.task_state["phase"],
                }
            )
            runner.stop(reason="hook_stop")

        runner = TaskRunner(box=box, task=task, task_config={}, step_hooks=[step_hook])

        runner.prepare()
        assert observed == []

        runner.start()
        runner.step()
        final_state = runner.finalize()

        assert observed == [{"prepared": True, "started": True, "phase": "running"}]
        assert final_state["stop_reason"] == "hook_stop"
