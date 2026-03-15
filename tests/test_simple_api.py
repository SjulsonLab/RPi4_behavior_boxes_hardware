import os
import tempfile
from pathlib import Path

import pytest

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox
from sample_tasks.common.runner import TaskRunner
from sample_tasks.simple_api import (
    SimpleTask,
    decrement_param,
    deliver_reward,
    increment_param,
    play_cue,
    record_event,
    set_param,
    stop_sound,
)
from sample_tasks.simple_api.loader import load_task_from_file
from sample_tasks.simple_api.run import (
    build_session_info_for_mode,
    configure_environment_for_mode,
    resolve_run_mode,
)


def test_builder_creates_runnable_task_protocol_object():
    task = (
        SimpleTask(name="builder_test", box_profile="head_fixed")
        .cue("go", duration_s=0.01, side="both")
        .state("cue")
        .on_enter(play_cue("go"))
        .after(0.01, goto="done")
    )
    task.state("done").finish("completed")
    compiled = task.build()

    for method_name in (
        "prepare_task",
        "start_task",
        "handle_event",
        "update_task",
        "should_stop",
        "stop_task",
        "finalize_task",
    ):
        assert hasattr(compiled, method_name)


def test_duplicate_state_names_fail_cleanly():
    task = SimpleTask(name="duplicate_state")
    task.state("cue")
    with pytest.raises(ValueError, match="duplicate"):
        task.state("cue")


def test_missing_transition_target_fails_cleanly():
    task = SimpleTask(name="missing_target").state("cue").after(0.1, goto="missing")
    with pytest.raises(ValueError, match="target state"):
        task.build()


def test_missing_terminal_path_fails_cleanly():
    task = SimpleTask(name="no_terminal").state("cue").after(0.1, goto="cue")
    with pytest.raises(ValueError, match="terminal"):
        task.build()


def test_unknown_cue_name_in_action_fails_cleanly():
    task = SimpleTask(name="unknown_cue").state("cue").on_enter(play_cue("missing"))
    task.state("done").finish("completed")
    task.state("cue").after(0.1, goto="done")
    with pytest.raises(ValueError, match="cue"):
        task.build()


def test_unknown_parameter_name_in_action_fails_cleanly():
    task = SimpleTask(name="unknown_param").cue("go", duration_s=0.01)
    task.state("cue").on_enter(play_cue("go"), increment_param("missing", 1)).after(0.01, goto="done")
    task.state("done").finish("completed")
    with pytest.raises(ValueError, match="parameter"):
        task.build()


