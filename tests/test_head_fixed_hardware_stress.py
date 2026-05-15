import os
import tempfile
import time
from pathlib import Path

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from sample_tasks.head_fixed_hardware_stress.session_config import (
    build_session_info,
    resolve_audio_session_settings,
)
from sample_tasks.head_fixed_hardware_stress import task as stress_task


class _FakeStressBox:
    """Minimal task-facing box double for hardware-stress task tests.

    Data contracts:
    - ``session_info``: ``dict[str, object]`` describing enabled hardware.
    - ``runtime_updates``: ``list[tuple[str, dict]]`` of published runtime state.
    - ``played_cues``: ``list[str]`` of cue names passed to ``play_sound``.
    - ``shown_gratings``: ``list[str]`` of grating names passed to
      ``show_grating``.
    - ``reward_calls``: ``list[tuple[str, float]]`` of reward outputs and
      reward sizes passed to ``deliver_reward``.
    """

    def __init__(self, *, visual_stimulus: bool = True) -> None:
        self.session_info = {"visual_stimulus": bool(visual_stimulus)}
        self.runtime_updates: list[tuple[str, dict]] = []
        self.played_cues: list[dict[str, object]] = []
        self.shown_gratings: list[str] = []
        self.displayed_gray_levels: list[int] = []
        self.reward_calls: list[tuple[str, float]] = []
        self.registered_noises: list[str] = []
        self.loaded_sounds: list[str] = []

    def publish_runtime_state(self, section: str, **values) -> None:
        self.runtime_updates.append((str(section), dict(values)))

    def register_noise_cue(self, name: str, duration_s: float, seed: int = 0) -> None:
        del duration_s, seed
        self.registered_noises.append(str(name))

    def load_sound(self, cue_name: str) -> None:
        self.loaded_sounds.append(str(cue_name))

    def play_sound(
        self,
        cue_name: str,
        side: str = "both",
        gain_db: float = 0.0,
        duration_s: float | None = None,
    ) -> None:
        self.played_cues.append(
            {
                "cue_name": str(cue_name),
                "side": str(side),
                "gain_db": float(gain_db),
                "duration_s": None if duration_s is None else float(duration_s),
            }
        )

    def show_grating(self, grating_name: str) -> None:
        self.shown_gratings.append(str(grating_name))

    def display_gray(self, gray_level: int) -> None:
        self.displayed_gray_levels.append(int(gray_level))

    def deliver_reward(self, output_name: str = "reward_center", reward_size_ul: float | None = None) -> None:
        self.reward_calls.append((str(output_name), float(reward_size_ul if reward_size_ul is not None else 0.0)))


def _run_until_trials(box: _FakeStressBox, task_state: dict, completed_trials: int) -> None:
    """Advance the task FSM until the requested number of trials finish.

    Args:
    - ``box``: Fake box consumed by the task callbacks.
    - ``task_state``: Mutable task-state dictionary returned by ``prepare_task``.
    - ``completed_trials``: Integer count of trials to finish.
    """

    now_s = time.time()
    guard = 0
    while int(task_state["counters"]["completed_trials"]) < int(completed_trials):
        stress_task.update_task(box, task_state, now_s=now_s)
        now_s += 0.01
        guard += 1
        if guard > 200:
            raise AssertionError("Stress task did not reach the expected trial count.")


def test_nogo_trials_trigger_nogo_cue_and_visual_without_input() -> None:
    box = _FakeStressBox(visual_stimulus=True)
    task_state = stress_task.prepare_task(
        box,
        {
            "iti_s": 0.0,
            "nogo_cue_duration_s": 0.0,
            "response_window_s": 0.0,
            "cleanup_s": 0.0,
            "reward_every_n_trials": 5,
            "reward_gap_s": 0.0,
            "max_trials": 2,
        },
    )

    stress_task.start_task(box, task_state)
    _run_until_trials(box, task_state, completed_trials=2)

    assert box.loaded_sounds == ["004-white-noise-44-1-16bit.wav"]
    assert box.registered_noises == []
    assert box.played_cues == [
        {
            "cue_name": "004-white-noise-44-1-16bit.wav",
            "side": "both",
            "gain_db": 0.0,
            "duration_s": 0.0,
        },
        {
            "cue_name": "004-white-noise-44-1-16bit.wav",
            "side": "both",
            "gain_db": 0.0,
            "duration_s": 0.0,
        },
    ]
    assert box.shown_gratings == ["nogo_grating", "nogo_grating"]
    assert box.displayed_gray_levels == [127, 127]
    assert [entry["trial_type"] for entry in task_state["trial_outcomes"]] == ["nogo", "nogo"]
    assert [entry["outcome"] for entry in task_state["trial_outcomes"]] == ["correct_reject", "correct_reject"]
    assert task_state["counters"]["correct_rejects"] == 2
    assert task_state["rewarded_trial_numbers"] == []


