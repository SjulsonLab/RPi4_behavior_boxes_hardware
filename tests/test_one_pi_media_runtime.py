import os
import tempfile
from pathlib import Path

import pytest

os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.mock_hw.registry import REGISTRY
from box_runtime.video_recording.local_camera_runtime import (
    CameraHardwareUnavailable,
    CameraManager,
    LocalCameraRuntime,
)


def _session_info(base_dir: str, **overrides) -> dict[str, object]:
    """Build one isolated session configuration for one-Pi media tests.

    Args:
        base_dir: Temporary directory root string.
        **overrides: Session fields overriding the defaults.

    Returns:
        dict[str, object]: Session configuration mapping.
    """

    info: dict[str, object] = {
        "external_storage": base_dir,
        "basename": "test_session",
        "dir_name": str(Path(base_dir) / "run"),
        "mouse_name": "mouseA",
        "datetime": "2026-03-14_120000",
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
        "camera_enabled": False,
        "camera_ids": ["camera0"],
        "camera_preview_modes": {"camera0": "off"},
        "camera_preview_connector": "HDMI-A-1",
        "camera_preview_max_hz": 15.0,
        "visual_display_backend": "fake",
        "visual_display_connector": "HDMI-A-2",
    }
    info.update(overrides)
    return info


class _FakeRecorder:
    """Simple recorder double used to verify local camera orchestration."""

    instances: list["_FakeRecorder"] = []

    def __init__(self, storage_root: Path, *, camera_num: int = 0, camera_id: str = "camera0") -> None:
        self.storage_root = Path(storage_root)
        self.camera_num = int(camera_num)
        self.camera_id = str(camera_id)
        self.calls: list[tuple[str, object]] = []
        self._preview_frame = b"jpeg-frame"
        _FakeRecorder.instances.append(self)

    def start(self, session_id: str, owner: str, payload: dict[str, object]) -> None:
        self.calls.append(("start", {"session_id": session_id, "owner": owner, "payload": dict(payload)}))

    def stop(self) -> None:
        self.calls.append(("stop", None))

    def preview_frame(self) -> bytes | None:
        return self._preview_frame

    def close(self) -> None:
        self.calls.append(("close", None))


class _FakePreviewSink:
    """Minimal preview sink double with explicit lifecycle hooks."""

    instances: list["_FakePreviewSink"] = []

    def __init__(self, camera_id: str, connector: str, frame_provider, max_preview_hz: float) -> None:
        self.camera_id = camera_id
        self.connector = connector
        self.frame_provider = frame_provider
        self.max_preview_hz = float(max_preview_hz)
        self.started = False
        self.closed = False
        _FakePreviewSink.instances.append(self)

    def start(self) -> "_FakePreviewSink":
        self.started = True
        return self

    def close(self) -> None:
        self.closed = True


def _fake_recorder_factory(storage_root: Path, *, camera_num: int, camera_id: str) -> _FakeRecorder:
    return _FakeRecorder(storage_root, camera_num=camera_num, camera_id=camera_id)


def _fake_preview_sink_factory(
    camera_id: str,
    connector: str,
    frame_provider,
    max_preview_hz: float,
) -> _FakePreviewSink:
    return _FakePreviewSink(
        camera_id=camera_id,
        connector=connector,
        frame_provider=frame_provider,
        max_preview_hz=max_preview_hz,
    )


def test_local_camera_runtime_starts_and_stops_recording_without_http_service() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _FakeRecorder.instances.clear()
        _FakePreviewSink.instances.clear()
        runtime = LocalCameraRuntime(
            camera_id="camera0",
            session_info=_session_info(tmp, camera_enabled=True),
            recorder_factory=_fake_recorder_factory,
            preview_sink_factory=_fake_preview_sink_factory,
        )

        runtime.prepare()
        runtime.start_recording(owner="automated")
        runtime.stop_recording()
        runtime.close()

        recorder = _FakeRecorder.instances[-1]
        assert recorder.camera_num == 0
        assert recorder.camera_id == "camera0"
        assert recorder.storage_root == Path(tmp) / "run" / "camera_recordings"
        assert recorder.calls[0][0] == "start"
        assert recorder.calls[0][1]["session_id"] == "camera0"
        assert recorder.calls[1][0] == "stop"


def test_local_camera_runtime_preview_off_does_not_create_preview_sink() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _FakePreviewSink.instances.clear()
        preview_count_before = len(_FakePreviewSink.instances)
        runtime = LocalCameraRuntime(
            camera_id="camera0",
            session_info=_session_info(
                tmp,
                camera_enabled=True,
                camera_preview_modes={"camera0": "off"},
            ),
            recorder_factory=_fake_recorder_factory,
            preview_sink_factory=_fake_preview_sink_factory,
        )

        runtime.prepare()
        runtime.start_preview()
        runtime.close()

        assert len(_FakePreviewSink.instances) == preview_count_before


