from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from debug.display_mode_guard import HeadlessModeStatus
from debug.camera_setup_preview_plus_blank_hdmi_a2_smoke import (
    run_camera_setup_preview_plus_blank_hdmi_a2_smoke,
)


def test_dual_output_setup_preview_reports_timing_metrics_and_blank_output(tmp_path: Path) -> None:
    """Dual-output setup preview should drive camera preview plus a blank second output."""

    calls: list[str] = []

    class FakeOutput:
        def __init__(self, role: str, connector: str, resolution_px: tuple[int, int]) -> None:
            self.role = role
            self.connector = connector
            self.resolution_px = resolution_px

        def display_rgb_frame(self, frame_rgb: np.ndarray) -> None:
            calls.append(f"{self.role}:rgb:{frame_rgb.shape}")

        def display_gray(self, gray_level_u8: int) -> None:
            calls.append(f"{self.role}:gray:{gray_level_u8}")

        def diagnostics(self) -> dict[str, object]:
            return {
                "requested_connector": self.connector,
                "reserved_crtc_id": 92 if self.role == "preview" else 104,
                "reserved_plane_id": 81 if self.role == "preview" else 93,
            }

    class FakeController:
        def __init__(self, *, preview_connector: str, stimulus_connector: str) -> None:
            calls.append(f"controller:init:{preview_connector}:{stimulus_connector}")
            self.preview = FakeOutput("preview", preview_connector, (1024, 600))
            self.stimulus = FakeOutput("blank", stimulus_connector, (1024, 600))

        def diagnostics(self) -> dict[str, object]:
            return {
                "preview": self.preview.diagnostics(),
                "stimulus": self.stimulus.diagnostics(),
            }

        def close(self) -> None:
            calls.append("controller:close")

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
            return np.zeros((600, 1024, 3), dtype=np.uint8)

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
            10.20,
            10.21,
        ]
    )

    summary = run_camera_setup_preview_plus_blank_hdmi_a2_smoke(
        output_root=tmp_path,
        duration_s=0.06,
        frame_rate_hz=30.0,
        blank_gray_level_u8=127,
        require_mode=lambda: HeadlessModeStatus(
            ok=True,
            lightdm_state="inactive",
            display=None,
            wayland_display=None,
            tty="/dev/tty1",
            reasons=(),
        ),
        frame_source_factory=FakeFrameSource,
        shared_controller_factory=FakeController,
        monotonic_fn=lambda: next(time_points),
        sleep_fn=lambda _seconds: None,
    )

    assert calls[0] == "frame_source:init:camera0:(1024, 600):(1024, 600):(1024, 600):rgb_main:30.0"
    assert calls[1] == "controller:init:HDMI-A-1:HDMI-A-2"
    assert "blank:gray:127" in calls
    assert "frame_source:capture_source" in calls
    assert "frame_source:prepare:(600, 1024, 3)" in calls
    assert any(call.startswith("preview:rgb:") for call in calls)
    assert calls[-2:] == ["controller:close", "frame_source:close"]
    assert summary["preview_connector"] == "HDMI-A-1"
    assert summary["blank_connector"] == "HDMI-A-2"
    assert summary["blank_gray_level_u8"] == 127
    assert summary["camera_preview_source_mode"] == "rgb_main"
    assert summary["preview_frame_count"] >= 1
    assert summary["preview_elapsed_s"] >= 0.0
    assert summary["preview_fps_achieved"] >= 0.0
    assert summary["capture_time_total_s"] >= 0.0
    assert summary["frame_prepare_time_total_s"] >= 0.0
    assert summary["display_time_total_s"] >= 0.0
    assert summary["preview_drm_diagnostics"]["requested_connector"] == "HDMI-A-1"
    assert summary["blank_drm_diagnostics"]["requested_connector"] == "HDMI-A-2"


def test_dual_output_setup_preview_closes_resources_when_blank_output_init_fails(
    tmp_path: Path,
) -> None:
    """Dual-output setup preview should clean up when the blank output path fails."""

    calls: list[str] = []

    class FakeFrameSource:
        def __init__(self, **_kwargs) -> None:
            calls.append("frame_source:init")

        def diagnostics(self) -> dict[str, object]:
            return {}

        def close(self) -> None:
            calls.append("frame_source:close")

    class FailingBlankOutput:
        def display_gray(self, _gray_level_u8: int) -> None:
            calls.append("blank:gray")
            raise RuntimeError("blank display failed")

        def diagnostics(self) -> dict[str, object]:
            return {"requested_connector": "HDMI-A-2"}

    class FakeController:
        def __init__(self, **_kwargs) -> None:
            calls.append("controller:init")
            self.preview = object()
            self.stimulus = FailingBlankOutput()

        def diagnostics(self) -> dict[str, object]:
            return {
                "preview": {},
                "stimulus": self.stimulus.diagnostics(),
            }

        def close(self) -> None:
            calls.append("controller:close")

    with pytest.raises(RuntimeError, match="blank display failed"):
        run_camera_setup_preview_plus_blank_hdmi_a2_smoke(
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
            shared_controller_factory=FakeController,
        )

    assert calls == ["frame_source:init", "controller:init", "blank:gray", "controller:close", "frame_source:close"]


def test_dual_output_setup_preview_reports_preview_loop_display_failure_stage(
    tmp_path: Path,
) -> None:
    """Preview-loop display failures should be labeled accurately in the summary."""

    class FakeOutput:
        def __init__(self, role: str) -> None:
            self.role = role

        def display_rgb_frame(self, _frame_rgb: np.ndarray) -> None:
            raise RuntimeError("preview display failed")

        def display_gray(self, _gray_level_u8: int) -> None:
            return None

        def diagnostics(self) -> dict[str, object]:
            return {"requested_connector": "HDMI-A-1" if self.role == "preview" else "HDMI-A-2"}

    class FakeController:
        def __init__(self, **_kwargs) -> None:
            self.preview = FakeOutput("preview")
            self.stimulus = FakeOutput("blank")

        def diagnostics(self) -> dict[str, object]:
            return {
                "preview": self.preview.diagnostics(),
                "stimulus": self.stimulus.diagnostics(),
            }

        def close(self) -> None:
            return None

    class FakeFrameSource:
        def __init__(self, **_kwargs) -> None:
            self.preview_source_mode = "rgb_main"
            self.acquisition_resolution_px = (1024, 600)
            self.preview_stream_resolution_px = (1024, 600)
            self.preview_rgb_resolution_px = (1024, 600)

        def capture_source_frame(self) -> np.ndarray:
            return np.zeros((600, 1024, 3), dtype=np.uint8)

        def prepare_preview_frame(self, source_frame: np.ndarray) -> np.ndarray:
            return source_frame

        def diagnostics(self) -> dict[str, object]:
            return {}

        def close(self) -> None:
            return None

    with pytest.raises(RuntimeError, match="preview display failed") as exc_info:
        run_camera_setup_preview_plus_blank_hdmi_a2_smoke(
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
            frame_source_factory=FakeFrameSource,
            shared_controller_factory=FakeController,
            sleep_fn=lambda _seconds: None,
        )

    assert exc_info.value.summary["failure_stage"] == "preview_loop_display"
