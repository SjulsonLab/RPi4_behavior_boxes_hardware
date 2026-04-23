from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from debug.display_mode_guard import HeadlessModeStatus
from debug.camera_setup_preview_plus_gratings_multiprocess_smoke import (
    run_camera_setup_preview_plus_gratings_multiprocess_smoke,
)


def test_multiprocess_preview_plus_grating_reports_metrics_and_starts_worker_after_preview(
    tmp_path: Path,
) -> None:
    """The multiprocess smoke should start preview first, then trigger one grating worker."""

    calls: list[str] = []
    worker_init_kwargs: dict[str, object] = {}

    class FakeOutput:
        def __init__(self, role: str, connector: str, resolution_px: tuple[int, int]) -> None:
            self.role = role
            self.connector = connector
            self.resolution_px = resolution_px
            self.refresh_hz = 60.0

        def display_rgb_frame(self, frame_rgb: np.ndarray) -> None:
            calls.append(f"{self.role}:rgb:{frame_rgb.shape}")

        def display_gray(self, gray_level_u8: int) -> None:
            calls.append(f"{self.role}:gray:{gray_level_u8}")

        def display_gray_frame(self, frame_gray: np.ndarray) -> None:
            calls.append(f"{self.role}:gray_frame:{frame_gray.shape}")

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
            self.stimulus = FakeOutput("stimulus", stimulus_connector, (1024, 600))

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

    class FakeWorker:
        def __init__(self, **kwargs) -> None:
            worker_init_kwargs.update(kwargs)
            calls.append("worker:init")
            self._snapshots = iter(
                [
                    {"active": False, "frame_index": 0, "update_count": 0},
                    {"active": True, "frame_index": 0, "update_count": 1},
                    {"active": True, "frame_index": 1, "update_count": 2},
                    {"active": False, "frame_index": 1, "update_count": 2},
                ]
            )

        def start(self) -> None:
            calls.append("worker:start")

        def snapshot(self) -> dict[str, object]:
            calls.append("worker:snapshot")
            return next(self._snapshots, {"active": False, "frame_index": 1, "update_count": 2})

        def diagnostics(self) -> dict[str, object]:
            return {"worker_started": True}

        def close(self) -> None:
            calls.append("worker:close")

    compiled_go = SimpleNamespace(
        frames=np.stack(
            [
                np.zeros((600, 1024), dtype=np.uint8),
                np.ones((600, 1024), dtype=np.uint8) * 255,
            ],
            axis=0,
        ),
        frame_interval_s=1.0 / 60.0,
        spec=SimpleNamespace(name="go_grating", background_gray_u8=127),
    )

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
            10.22,
            10.23,
            10.24,
            10.25,
        ]
    )

    summary = run_camera_setup_preview_plus_gratings_multiprocess_smoke(
        output_root=tmp_path,
        duration_s=0.08,
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
        shared_controller_factory=FakeController,
        compile_stimulus_fn=lambda **_kwargs: compiled_go,
        worker_factory=FakeWorker,
        monotonic_fn=lambda: next(time_points),
        sleep_fn=lambda _seconds: None,
    )

    assert calls[0] == "frame_source:init:camera0:(1024, 600):(1024, 600):(1024, 600):rgb_main:30.0"
    assert calls[1] == "controller:init:HDMI-A-1:HDMI-A-2"
    assert "worker:init" in calls
    assert calls.index("preview:rgb:(600, 1024, 3)") < calls.index("worker:start")
    assert calls.count("worker:start") == 1
    assert any(call.startswith("stimulus:gray_frame:") for call in calls)
    assert calls[-3:] == ["worker:close", "controller:close", "frame_source:close"]
    assert "controller" not in worker_init_kwargs
    assert "stimulus_output" not in worker_init_kwargs
    assert summary["preview_connector"] == "HDMI-A-1"
    assert summary["stimulus_connector"] == "HDMI-A-2"
    assert summary["stimulus_name"] == "go_grating"
    assert summary["preview_frame_count"] >= 1
    assert summary["preview_elapsed_s"] >= 0.0
    assert summary["preview_fps_achieved"] >= 0.0
    assert summary["capture_time_total_s"] >= 0.0
    assert summary["frame_prepare_time_total_s"] >= 0.0
    assert summary["display_time_total_s"] >= 0.0
    assert summary["stimulus_display_time_total_s"] >= 0.0
    assert summary["stimulus_frame_update_count"] >= 1
    assert summary["stimulus_worker_update_count"] >= 1
    assert summary["preview_drm_diagnostics"]["requested_connector"] == "HDMI-A-1"
    assert summary["stimulus_drm_diagnostics"]["requested_connector"] == "HDMI-A-2"