def test_camera_manager_accepts_two_camera_ids_and_tracks_per_camera_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _FakeRecorder.instances.clear()
        _FakePreviewSink.instances.clear()
        manager = CameraManager(
            _session_info(
                tmp,
                camera_enabled=True,
                camera_ids=["camera0", "camera1"],
                camera_preview_modes={"camera0": "drm_local", "camera1": "off"},
            ),
            recorder_factory=_fake_recorder_factory,
            preview_sink_factory=_fake_preview_sink_factory,
        )

        manager.prepare()
        manager.start_session(owner="automated")
        state = manager.runtime_state()
        manager.stop_session()
        manager.close()

        assert sorted(state.keys()) == ["camera0", "camera1"]
        assert state["camera0"]["recording"] is True
        assert state["camera0"]["preview_mode"] == "drm_local"
        assert state["camera1"]["recording"] is True
        assert state["camera1"]["preview_mode"] == "off"
        assert [instance.camera_id for instance in _FakePreviewSink.instances] == ["camera0"]


def test_camera_manager_missing_hardware_fails_cleanly() -> None:
    def missing_camera_factory(storage_root: Path, *, camera_num: int, camera_id: str):
        if camera_id == "camera1":
            raise CameraHardwareUnavailable("camera1 is not available on this host")
        return _FakeRecorder(storage_root, camera_num=camera_num, camera_id=camera_id)

    with tempfile.TemporaryDirectory() as tmp:
        manager = CameraManager(
            _session_info(
                tmp,
                camera_enabled=True,
                camera_ids=["camera0", "camera1"],
            ),
            recorder_factory=missing_camera_factory,
            preview_sink_factory=_fake_preview_sink_factory,
        )

        with pytest.raises(CameraHardwareUnavailable, match="camera1"):
            manager.prepare()


def test_behavbox_validate_media_config_rejects_visual_preview_connector_conflict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(
            _session_info(
                tmp,
                visual_stimulus=True,
                camera_enabled=True,
                camera_preview_modes={"camera0": "drm_local"},
                camera_preview_connector="HDMI-A-2",
            )
        )

        with pytest.raises(ValueError, match="HDMI-A-2"):
            box.validate_media_config()


def test_behavbox_prepare_session_rejects_visual_connector_other_than_hdmi_a_2() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        box = BehavBox(
            _session_info(
                tmp,
                visual_stimulus=True,
                visual_display_connector="HDMI-A-1",
            )
        )

        with pytest.raises(ValueError, match="HDMI-A-2"):
            box.prepare_session()


def test_behavbox_start_session_uses_local_camera_manager_and_publishes_runtime_state() -> None:
    calls: list[str] = []

    class _FakeCameraManager:
        def __init__(self, session_info, *, state_callback=None, recorder_factory=None, preview_sink_factory=None):
            self.state_callback = state_callback
            calls.append("init")

        def prepare(self) -> None:
            calls.append("prepare")
            if self.state_callback is not None:
                self.state_callback({"camera0": {"recording": False, "preview_active": False}})

        def start_session(self, owner: str = "automated") -> None:
            calls.append(f"start:{owner}")
            if self.state_callback is not None:
                self.state_callback({"camera0": {"recording": True, "preview_active": True}})

        def stop_session(self) -> None:
            calls.append("stop")
            if self.state_callback is not None:
                self.state_callback({"camera0": {"recording": False, "preview_active": False}})

        def close(self) -> None:
            calls.append("close")

    with tempfile.TemporaryDirectory() as tmp:
        REGISTRY.reset()
        box = BehavBox(
            _session_info(
                tmp,
                camera_enabled=True,
                camera_preview_modes={"camera0": "drm_local"},
            ),
            camera_manager_factory=lambda box_obj: _FakeCameraManager(box_obj.session_info, state_callback=box_obj._handle_camera_runtime_state),
        )
        box.prepare_session()
        box.start_session()
        state_during_run = REGISTRY.get_state()["runtime"]["camera"]
        box.stop_session()
        state_after_stop = REGISTRY.get_state()["runtime"]["camera"]
        box.close()

        assert calls[:3] == ["init", "prepare", "start:automated"]
        assert state_during_run["camera0"]["recording"] is True
        assert state_during_run["camera0"]["preview_active"] is True
        assert state_after_stop["camera0"]["recording"] is False
        assert "stop" in calls
