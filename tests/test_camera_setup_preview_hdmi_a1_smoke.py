from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from debug.display_mode_guard import HeadlessModeStatus
from debug.camera_setup_preview_hdmi_a1_smoke import (
    run_camera_setup_preview_hdmi_a1_smoke,
)


def test_camera_setup_preview_hdmi_a1_smoke_reports_timing_metrics(tmp_path: Path) -> None:
    """Single-output setup preview should report frame-rate and timing summaries."""

    calls: list[str] = []

    class FakeFrameSource:
        def __init__(
            self,
            *,
            camera_id: str,
            resolution_px: tuple[int, int],
            acquisition_resolution_px: tuple[int, int],
            preview_stream_resolution_px: tuple[int, int],
            preview_source_mode: str,
            frame_rate_hz: float,
        ) -> None:
            calls.append(
                "frame_source:init:"
                f"{camera_id}:{resolution_px}:{acquisition_resolution_px}:{preview_stream_resolution_px}:{preview_source_mode}:{frame_rate_hz}"
            )
            self.preview_source_mode = preview_source_mode
            self.acquisition_resolution_px = acquisition_resolution_px
            self.preview_stream_resolution_px = preview_stream_resolution_px
            self.preview_rgb_resolution_px = resolution_px

        def capture_source_frame(self) -> np.ndarray:
            calls.append("frame_source:capture_source")
            return np.zeros((480, 640, 3), dtype=np.uint8)

        def prepare_preview_frame(self, source_frame: np.ndarray) -> np.ndarray:
            calls.append(f"frame_source:prepare:{source_frame.shape}")
            return np.ascontiguousarray(source_frame, dtype=np.uint8)

        def diagnostics(self) -> dict[str, object]:
            return {
                "camera_id": "camera0",
                "camera_preview_source_mode": self.preview_source_mode,
                "acquisition_resolution_px": self.acquisition_resolution_px,
                "preview_stream_resolution_px": self.preview_stream_resolution_px,
                "preview_frame_resolution_px": self.preview_rgb_resolution_px,
                "output_resolution_px": self.preview_rgb_resolution_px,
                "frame_rate_hz": 30.0,
            }

        def close(self) -> None:
            calls.append("frame_source:close")

    class FakeBackend:
        def __init__(self, **kwargs) -> None:
            calls.append(f"backend:init:{kwargs['connector']}:{kwargs['resolution_px']}")

        def display_frame(self, frame_rgb: np.ndarray) -> None:
            calls.append(f"backend:display:{frame_rgb.shape}")

        def diagnostics(self) -> dict[str, object]:
            return {
                "requested_connector": "HDMI-A-1",
                "reserved_crtc_id": 92,
                "reserved_plane_id": 81,
            }

        def close(self) -> None:
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
            10.14,
            10.15,
            10.16,
            10.17,
            10.18,
            10.19,
        ]
    )

    summary = run_camera_setup_preview_hdmi_a1_smoke(
        output_root=tmp_path,
        duration_s=0.06,
        frame_rate_hz=30.0,
        require_mode=lambda: HeadlessModeStatus(
            ok=True,
            lightdm_state="inactive",
            display=None,
            wayland_display=None,
            tty="/dev/tty1",
            reasons=(),
        ),
        frame_source_factory=FakeFrameSource,
        preview_backend_factory=FakeBackend,
        monotonic_fn=lambda: next(time_points),
        sleep_fn=lambda _seconds: None,
    )

    assert calls[0] == "frame_source:init:camera0:(1024, 600):(1024, 600):(1024, 600):rgb_main:30.0"
    assert calls[1] == "backend:init:HDMI-A-1:(1024, 600)"
    assert "frame_source:capture_source" in calls
    assert "frame_source:prepare:(480, 640, 3)" in calls
    assert any(call.startswith("backend:display:") for call in calls)
    assert calls[-2:] == ["backend:close", "frame_source:close"]
    assert summary["preview_connector"] == "HDMI-A-1"
    assert summary["camera_preview_source_mode"] == "rgb_main"
    assert summary["preview_frame_count"] >= 1
    assert summary["preview_elapsed_s"] >= 0.0
    assert summary["preview_fps_achieved"] >= 0.0
    assert summary["capture_time_total_s"] >= 0.0
    assert summary["frame_prepare_time_total_s"] >= 0.0
    assert summary["display_time_total_s"] >= 0.0
    assert summary["capture_time_avg_s"] >= 0.0
    assert summary["frame_prepare_time_avg_s"] >= 0.0
    assert summary["display_time_avg_s"] >= 0.0
    assert summary["preview_drm_diagnostics"]["requested_connector"] == "HDMI-A-1"


def test_camera_setup_preview_hdmi_a1_smoke_closes_frame_source_when_backend_init_fails(
    tmp_path: Path,
) -> None:
    """Setup preview should close the camera source if display startup fails."""

    calls: list[str] = []

    class FakeFrameSource:
        def __init__(self, **_kwargs) -> None:
            calls.append("frame_source:init")

        def close(self) -> None:
            calls.append("frame_source:close")

    class FailingBackend:
        def __init__(self, **_kwargs) -> None:
            calls.append("backend:init")
            raise RuntimeError("backend init failed")

    with pytest.raises(RuntimeError, match="backend init failed"):
        run_camera_setup_preview_hdmi_a1_smoke(
            output_root=tmp_path,
            require_mode=lambda: HeadlessModeStatus(
                ok=True,
                lightdm_state="inactive",
                display=None,
                wayland_display=None,
                tty="/dev/tty1",
                reasons=(),
            ),
            frame_source_factory=FakeFrameSource,
            preview_backend_factory=FailingBackend,
        )

    assert calls == ["frame_source:init", "backend:init", "frame_source:close"]