def test_loader_loads_task_from_python_file(tmp_path: Path):
    task_file = tmp_path / "tutorial_task.py"
    task_file.write_text(
        "\n".join(
            [
                "from sample_tasks.simple_api import SimpleTask, play_cue",
                "TASK = (",
                "    SimpleTask(name='loaded_task')",
                "    .cue('go', duration_s=0.01)",
                "    .state('cue')",
                "    .on_enter(play_cue('go'))",
                "    .after(0.01, goto='done')",
                ")",
                "TASK.state('done').finish('completed')",
                "TASK = TASK.build()",
                "",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_task_from_file(task_file)
    assert getattr(loaded, "PROTOCOL_NAME", None) == "loaded_task"


def test_auto_mode_selects_mock_on_non_pi():
    assert resolve_run_mode("auto", detector=lambda: False) == "mock"


def test_auto_mode_selects_pi_on_pi():
    assert resolve_run_mode("auto", detector=lambda: True) == "pi"


def test_mock_mode_forces_mock_even_if_pi(monkeypatch):
    monkeypatch.delenv("BEHAVBOX_FORCE_MOCK", raising=False)
    monkeypatch.delenv("BEHAVBOX_MOCK_UI_AUTOSTART", raising=False)

    configure_environment_for_mode("mock", detector=lambda: True)

    assert os.environ["BEHAVBOX_FORCE_MOCK"] == "1"
    assert os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] == "1"


def test_pi_mode_fails_cleanly_off_pi():
    with pytest.raises(RuntimeError, match="Raspberry Pi"):
        configure_environment_for_mode("pi", detector=lambda: False)


def test_output_root_and_session_tag_are_propagated_for_mock_mode(tmp_path: Path):
    task = (
        SimpleTask(name="config_task", box_profile="head_fixed")
        .state("done")
        .finish("completed")
        .build()
    )
    session_info = build_session_info_for_mode(
        task=task,
        output_root=tmp_path,
        session_tag="tutorial_tag",
        mode="mock",
    )

    assert session_info["dir_name"] == str((tmp_path / "tutorial_tag").resolve())
    assert session_info["mock_audio"] is True


def _base_session_info(base_dir: Path) -> dict:
    return {
        "external_storage": str(base_dir),
        "basename": "simple_task_session",
        "dir_name": str(base_dir / "run"),
        "mouse_name": "mouseA",
        "datetime": "2026-03-15_220000",
        "box_name": "simple_task_box",
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
        "mock_audio": True,
    }


def test_simple_cue_response_reward_task_completes_and_writes_outputs():
    with tempfile.TemporaryDirectory() as tmp:
        task = SimpleTask(name="simple_response", box_profile="head_fixed").param("reward_amount_ul", 25.0)
        task.cue("go", duration_s=0.01, side="both")
        task.state("cue").on_enter(play_cue("go")).after(0.01, goto="response")
        task.state("response").on_event(
            "center_entry",
            record_event("response_detected"),
            deliver_reward(output_name="reward_center", amount_ul="reward_amount_ul"),
            goto="rewarded",
        ).after(5.0, goto="timed_out")
        task.state("rewarded").finish("response_received")
        task.state("timed_out").finish("response_window_elapsed")
        compiled = task.build()

        box = BehavBox(_base_session_info(Path(tmp)))
        injected = {"done": False}

        def step_hook(runner):
            if runner.task_state["phase"] == "response" and not injected["done"]:
                runner.box.center_entry()
                injected["done"] = True

        runner = TaskRunner(box=box, task=compiled, task_config={}, step_hooks=[step_hook])
        final_state = runner.run(poll_interval_s=0.005)

        session_dir = Path(box.session_info["dir_name"])
        assert injected["done"] is True
        assert final_state["stop_reason"] == "response_received"
        assert final_state["params"]["reward_amount_ul"] == 25.0
        assert (session_dir / "final_task_state.json").exists()
        assert (session_dir / "task_events.jsonl").exists()


def test_timeout_only_task_completes_without_response():
    with tempfile.TemporaryDirectory() as tmp:
        task = SimpleTask(name="timeout_only").cue("go", duration_s=0.01)
        task.state("cue").on_enter(play_cue("go")).after(0.01, goto="response")
        task.state("response").after(0.02, goto="timed_out")
        task.state("timed_out").finish("timed_out")
        compiled = task.build()

        box = BehavBox(_base_session_info(Path(tmp)))
        runner = TaskRunner(box=box, task=compiled, task_config={})
        final_state = runner.run(poll_interval_s=0.005)

        assert final_state["response_detected"] is False
        assert final_state["stop_reason"] == "timed_out"


def test_adaptive_helpers_update_parameters_and_persist_final_values():
    with tempfile.TemporaryDirectory() as tmp:
        task = SimpleTask(name="adaptive_task")
        task.param("reward_left_ul", 20.0)
        task.param("reward_right_ul", 20.0)
        task.cue("go", duration_s=0.01)
        task.state("cue").on_enter(play_cue("go")).after(0.01, goto="response")
        task.state("response").on_event(
            "left_entry",
            decrement_param("reward_left_ul", 5.0, min_value=0.0),
            increment_param("reward_right_ul", 5.0, max_value=50.0),
            set_param("last_choice", "left"),
            record_event("adapted"),
            goto="done",
        ).after(5.0, goto="timed_out")
        task.state("done").finish("adapted")
        task.state("timed_out").finish("timed_out")
        compiled = task.build()

        box = BehavBox(_base_session_info(Path(tmp)))
        injected = {"done": False}

        def step_hook(runner):
            if runner.task_state["phase"] == "response" and not injected["done"]:
                runner.box.left_entry()
                injected["done"] = True

        runner = TaskRunner(box=box, task=compiled, task_config={}, step_hooks=[step_hook])
        final_state = runner.run(poll_interval_s=0.005)

        assert final_state["params"]["reward_left_ul"] == 15.0
        assert final_state["params"]["reward_right_ul"] == 25.0
        assert final_state["params"]["last_choice"] == "left"

