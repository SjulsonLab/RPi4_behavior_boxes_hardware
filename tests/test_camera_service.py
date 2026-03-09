import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.video_recording.VideoCapture import VideoCapture
from box_runtime.video_recording.camera_client import CameraClient
from box_runtime.video_recording.camera_session import (
    CameraSessionError,
    derive_frame_utc_ns,
    estimate_required_bytes,
    finalize_session_directory,
    load_manifest,
    verify_manifest_hashes,
    write_manifest,
)
from box_runtime.video_recording.http_camera_service import create_app
import box_runtime.video_recording.http_camera_service as http_camera_service


def _session_info(base_dir: str):
    return {
        "external_storage": base_dir,
        "basename": "test_session",
        "dir_name": str(Path(base_dir) / "run"),
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
    }


def _write_raw_attempt(
    session_dir: Path,
    attempt_index: int,
    sensor_timestamps_ns: np.ndarray,
    arrival_utc_ns: np.ndarray,
) -> None:
    raw_video = session_dir / f"attempt_{attempt_index:03d}.h264"
    raw_video.write_bytes(b"\x00\x00\x00\x01\x09\xf0")
    raw_tsv = session_dir / f"attempt_{attempt_index:03d}_raw_frames.tsv"
    with raw_tsv.open("w", encoding="utf-8") as handle:
        handle.write("frame_index\tsensor_timestamp_ns\tarrival_utc_ns\n")
        for frame_index, (sensor_ns, arrival_ns) in enumerate(
            zip(sensor_timestamps_ns, arrival_utc_ns)
        ):
            handle.write(f"{frame_index}\t{int(sensor_ns)}\t{int(arrival_ns)}\n")


def test_derive_frame_utc_ns_tracks_slow_drift_better_than_single_anchor():
    rng = np.random.default_rng(1234)
    frame_period_ns = int(1e9 / 10.0)
    frame_count = 3 * 60 * 60 * 10
    sensor_timestamps_ns = (
        np.arange(frame_count, dtype=np.int64) * frame_period_ns + 5_000_000_000
    )
    true_offset_ns = np.linspace(
        1_700_000_000_000_000_000,
        1_700_000_000_000_009_000,
        frame_count,
        dtype=np.int64,
    )
    true_utc_ns = sensor_timestamps_ns + true_offset_ns
    jitter_ns = rng.normal(0.0, 2_000_000.0, frame_count).astype(np.int64)
    arrival_utc_ns = true_utc_ns + jitter_ns

    derived_utc_ns, diagnostics = derive_frame_utc_ns(
        sensor_timestamps_ns,
        arrival_utc_ns,
        bin_size_ns=60 * 1_000_000_000,
    )
    single_anchor_ns = arrival_utc_ns[0] + (
        sensor_timestamps_ns - sensor_timestamps_ns[0]
    )

    derived_error = np.abs(derived_utc_ns - true_utc_ns)
    single_anchor_error = np.abs(single_anchor_ns - true_utc_ns)

    assert derived_error.max() < single_anchor_error.max()
    assert derived_error.max() < 8_000_000
    assert diagnostics["max_abs_residual_ns"] >= diagnostics["p95_abs_residual_ns"]


def test_finalize_clean_session_emits_single_mp4_and_tsv():
    with tempfile.TemporaryDirectory() as tmp:
        session_dir = Path(tmp) / "sessionA"
        session_dir.mkdir()
        sensor_ns = np.array([10, 20, 30], dtype=np.int64)
        arrival_ns = np.array(
            [1_700_000_000_000_000_010, 1_700_000_000_000_000_020, 1_700_000_000_000_000_030],
            dtype=np.int64,
        )
        _write_raw_attempt(session_dir, 1, sensor_ns, arrival_ns)

        remux_calls = []

        def fake_remux(src: Path, dst: Path, fps: float) -> None:
            remux_calls.append((src.name, dst.name, fps))
            dst.write_bytes(src.read_bytes() + b"mp4")

        finalize_session_directory(session_dir, fps=30.0, remuxer=fake_remux)

        assert (session_dir / "session.mp4").exists()
        assert (session_dir / "session.tsv").exists()
        manifest = load_manifest(session_dir / "session_manifest.json")
        assert manifest["clean_session"] is True
        assert manifest["attempt_count"] == 1
        assert remux_calls == [("attempt_001.h264", "session.mp4", 30.0)]


