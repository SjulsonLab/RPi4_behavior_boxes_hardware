import os
import tempfile
import time
from pathlib import Path

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.behavior.gpio_backend import is_raspberry_pi
from sample_tasks.common.mock_inputs import MockInputInjector
from sample_tasks.head_fixed_gonogo.session_config import build_session_info
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
        "box_profile": "head_fixed",
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
                    "reward_phase_s": 0.01,
                    "cleanup_s": 0.01,
                    "reward_output": "reward_center",
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
        reward_events = [
            event for event in box.output_service.history
            if event["name"] == "reward_center_pulse"
        ]
        assert reward_events

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
                    "cleanup_s": 0.01,
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
                    "reward_phase_s": 0.01,
                    "cleanup_s": 0.01,
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


def test_gonogo_publishes_runtime_state_for_phase_trial_and_audio():
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(_session_info(tmp))
        box.prepare_session()
        box.start_session()
        task_state = gonogo_task.prepare_task(
            box,
            {
                "trial_sequence": ["go"],
                "go_cue_duration_s": 0.2,
                "nogo_cue_duration_s": 0.2,
                "iti_s": 0.0,
                "response_window_s": 0.25,
                "max_trials": 1,
            },
        )
        gonogo_task.start_task(box, task_state)
        gonogo_task.update_task(box, task_state, now_s=time.time())

        runtime_state = box.runtime_status
        assert runtime_state["task"]["protocol_name"] == "head_fixed_gonogo"
        assert runtime_state["task"]["phase"] == "stimulus"
        assert runtime_state["task"]["trial_index"] == 0
        assert runtime_state["task"]["trial_type"] == "go"
        assert runtime_state["audio"]["current_cue_name"] == "gonogo_go"

        box.stop_session()
        box.finalize_session()
        box.close()


class _FakeGonogoBox:
    """Minimal task-facing box double used for deterministic grating checks."""

    def __init__(self) -> None:
        self.session_info = {"visual_stimulus": True}
        self.runtime_updates: list[tuple[str, dict]] = []
        self.played_cues: list[str] = []
        self.shown_gratings: list[str] = []
        self.registered_noises: list[str] = []

    def publish_runtime_state(self, section: str, **values) -> None:
        self.runtime_updates.append((section, dict(values)))

    def register_noise_cue(self, name: str, duration_s: float, seed: int = 0) -> None:
        del duration_s, seed
        self.registered_noises.append(str(name))

    def play_sound(self, cue_name: str, side: str = "both", gain_db: float = 0.0) -> None:
        del side, gain_db
        self.played_cues.append(str(cue_name))

    def show_grating(self, grating_name: str) -> None:
        self.shown_gratings.append(str(grating_name))


def test_begin_trial_maps_go_and_nogo_to_expected_gratings() -> None:
    box = _FakeGonogoBox()
    task_state = gonogo_task.prepare_task(
        box,
        {
            "trial_sequence": ["go", "nogo"],
            "go_cue_duration_s": 0.02,
            "nogo_cue_duration_s": 0.02,
            "iti_s": 0.0,
            "response_window_s": 0.2,
            "max_trials": 2,
        },
    )

    gonogo_task._begin_trial(box, task_state, now_s=time.time())
    gonogo_task._begin_trial(box, task_state, now_s=time.time())

    assert box.shown_gratings == ["go_grating", "nogo_grating"]
    assert box.played_cues == ["gonogo_go", "gonogo_nogo"]


def test_head_fixed_session_config_enables_visual_stimulus_with_grating_files(tmp_path: Path) -> None:
    session_info = build_session_info(tmp_path, "sessionA")

    assert session_info["visual_stimulus"] is True
    expected_backend = "drm" if is_raspberry_pi() else "fake"
    assert session_info["visual_display_backend"] == expected_backend
    assert session_info["visual_display_connector"] == "HDMI-A-2"
    vis_gratings = [Path(path) for path in session_info["vis_gratings"]]
    assert len(vis_gratings) == 2
    assert vis_gratings[0].name == "go_grating.yaml"
    assert vis_gratings[1].name == "nogo_grating.yaml"
    assert session_info["camera_enabled"] is True
    assert session_info["camera_ids"] == ["camera0"]
    assert session_info["camera_recording_enabled"] is False
    assert session_info["camera_preview_modes"] == {"camera0": "qt_local"}
    assert session_info["camera_preview_connector"] == "HDMI-A-1"
