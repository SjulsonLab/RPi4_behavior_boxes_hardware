from __future__ import annotations

import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from box_runtime.behavior.shared_experiment_media_runtime import (
    SharedDrmExperimentMediaRuntime,
)


def _session_info(
    base_dir: str,
    grating_path: Path,
    *,
    camera_ids: list[str] | None = None,
) -> dict[str, object]:
    resolved_camera_ids = ["camera0", "camera1"] if camera_ids is None else list(camera_ids)
    return {
        "external_storage": base_dir,
        "basename": "test_session",
        "dir_name": str(Path(base_dir) / "run"),
        "mouse_name": "mouseA",
        "datetime": "2026-05-13_120000",
        "box_name": "test_box",
        "gray_level": 96,
        "visual_stimulus": True,
        "visual_display_connector": "HDMI-A-2",
        "visual_display_degrees_subtended": 80.0,
        "camera_enabled": True,
        "camera_ids": resolved_camera_ids,
        "camera_preview_connector": "HDMI-A-1",
        "camera_preview_max_hz": 15.0,
        "camera_recording_enabled": True,
        "experiment_media_backend": "shared_drm",
        "experiment_media_preview_camera_id": "camera0",
        "experiment_media_preview_source_mode": "dmabuf_main",
        "experiment_media_request_mode": "next",
        "vis_gratings": [str(grating_path)],
    }


class _FakeFrame:
    def __init__(self, *, width_px: int = 8, height_px: int = 6) -> None:
        self.pixel_format = "XBGR8888"
        self.width_px = width_px
        self.height_px = height_px
        self.plane_fds = (57,)
        self.strides_bytes = (width_px * 4,)
        self.offsets_bytes = (0,)
        self.buffer_key = ("buffer", width_px, height_px)


class _FakeFrameSource:
    instances: list["_FakeFrameSource"] = []

    def __init__(
        self,
        *,
        camera_id: str,
        resolution_px: tuple[int, int],
        video_path: Path,
        timestamp_csv_path: Path,
        frame_rate_hz: float,
        sensor_mode: int,
        request_mode: str,
        overlay_enabled: bool,
    ) -> None:
        self.camera_id = camera_id
        self.resolution_px = resolution_px
        self.video_path = Path(video_path)
        self.timestamp_csv_path = Path(timestamp_csv_path)
        self.frame_rate_hz = float(frame_rate_hz)
        self.sensor_mode = int(sensor_mode)
        self.request_mode = str(request_mode)
        self.overlay_enabled = bool(overlay_enabled)
        self.calls: list[object] = []
        self._frame_counter = 0
        self._released_frames: list[object] = []
        self.closed = False
        _FakeFrameSource.instances.append(self)

    def capture_frame_for_preview(self) -> _FakeFrame:
        self._frame_counter += 1
        frame = _FakeFrame()
        frame.buffer_key = ("buffer", self._frame_counter)
        self.calls.append(("capture", frame.buffer_key))
        return frame

    def release_frame(self, frame: _FakeFrame) -> None:
        self._released_frames.append(frame.buffer_key)
        self.calls.append(("release", frame.buffer_key))

    def diagnostics(self) -> dict[str, object]:
        return {
            "request_mode": self.request_mode,
            "camera_preview_source_mode": "dmabuf_main",
            "sensor_mode": self.sensor_mode,
            "video_path": str(self.video_path),
            "timestamp_csv_path": str(self.timestamp_csv_path),
            "overlay_enabled": self.overlay_enabled,
        }

    def close(self) -> None:
        self.closed = True
        self.calls.append("close")