def test_finalize_crashed_session_emits_attempt_outputs_and_gap_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        session_dir = Path(tmp) / "sessionB"
        session_dir.mkdir()
        _write_raw_attempt(
            session_dir,
            1,
            np.array([10, 20], dtype=np.int64),
            np.array([1000, 2000], dtype=np.int64),
        )
        _write_raw_attempt(
            session_dir,
            2,
            np.array([1_000_000, 1_000_010], dtype=np.int64),
            np.array([4_000_000, 4_000_010], dtype=np.int64),
        )

        def fake_remux(src: Path, dst: Path, fps: float) -> None:
            dst.write_bytes(src.read_bytes() + b"mp4")

        finalize_session_directory(session_dir, fps=30.0, remuxer=fake_remux)

        assert (session_dir / "attempt_001.mp4").exists()
        assert (session_dir / "attempt_001.tsv").exists()
        assert (session_dir / "attempt_002.mp4").exists()
        assert (session_dir / "attempt_002.tsv").exists()
        assert not (session_dir / "session.mp4").exists()
        manifest = load_manifest(session_dir / "session_manifest.json")
        assert manifest["clean_session"] is False
        assert manifest["attempt_count"] == 2
        assert len(manifest["gaps"]) == 1


def test_verify_manifest_hashes_rejects_modified_file():
    with tempfile.TemporaryDirectory() as tmp:
        session_dir = Path(tmp) / "sessionC"
        session_dir.mkdir()
        payload = session_dir / "session.tsv"
        payload.write_text("hello\n", encoding="utf-8")
        write_manifest(
            session_dir / "session_manifest.json",
            {
                "files": [
                    {
                        "name": "session.tsv",
                        "sha256": "",
                    }
                ]
            },
            base_dir=session_dir,
        )
        assert verify_manifest_hashes(session_dir / "session_manifest.json", session_dir)
        payload.write_text("changed\n", encoding="utf-8")
        with pytest.raises(CameraSessionError):
            verify_manifest_hashes(
                session_dir / "session_manifest.json",
                session_dir,
                raise_on_error=True,
            )


def test_estimate_required_bytes_applies_safety_margin():
    assert estimate_required_bytes(60, 8_000_000, safety_margin=1.25) == 75_000_000


def test_http_service_blocks_manual_start_during_automated_run():
    app = create_app(storage_root=Path(tempfile.mkdtemp()), recorder_factory=None)
    client = app.test_client()

    automated = client.post(
        "/api/start",
        json={"session_id": "s1", "owner": "automated", "duration_s": 30},
    )
    assert automated.status_code == 200

    manual = client.post(
        "/api/start",
        json={"session_id": "s2", "owner": "manual", "duration_s": 30},
    )
    assert manual.status_code == 409

    stop = client.post("/api/stop", json={"owner": "automated"})
    assert stop.status_code == 200

    manual_after = client.post(
        "/api/start",
        json={"session_id": "s2", "owner": "manual", "duration_s": 30},
    )
    assert manual_after.status_code == 200


