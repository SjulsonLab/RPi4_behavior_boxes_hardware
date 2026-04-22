from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from debug.shared_camera_frame_source import (
    SharedCameraFrameSource,
    _letterbox_rgb_frame_local,
)


def test_shared_camera_frame_source_configures_camera0_and_returns_rgb_frame(tmp_path: Path) -> None:
    """The default frame source should use the main RGB stream for preview.

    Args:
        tmp_path: Unused pytest temporary path kept for fixture consistency.

    Returns:
        None: Assertions validate camera configuration and returned frame shape.
    """

    del tmp_path
    calls: list[tuple[str, object]] = []

    class FakePicamera2:
        def __init__(self, *, camera_num: int) -> None:
            calls.append(("init", camera_num))

        def create_video_configuration(self, **kwargs):
            calls.append(("create_video_configuration", kwargs))
            return {"configured": True, **kwargs}

        def configure(self, configuration) -> None:
            calls.append(("configure", configuration))

        def start(self) -> None:
            calls.append(("start", None))

        def capture_array(self, name: str = "main"):
            calls.append(("capture_array", name))
            return np.zeros((480, 640, 3), dtype=np.uint8)

        def stop(self) -> None:
            calls.append(("stop", None))

        def close(self) -> None:
            calls.append(("close", None))

    source = SharedCameraFrameSource(
        camera_id="camera0",
        resolution_px=(640, 480),
        acquisition_resolution_px=(640, 480),
        preview_stream_resolution_px=(640, 480),
        frame_rate_hz=30.0,
        picamera2_factory=FakePicamera2,
    )
    frame_rgb = source.capture_rgb_frame()
    source.close()

    assert frame_rgb.shape == (480, 640, 3)
    assert frame_rgb.dtype == np.uint8
    assert source.preview_source_mode == "rgb_main"
    assert source.acquisition_resolution_px == (640, 480)
    assert source.preview_stream_resolution_px == (640, 480)
    assert source.preview_rgb_resolution_px == (640, 480)
    assert calls[0] == ("init", 0)
    assert calls[1][0] == "create_video_configuration"
    assert calls[1][1]["main"] == {"size": (640, 480), "format": "RGB888"}
    assert calls[1][1]["lores"] is None
    assert calls[1][1]["controls"] == {"FrameRate": 30.0}
    assert ("capture_array", "main") in calls
    assert calls[-2:] == [("stop", None), ("close", None)]


def test_shared_camera_frame_source_closes_after_partial_start_failure() -> None:
    """The frame source should close cleanly if camera start raises.

    Returns:
        None: Assertions validate cleanup after startup failure.
    """

    calls: list[str] = []

    class FailingPicamera2:
        def __init__(self, *, camera_num: int) -> None:
            calls.append(f"init:{camera_num}")

        def create_video_configuration(self, **kwargs):
            calls.append("create_config")
            return kwargs

        def configure(self, _configuration) -> None:
            calls.append("configure")

        def start(self) -> None:
            calls.append("start")
            raise RuntimeError("camera start failed")

        def stop(self) -> None:
            calls.append("stop")

        def close(self) -> None:
            calls.append("close")

    with pytest.raises(RuntimeError, match="camera start failed"):
        SharedCameraFrameSource(
            camera_id="camera0",
            resolution_px=(640, 480),
            acquisition_resolution_px=(640, 480),
            preview_stream_resolution_px=(640, 480),
            picamera2_factory=FailingPicamera2,
        )

    assert calls == ["init:0", "create_config", "configure", "start", "stop", "close"]


def test_shared_camera_frame_source_can_use_optional_yuv_lores_preview_mode() -> None:
    """Frame source should preserve the optional YUV/lores preview path for later experiments."""

    configuration_kwargs: dict[str, object] = {}

    class FakePicamera2:
        def __init__(self, *, camera_num: int) -> None:
            assert camera_num == 0

        def create_video_configuration(self, **kwargs):
            configuration_kwargs.update(kwargs)
            return kwargs

        def configure(self, _configuration) -> None:
            return None

        def start(self) -> None:
            return None

        def capture_array(self, name: str = "main"):
            assert name == "lores"
            return np.zeros((720, 640), dtype=np.uint8)

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

    source = SharedCameraFrameSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        acquisition_resolution_px=(1024, 600),
        preview_stream_resolution_px=(640, 480),
        preview_source_mode="yuv_lores",
        frame_rate_hz=30.0,
        picamera2_factory=FakePicamera2,
        yuv420_to_rgb_fn=lambda frame_yuv, size: np.zeros((240, 320, 3), dtype=np.uint8),
    )
    source.close()

    assert source.preview_source_mode == "yuv_lores"
    assert configuration_kwargs["main"] == {"size": (1024, 600), "format": "RGB888"}
    assert configuration_kwargs["lores"] == {"size": (640, 480), "format": "YUV420"}


def test_shared_camera_frame_source_exposes_capture_and_prepare_steps() -> None:
    """Frame source should expose separate source-capture and preview-prep helpers."""

    calls: list[tuple[str, object]] = []

    class FakePicamera2:
        def __init__(self, *, camera_num: int) -> None:
            calls.append(("init", camera_num))

        def create_video_configuration(self, **kwargs):
            calls.append(("create_video_configuration", kwargs))
            return kwargs

        def configure(self, _configuration) -> None:
            calls.append(("configure", None))

        def start(self) -> None:
            calls.append(("start", None))

        def capture_array(self, name: str = "main"):
            calls.append(("capture_array", name))
            return np.full((480, 640, 3), 17, dtype=np.uint8)

        def stop(self) -> None:
            calls.append(("stop", None))

        def close(self) -> None:
            calls.append(("close", None))

    source = SharedCameraFrameSource(
        camera_id="camera0",
        resolution_px=(640, 480),
        acquisition_resolution_px=(640, 480),
        frame_rate_hz=30.0,
        picamera2_factory=FakePicamera2,
    )

    raw_frame = source.capture_source_frame()
    preview_frame = source.prepare_preview_frame(raw_frame)
    source.close()

    assert raw_frame.shape == (480, 640, 3)
    assert preview_frame.shape == (480, 640, 3)
    assert preview_frame.dtype == np.uint8
    assert ("capture_array", "main") in calls


def test_letterbox_rgb_frame_local_resizes_to_target_shape() -> None:
    """Local letterbox helper should return a uint8 RGB frame at target size.

    Returns:
        None: Assertions validate shape, dtype, and nonzero content placement.
    """

    source_frame = np.full((3, 5, 3), 200, dtype=np.uint8)

    letterboxed = _letterbox_rgb_frame_local(source_frame, (10, 8))

    assert letterboxed.shape == (8, 10, 3)
    assert letterboxed.dtype == np.uint8
    assert int(letterboxed.max()) == 200