class _FakeDualFrameSource:
    instances: list["_FakeDualFrameSource"] = []

    def __init__(
        self,
        *,
        output_root: Path,
        preview_camera_id: str,
        recording_camera_id: str,
        resolution_px: tuple[int, int],
        frame_rate_hz: float,
        sensor_mode: int,
        request_mode: str,
        preview_overlay_enabled: bool,
        recording_overlay_enabled: bool,
    ) -> None:
        self.output_root = Path(output_root)
        self.preview_camera_id = str(preview_camera_id)
        self.recording_camera_id = str(recording_camera_id)
        self.resolution_px = resolution_px
        self.frame_rate_hz = float(frame_rate_hz)
        self.sensor_mode = int(sensor_mode)
        self.request_mode = str(request_mode)
        self.preview_overlay_enabled = bool(preview_overlay_enabled)
        self.recording_overlay_enabled = bool(recording_overlay_enabled)
        self.calls: list[object] = []
        self._frame_counter = 0
        self.closed = False
        _FakeDualFrameSource.instances.append(self)

    def capture_frame_for_preview(self) -> _FakeFrame:
        self._frame_counter += 1
        frame = _FakeFrame()
        frame.buffer_key = ("dual-buffer", self._frame_counter)
        self.calls.append(("capture", frame.buffer_key))
        return frame

    def release_frame(self, frame: _FakeFrame) -> None:
        self.calls.append(("release", frame.buffer_key))

    def diagnostics(self) -> dict[str, object]:
        return {
            "preview_camera_id": self.preview_camera_id,
            "recording_camera_id": self.recording_camera_id,
            "camera0_overlay_enabled": self.preview_overlay_enabled,
            "camera1_overlay_enabled": self.recording_overlay_enabled,
            "camera0_video_path": str(self.output_root / "camera0_preview_recording_output.h264"),
            "camera0_timestamp_csv_path": str(self.output_root / "camera0_preview_recording_timestamp.csv"),
            "camera1_video_path": str(self.output_root / "camera1_recording_output.h264"),
            "camera1_timestamp_csv_path": str(self.output_root / "camera1_recording_timestamp.csv"),
            "camera0_timestamp_sample_count": 11,
            "camera1_timestamp_sample_count": 13,
            "request_mode": self.request_mode,
            "camera_preview_source_mode": "dmabuf_main",
        }

    def close(self) -> None:
        self.closed = True
        self.calls.append("close")


class _FakePreviewOutput:
    def __init__(self) -> None:
        self.resolution_px = (8, 6)
        self.frames: list[tuple[str, object]] = []

    def display_dmabuf_frame(self, frame) -> None:
        self.frames.append(("dmabuf", frame.buffer_key))

    def diagnostics(self) -> dict[str, object]:
        return {"requested_connector": "HDMI-A-1"}


class _FakeStimulusOutput:
    def __init__(self) -> None:
        self.resolution_px = (8, 6)
        self.refresh_hz = 60.0
        self.frames: list[tuple[str, object]] = []

    def display_gray(self, gray_level_u8: int) -> None:
        self.frames.append(("gray", int(gray_level_u8)))

    def display_gray_frame(self, frame_gray_u8) -> None:
        self.frames.append(("frame", tuple(frame_gray_u8.shape)))

    def diagnostics(self) -> dict[str, object]:
        return {"requested_connector": "HDMI-A-2"}


class _FakeController:
    def __init__(self, *, preview_connector: str, stimulus_connector: str) -> None:
        assert preview_connector == "HDMI-A-1"
        assert stimulus_connector == "HDMI-A-2"
        self.preview = _FakePreviewOutput()
        self.stimulus = _FakeStimulusOutput()
        self.closed = False

    def diagnostics(self) -> dict[str, object]:
        return {
            "preview": self.preview.diagnostics(),
            "stimulus": self.stimulus.diagnostics(),
        }

    def close(self) -> None:
        self.closed = True


def _fake_frame_source_factory(
    *,
    camera_id: str,
    resolution_px: tuple[int, int],
    video_path: Path,
    timestamp_csv_path: Path,
    frame_rate_hz: float,
    sensor_mode: int,
    request_mode: str,
    overlay_enabled: bool,
) -> _FakeFrameSource:
    return _FakeFrameSource(
        camera_id=camera_id,
        resolution_px=resolution_px,
        video_path=video_path,
        timestamp_csv_path=timestamp_csv_path,
        frame_rate_hz=frame_rate_hz,
        sensor_mode=sensor_mode,
        request_mode=request_mode,
        overlay_enabled=overlay_enabled,
    )


def _fake_dual_frame_source_factory(
    *,
    output_root: Path,
    preview_camera_id: str,
    recording_camera_id: str,
    resolution_px: tuple[int, int],
    frame_rate_hz: float,
    sensor_mode: int,
    request_mode: str,
    preview_overlay_enabled: bool,
    recording_overlay_enabled: bool,
) -> _FakeDualFrameSource:
    return _FakeDualFrameSource(
        output_root=output_root,
        preview_camera_id=preview_camera_id,
        recording_camera_id=recording_camera_id,
        resolution_px=resolution_px,
        frame_rate_hz=frame_rate_hz,
        sensor_mode=sensor_mode,
        request_mode=request_mode,
        preview_overlay_enabled=preview_overlay_enabled,
        recording_overlay_enabled=recording_overlay_enabled,
    )


