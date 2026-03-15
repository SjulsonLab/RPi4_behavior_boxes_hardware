import os
import tempfile
import time
from pathlib import Path

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox
from sample_tasks.common.runner import TaskRunner
from sample_tasks.head_fixed_gonogo.fake_mouse import build_fake_mouse_step_hook
from sample_tasks.head_fixed_gonogo.plot_state import build_plot_step_hook
from sample_tasks.head_fixed_gonogo import task as gonogo_task


def _session_info(base_dir: str) -> dict:
    return {
        "external_storage": base_dir,
        "basename": "fake_mouse_session",
        "dir_name": str(Path(base_dir) / "run"),
        "mouse_name": "mouseA",
        "datetime": "2026-03-15_220000",
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


def _runner_with_fake_mouse(box: BehavBox, seed: int) -> TaskRunner:
    return TaskRunner(
        box=box,
        task=gonogo_task,
        task_config={
            "trial_sequence": ["go", "nogo", "go", "nogo", "go", "nogo"],
            "go_cue_duration_s": 0.02,
            "nogo_cue_duration_s": 0.02,
            "iti_s": 0.0,
            "response_window_s": 0.08,
            "reward_phase_s": 0.01,
            "timeout_s": 0.02,
            "cleanup_s": 0.01,
            "max_trials": 6,
            "fake_mouse_enabled": True,
            "fake_mouse_seed": seed,
        },
        step_hooks=[
            build_fake_mouse_step_hook(seed=seed),
            build_plot_step_hook(history_limit=32),
        ],
    )


def test_fake_mouse_same_seed_produces_same_trial_outcomes():
    observed_sequences = []

    for _ in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp))
            runner = _runner_with_fake_mouse(box, seed=123)
            final_state = runner.run(poll_interval_s=0.005)
            observed_sequences.append(
                [
                    (entry["trial_type"], entry["outcome"])
                    for entry in final_state["trial_outcomes"]
                ]
            )

    assert observed_sequences[0] == observed_sequences[1]
    assert len(observed_sequences[0]) == 6


def test_plot_state_stays_empty_until_task_starts_then_updates():
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))
        runner = _runner_with_fake_mouse(box, seed=7)

        runner.prepare()
        assert box.runtime_status["plot"]["trial_outcomes"] == []

        runner.start()
        saw_nonempty_plot = False
        while runner.step():
            if box.runtime_status["plot"]["trial_outcomes"]:
                saw_nonempty_plot = True
                break
            time.sleep(0.005)

        runner.stop(reason="plot_observed")
        runner.finalize()

        assert saw_nonempty_plot is True
        assert len(box.runtime_status["plot"]["trial_outcomes"]) >= 1
