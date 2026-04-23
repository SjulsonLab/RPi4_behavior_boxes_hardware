from __future__ import annotations

from pathlib import Path

import pytest

from debug.camera_preview_recording_hdmi_a1_smoke import (
    run_camera_preview_recording_hdmi_a1_smoke,
)
from debug.display_mode_guard import HeadlessModeStatus


def test_camera_preview_recording_smoke_reports_preview_metrics_and_output_paths(
    tmp_path: Path,
) -> None:
    """Preview+recording smoke should report preview timing and recording artifact paths."""

    calls: list[str] = []

    class FakeFrame:
        def __init__(self, key: str) -> None:
            self.buffer_key = key
            self.request = object()

    class FakeSource:
        def __init__(self, **kwargs) -> None:
            calls.append("source:init")
            assert kwargs["camera_id"] == "camera1"
            assert kwargs["sensor_mode"] == 1
            assert kwargs["request_mode"] == "next"
            assert kwargs["overlay_enabled"] is True
            assert Path(kwargs["video_path"]).name == "camera1_preview_recording_output.h264"
            assert Path(kwargs["timestamp_csv_path"]).name == "camera1_preview_recording_timestamp.csv"
            Path(kwargs["video_path"]).write_text("fake h264", encoding="utf-8")
            Path(kwargs["timestamp_csv_path"]).write_text(
                "SensorTimestamp_ns,FrameDuration_us,UnixTimestamp_s\n1,2,3.0\n",
                encoding="utf-8",
            )
            self._frame_index = 0
            self._video_path = kwargs["video_path"]
            self._timestamp_csv_path = kwargs["timestamp_csv_path"]

        def capture_frame_for_preview(self) -> FakeFrame:
            calls.append("source:capture_frame_for_preview")
            self._frame_index += 1
            return FakeFrame(f"frame-{self._frame_index}")

        def release_frame(self, _frame: FakeFrame) -> None:
            calls.append("source:release_frame")

        def diagnostics(self) -> dict[str, object]:
            return {
                "camera_id": "camera0",
                "sensor_mode": 1,
                "request_mode": "next",
                "overlay_enabled": True,
                "video_path": str(self._video_path),
                "timestamp_csv_path": str(self._timestamp_csv_path),
                "frame_rate_hz": 50.0,
            }

        def close(self) -> None:
            calls.append("source:close")

    class FakeBackend:
        def __init__(self, **kwargs) -> None:
            calls.append("backend:init")
            self._frame_release_fn = kwargs["frame_release_fn"]
            self._current_frame = None

        def display_dmabuf_frame(self, frame: FakeFrame) -> None:
            calls.append(f"backend:display:{frame.buffer_key}")
            if self._current_frame is not None:
                self._frame_release_fn(self._current_frame)
            self._current_frame = frame

        def diagnostics(self) -> dict[str, object]:
            return {"requested_connector": "HDMI-A-1"}

        def close(self) -> None:
            if self._current_frame is not None:
                self._frame_release_fn(self._current_frame)
                self._current_frame = None
            calls.append("backend:close")

    time_points = iter(
        [
            10.00,
            10.01,
            10.02,
            10.03,
            10.04,
            10.05,
            10.06,
            10.07,
            10.08,
            10.09,
            10.10,
            10.11,
            10.12,
            10.13,
        ]
    )

    summary = run_camera_preview_recording_hdmi_a1_smoke(
        output_root=tmp_path,
        camera_id="camera1",
        duration_s=0.05,
        frame_rate_hz=50.0,
        sensor_mode=1,
        request_mode="next",
        overlay_enabled=True,
        require_mode=lambda: HeadlessModeStatus(
            ok=True,
            lightdm_state="inactive",
            display=None,
            wayland_display=None,
            tty="/dev/tty1",
            reasons=(),
        ),
        frame_source_factory=FakeSource,
        preview_backend_factory=FakeBackend,
        monotonic_fn=lambda: next(time_points),
    )

    assert calls[:2] == ["source:init", "backend:init"]
    assert "source:capture_frame_for_preview" in calls
    assert any(call.startswith("backend:display:") for call in calls)
    assert "source:release_frame" in calls
    assert calls[-2:] == ["backend:close", "source:close"]
    assert summary["camera_id"] == "camera1"
    assert summary["sensor_mode"] == 1
    assert summary["request_mode"] == "next"
    assert summary["overlay_enabled"] is True
    assert summary["preview_frame_count"] >= 1
    assert summary["preview_fps_achieved"] >= 0.0
    assert summary["video_path"].endswith("camera1_preview_recording_output.h264")
    assert summary["timestamp_csv_path"].endswith("camera1_preview_recording_timestamp.csv")
    assert summary["preview_drm_diagnostics"]["requested_connector"] == "HDMI-A-1"


def test_camera_preview_recording_smoke_closes_resources_on_display_failure(
    tmp_path: Path,
) -> None:
    """Preview display failures should still close the recording source cleanly."""

    calls: list[str] = []

    class FakeFrame:
        def __init__(self) -> None:
            self.buffer_key = "frame-1"
            self.request = object()

    class FakeSource:
        def __init__(self, **kwargs) -> None:
            calls.append("source:init")
            assert kwargs["camera_id"] == "camera1"
            Path(kwargs["video_path"]).write_text("fake h264", encoding="utf-8")
            Path(kwargs["timestamp_csv_path"]).write_text(
                "SensorTimestamp_ns,FrameDuration_us,UnixTimestamp_s\n",
                encoding="utf-8",
            )

        def capture_frame_for_preview(self) -> FakeFrame:
            calls.append("source:capture_frame_for_preview")
            return FakeFrame()

        def release_frame(self, _frame: FakeFrame) -> None:
            calls.append("source:release_frame")

        def diagnostics(self) -> dict[str, object]:
            return {}

        def close(self) -> None:
            calls.append("source:close")

    class FailingBackend:
        def __init__(self, **kwargs) -> None:
            calls.append("backend:init")
            self._frame_release_fn = kwargs["frame_release_fn"]

        def display_dmabuf_frame(self, frame: FakeFrame) -> None:
            self._frame_release_fn(frame)
            raise RuntimeError("display failed")

        def diagnostics(self) -> dict[str, object]:
            return {"requested_connector": "HDMI-A-1"}

        def close(self) -> None:
            calls.append("backend:close")

    with pytest.raises(RuntimeError, match="display failed") as exc_info:
        run_camera_preview_recording_hdmi_a1_smoke(
            output_root=tmp_path,
            camera_id="camera1",
            duration_s=0.05,
            sensor_mode=0,
            request_mode="next",
            overlay_enabled=False,
            require_mode=lambda: HeadlessModeStatus(
                ok=True,
                lightdm_state="inactive",
                display=None,
                wayland_display=None,
                tty="/dev/tty1",
                reasons=(),
            ),
            frame_source_factory=FakeSource,
            preview_backend_factory=FailingBackend,
        )

    assert exc_info.value.summary["failure_stage"] == "preview_loop_display"
    assert exc_info.value.summary["camera_id"] == "camera1"
    assert "source:release_frame" in calls
    assert calls[-2:] == ["backend:close", "source:close"]