def test_shared_experiment_media_runtime_uses_dmabuf_recording_source_and_next_request_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        grating_path = Path(tmp) / "test_grating.yaml"
        grating_path.write_text(
            "\n".join(
                [
                    "name: test_grating",
                    "duration_s: 0.02",
                    "angle_deg: 45.0",
                    "spatial_freq_cpd: 0.08",
                    "temporal_freq_hz: 1.0",
                    "contrast: 0.9",
                    "background_gray_u8: 96",
                    "waveform: sine",
                    "resolution_px: [8, 6]",
                    "degrees_subtended: 80.0",
                ]
            ),
            encoding="utf-8",
        )
        runtime = SharedDrmExperimentMediaRuntime(
            _session_info(tmp, grating_path, camera_ids=["camera0"]),
            frame_source_factory=_fake_frame_source_factory,
            controller_factory=_FakeController,
        )

        runtime.prepare()
        runtime.start_session(owner="automated")
        time.sleep(0.05)
        runtime.show_grating("test_grating")
        time.sleep(0.10)
        state = runtime.runtime_state()
        controller = runtime.controller
        frame_source = runtime.frame_source
        runtime.stop_session()
        runtime.close()

        assert frame_source is not None
        assert frame_source.request_mode == "next"
        assert frame_source.overlay_enabled is True
        assert controller is not None
        assert any(entry[0] == "dmabuf" for entry in controller.preview.frames)
        assert any(entry[0] == "frame" for entry in controller.stimulus.frames)
        assert any(call[0] == "release" for call in frame_source.calls if isinstance(call, tuple))
        assert state["camera0"]["preview_mode"] == "shared_drm_dmabuf"
        assert state["camera0"]["preview_connector"] == "HDMI-A-1"
        assert state["camera0"]["stimulus_drm_diagnostics"]["requested_connector"] == "HDMI-A-2"


def test_shared_experiment_media_runtime_can_restore_gray_on_demand() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        grating_path = Path(tmp) / "test_grating.yaml"
        grating_path.write_text(
            "\n".join(
                [
                    "name: test_grating",
                    "duration_s: 0.02",
                    "angle_deg: 45.0",
                    "spatial_freq_cpd: 0.08",
                    "temporal_freq_hz: 1.0",
                    "contrast: 0.9",
                    "background_gray_u8: 96",
                    "waveform: sine",
                    "resolution_px: [8, 6]",
                    "degrees_subtended: 80.0",
                ]
            ),
            encoding="utf-8",
        )
        runtime = SharedDrmExperimentMediaRuntime(
            _session_info(tmp, grating_path, camera_ids=["camera0"]),
            frame_source_factory=_fake_frame_source_factory,
            controller_factory=_FakeController,
        )

        runtime.prepare()
        runtime.display_gray()
        controller = runtime.controller
        runtime.close()

        assert controller is not None
        assert controller.stimulus.frames[-1] == ("gray", 96)


def test_shared_experiment_media_runtime_display_gray_clears_pending_grating_queue() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        grating_path = Path(tmp) / "test_grating.yaml"
        grating_path.write_text(
            "\n".join(
                [
                    "name: test_grating",
                    "duration_s: 0.02",
                    "angle_deg: 45.0",
                    "spatial_freq_cpd: 0.08",
                    "temporal_freq_hz: 1.0",
                    "contrast: 0.9",
                    "background_gray_u8: 96",
                    "waveform: sine",
                    "resolution_px: [8, 6]",
                    "degrees_subtended: 80.0",
                ]
            ),
            encoding="utf-8",
        )
        runtime = SharedDrmExperimentMediaRuntime(
            _session_info(tmp, grating_path, camera_ids=["camera0"]),
            frame_source_factory=_fake_frame_source_factory,
            controller_factory=_FakeController,
        )

        runtime.prepare()
        runtime.show_grating("test_grating")
        runtime.show_grating("test_grating")
        runtime.display_gray()
        queued_names = list(runtime._queued_gratings)
        runtime.close()

        assert queued_names == []


