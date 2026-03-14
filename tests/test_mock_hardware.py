import importlib
import json
import os
import tempfile
import time
import unittest
from urllib.request import Request, urlopen

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox, BehaviorEvent
from box_runtime.behavior.gpio_backend import (
    Button as BackendButton,
    DigitalOutputDevice as BackendDigitalOutputDevice,
    LED as BackendLED,
    PWMLED as BackendPWMLED,
    ReservedPinError,
)
from box_runtime.io_manifest import load_box_profile
from box_runtime.mock_hw.devices import Button, LED
from box_runtime.mock_hw.registry import (
    REGISTRY,
    register_pin_label,
    set_audio_state,
    set_session_state,
    set_task_state,
)
from box_runtime.mock_hw.server import ensure_server_running
from box_runtime.mock_hw.visual_stim import MockVisualStim


def _session_info(base_dir: str, **overrides):
    info = {
        "external_storage": base_dir,
        "basename": "test_session",
        "dir_name": os.path.join(base_dir, "run"),
        "mouse_name": "mouseA",
        "datetime": "2026-02-18_120000",
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
    info.update(overrides)
    return info


def _json_request(url: str, method: str = "GET", payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, method=method, data=data, headers=headers)
    with urlopen(req, timeout=3) as resp:
        return json.loads(resp.read().decode("utf-8"))


class TestManifestAndMapping(unittest.TestCase):
    def setUp(self):
        REGISTRY.reset()
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)

    def test_head_fixed_manifest_uses_v4_mapping(self):
        manifest = load_box_profile("head_fixed")

        self.assertEqual(manifest.source_csv.name, "unified_GPIO_pin_arrangement_v4.csv")
        self.assertEqual(manifest.inputs["trigger_in"].pin, 23)
        self.assertEqual(manifest.outputs["trigger_out"].pin, 24)
        self.assertEqual(manifest.outputs["cue_led_5"].pin, 10)
        self.assertEqual(manifest.outputs["cue_led_6"].pin, 11)
        self.assertEqual(manifest.user_configurable["user_configurable"].pin, 4)
        self.assertIn(9, manifest.reserved)
        self.assertEqual(manifest.reserved[9].board_alias, "DIO3")

    def test_behavbox_registers_v4_head_fixed_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp))
            box.prepare_session()

            self.assertEqual(box.trigger_in.pin, 23)
            self.assertEqual(box.treadmill_encoder.a.pin, 13)
            self.assertEqual(box.treadmill_encoder.b.pin, 16)

            state = REGISTRY.get_state()
            registered_pins = {pin["pin"] for pin in state["pins"]}
            self.assertNotIn(9, registered_pins)
            self.assertIn(10, registered_pins)
            self.assertIn(11, registered_pins)

            reward_left = next(pin for pin in state["pins"] if pin.get("label") == "reward_left")
            self.assertIn("pump1", reward_left.get("aliases", []))
            trigger_out = next(pin for pin in state["pins"] if pin.get("label") == "trigger_out")
            self.assertIn("DIO2", trigger_out.get("aliases", []))

    def test_reserved_gpio9_raises_in_backend(self):
        with self.assertRaises(ReservedPinError):
            BackendLED(9)
        with self.assertRaises(ReservedPinError):
            BackendPWMLED(9)
        with self.assertRaises(ReservedPinError):
            BackendDigitalOutputDevice(9)
        with self.assertRaises(ReservedPinError):
            BackendButton(9)


class TestMockDevices(unittest.TestCase):
    def setUp(self):
        REGISTRY.reset()

    def test_button_callbacks_once_per_edge(self):
        btn = Button(900)
        counts = {"pressed": 0, "released": 0}

        btn.when_pressed = lambda: counts.__setitem__("pressed", counts["pressed"] + 1)
        btn.when_released = lambda: counts.__setitem__("released", counts["released"] + 1)

        btn.press(source="test")
        btn.release(source="test")

        self.assertEqual(counts["pressed"], 1)
        self.assertEqual(counts["released"], 1)

    def test_led_blink_records_transitions(self):
        led = LED(901)
        register_pin_label(901, "test_led", direction="output")
        led.blink(on_time=0.01, off_time=0.01, n=2, background=False)

        events = REGISTRY.get_events(limit=50)["events"]
        pin_events = [e for e in events if e.get("kind") == "pin" and e.get("pin") == 901]
        self.assertGreaterEqual(len(pin_events), 4)

    def test_visual_stim_proxy_updates_state(self):
        vis = MockVisualStim({"mock_visual_stim_duration_s": 0.05})
        vis.show_grating("g_test")

        state_now = REGISTRY.get_state()["visual"]
        self.assertTrue(state_now["visual_stim_active"])
        self.assertEqual(state_now["current_grating"], "g_test")

        time.sleep(0.1)
        state_after = REGISTRY.get_state()["visual"]
        self.assertFalse(state_after["visual_stim_active"])


