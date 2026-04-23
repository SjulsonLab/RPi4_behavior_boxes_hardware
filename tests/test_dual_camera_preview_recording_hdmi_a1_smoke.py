from __future__ import annotations

from pathlib import Path

import pytest

from debug.display_mode_guard import HeadlessModeStatus
from debug.dual_camera_preview_recording_hdmi_a1_smoke import (
    run_dual_camera_preview_recording_hdmi_a1_smoke,
)


def test_dual_camera_preview_recording_smoke_reports_both_camera_artifacts(tmp_path: Path) -> None:
    """Dual-camera smoke should report preview metrics and both recording artifacts."""

    calls: list[str] = []

    class FakeFrame:
        def __init__(self, key: str) -> None:
            self.buffer_key = key
            self.request = object()

    class FakeDualSource:
        def __init__(self, **kwargs) -> None:
            calls.append("source:init")
            assert kwargs["preview_overlay_enabled"] is True
            assert kwargs["recording_overlay_enabled"] is True

        def capture_frame_for_preview(self) -> FakeFrame:
            calls.append("source:capture")
            return FakeFrame("camera0-frame")

        def release_frame(self, _frame: FakeFrame) -> None:
            calls.append("source:release")

        def diagnostics(self) -> dict[str, object]:
            return {
                "preview_camera_id": "camera0",
                "recording_camera_id": "camera1",
                "camera0_video_path": str(tmp_path / "camera0_preview_recording_output.h264"),
                "camera0_timestamp_csv_path": str(tmp_path / "camera0_preview_recording_timestamp.csv"),
                "camera1_video_path": str(tmp_path / "camera1_recording_output.h264"),
                "camera1_timestamp_csv_path": str(tmp_path / "camera1_recording_timestamp.csv"),
                "camera0_timestamp_sample_count": 11,
                "camera1_timestamp_sample_count": 13,
                "camera0_overlay_enabled": True,
                "camera1_overlay_enabled": True,
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

    summary = run_dual_camera_preview_recording_hdmi_a1_smoke(
        output_root=tmp_path,
        duration_s=0.05,
        frame_rate_hz=30.0,
        sensor_mode=0,
        request_mode="next",
        preview_overlay_enabled=True,
        recording_overlay_enabled=True,
        require_mode=lambda: HeadlessModeStatus(
            ok=True,
            lightdm_state="inactive",
            display=None,
            wayland_display=None,
            tty="/dev/tty1",
            reasons=(),
        ),
        dual_source_factory=FakeDualSource,
        preview_backend_factory=FakeBackend,
        monotonic_fn=lambda: next(time_points),
    )

    assert calls[:2] == ["source:init", "backend:init"]
    assert "source:capture" in calls
    assert any(call.startswith("backend:display:camera0-frame") for call in calls)
    assert "source:release" in calls
    assert calls[-2:] == ["backend:close", "source:close"]
    assert summary["preview_camera_id"] == "camera0"
    assert summary["recording_camera_id"] == "camera1"
    assert summary["camera0_video_path"].endswith(".h264")
    assert summary["camera1_video_path"].endswith(".h264")
    assert summary["camera0_timestamp_csv_path"].endswith(".csv")
    assert summary["camera1_timestamp_csv_path"].endswith(".csv")
    assert summary["camera0_timestamp_sample_count"] == 11
    assert summary["camera1_timestamp_sample_count"] == 13
    assert summary["preview_drm_diagnostics"]["requested_connector"] == "HDMI-A-1"


def test_dual_camera_preview_recording_smoke_closes_resources_on_display_failure(
    tmp_path: Path,
) -> None:
    """Display failures should still close the dual-camera source cleanly."""

    calls: list[str] = []

    class FakeFrame:
        def __init__(self) -> None:
            self.buffer_key = "camera0-frame"
            self.request = object()

    class FakeDualSource:
        def __init__(self, **_kwargs) -> None:
            calls.append("source:init")

        def capture_frame_for_preview(self) -> FakeFrame:
            calls.append("source:capture")
            return FakeFrame()

        def release_frame(self, _frame: FakeFrame) -> None:
            calls.append("source:release")

        def diagnostics(self) -> dict[str, object]:
            return {
                "preview_camera_id": "camera0",
                "recording_camera_id": "camera1",
            }

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
        run_dual_camera_preview_recording_hdmi_a1_smoke(
            output_root=tmp_path,
            duration_s=0.05,
            require_mode=lambda: HeadlessModeStatus(
                ok=True,
                lightdm_state="inactive",
                display=None,
                wayland_display=None,
                tty="/dev/tty1",
                reasons=(),
            ),
            dual_source_factory=FakeDualSource,
            preview_backend_factory=FailingBackend,
        )

    assert exc_info.value.summary["failure_stage"] == "preview_loop_display"
    assert exc_info.value.summary["preview_camera_id"] == "camera0"
    assert exc_info.value.summary["recording_camera_id"] == "camera1"
    assert "source:release" in calls
    assert calls[-2:] == ["backend:close", "source:close"]
