from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from debug.camera_preview_recording_plus_gratings_hdmi_a2_smoke import (
    run_camera_preview_recording_plus_gratings_hdmi_a2_smoke,
)
from debug.display_mode_guard import HeadlessModeStatus


def test_camera_preview_recording_plus_gratings_reports_metrics_and_output_paths(
    tmp_path: Path,
) -> None:
    """Preview+recording+gratings smoke should report recording and stimulus summaries."""

    calls: list[str] = []
    released_frames: list[str] = []

    class FakeFrame:
        def __init__(self, key: str) -> None:
            self.buffer_key = key
            self.request = object()

    class FakeOutput:
        def __init__(self, role: str, connector: str, resolution_px: tuple[int, int]) -> None:
            self.role = role
            self.connector = connector
            self.resolution_px = resolution_px
            self.refresh_hz = 60.0

        def display_dmabuf_frame(self, frame: FakeFrame) -> None:
            calls.append(f"{self.role}:dmabuf:{frame.buffer_key}")

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

    class FakeRecordingSource:
        def __init__(self, **kwargs) -> None:
            calls.append(
                "source:init:"
                f"{kwargs['camera_id']}:{kwargs['resolution_px']}:{kwargs['frame_rate_hz']}:{kwargs['sensor_mode']}:{kwargs['request_mode']}:{kwargs['overlay_enabled']}"
            )
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

        def release_frame(self, frame: FakeFrame) -> None:
            released_frames.append(frame.buffer_key)

        def diagnostics(self) -> dict[str, object]:
            return {
                "camera_id": "camera0",
                "sensor_mode": 0,
                "request_mode": "next",
                "overlay_enabled": True,
                "camera_preview_source_mode": "dmabuf_main",
                "camera_preview_transport": "dmabuf",
                "video_path": str(self._video_path),
                "timestamp_csv_path": str(self._timestamp_csv_path),
            }

        def close(self) -> None:
            calls.append("source:close")

    compiled_go = SimpleNamespace(
        frames=np.zeros((3, 600, 1024), dtype=np.uint8),
        frame_interval_s=1.0 / 60.0,
    )
    compiled_nogo = SimpleNamespace(
        frames=np.ones((3, 600, 1024), dtype=np.uint8) * 255,
        frame_interval_s=1.0 / 60.0,
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

    summary = run_camera_preview_recording_plus_gratings_hdmi_a2_smoke(
        output_root=tmp_path,
        duration_s=0.08,
        frame_rate_hz=50.0,
        sensor_mode=0,
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
        frame_source_factory=FakeRecordingSource,
        shared_controller_factory=FakeController,
        compile_stimuli_fn=lambda **_kwargs: {
            "go_grating": compiled_go,
            "nogo_grating": compiled_nogo,
        },
        monotonic_fn=lambda: next(time_points),
    )

    assert calls[0] == "source:init:camera0:(1024, 600):50.0:0:next:True"
    assert calls[1] == "controller:init:HDMI-A-1:HDMI-A-2"
    assert "source:capture_frame_for_preview" in calls
    assert any(call.startswith("preview:dmabuf:") for call in calls)
    assert any(call.startswith("stimulus:gray_frame:") for call in calls)
    assert calls[-2:] == ["controller:close", "source:close"]
    assert released_frames
    assert summary["sensor_mode"] == 0
    assert summary["request_mode"] == "next"
    assert summary["overlay_enabled"] is True
    assert summary["camera_preview_transport"] == "dmabuf"
    assert summary["preview_frame_count"] >= 1
    assert summary["stimulus_frame_update_count"] >= 1
    assert summary["video_path"].endswith(".h264")
    assert summary["timestamp_csv_path"].endswith(".csv")
    assert summary["preview_drm_diagnostics"]["requested_connector"] == "HDMI-A-1"
    assert summary["stimulus_drm_diagnostics"]["requested_connector"] == "HDMI-A-2"


def test_camera_preview_recording_plus_gratings_reports_stimulus_display_failure_stage(
    tmp_path: Path,
) -> None:
    """Stimulus display failures should be labeled accurately after recording starts."""

    class FakeFrame:
        def __init__(self) -> None:
            self.buffer_key = "frame-1"
            self.request = object()

    class FakeOutput:
        def __init__(self, role: str) -> None:
            self.role = role
            self.resolution_px = (1024, 600)
            self.refresh_hz = 60.0

        def display_dmabuf_frame(self, _frame: FakeFrame) -> None:
            return None

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
            return None

    class FakeRecordingSource:
        def __init__(self, **kwargs) -> None:
            Path(kwargs["video_path"]).write_text("fake h264", encoding="utf-8")
            Path(kwargs["timestamp_csv_path"]).write_text(
                "SensorTimestamp_ns,FrameDuration_us,UnixTimestamp_s\n",
                encoding="utf-8",
            )
            self.released_frames: list[object] = []

        def capture_frame_for_preview(self) -> FakeFrame:
            return FakeFrame()

        def release_frame(self, frame: FakeFrame) -> None:
            self.released_frames.append(frame)

        def diagnostics(self) -> dict[str, object]:
            return {}

        def close(self) -> None:
            return None

    compiled_go = SimpleNamespace(
        frames=np.zeros((2, 600, 1024), dtype=np.uint8),
        frame_interval_s=1.0 / 60.0,
    )
    compiled_nogo = SimpleNamespace(
        frames=np.ones((2, 600, 1024), dtype=np.uint8),
        frame_interval_s=1.0 / 60.0,
    )

    with pytest.raises(RuntimeError, match="stimulus display failed") as exc_info:
        run_camera_preview_recording_plus_gratings_hdmi_a2_smoke(
            output_root=tmp_path,
            duration_s=0.05,
            sensor_mode=1,
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
            frame_source_factory=FakeRecordingSource,
            shared_controller_factory=FakeController,
            compile_stimuli_fn=lambda **_kwargs: {
                "go_grating": compiled_go,
                "nogo_grating": compiled_nogo,
            },
        )

    assert exc_info.value.summary["failure_stage"] == "preview_loop_stimulus_display"