class TestIntegration(unittest.TestCase):
    def setUp(self):
        REGISTRY.reset()
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)

    def test_behavbox_import_and_instantiation_non_pi(self):
        module = importlib.import_module("box_runtime.behavior.behavbox")
        with tempfile.TemporaryDirectory() as tmp:
            info = _session_info(tmp)
            box = module.BehavBox(info)
            self.assertIsInstance(box, module.BehavBox)

    def test_event_queue_entries_include_detection_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = _session_info(tmp)
            box = BehavBox(info)
            box.prepare_session()
            box.event_list.clear()

            before = time.time()
            box.left_entry()
            after = time.time()

            self.assertEqual(len(box.event_list), 1)
            queued_event = box.event_list.popleft()
            self.assertIsInstance(queued_event, BehaviorEvent)
            self.assertEqual(queued_event.name, "left_entry")
            self.assertGreaterEqual(queued_event.timestamp, before)
            self.assertLessEqual(queued_event.timestamp, after)

            interact_timestamp, interact_name = box.interact_list[-1]
            self.assertEqual(interact_name, "left_entry")
            self.assertEqual(interact_timestamp, queued_event.timestamp)

    def test_web_api_press_release_and_pulse(self):
        btn = Button(902)
        register_pin_label(902, "test_input", direction="input")
        url = ensure_server_running(host="127.0.0.1", port=0)

        _json_request(f"{url}/api/input/test_input/press", method="POST")
        self.assertTrue(btn.is_active)

        _json_request(f"{url}/api/input/test_input/release", method="POST")
        self.assertFalse(btn.is_active)

        _json_request(
            f"{url}/api/input/test_input/pulse",
            method="POST",
            payload={"duration_ms": 30},
        )
        time.sleep(0.06)
        self.assertFalse(btn.is_active)

        state = _json_request(f"{url}/api/state")
        self.assertIn("pins", state)

    def test_web_api_output_endpoints_support_canonical_and_alias_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp))
            box.prepare_session()
            url = ensure_server_running(host="127.0.0.1", port=0)

            _json_request(f"{url}/api/output/reward_left/on", method="POST")
            state = _json_request(f"{url}/api/state")
            reward_left = next(pin for pin in state["pins"] if pin.get("label") == "reward_left")
            self.assertEqual(reward_left["value"], 1)
            self.assertIn("pump1", reward_left.get("aliases", []))

            _json_request(f"{url}/api/output/reward_left/off", method="POST")
            _json_request(
                f"{url}/api/output/pump1/pulse",
                method="POST",
                payload={"duration_ms": 30},
            )
            time.sleep(0.06)
            state = _json_request(f"{url}/api/state")
            reward_left = next(pin for pin in state["pins"] if pin.get("label") == "reward_left")
            self.assertEqual(reward_left["value"], 0)

    def test_web_api_state_includes_runtime_sections(self):
        url = ensure_server_running(host="127.0.0.1", port=0)
        set_session_state(lifecycle_state="running", active=True, protocol_name="head_fixed_gonogo")
        set_task_state(phase="stimulus", protocol_name="head_fixed_gonogo", trial_index=2, trial_type="go")
        set_audio_state(active=True, current_cue_name="gonogo_go", last_cue_name="gonogo_go")

        state = _json_request(f"{url}/api/state")

        self.assertIn("runtime", state)
        self.assertEqual(state["runtime"]["session"]["protocol_name"], "head_fixed_gonogo")
        self.assertEqual(state["runtime"]["task"]["phase"], "stimulus")
        self.assertEqual(state["runtime"]["audio"]["current_cue_name"], "gonogo_go")

    def test_deliver_reward_records_output_activity_without_pump_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = BehavBox(_session_info(tmp))
            box.prepare_session()
            box.deliver_reward("reward_left", 100)
            time.sleep(0.08)
            events = REGISTRY.get_events(limit=200)["events"]
            reward_events = [
                e for e in events
                if e.get("kind") == "pin" and e.get("label") == "reward_left"
            ]
            self.assertGreaterEqual(len(reward_events), 1)


if __name__ == "__main__":
    unittest.main()
