import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox, BehaviorEvent
from box_runtime.mock_hw.registry import REGISTRY


def _session_info(base_dir: str, **overrides):
    info = {
        "external_storage": base_dir,
        "basename": "test_session",
        "dir_name": os.path.join(base_dir, "run"),
        "mouse_name": "mouseA",
        "datetime": "2026-03-13_120000",
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
        "treadmill_speed_hz": 20.0,
        "treadmill_wheel_diameter_cm": 2.5,
        "treadmill_pulses_per_rotation": 200,
    }
    info.update(overrides)
    return info


class TestInputProfiles(unittest.TestCase):
    def setUp(self):
        REGISTRY.reset()
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)

    def test_head_fixed_profile_uses_rotary_encoder_and_trigger_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp, box_profile="head_fixed"))
            box.prepare_session()

            self.assertIsNotNone(box.input_service)
            self.assertEqual(box.trigger_in.pin, 23)
            self.assertEqual(box.ir_lick_left.pin, 5)
            self.assertEqual(box.ir_lick_right.pin, 6)
            self.assertEqual(box.ir_lick_center.pin, 12)
            self.assertEqual(box.lick_left.pin, 26)
            self.assertEqual(box.lick_right.pin, 27)
            self.assertEqual(box.lick_center.pin, 15)
            self.assertIsNotNone(box.treadmill_encoder)
            self.assertEqual(box.treadmill_encoder.a.pin, 13)
            self.assertEqual(box.treadmill_encoder.b.pin, 16)
            self.assertIsNone(getattr(box, "poke_left", None))
            self.assertIsNone(getattr(box, "poke_right", None))
            self.assertIsNone(getattr(box, "poke_center", None))

    def test_freely_moving_profile_uses_pokes_and_no_rotary_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp, box_profile="freely_moving"))
            box.prepare_session()

            self.assertIsNotNone(box.input_service)
            self.assertEqual(box.trigger_in.pin, 23)
            self.assertIsNone(getattr(box, "treadmill_encoder", None))
            self.assertEqual(box.poke_left.pin, 5)
            self.assertEqual(box.poke_right.pin, 6)
            self.assertEqual(box.poke_center.pin, 12)
            self.assertEqual(box.poke_extra1.pin, 13)
            self.assertEqual(box.poke_extra2.pin, 16)
            self.assertIsNone(getattr(box, "lick_left", None))
            self.assertIsNone(getattr(box, "lick_right", None))
            self.assertIsNone(getattr(box, "lick_center", None))

    def test_input_profile_fallback_still_works_when_box_profile_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp, box_profile=None, input_profile="head_fixed"))
            box.prepare_session()

            self.assertEqual(box.trigger_in.pin, 23)
            self.assertIsNotNone(box.treadmill_encoder)


class TestInputRecordingLifecycle(unittest.TestCase):
    def setUp(self):
        REGISTRY.reset()
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)

    def test_manual_recording_creates_timestamped_directory_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp))
            box.prepare_session()

            recording_dir = Path(box.start_recording())

            self.assertTrue(recording_dir.exists())
            self.assertTrue(recording_dir.name.endswith("_input_recording"))
            self.assertTrue((recording_dir / "input_events.log").exists())
            self.assertTrue((recording_dir / "events.jsonl").exists())
            self.assertTrue((recording_dir / "treadmill_speed.tsv").exists())

            box.stop_recording()

    def test_task_recording_uses_session_directory_when_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = _session_info(tmp)
            box = BehavBox(info)
            box.prepare_session()

            recording_dir = Path(box.start_task_recording())

            self.assertEqual(recording_dir.resolve(), Path(info["dir_name"]).resolve())
            self.assertTrue((recording_dir / "input_events.log").exists())
            self.assertTrue((recording_dir / "events.jsonl").exists())

            box.stop_task_recording()

    def test_task_reuses_manual_recording_and_user_stop_defers_until_task_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp))
            box.prepare_session()

            manual_dir = Path(box.start_recording())
            task_dir = Path(box.start_task_recording())
            self.assertEqual(task_dir, manual_dir)

            stop_state = box.stop_recording()
            self.assertEqual(stop_state["status"], "deferred")
            self.assertTrue(box.input_service.is_recording)

            final_state = box.stop_task_recording()
            self.assertEqual(final_state["status"], "stopped")
            self.assertFalse(box.input_service.is_recording)


class TestInputArtifacts(unittest.TestCase):
    def setUp(self):
        REGISTRY.reset()
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)

    def test_trigger_in_edges_are_written_to_log_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp))
            box.prepare_session()
            recording_dir = Path(box.start_recording())

            box.trigger_in.press(source="test")
            box.trigger_in.release(source="test")
            time.sleep(0.02)
            box.stop_recording()

            jsonl_path = recording_dir / "events.jsonl"
            rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
            names = [row["name"] for row in rows]
            self.assertIn("trigger_in_rising", names)
            self.assertIn("trigger_in_falling", names)

            log_text = (recording_dir / "input_events.log").read_text(encoding="utf-8")
            self.assertIn("trigger_in_rising", log_text)
            self.assertIn("trigger_in_falling", log_text)

    def test_treadmill_speed_tsv_contains_signed_cm_per_s_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp, treadmill_speed_hz=25.0))
            box.prepare_session()
            recording_dir = Path(box.start_recording())

            box.treadmill_encoder.rotate(20)
            time.sleep(0.08)
            box.treadmill_encoder.rotate(-10)
            time.sleep(0.08)
            box.stop_recording()

            rows = (recording_dir / "treadmill_speed.tsv").read_text(encoding="utf-8").splitlines()
            self.assertGreaterEqual(len(rows), 2)
            self.assertEqual(rows[0], "utc_posix_s\tspeed_cm_per_s")
            numeric_speeds = [float(line.split("\t")[1]) for line in rows[1:]]
            self.assertTrue(any(speed > 0 for speed in numeric_speeds))
            self.assertTrue(any(speed < 0 for speed in numeric_speeds))

    def test_minimal_behavior_events_still_flow_into_queue_and_interact_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp))
            box.prepare_session()
            box.event_list.clear()

            box.left_entry()

            self.assertEqual(len(box.event_list), 1)
            event = box.event_list.popleft()
            self.assertIsInstance(event, BehaviorEvent)
            self.assertEqual(event.name, "left_entry")
            self.assertEqual(box.interact_list[-1][1], "left_entry")