def test_http_service_main_starts_local_preview_viewer(monkeypatch, tmp_path: Path):
    """Service boot should start the local DRM preview helper before Flask runs.

    Inputs:
        monkeypatch: pytest fixture used to override side-effectful service startup.
        tmp_path: Temporary storage root passed through the environment.

    Returns:
        None.
    """

    observed: list[tuple[str, object]] = []

    class _FakeApp:
        logger = object()
        config: dict[str, object] = {}

        def run(self, host: str, port: int, threaded: bool) -> None:
            observed.append(("run", host, port, threaded))

    monkeypatch.setenv("CAMERA_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CAMERA_SERVICE_HOST", "127.0.0.1")
    monkeypatch.setenv("CAMERA_SERVICE_PORT", "8123")
    monkeypatch.setattr(http_camera_service, "Picamera2Recorder", object())
    monkeypatch.setattr(
        http_camera_service,
        "create_app",
        lambda storage_root, recorder_factory=None: observed.append(
            ("create_app", Path(storage_root), recorder_factory)
        )
        or _FakeApp(),
    )
    monkeypatch.setattr(
        http_camera_service,
        "start_preview_viewer_from_env",
        lambda port, logger=None: observed.append(("preview", port)),
    )

    http_camera_service.main()

    assert ("preview", 8123) in observed
    assert ("run", "127.0.0.1", 8123, True) in observed


def test_http_service_reports_low_space_blocking_state():
    app = create_app(
        storage_root=Path(tempfile.mkdtemp()),
        recorder_factory=None,
        free_space_bytes=10,
    )
    client = app.test_client()
    response = client.post(
        "/api/start",
        json={
            "session_id": "s_low",
            "owner": "automated",
            "duration_s": 120,
            "bitrate_bps": 8_000_000,
        },
    )
    assert response.status_code == 507
    payload = json.loads(response.data.decode("utf-8"))
    assert payload["status"] == "error"
    assert payload["block_behavbox"] is True


def test_manual_and_monitor_pages_expose_expected_controls():
    app = create_app(storage_root=Path(tempfile.mkdtemp()), recorder_factory=None)
    client = app.test_client()

    manual = client.get("/manual")
    monitor = client.get("/monitor")

    manual_html = manual.data.decode("utf-8")
    monitor_html = monitor.data.decode("utf-8")

    assert manual.status_code == 200
    assert "Manual Start" in manual_html
    assert "Manual Stop" in manual_html
    assert "/api/start" in manual_html
    assert monitor.status_code == 200
    assert "Emergency Stop" in monitor_html
    assert "Manual Start" not in monitor_html


def test_behavbox_video_methods_use_camera_client(monkeypatch):
    calls = []
    original_cwd = os.getcwd()

    class FakeCameraClient:
        def __init__(self, host, port=8000, remote_storage_subdir="behvideos"):
            calls.append(("init", host, port, remote_storage_subdir))

        def start_recording(self, **payload):
            calls.append(("start", payload))

        def stop_recording(self, owner="automated"):
            calls.append(("stop", owner))

        def offload_session(self, session_id, destination_root):
            calls.append(("offload", session_id, destination_root))

    monkeypatch.setattr("box_runtime.behavior.behavbox.CameraClient", FakeCameraClient)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            info = _session_info(tmp)
            box = BehavBox(info)
            box.video_start()
            box.video_stop()
        finally:
            os.chdir(original_cwd)

    assert ("init", "127.0.0.1", 8000, "behvideos") in calls
    assert any(item[0] == "start" for item in calls)
    assert ("offload", "test_session", f"{tmp}/") in calls


def test_behavbox_respects_camera_host_override(monkeypatch):
    calls = []
    original_cwd = os.getcwd()

    class FakeCameraClient:
        def __init__(self, host, port=8000, remote_storage_subdir="behvideos"):
            calls.append(("init", host, port, remote_storage_subdir))

        def start_recording(self, **payload):
            calls.append(("start", payload))

        def stop_recording(self, owner="automated"):
            calls.append(("stop", owner))

        def offload_session(self, session_id, destination_root):
            calls.append(("offload", session_id, destination_root))

    monkeypatch.setattr("box_runtime.behavior.behavbox.CameraClient", FakeCameraClient)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            info = _session_info(tmp)
            info["camera_host"] = "10.0.0.99"
            box = BehavBox(info)
            box.video_start()
            box.video_stop()
        finally:
            os.chdir(original_cwd)

    assert ("init", "10.0.0.99", 8000, "behvideos") in calls