def test_shared_experiment_media_runtime_display_gray_interrupts_in_progress_grating() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        grating_path = Path(tmp) / "test_grating.yaml"
        grating_path.write_text(
            "\n".join(
                [
                    "name: test_grating",
                    "duration_s: 0.02",
                    "angle_deg: 45.0",
                    "spatial_freq_cpd: 0.08",
                    "temporal_freq_hz: 1.0",
                    "contrast: 0.9",
                    "background_gray_u8: 96",
                    "waveform: sine",
                    "resolution_px: [8, 6]",
                    "degrees_subtended: 80.0",
                ]
            ),
            encoding="utf-8",
        )
        runtime = SharedDrmExperimentMediaRuntime(
            _session_info(tmp, grating_path, camera_ids=["camera0"]),
            frame_source_factory=_fake_frame_source_factory,
            controller_factory=_FakeController,
        )

        runtime.prepare()
        runtime._compiled_stimuli["test_grating"] = SimpleNamespace(
            frames=[np.full((6, 8), fill_value=index, dtype=np.uint8) for index in range(6)],
            frame_interval_s=0.03,
        )
        runtime.start_session(owner="automated")
        runtime.show_grating("test_grating")

        controller = runtime.controller
        assert controller is not None
        deadline_s = time.time() + 1.0
        while time.time() < deadline_s:
            if any(entry[0] == "frame" for entry in controller.stimulus.frames):
                break
            time.sleep(0.01)
        else:
            runtime.stop_session()
            runtime.close()
            raise AssertionError("Timed out waiting for the first stimulus frame.")

        runtime.display_gray()
        time.sleep(0.03)
        frame_count_after_interrupt = len([entry for entry in controller.stimulus.frames if entry[0] == "frame"])
        time.sleep(0.12)
        frame_count_final = len([entry for entry in controller.stimulus.frames if entry[0] == "frame"])
        runtime.stop_session()
        runtime.close()

        assert frame_count_final == frame_count_after_interrupt
        assert controller.stimulus.frames[-1] == ("gray", 96)


def test_shared_experiment_media_runtime_uses_camera0_preview_and_camera1_recording_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        grating_path = Path(tmp) / "test_grating.yaml"
        grating_path.write_text(
            "\n".join(
                [
                    "name: test_grating",
                    "duration_s: 0.02",
                    "angle_deg: 45.0",
                    "spatial_freq_cpd: 0.08",
                    "temporal_freq_hz: 1.0",
                    "contrast: 0.9",
                    "background_gray_u8: 96",
                    "waveform: sine",
                    "resolution_px: [8, 6]",
                    "degrees_subtended: 80.0",
                ]
            ),
            encoding="utf-8",
        )
        runtime = SharedDrmExperimentMediaRuntime(
            _session_info(tmp, grating_path),
            frame_source_factory=_fake_frame_source_factory,
            dual_camera_source_factory=_fake_dual_frame_source_factory,
            controller_factory=_FakeController,
        )

        runtime.prepare()
        runtime.start_session(owner="automated")
        time.sleep(0.05)
        state = runtime.runtime_state()
        frame_source = runtime.frame_source
        runtime.stop_session()
        runtime.close()

        assert isinstance(frame_source, _FakeDualFrameSource)
        assert frame_source.preview_camera_id == "camera0"
        assert frame_source.recording_camera_id == "camera1"
        assert state["camera0"]["preview_mode"] == "shared_drm_dmabuf"
        assert state["camera0"]["preview_active"] is True
        assert state["camera1"]["preview_mode"] == "off"
        assert state["camera1"]["preview_active"] is False
        assert state["camera1"]["recording"] is True
        assert state["camera1"]["video_path"].endswith("camera1_recording_output.h264")
        assert state["camera1"]["timestamp_csv_path"].endswith("camera1_recording_timestamp.csv")


def test_shared_experiment_media_runtime_rejects_unknown_grating_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        grating_path = Path(tmp) / "test_grating.yaml"
        grating_path.write_text(
            "\n".join(
                [
                    "name: test_grating",
                    "duration_s: 0.02",
                    "angle_deg: 45.0",
                    "spatial_freq_cpd: 0.08",
                    "temporal_freq_hz: 1.0",
                    "contrast: 0.9",
                    "background_gray_u8: 96",
                    "waveform: sine",
                    "resolution_px: [8, 6]",
                    "degrees_subtended: 80.0",
                ]
            ),
            encoding="utf-8",
        )
        runtime = SharedDrmExperimentMediaRuntime(
            _session_info(tmp, grating_path),
            frame_source_factory=_fake_frame_source_factory,
            controller_factory=_FakeController,
        )

        runtime.prepare()

        try:
            runtime.show_grating("missing_grating")
        except KeyError as exc:
            assert "missing_grating" in str(exc)
        else:
            raise AssertionError("missing grating should raise KeyError")

        runtime.close()