def test_multiprocess_preview_plus_grating_passes_requested_stimulus_duration_to_compile(
    tmp_path: Path,
) -> None:
    """Requested stimulus duration should drive grating compilation, not only worker runtime."""

    compile_calls: list[dict[str, object]] = []
    worker_init_kwargs: dict[str, object] = {}

    class FakeOutput:
        def __init__(self, role: str) -> None:
            self.role = role
            self.resolution_px = (1024, 600)
            self.refresh_hz = 60.0

        def display_rgb_frame(self, _frame_rgb: np.ndarray) -> None:
            return None

        def display_gray(self, _gray_level_u8: int) -> None:
            return None

        def display_gray_frame(self, _frame_gray: np.ndarray) -> None:
            return None

        def diagnostics(self) -> dict[str, object]:
            return {"requested_connector": "HDMI-A-1" if self.role == "preview" else "HDMI-A-2"}

    class FakeController:
        def __init__(self, **_kwargs) -> None:
            self.preview = FakeOutput("preview")
            self.stimulus = FakeOutput("stimulus")

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

    class FakeWorker:
        def __init__(self, **kwargs) -> None:
            worker_init_kwargs.update(kwargs)

        def start(self) -> None:
            return None

        def snapshot(self) -> dict[str, object]:
            return {"active": False, "frame_index": 0, "update_count": 0}

        def close(self) -> None:
            return None

    def fake_compile(**kwargs) -> object:
        compile_calls.append(dict(kwargs))
        return SimpleNamespace(
            frames=np.zeros((300, 600, 1024), dtype=np.uint8),
            frame_interval_s=1.0 / 60.0,
            spec=SimpleNamespace(name="go_grating", background_gray_u8=127),
        )

    summary = run_camera_setup_preview_plus_gratings_multiprocess_smoke(
        output_root=tmp_path,
        duration_s=0.05,
        stimulus_duration_s=5.0,
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
        compile_stimulus_fn=fake_compile,
        worker_factory=FakeWorker,
        sleep_fn=lambda _seconds: None,
    )

    assert compile_calls[0]["duration_s"] == 5.0
    assert worker_init_kwargs["frame_count"] == 300
    assert summary["stimulus_duration_s"] == 5.0