def test_camera_client_offload_session_creates_single_session_directory(monkeypatch):
    def fake_run(command, capture_output, text):
        destination_dir = Path(command[-1])
        (destination_dir / "session_manifest.json").write_text(
            json.dumps(
                {
                    "files": [
                        {
                            "name": "session.tsv",
                            "sha256": "",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (destination_dir / "session.tsv").write_text("frame\n", encoding="utf-8")
        write_manifest(
            destination_dir / "session_manifest.json",
            load_manifest(destination_dir / "session_manifest.json"),
            base_dir=destination_dir,
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("box_runtime.video_recording.camera_client.subprocess.run", fake_run)
    monkeypatch.setattr(
        CameraClient,
        "acknowledge_transfer",
        lambda self, session_id: {"status": "ok", "session_id": session_id},
    )

    with tempfile.TemporaryDirectory() as tmp:
        destination_root = Path(tmp)
        client = CameraClient("camera-pi")
        session_dir = client.offload_session("session123", destination_root)

        assert session_dir == destination_root / "session123"
        assert (destination_root / "session123" / "session.tsv").exists()
        assert not (destination_root / "session123" / "session123").exists()


def test_video_capture_uses_camera_client_for_start_stop(monkeypatch):
    calls = []

    class FakeCameraClient:
        def __init__(self, host, port=8000, remote_storage_subdir="behvideos"):
            calls.append(("init", host, port, remote_storage_subdir))

        def start_recording(self, **payload):
            calls.append(("start", payload))

        def stop_recording(self, owner="automated"):
            calls.append(("stop", owner))

        def offload_session(self, session_id, destination_root):
            calls.append(("offload", session_id, destination_root))
            target = Path(destination_root) / session_id
            target.mkdir(parents=True, exist_ok=True)
            return target

    monkeypatch.setattr(
        "box_runtime.video_recording.VideoCapture.CameraClient",
        FakeCameraClient,
        raising=False,
    )

    with tempfile.TemporaryDirectory() as tmp:
        capture = VideoCapture(
            IP_address_video="10.0.0.2",
            video_name="videoA",
            base_pi_dir="/remote/ignored",
            local_storage_dir=tmp,
            frame_rate=60,
        )
        capture.video_start()
        capture.video_stop()

    assert ("init", "10.0.0.2", 8000, "behvideos") in calls
    assert any(item[0] == "start" for item in calls)
    assert ("offload", "videoA", tmp) in calls
    assert any(item[0] == "stop" for item in calls)
    assert any(item[0] == "offload" for item in calls)


def test_video_capture_defaults_camera_host_to_localhost(monkeypatch):
    calls = []

    class FakeCameraClient:
        def __init__(self, host, port=8000, remote_storage_subdir="behvideos"):
            calls.append(("init", host, port, remote_storage_subdir))

        def start_recording(self, **payload):
            calls.append(("start", payload))

        def stop_recording(self, owner="automated"):
            calls.append(("stop", owner))

        def offload_session(self, session_id, destination_root):
            calls.append(("offload", session_id, destination_root))
            target = Path(destination_root) / session_id
            target.mkdir(parents=True, exist_ok=True)
            return target

    monkeypatch.setattr(
        "box_runtime.video_recording.VideoCapture.CameraClient",
        FakeCameraClient,
        raising=False,
    )

    with tempfile.TemporaryDirectory() as tmp:
        capture = VideoCapture(
            IP_address_video=None,
            video_name="videoB",
            base_pi_dir="/remote/ignored",
            local_storage_dir=tmp,
            frame_rate=30,
        )
        capture.video_start()
        capture.video_stop()

    assert ("init", "127.0.0.1", 8000, "behvideos") in calls
