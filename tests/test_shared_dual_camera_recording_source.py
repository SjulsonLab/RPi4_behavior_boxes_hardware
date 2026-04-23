from __future__ import annotations

from pathlib import Path

import pytest

from debug.shared_dual_camera_recording_source import SharedDualCameraRecordingSource


def test_shared_dual_camera_recording_source_reports_both_camera_outputs(tmp_path: Path) -> None:
    """Dual-camera helper should namespace diagnostics for both recordings."""

    calls: list[str] = []

    class FakeFrame:
        def __init__(self, key: str) -> None:
            self.buffer_key = key
            self.request = object()

    class FakeRecordingSource:
        def __init__(self, **kwargs) -> None:
            calls.append(f"init:{kwargs['camera_id']}:{kwargs['overlay_enabled']}")
            self.camera_id = kwargs["camera_id"]
            self.video_path = Path(kwargs["video_path"])
            self.timestamp_csv_path = Path(kwargs["timestamp_csv_path"])
            self.video_path.write_text("fake h264", encoding="utf-8")
            self.timestamp_csv_path.write_text(
                "SensorTimestamp_ns,FrameDuration_us,UnixTimestamp_s\n1,2,3.0\n",
                encoding="utf-8",
            )

        def capture_frame_for_preview(self) -> FakeFrame:
            calls.append(f"capture:{self.camera_id}")
            return FakeFrame(f"{self.camera_id}-frame")

        def release_frame(self, _frame: FakeFrame) -> None:
            calls.append(f"release:{self.camera_id}")

        def diagnostics(self) -> dict[str, object]:
            return {
                "camera_id": self.camera_id,
                "overlay_enabled": True,
                "video_path": str(self.video_path),
                "timestamp_csv_path": str(self.timestamp_csv_path),
                "timestamp_sample_count": 7,
            }

        def close(self) -> None:
            calls.append(f"close:{self.camera_id}")

    source = SharedDualCameraRecordingSource(
        output_root=tmp_path,
        resolution_px=(1024, 600),
        frame_rate_hz=30.0,
        sensor_mode=0,
        request_mode="next",
        preview_overlay_enabled=True,
        recording_overlay_enabled=True,
        recording_source_factory=FakeRecordingSource,
    )

    frame = source.capture_frame_for_preview()
    source.release_frame(frame)
    diagnostics = source.diagnostics()
    source.close()

    assert calls[:2] == ["init:camera0:True", "init:camera1:True"]
    assert "capture:camera0" in calls
    assert "release:camera0" in calls
    assert calls[-2:] == ["close:camera1", "close:camera0"]
    assert diagnostics["preview_camera_id"] == "camera0"
    assert diagnostics["recording_camera_id"] == "camera1"
    assert diagnostics["camera0_overlay_enabled"] is True
    assert diagnostics["camera1_overlay_enabled"] is True
    assert diagnostics["camera0_video_path"].endswith("camera0_preview_recording_output.h264")
    assert diagnostics["camera0_timestamp_csv_path"].endswith("camera0_preview_recording_timestamp.csv")
    assert diagnostics["camera1_video_path"].endswith("camera1_recording_output.h264")
    assert diagnostics["camera1_timestamp_csv_path"].endswith("camera1_recording_timestamp.csv")
    assert diagnostics["camera0_timestamp_sample_count"] == 7
    assert diagnostics["camera1_timestamp_sample_count"] == 7


def test_shared_dual_camera_recording_source_closes_camera0_if_camera1_init_fails(
    tmp_path: Path,
) -> None:
    """Camera0 should be closed if camera1 fails during helper startup."""

    calls: list[str] = []

    class FakeRecordingSource:
        def __init__(self, **kwargs) -> None:
            calls.append(f"init:{kwargs['camera_id']}")
            self.camera_id = kwargs["camera_id"]
            if self.camera_id == "camera1":
                raise RuntimeError("camera1 init failed")

        def close(self) -> None:
            calls.append(f"close:{self.camera_id}")

    with pytest.raises(RuntimeError, match="camera1 init failed"):
        SharedDualCameraRecordingSource(
            output_root=tmp_path,
            resolution_px=(1024, 600),
            recording_source_factory=FakeRecordingSource,
        )

    assert calls == ["init:camera0", "init:camera1", "close:camera0"]