def test_multiprocess_preview_plus_grating_reports_worker_start_failure_stage(
    tmp_path: Path,
) -> None:
    """Worker start failures should be labeled accurately in the summary."""

    calls: list[str] = []

    class FakeOutput:
        def __init__(self, role: str) -> None:
            self.role = role
            self.resolution_px = (1024, 600)
            self.refresh_hz = 60.0

        def display_rgb_frame(self, _frame_rgb: np.ndarray) -> None:
            calls.append("preview:rgb")

        def display_gray(self, _gray_level_u8: int) -> None:
            calls.append("stimulus:gray")

        def diagnostics(self) -> dict[str, object]:
            return {"requested_connector": "HDMI-A-1" if self.role == "preview" else "HDMI-A-2"}

    class FakeController:
        def __init__(self, **_kwargs) -> None:
            self.preview = FakeOutput("preview")
            self.stimulus = FakeOutput("stimulus")

        def diagnostics(self) -> dict[str, object]:
            return {
                "preview": self.preview.diagnostics(),
                "stimulus": self.stimulus.diagnostics(),
            }

        def close(self) -> None:
            calls.append("controller:close")

    class FakeFrameSource:
        def __init__(self, **_kwargs) -> None:
            pass

        def capture_source_frame(self) -> np.ndarray:
            return np.zeros((600, 1024, 3), dtype=np.uint8)

        def prepare_preview_frame(self, source_frame: np.ndarray) -> np.ndarray:
            return source_frame

        def diagnostics(self) -> dict[str, object]:
            return {}

        def close(self) -> None:
            calls.append("frame_source:close")

    class FailingWorker:
        def __init__(self, **_kwargs) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("worker start failed")

        def close(self) -> None:
            calls.append("worker:close")

    compiled_go = SimpleNamespace(
        frames=np.zeros((2, 600, 1024), dtype=np.uint8),
        frame_interval_s=1.0 / 60.0,
        spec=SimpleNamespace(name="go_grating", background_gray_u8=127),
    )

    with pytest.raises(RuntimeError, match="worker start failed") as exc_info:
        run_camera_setup_preview_plus_gratings_multiprocess_smoke(
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
            compile_stimulus_fn=lambda **_kwargs: compiled_go,
            worker_factory=FailingWorker,
            sleep_fn=lambda _seconds: None,
        )

    assert exc_info.value.summary["failure_stage"] == "worker_start"
    assert calls[-3:] == ["worker:close", "controller:close", "frame_source:close"]


def test_multiprocess_preview_plus_grating_reports_stimulus_display_failure_stage(
    tmp_path: Path,
) -> None:
    """Stimulus display failures after worker start should be labeled accurately."""

    calls: list[str] = []

    class FakeOutput:
        def __init__(self, role: str) -> None:
            self.role = role
            self.resolution_px = (1024, 600)
            self.refresh_hz = 60.0

        def display_rgb_frame(self, _frame_rgb: np.ndarray) -> None:
            calls.append("preview:rgb")

        def display_gray(self, _gray_level_u8: int) -> None:
            calls.append("stimulus:gray")

        def display_gray_frame(self, _frame_gray: np.ndarray) -> None:
            raise RuntimeError("stimulus display failed")

        def diagnostics(self) -> dict[str, object]:
            return {"requested_connector": "HDMI-A-1" if self.role == "preview" else "HDMI-A-2"}

    class FakeController:
        def __init__(self, **_kwargs) -> None:
            self.preview = FakeOutput("preview")
            self.stimulus = FakeOutput("stimulus")

        def diagnostics(self) -> dict[str, object]:
            return {
                "preview": self.preview.diagnostics(),
                "stimulus": self.stimulus.diagnostics(),
            }

        def close(self) -> None:
            calls.append("controller:close")

    class FakeFrameSource:
        def __init__(self, **_kwargs) -> None:
            pass

        def capture_source_frame(self) -> np.ndarray:
            return np.zeros((600, 1024, 3), dtype=np.uint8)

        def prepare_preview_frame(self, source_frame: np.ndarray) -> np.ndarray:
            return source_frame

        def diagnostics(self) -> dict[str, object]:
            return {}

        def close(self) -> None:
            calls.append("frame_source:close")

    class FakeWorker:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            calls.append("worker:start")

        def snapshot(self) -> dict[str, object]:
            return {"active": True, "frame_index": 0, "update_count": 1}

        def close(self) -> None:
            calls.append("worker:close")

    compiled_go = SimpleNamespace(
        frames=np.zeros((2, 600, 1024), dtype=np.uint8),
        frame_interval_s=1.0 / 60.0,
        spec=SimpleNamespace(name="go_grating", background_gray_u8=127),
    )

    with pytest.raises(RuntimeError, match="stimulus display failed") as exc_info:
        run_camera_setup_preview_plus_gratings_multiprocess_smoke(
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
            compile_stimulus_fn=lambda **_kwargs: compiled_go,
            worker_factory=FakeWorker,
            sleep_fn=lambda _seconds: None,
        )

    assert exc_info.value.summary["failure_stage"] == "preview_loop_stimulus_display"
    assert calls[-3:] == ["worker:close", "controller:close", "frame_source:close"]
