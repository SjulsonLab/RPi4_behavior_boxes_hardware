from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from debug.display_mode_guard import HeadlessModeStatus
from debug.shared_drm_preview_and_visual_smoke import (
    run_shared_drm_preview_and_visual_smoke,
)


def test_shared_drm_smoke_aborts_before_drm_init_when_mode_guard_fails(tmp_path: Path) -> None:
    """The shared-DRM smoke should not construct a controller in the wrong mode."""

    class ShouldNotConstruct:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("controller should not be constructed")

    def fail_guard() -> HeadlessModeStatus:
        raise RuntimeError("wrong mode")

    with pytest.raises(RuntimeError, match="wrong mode"):
        run_shared_drm_preview_and_visual_smoke(
            output_root=tmp_path,
            require_mode=fail_guard,
            controller_factory=ShouldNotConstruct,
        )


def test_shared_drm_smoke_initializes_preview_before_stimulus_and_reports_diagnostics(
    tmp_path: Path,
) -> None:
    """The shared smoke should initialize preview first and summarize both outputs."""

    calls: list[str] = []

    class FakeOutput:
        def __init__(self, role: str, resolution_px: tuple[int, int]) -> None:
            self.role = role
            self.resolution_px = resolution_px
            self.refresh_hz = 60.0
            self.connector = "HDMI-A-1" if role == "preview" else "HDMI-A-2"

        def display_rgb_frame(self, frame_rgb: np.ndarray) -> None:
            calls.append(f"{self.role}:rgb:{frame_rgb.shape}")

        def display_gray(self, gray_level_u8: int) -> None:
            calls.append(f"{self.role}:gray:{gray_level_u8}")

        def play_grating(self, stimulus_name: str, _compiled: object) -> None:
            calls.append(f"{self.role}:play:{stimulus_name}")

        def diagnostics(self) -> dict[str, object]:
            return {
                "requested_connector": self.connector,
                "reserved_crtc_id": 92 if self.role == "preview" else 104,
                "reserved_plane_id": 81 if self.role == "preview" else 93,
            }

    class FakeController:
        def __init__(self, **_kwargs) -> None:
            self.preview = FakeOutput("preview", (640, 480))
            self.stimulus = FakeOutput("stimulus", (1024, 600))

        def diagnostics(self) -> dict[str, object]:
            return {
                "preview": self.preview.diagnostics(),
                "stimulus": self.stimulus.diagnostics(),
            }

        def close(self) -> None:
            calls.append("controller:close")

    summary = run_shared_drm_preview_and_visual_smoke(
        output_root=tmp_path,
        hold_s=0.0,
        require_mode=lambda: HeadlessModeStatus(
            ok=True,
            lightdm_state="inactive",
            display=None,
            wayland_display=None,
            tty="/dev/tty1",
            reasons=(),
        ),
        controller_factory=FakeController,
        compile_stimuli_fn=lambda **_kwargs: {
            "go_grating": object(),
            "nogo_grating": object(),
        },
        sleep_fn=lambda _seconds: None,
    )

    assert calls[0].startswith("preview:rgb")
    assert "stimulus:gray:127" in calls
    assert "stimulus:play:go_grating" in calls
    assert "stimulus:play:nogo_grating" in calls
    assert calls[-1] == "controller:close"
    assert summary["preview_drm_diagnostics"]["requested_connector"] == "HDMI-A-1"
    assert summary["visual_drm_diagnostics"]["requested_connector"] == "HDMI-A-2"


def test_shared_drm_smoke_live_camera0_uses_frame_source_before_stimulus(tmp_path: Path) -> None:
    """Live shared smoke should use camera0 frames instead of the placeholder path."""

    calls: list[str] = []

    class FakeOutput:
        def __init__(self, role: str, resolution_px: tuple[int, int]) -> None:
            self.role = role
            self.resolution_px = resolution_px
            self.refresh_hz = 60.0
            self.connector = "HDMI-A-1" if role == "preview" else "HDMI-A-2"

        def display_rgb_frame(self, frame_rgb: np.ndarray) -> None:
            calls.append(f"{self.role}:rgb:{frame_rgb.shape}")

        def display_gray(self, gray_level_u8: int) -> None:
            calls.append(f"{self.role}:gray:{gray_level_u8}")

        def play_grating(self, stimulus_name: str, _compiled: object) -> None:
            calls.append(f"{self.role}:play:{stimulus_name}")

        def diagnostics(self) -> dict[str, object]:
            return {
                "requested_connector": self.connector,
                "reserved_crtc_id": 92 if self.role == "preview" else 104,
                "reserved_plane_id": 81 if self.role == "preview" else 93,
            }

    class FakeController:
        def __init__(self, **_kwargs) -> None:
            self.preview = FakeOutput("preview", (640, 480))
            self.stimulus = FakeOutput("stimulus", (1024, 600))

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
            self.preview_rgb_resolution_px = (640, 480)

        def capture_rgb_frame(self) -> np.ndarray:
            calls.append("frame_source:capture")
            return np.zeros((480, 640, 3), dtype=np.uint8)

        def close(self) -> None:
            calls.append("frame_source:close")

    time_points = iter(
        [
            10.00,
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
        ]
    )

    summary = run_shared_drm_preview_and_visual_smoke(
        output_root=tmp_path,
        hold_s=0.05,
        preview_mode="live_camera0",
        preview_frame_rate_hz=30.0,
        require_mode=lambda: HeadlessModeStatus(
            ok=True,
            lightdm_state="inactive",
            display=None,
            wayland_display=None,
            tty="/dev/tty1",
            reasons=(),
        ),
        controller_factory=FakeController,
        frame_source_factory=FakeFrameSource,
        compile_stimuli_fn=lambda **_kwargs: {
            "go_grating": object(),
            "nogo_grating": object(),
        },
        sleep_fn=lambda _seconds: None,
        monotonic_fn=lambda: next(time_points),
    )

    assert calls[0] == "frame_source:init:camera0:(640, 480):(640, 480):(640, 480):rgb_main:30.0"
    assert calls[1] == "frame_source:capture"
    assert calls[2].startswith("preview:rgb")
    assert "stimulus:play:go_grating" in calls
    assert calls[-2:] == ["frame_source:close", "controller:close"]
    assert summary["preview_mode"] == "live_camera0"
    assert summary["camera_preview_source_mode"] == "rgb_main"
    assert summary["camera_acquisition_resolution_px"] == (640, 480)
    assert summary["preview_stream_resolution_px"] == (640, 480)
    assert summary["preview_frame_resolution_px"] == (640, 480)
    assert summary["preview_target_fps"] == 30.0
    assert summary["preview_frame_count"] >= 3
    assert summary["preview_elapsed_s"] >= 0.0
    assert summary["preview_fps_achieved"] >= 0.0
