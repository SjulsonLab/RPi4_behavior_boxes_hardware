"""Tests for the best-effort DRM preview viewer."""

from __future__ import annotations

from io import BytesIO
import threading

import numpy as np
from PIL import Image
import pytest

from box_runtime.video_recording.drm_preview_viewer import (
    DrmPreviewViewer,
    MjpegFrameDecoder,
    PreviewConnectorUnavailable,
    PreviewDisplayConfig,
    PreviewRenderer,
    start_preview_viewer_from_env,
)


def _jpeg_bytes(color_rgb: tuple[int, int, int], size_px: tuple[int, int]) -> bytes:
    """Build one small JPEG image for preview-viewer tests.

    Args:
        color_rgb: Fill color as ``(red_u8, green_u8, blue_u8)``.
        size_px: Image size as ``(width_px, height_px)``.

    Returns:
        bytes: Encoded JPEG byte string.
    """

    image = Image.new("RGB", size_px, color_rgb)
    handle = BytesIO()
    image.save(handle, format="JPEG")
    return handle.getvalue()


class _FakePreviewBackend:
    """In-memory preview backend used to verify render behavior in tests."""

    def __init__(self, config: PreviewDisplayConfig) -> None:
        self.config = config
        self.frames: list[np.ndarray] = []
        self.black_count = 0

    def display_frame(self, frame_rgb: np.ndarray) -> None:
        self.frames.append(np.array(frame_rgb, copy=True))

    def display_black(self) -> None:
        width_px, height_px = self.config.resolution_px
        self.frames.append(np.zeros((height_px, width_px, 3), dtype=np.uint8))
        self.black_count += 1

    def close(self) -> None:
        return None


def test_mjpeg_frame_decoder_extracts_jpegs_and_ignores_malformed_chunks() -> None:
    """The MJPEG decoder should yield complete JPEGs and ignore non-image bytes.

    Returns:
        None.
    """

    decoder = MjpegFrameDecoder()
    jpeg_bytes = _jpeg_bytes((255, 0, 0), (4, 2))

    assert decoder.feed(b"garbage without markers") == []
    assert decoder.feed(jpeg_bytes[:7]) == []

    frames = decoder.feed(jpeg_bytes[7:] + b"trailing-noise")

    assert len(frames) == 1
    assert frames[0].startswith(b"\xff\xd8")
    assert frames[0].endswith(b"\xff\xd9")


def test_preview_renderer_uses_latest_frame_and_letterboxes() -> None:
    """Rendering should prefer the newest pending frame and preserve aspect ratio.

    Returns:
        None.
    """

    config = PreviewDisplayConfig(
        connector="HDMI-A-2",
        resolution_px=(8, 8),
        stream_url="http://127.0.0.1:8000/stream.mjpg",
        max_preview_hz=10.0,
        stall_timeout_s=0.5,
    )
    backend = _FakePreviewBackend(config)
    renderer = PreviewRenderer(config=config, backend=backend)

    renderer.submit_jpeg_frame(_jpeg_bytes((255, 0, 0), (8, 4)), received_time_s=0.0)
    renderer.submit_jpeg_frame(_jpeg_bytes((0, 255, 0), (8, 4)), received_time_s=0.05)
    renderer.render_pending(now_s=0.2)

    assert len(backend.frames) == 1
    frame = backend.frames[0]
    assert frame.shape == (8, 8, 3)
    assert np.all(frame[0] == 0)
    assert np.all(frame[-1] == 0)
    assert frame[4, 4, 1] > frame[4, 4, 0]


def test_preview_renderer_stalls_to_black() -> None:
    """A stalled stream should eventually replace the last preview with black.

    Returns:
        None.
    """

    config = PreviewDisplayConfig(
        connector="HDMI-A-2",
        resolution_px=(8, 8),
        stream_url="http://127.0.0.1:8000/stream.mjpg",
        max_preview_hz=10.0,
        stall_timeout_s=0.25,
    )
    backend = _FakePreviewBackend(config)
    renderer = PreviewRenderer(config=config, backend=backend)

    renderer.submit_jpeg_frame(_jpeg_bytes((0, 0, 255), (8, 8)), received_time_s=0.0)
    renderer.render_pending(now_s=0.0)
    renderer.render_pending(now_s=0.1)
    renderer.render_pending(now_s=0.4)

    assert len(backend.frames) == 2
    assert backend.black_count == 1
    assert np.all(backend.frames[-1] == 0)


def test_start_preview_viewer_from_env_disables_missing_connector(monkeypatch) -> None:
    """Preview startup should fail non-fatally if the preview connector is missing.

    Inputs:
        monkeypatch: pytest fixture used to set preview environment variables.

    Returns:
        None.
    """

    monkeypatch.setenv("CAMERA_PREVIEW_DRM_ENABLE", "1")
    monkeypatch.setenv("CAMERA_PREVIEW_DRM_CONNECTOR", "HDMI-A-9")

    def failing_backend_factory(config: PreviewDisplayConfig):
        raise PreviewConnectorUnavailable(f"missing {config.connector}")

    handle = start_preview_viewer_from_env(
        port=8123,
        backend_factory=failing_backend_factory,
        opener=lambda url, timeout: BytesIO(),
    )

    assert handle is None


def test_drm_preview_viewer_opens_local_stream_url() -> None:
    """Viewer startup should target the configured local MJPEG preview URL.

    Returns:
        None.
    """

    observed: dict[str, object] = {}

    class _SingleFrameStream(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def opener(url: str, timeout: float):
        observed["url"] = url
        observed["timeout"] = timeout
        return _SingleFrameStream(_jpeg_bytes((12, 34, 56), (2, 2)))

    config = PreviewDisplayConfig(
        connector="HDMI-A-2",
        resolution_px=(8, 8),
        stream_url="http://127.0.0.1:8123/stream.mjpg",
        max_preview_hz=30.0,
        stall_timeout_s=0.5,
    )
    viewer = DrmPreviewViewer(
        config=config,
        backend_factory=_FakePreviewBackend,
        opener=opener,
        reconnect_sleep_s=0.0,
        open_timeout_s=0.2,
        read_chunk_size=4096,
    )
    stop_event = threading.Event()
    stop_event.set()

    viewer.run(stop_event=stop_event)

    assert observed["url"] == "http://127.0.0.1:8123/stream.mjpg"

