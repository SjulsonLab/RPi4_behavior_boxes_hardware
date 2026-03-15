import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.mock_hw.operator_controller import OperatorRunController
from box_runtime.mock_hw.registry import REGISTRY, set_audio_state, set_camera_state, set_session_state, set_task_state
from box_runtime.mock_hw.web import make_handler


def _json_request(url: str, method: str = "GET", payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, method=method, data=data, headers=headers)
    with urlopen(req, timeout=3) as resp:
        body = resp.read()
        if not body:
            return None
        content_type = resp.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(body.decode("utf-8"))
        return body.decode("utf-8")


@contextmanager
def _temporary_server(operator_controller):
    static_dir = Path(__file__).resolve().parents[1] / "box_runtime" / "mock_hw" / "static"
    handler = make_handler(
        registry=REGISTRY,
        static_dir=str(static_dir),
        operator_controller=operator_controller,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class _FakeBox:
    """Minimal box double exposing the session info used by the controller."""

    def __init__(self, session_info):
        self.session_info = dict(session_info)


class _BlockingRunner:
    """Runner double that stays alive until stop is requested."""

    instances = []

    def __init__(self, box, task, task_config=None):
        self.box = box
        self.task = task
        self.task_config = {} if task_config is None else dict(task_config)
        self.stop_calls = []
        self.stop_event = threading.Event()
        _BlockingRunner.instances.append(self)

    def run(self):
        self.stop_event.wait(timeout=2)
        return {"status": "completed", "stop_calls": list(self.stop_calls)}

    def stop(self, reason: str = "manual") -> None:
        self.stop_calls.append(str(reason))
        self.stop_event.set()


class _InstantRunner:
    """Runner double that completes immediately for controller tests."""

    instances = []

    def __init__(self, box, task, task_config=None):
        self.box = box
        self.task = task
        self.task_config = {} if task_config is None else dict(task_config)
        self.stop_calls = []
        _InstantRunner.instances.append(self)

    def run(self):
        return {"status": "completed"}

    def stop(self, reason: str = "manual") -> None:
        self.stop_calls.append(str(reason))


def _build_controller(tmp_path: Path, runner_class):
    return OperatorRunController(
        output_root=tmp_path / "operator_runs",
        box_factory=_FakeBox,
        runner_factory=runner_class,
    )


def _wait_for_status(controller: OperatorRunController, expected: str, timeout_s: float = 2.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = controller.state()
        if state["status"] == expected:
            return state
        time.sleep(0.01)
    raise AssertionError(f"controller did not reach status {expected!r}; last state={controller.state()!r}")


def test_operator_controller_is_idle_before_launch(tmp_path: Path) -> None:
    controller = _build_controller(tmp_path, _InstantRunner)

    state = controller.state()

    assert state["status"] == "idle"
    assert state["run_active"] is False
    assert state["session_tag"] is None
    assert state["output_root"] == str(tmp_path / "operator_runs")


def test_operator_controller_start_uses_dedicated_output_root_and_rejects_concurrent_start(tmp_path: Path) -> None:
    _BlockingRunner.instances.clear()
    controller = _build_controller(tmp_path, _BlockingRunner)

    started = controller.start_run(
        session_tag="bench_run_a",
        max_trials=7,
        max_duration_s=90.0,
    )

    assert started["status"] in {"starting", "running"}
    runner = _BlockingRunner.instances[-1]
    assert Path(runner.box.session_info["external_storage"]) == tmp_path / "operator_runs"
    assert Path(runner.box.session_info["dir_name"]).parent == tmp_path / "operator_runs"
    assert runner.task_config == {"max_trials": 7, "max_duration_s": 90.0}

    with pytest.raises(RuntimeError, match="already active"):
        controller.start_run(session_tag="bench_run_b", max_trials=5, max_duration_s=60.0)

    controller.stop_run()
    _wait_for_status(controller, "completed")


def test_operator_controller_stop_requests_graceful_runner_stop(tmp_path: Path) -> None:
    _BlockingRunner.instances.clear()
    controller = _build_controller(tmp_path, _BlockingRunner)
    controller.start_run(session_tag="bench_run_stop", max_trials=4, max_duration_s=30.0)
    runner = _BlockingRunner.instances[-1]

    stopping = controller.stop_run()

    assert stopping["status"] in {"stopping", "completed"}
    completed = _wait_for_status(controller, "completed")
    assert runner.stop_calls == ["operator_stop"]
    assert completed["stop_reason"] == "operator_stop"


def test_operator_controller_state_transitions_include_completed(tmp_path: Path) -> None:
    _InstantRunner.instances.clear()
    controller = _build_controller(tmp_path, _InstantRunner)

    controller.start_run(session_tag="bench_run_finish", max_trials=3, max_duration_s=20.0)
    completed = _wait_for_status(controller, "completed")

    assert completed["run_active"] is False
    assert completed["final_task_state"] == {"status": "completed"}
    assert completed["error_message"] is None


class _FakeOperatorController:
    """Static operator controller double used for route and page tests."""

    def __init__(self):
        self._state = {
            "status": "idle",
            "run_active": False,
            "session_tag": None,
            "protocol_name": "head_fixed_gonogo",
            "max_trials": None,
            "max_duration_s": None,
            "output_root": "/tmp/operator_runs",
            "active_run_dir": None,
            "started_at_s": None,
            "stopped_at_s": None,
            "stop_reason": None,
            "error_message": None,
            "final_task_state": None,
        }

    def state(self):
        return dict(self._state)

    def start_run(self, session_tag: str, max_trials: int, max_duration_s: float):
        self._state.update(
            {
                "status": "running",
                "run_active": True,
                "session_tag": session_tag,
                "max_trials": int(max_trials),
                "max_duration_s": float(max_duration_s),
            }
        )
        return self.state()

    def stop_run(self):
        self._state.update({"status": "completed", "run_active": False, "stop_reason": "operator_stop"})
        return self.state()


def test_operator_routes_and_page_contract(tmp_path: Path) -> None:
    REGISTRY.reset()
    set_session_state(active=True, lifecycle_state="running", protocol_name="head_fixed_gonogo", box_name="test_box")
    set_task_state(
        protocol_name="head_fixed_gonogo",
        phase="stimulus",
        trial_index=1,
        trial_type="go",
        completed_trials=1,
        max_trials=5,
        stimulus_active=True,
    )
    set_audio_state(active=True, current_cue_name="gonogo_go", last_cue_name="gonogo_go")
    set_camera_state(
        camera0={
            "camera_id": "camera0",
            "recording": True,
            "preview_active": False,
            "preview_available": False,
            "preview_url": None,
        },
        camera1={
            "camera_id": "camera1",
            "recording": False,
            "preview_active": False,
            "preview_available": False,
            "preview_url": None,
        },
    )
    controller = _FakeOperatorController()

    with _temporary_server(controller) as base_url:
        debug_html = _json_request(f"{base_url}/")
        operator_html = _json_request(f"{base_url}/operator")
        operator_state = _json_request(f"{base_url}/api/operator/state")
        started = _json_request(
            f"{base_url}/api/operator/start",
            method="POST",
            payload={"session_tag": "operator_ui_test", "max_trials": 6, "max_duration_s": 120.0},
        )
        stopped = _json_request(f"{base_url}/api/operator/stop", method="POST")

    assert "BehavBox Mock Hardware" in debug_html
    assert 'href="/operator"' in debug_html
    assert "BehavBox Operator Console" in operator_html
    assert 'href="/"' in operator_html
    assert "Start Session" in operator_html
    assert "Stop Session" in operator_html
    assert "camera-slot" in operator_html
    assert "button data-label=" not in operator_html
    assert operator_state["status"] == "idle"
    assert started["status"] == "running"
    assert started["session_tag"] == "operator_ui_test"
    assert stopped["status"] == "completed"


def test_operator_page_camera_slots_show_clean_no_preview_state() -> None:
    REGISTRY.reset()
    set_camera_state(
        camera0={
            "camera_id": "camera0",
            "recording": False,
            "preview_active": False,
            "preview_available": False,
            "preview_url": None,
        }
    )

    with _temporary_server(_FakeOperatorController()) as base_url:
        operator_html = _json_request(f"{base_url}/operator")
        operator_js = _json_request(f"{base_url}/operator.js")
        state = _json_request(f"{base_url}/api/state")

    assert "camera-slot" in operator_html
    assert "Preview hidden" in operator_js
    assert "camera0" in state["runtime"]["camera"]
    assert state["runtime"]["camera"]["camera0"]["preview_available"] is False
    assert state["runtime"]["camera"]["camera0"]["preview_url"] is None
