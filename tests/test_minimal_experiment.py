import os
import tempfile
import time
from pathlib import Path

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox
from sample_tasks.common.runner import TaskRunner
from sample_tasks.minimal_experiment import task as minimal_task
from sample_tasks.minimal_experiment.run_mock import configure_mock_environment
from sample_tasks.minimal_experiment.run_pi import configure_headless_pi_environment
from sample_tasks.minimal_experiment.session_config import (
    build_headless_pi_session_info,
    build_mock_session_info,
)


def test_mock_session_config_uses_mock_audio_and_head_fixed_profile():
    with tempfile.TemporaryDirectory() as tmp:
        session_info = build_mock_session_info(Path(tmp), "tutorial_session")

    assert session_info["box_profile"] == "head_fixed"
    assert session_info["mock_audio"] is True
    assert session_info["dir_name"].endswith("tutorial_session")


def test_headless_pi_session_config_disables_mock_audio():
    with tempfile.TemporaryDirectory() as tmp:
        session_info = build_headless_pi_session_info(Path(tmp), "pi_session")

    assert session_info["box_profile"] == "head_fixed"
    assert session_info["mock_audio"] is False
    assert session_info["dir_name"].endswith("pi_session")


def test_mock_environment_configuration_forces_mock_mode(monkeypatch):
    monkeypatch.delenv("BEHAVBOX_FORCE_MOCK", raising=False)
    monkeypatch.delenv("BEHAVBOX_MOCK_UI_AUTOSTART", raising=False)

    configure_mock_environment()

    assert os.environ["BEHAVBOX_FORCE_MOCK"] == "1"
    assert os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] == "1"


def test_headless_pi_environment_does_not_force_mock(monkeypatch):
    monkeypatch.setenv("BEHAVBOX_FORCE_MOCK", "1")
    monkeypatch.delenv("BEHAVBOX_MOCK_UI_AUTOSTART", raising=False)

    configure_headless_pi_environment()

    assert "BEHAVBOX_FORCE_MOCK" not in os.environ
    assert os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] == "0"


def test_minimal_experiment_completes_and_writes_standard_outputs():
    with tempfile.TemporaryDirectory() as tmp:
        session_info = build_mock_session_info(Path(tmp), "minimal_run")
        box = BehavBox(session_info)
        injected = {"done": False}

        def inject_center_response(runner):
            if runner.task_state["phase"] == "response_window" and not injected["done"]:
                runner.box.center_entry()
                injected["done"] = True

        runner = TaskRunner(
            box=box,
            task=minimal_task,
            task_config={
                "cue_duration_s": 0.01,
                "response_window_s": 0.2,
                "max_duration_s": 5.0,
                "reward_on_response": True,
                "reward_size_ul": 25.0,
            },
            step_hooks=[inject_center_response],
        )

        final_state = runner.run(poll_interval_s=0.005)

        session_dir = Path(session_info["dir_name"])
        assert injected["done"] is True
        assert final_state["response_detected"] is True
        assert final_state["stop_reason"] == "response_received"
        assert (session_dir / "final_task_state.json").exists()
        assert (session_dir / "task_events.jsonl").exists()

