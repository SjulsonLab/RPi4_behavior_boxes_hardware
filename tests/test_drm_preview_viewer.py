"""Tests for the best-effort DRM preview viewer."""

from __future__ import annotations

from io import BytesIO
import threading

import numpy as np
from PIL import Image
import pytest

from box_runtime.video_recording.drm_preview_viewer import (
    _PykmsPreviewBackend,
    DirectJpegPreviewViewer,
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


def test_direct_preview_viewer_recovers_by_reinitializing_backend() -> None:
    """Direct preview should reinitialize the backend after repeated DRM errors.

    Returns:
        None.
    """

    config = PreviewDisplayConfig(
        connector="HDMI-A-1",
        resolution_px=(8, 8),
        stream_url="",
        max_preview_hz=60.0,
        stall_timeout_s=0.5,
    )

    class _FailThenOkBackend:
        def __init__(self, _config: PreviewDisplayConfig, attempt: int) -> None:
            self.attempt = attempt
            self.frames: list[np.ndarray] = []
            self.closed = False

        def display_frame(self, frame_rgb: np.ndarray) -> None:
            if self.attempt == 0:
                raise RuntimeError("preview atomic mode set failed with -13")
            self.frames.append(np.array(frame_rgb, copy=True))

        def display_black(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    backends: list[_FailThenOkBackend] = []

    def backend_factory(local_config: PreviewDisplayConfig) -> _FailThenOkBackend:
        backend = _FailThenOkBackend(local_config, attempt=len(backends))
        backends.append(backend)
        return backend

    stop_event = threading.Event()
    frame_call_count = 0
    frame_bytes = _jpeg_bytes((100, 120, 140), (8, 8))

    def frame_provider() -> bytes:
        nonlocal frame_call_count
        frame_call_count += 1
        if frame_call_count >= 24:
            stop_event.set()
        return frame_bytes

    viewer = DirectJpegPreviewViewer(
        config=config,
        frame_provider=frame_provider,
        backend_factory=backend_factory,
        poll_interval_s=0.0005,
        max_consecutive_errors_before_reinit=1,
        runtime_retry_backoff_s=0.0,
    )
    viewer.run(stop_event=stop_event)

    assert len(backends) >= 2
    assert backends[0].closed is True
    assert len(backends[-1].frames) >= 1


def test_direct_preview_viewer_throttles_repeated_error_logs(caplog) -> None:
    """Direct preview should throttle repeated identical render failures.

    Inputs:
        caplog: pytest fixture that captures log records.

    Returns:
        None.
    """

    config = PreviewDisplayConfig(
        connector="HDMI-A-1",
        resolution_px=(8, 8),
        stream_url="",
        max_preview_hz=60.0,
        stall_timeout_s=0.5,
    )
    stop_event = threading.Event()
    frame_call_count = 0
    frame_bytes = _jpeg_bytes((10, 20, 30), (8, 8))

    class _AlwaysFailBackend:
        def __init__(self, _config: PreviewDisplayConfig) -> None:
            self.closed = False

        def display_frame(self, frame_rgb: np.ndarray) -> None:
            raise RuntimeError("preview atomic mode set failed with -13")

        def display_black(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    def frame_provider() -> bytes:
        nonlocal frame_call_count
        frame_call_count += 1
        if frame_call_count >= 32:
            stop_event.set()
        return frame_bytes

    viewer = DirectJpegPreviewViewer(
        config=config,
        frame_provider=frame_provider,
        backend_factory=_AlwaysFailBackend,
        poll_interval_s=0.0005,
        max_consecutive_errors_before_reinit=1000,
    )
    with caplog.at_level("WARNING"):
        viewer.run(stop_event=stop_event)

    warning_messages = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert frame_call_count >= 32
    assert len(warning_messages) <= 8


def test_pykms_preview_backend_reports_connector_crtc_and_plane_diagnostics() -> None:
    """The DRM preview backend should expose lightweight resource diagnostics.

    Returns:
        None.
    """

    backend = object.__new__(_PykmsPreviewBackend)
    backend.connector = "HDMI-A-1"
    backend.conn = type("Conn", (), {"id": 11, "fullname": "HDMI-A-1"})()
    backend.crtc = type("Crtc", (), {"id": 22})()
    backend._plane = type("Plane", (), {"id": 33})()
    backend._mode_set = True
    backend._last_commit_stage = "page_flip"
    backend._last_commit_error = "preview atomic page flip failed with -13"
    backend._last_request_summary = {
        "commit_kind": "atomic",
        "allow_modeset": False,
        "framebuffer_id": 44,
        "object_properties": {
            "crtc_primary_plane": {"FB_ID": 44},
        },
    }

    diagnostics = backend.diagnostics()

    assert diagnostics["backend"] == "drm_preview"
    assert diagnostics["requested_connector"] == "HDMI-A-1"
    assert diagnostics["reserved_connector_id"] == 11
    assert diagnostics["reserved_connector_name"] == "HDMI-A-1"
    assert diagnostics["reserved_crtc_id"] == 22
    assert diagnostics["reserved_plane_id"] == 33
    assert diagnostics["mode_set_done"] is True
    assert diagnostics["last_commit_stage"] == "page_flip"
    assert diagnostics["last_commit_error"] == "preview atomic page flip failed with -13"
    assert diagnostics["last_request"]["framebuffer_id"] == 44
    assert diagnostics["last_request"]["allow_modeset"] is False


def test_pykms_preview_backend_records_modeset_request_summary(monkeypatch) -> None:
    """Preview modesets should preserve the submitted atomic request summary.

    Args:
        monkeypatch: Pytest fixture used to patch atomic request creation.
    """

    recorded_calls: list[tuple[object, object, object]] = []

    class FakeAtomicReq:
        def __init__(self, card: object) -> None:
            self.card = card

        def add(self, obj: object, prop: object, value: object | None = None) -> None:
            recorded_calls.append((obj, prop, value))

        def commit(self, allow_modeset: bool = False) -> int:
            return 0

    class FakeModeBlob:
        id = 71

    backend = object.__new__(_PykmsPreviewBackend)
    backend._pykms = type("FakePykms", (), {"AtomicReq": FakeAtomicReq})
    backend.connector = "HDMI-A-1"
    backend.card = object()
    backend.conn = type("Conn", (), {"id": 11, "fullname": "HDMI-A-1"})()
    backend.crtc = type("Crtc", (), {"id": 22})()
    backend.mode = type(
        "Mode",
        (),
        {
            "hdisplay": 800,
            "vdisplay": 600,
            "to_blob": lambda self, _card: FakeModeBlob(),
        },
    )()
    backend._plane = type("Plane", (), {"id": 33})()
    backend._mode_set = False
    backend._last_commit_error = None
    backend._last_commit_stage = None
    backend._last_request_summary = {}

    framebuffer = type("Framebuffer", (), {"id": 44, "width": 800, "height": 600})()

    backend._flip(framebuffer)

    diagnostics = backend.diagnostics()
    assert diagnostics["last_request"]["allow_modeset"] is True
    assert diagnostics["last_request"]["framebuffer_id"] == 44
    assert diagnostics["last_request"]["object_properties"]["connector"]["CRTC_ID"] == 22
    assert diagnostics["last_request"]["object_properties"]["crtc"]["ACTIVE"] == 1
    assert diagnostics["last_request"]["object_properties"]["plane"]["FB_ID"] == 44


def test_direct_preview_viewer_state_dict_includes_backend_diagnostics() -> None:
    """Preview viewer state snapshots should include backend resource diagnostics.

    Returns:
        None.
    """

    config = PreviewDisplayConfig(
        connector="HDMI-A-1",
        resolution_px=(8, 8),
        stream_url="",
        max_preview_hz=60.0,
        stall_timeout_s=0.5,
    )

    class _DiagnosticBackend:
        def __init__(self, _config: PreviewDisplayConfig) -> None:
            return None

        def diagnostics(self) -> dict[str, object]:
            return {
                "backend": "drm_preview",
                "requested_connector": "HDMI-A-1",
                "reserved_crtc_id": 22,
                "reserved_plane_id": 33,
            }

        def close(self) -> None:
            return None

    viewer = DirectJpegPreviewViewer(
        config=config,
        frame_provider=lambda: None,
        backend_factory=_DiagnosticBackend,
    )

    viewer._ensure_runtime()
    state = viewer.state_dict()

    assert state["preview_connector"] == "HDMI-A-1"
    assert state["drm_diagnostics"]["reserved_crtc_id"] == 22
    assert state["drm_diagnostics"]["reserved_plane_id"] == 33