def test_stress_task_defaults_use_half_second_stimulus_and_one_second_iti() -> None:
    config = stress_task._merged_config({})

    assert config["nogo_cue_duration_s"] == 0.5
    assert config["iti_s"] == 1.0


def test_every_fifth_trial_delivers_all_solenoids_serially() -> None:
    box = _FakeStressBox(visual_stimulus=True)
    task_state = stress_task.prepare_task(
        box,
        {
            "iti_s": 0.0,
            "nogo_cue_duration_s": 0.0,
            "response_window_s": 0.0,
            "cleanup_s": 0.0,
            "reward_every_n_trials": 5,
            "reward_gap_s": 0.0,
            "reward_outputs": ["reward_left", "reward_right", "reward_center", "reward_4"],
            "reward_size_ul": 50.0,
            "max_trials": 5,
        },
    )

    stress_task.start_task(box, task_state)
    _run_until_trials(box, task_state, completed_trials=5)

    assert box.reward_calls == [
        ("reward_left", 50.0),
        ("reward_right", 50.0),
        ("reward_center", 50.0),
        ("reward_4", 50.0),
    ]
    assert task_state["rewarded_trial_numbers"] == [5]
    assert task_state["counters"]["completed_trials"] == 5


def test_session_config_enables_dual_camera_treadmill_and_recording() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session_info = build_session_info(Path(tmp), "stress_session")

    assert session_info["visual_stimulus"] is True
    assert session_info["camera_enabled"] is True
    assert session_info["camera_ids"] == ["camera0", "camera1"]
    assert session_info["camera_recording_enabled"] is True
    assert session_info["camera_preview_modes"] == {"camera0": "qt_local", "camera1": "off"}
    assert session_info["camera_preview_connector"] == "HDMI-A-1"
    assert session_info["visual_display_connector"] == "HDMI-A-2"
    assert session_info["treadmill"] is True


def test_session_config_uses_real_usb_audio_on_raspberry_pi() -> None:
    session_audio = resolve_audio_session_settings(
        is_raspberry_pi_fn=lambda: True,
        env={},
    )

    assert session_audio == {
        "mock_audio": False,
        "audio_device": "plughw:CARD=Device,DEV=0",
    }


def test_session_config_allows_usb_audio_device_override_from_environment() -> None:
    session_audio = resolve_audio_session_settings(
        is_raspberry_pi_fn=lambda: True,
        env={"BEHAVBOX_AUDIO_DEVICE": "default:CARD=Device"},
    )

    assert session_audio == {
        "mock_audio": False,
        "audio_device": "default:CARD=Device",
    }


def test_session_config_keeps_mock_audio_off_pi() -> None:
    session_audio = resolve_audio_session_settings(
        is_raspberry_pi_fn=lambda: False,
        env={},
    )

    assert session_audio == {
        "mock_audio": True,
        "audio_device": None,
    }


def test_prepare_task_raises_clear_error_when_configured_wav_cue_is_missing() -> None:
    class _MissingCueBox(_FakeStressBox):
        def load_sound(self, cue_name: str) -> None:
            raise FileNotFoundError(f"Canonical cue not found: {cue_name}")

    box = _MissingCueBox()

    try:
        stress_task.prepare_task(
            box,
            {
                "nogo_cue_name": "missing-beep.wav",
            },
        )
    except FileNotFoundError as exc:
        assert "missing-beep.wav" in str(exc)
    else:
        raise AssertionError("Expected prepare_task to fail for a missing WAV cue.")
