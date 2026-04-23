from __future__ import annotations

from pathlib import Path

import pytest

from debug.camera_setup_preview_hdmi_a1_dmabuf_smoke import (
    run_camera_setup_preview_hdmi_a1_dmabuf_smoke,
)
from debug.display_mode_guard import HeadlessModeStatus


def test_camera_setup_preview_dmabuf_smoke_reports_timing_metrics(tmp_path: Path) -> None:
    """The dmabuf smoke should report timing metrics and DRM diagnostics."""

    calls: list[str] = []

    class FakeFrame:
        def __init__(self) -> None:
            self.request = object()

    class FakeSource:
        def __init__(self, **kwargs) -> None:
            calls.append("source:init")
            assert kwargs["sensor_mode"] == 0
            assert kwargs["request_mode"] == "next"

        def capture_frame_for_preview(self) -> FakeFrame:
            calls.append("source:capture_frame_for_preview")
            return FakeFrame()

        def release_frame(self, _frame: FakeFrame) -> None:
            calls.append("source:release_frame")

        def diagnostics(self) -> dict[str, object]:
            return {
                "camera_id": "camera0",
                "frame_rate_hz": 30.0,
                "sensor_mode": 0,
                "frame_duration_us": 33333,
                "output_resolution_px": (1024, 600),
            }

        def close(self) -> None:
            calls.append("source:close")

    class FakeBackend:
        def __init__(self, **kwargs) -> None:
            calls.append("backend:init")
            self._frame_release_fn = kwargs["frame_release_fn"]
            self._current_frame: FakeFrame | None = None

        def display_dmabuf_frame(self, frame: FakeFrame) -> None:
            calls.append("backend:display")
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

    summary = run_camera_setup_preview_hdmi_a1_dmabuf_smoke(
        output_root=tmp_path,
        duration_s=0.05,
        frame_rate_hz=30.0,
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
        sleep_fn=lambda _seconds: None,
    )

    assert calls[:2] == ["source:init", "backend:init"]
    assert "source:capture_frame_for_preview" in calls
    assert "backend:display" in calls
    assert "source:release_frame" in calls
    assert calls[-2:] == ["backend:close", "source:close"]
    assert summary["preview_connector"] == "HDMI-A-1"
    assert summary["preview_frame_count"] >= 1
    assert summary["preview_elapsed_s"] >= 0.0
    assert summary["preview_fps_achieved"] >= 0.0
    assert summary["capture_time_total_s"] >= 0.0
    assert summary["display_time_total_s"] >= 0.0
    assert summary["sensor_mode"] == 0
    assert summary["frame_duration_us"] == 33333
    assert summary["request_mode"] == "next"
    assert summary["preview_drm_diagnostics"]["requested_connector"] == "HDMI-A-1"


def test_camera_setup_preview_dmabuf_smoke_releases_frame_on_display_failure(tmp_path: Path) -> None:
    """Display failures should still release the current captured frame."""

    calls: list[str] = []

    class FakeFrame:
        def __init__(self) -> None:
            self.request = object()

    class FakeSource:
        def __init__(self, **_kwargs) -> None:
            calls.append("source:init")

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
        run_camera_setup_preview_hdmi_a1_dmabuf_smoke(
            output_root=tmp_path,
            duration_s=0.05,
            request_mode="next",
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
            sleep_fn=lambda _seconds: None,
        )

    assert exc_info.value.summary["failure_stage"] == "preview_loop_display"
    assert "source:release_frame" in calls
    assert calls[-2:] == ["backend:close", "source:close"]
