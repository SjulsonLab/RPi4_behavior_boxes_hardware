"""Best-effort DRM/KMS preview viewer for the local camera service."""

from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO
import logging
import os
import selectors
import threading
import time
from typing import Any, Callable
from urllib.request import urlopen

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class PreviewDisplayConfig:
    """Preview output and stream settings.

    Attributes:
        connector: DRM connector name such as ``"HDMI-A-2"``.
        resolution_px: Preview output resolution as ``(width_px, height_px)``.
        stream_url: MJPEG stream URL exposed by the local camera service.
        max_preview_hz: Maximum preview refresh rate in frames per second.
        stall_timeout_s: Seconds without a new frame before blacking the display.
    """

    connector: str
    resolution_px: tuple[int, int]
    stream_url: str
    max_preview_hz: float
    stall_timeout_s: float


class PreviewConnectorUnavailable(RuntimeError):
    """Raised when the requested preview connector cannot be reserved."""


class MjpegFrameDecoder:
    """Extract JPEG frames from an MJPEG byte stream."""

    _SOI = b"\xff\xd8"
    _EOI = b"\xff\xd9"

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        """Consume one chunk of MJPEG bytes and return complete JPEG frames.

        Args:
            chunk: Raw MJPEG byte chunk.

        Returns:
            list[bytes]: Ordered JPEG frame payloads.
        """

        if chunk:
            self._buffer.extend(chunk)

        frames: list[bytes] = []
        while True:
            start_index = self._buffer.find(self._SOI)
            if start_index < 0:
                if len(self._buffer) > 1:
                    del self._buffer[:-1]
                return frames
            if start_index > 0:
                del self._buffer[:start_index]

            end_index = self._buffer.find(self._EOI, len(self._SOI))
            if end_index < 0:
                return frames

            frame = bytes(self._buffer[: end_index + len(self._EOI)])
            del self._buffer[: end_index + len(self._EOI)]
            frames.append(frame)


class PreviewRenderer:
    """Latest-frame-wins preview renderer with letterboxing and stall blackout."""

    def __init__(
        self,
        config: PreviewDisplayConfig,
        backend: Any,
        decode_jpeg_fn: Callable[[bytes], np.ndarray] | None = None,
    ) -> None:
        self.config = config
        self.backend = backend
        self._decode_jpeg_fn = decode_jpeg_fn or _decode_jpeg_rgb
        self._latest_jpeg_bytes: bytes | None = None
        self._latest_received_s: float | None = None
        self._last_display_s: float | None = None
        self._display_is_black = True

    def submit_jpeg_frame(self, jpeg_bytes: bytes, received_time_s: float) -> None:
        """Store the most recent JPEG frame for later rendering.

        Args:
            jpeg_bytes: JPEG-encoded preview frame.
            received_time_s: Monotonic receipt time in seconds.
        """

        self._latest_jpeg_bytes = bytes(jpeg_bytes)
        self._latest_received_s = float(received_time_s)

    def render_pending(self, now_s: float) -> None:
        """Render the newest pending frame or black out a stalled preview.

        Args:
            now_s: Current monotonic time in seconds.
        """

        frame_interval_s = 1.0 / float(self.config.max_preview_hz)
        can_display = (
            self._last_display_s is None
            or (float(now_s) - float(self._last_display_s)) >= frame_interval_s
        )
        if self._latest_jpeg_bytes is not None and can_display:
            frame_rgb = self._decode_jpeg_fn(self._latest_jpeg_bytes)
            letterboxed = _letterbox_rgb_frame(frame_rgb, self.config.resolution_px)
            self.backend.display_frame(letterboxed)
            self._latest_jpeg_bytes = None
            self._last_display_s = float(now_s)
            self._display_is_black = False
            return

        if (
            self._latest_received_s is not None
            and (float(now_s) - float(self._latest_received_s)) >= float(self.config.stall_timeout_s)
            and not self._display_is_black
            and can_display
        ):
            self.backend.display_black()
            self._last_display_s = float(now_s)
            self._display_is_black = True


class DrmPreviewViewer:
    """Stream MJPEG frames from the local camera service onto a DRM connector."""

    def __init__(
        self,
        config: PreviewDisplayConfig,
        backend_factory: Callable[[PreviewDisplayConfig], Any] | None = None,
        opener: Callable[[str, float], Any] | None = None,
        logger: logging.Logger | None = None,
        reconnect_sleep_s: float = 1.0,
        open_timeout_s: float = 1.0,
        read_chunk_size: int = 8192,
    ) -> None:
        self.config = config
        self._backend_factory = backend_factory or _PykmsPreviewBackend
        self._opener = opener or (lambda url, timeout: urlopen(url, timeout=timeout))
        self._logger = logger or logging.getLogger(__name__)
        self._reconnect_sleep_s = float(reconnect_sleep_s)
        self._open_timeout_s = float(open_timeout_s)
        self._read_chunk_size = int(read_chunk_size)
        self._backend: Any | None = None
        self._renderer: PreviewRenderer | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "DrmPreviewViewer":
        """Start the preview loop in a daemon thread.

        Returns:
            DrmPreviewViewer: Running preview viewer instance.
        """

        self._ensure_runtime()
        if self._thread is not None and self._thread.is_alive():
            return self
        self._thread = threading.Thread(
            target=self.run,
            kwargs={"stop_event": self._stop_event},
            name="drm-preview-viewer",
            daemon=True,
        )
        self._thread.start()
        return self

    def run(self, stop_event: threading.Event | None = None) -> None:
        """Run the preview loop in the current thread.

        Args:
            stop_event: Optional stop event that terminates the stream loop.
        """

        local_stop = stop_event or self._stop_event
        self._ensure_runtime()
        assert self._renderer is not None

        while True:
            try:
                with self._opener(self.config.stream_url, self._open_timeout_s) as stream:
                    decoder = MjpegFrameDecoder()
                    while True:
                        chunk = stream.read(self._read_chunk_size)
                        now_s = time.monotonic()
                        if chunk:
                            for frame_bytes in decoder.feed(chunk):
                                self._renderer.submit_jpeg_frame(frame_bytes, received_time_s=now_s)
                        self._renderer.render_pending(now_s)
                        if local_stop.is_set():
                            return
                        if not chunk:
                            break
            except Exception as exc:
                self._logger.warning("preview stream error on %s: %s", self.config.stream_url, exc)

            if local_stop.is_set():
                return
            self._renderer.render_pending(time.monotonic())
            time.sleep(self._reconnect_sleep_s)

    def close(self) -> None:
        """Stop the preview worker thread and close the backend."""

        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._backend is not None:
            self._backend.close()

    def _ensure_runtime(self) -> None:
        """Construct the preview backend and renderer on first use."""

        if self._renderer is not None:
            return
        backend = self._backend_factory(self.config)
        runtime_config = self.config
        backend_resolution = getattr(backend, "resolution_px", None)
        if backend_resolution is not None:
            runtime_config = replace(self.config, resolution_px=tuple(backend_resolution))
        self._backend = backend
        self._renderer = PreviewRenderer(config=runtime_config, backend=backend)


class DirectJpegPreviewViewer:
    """Display latest local JPEG preview frames on one DRM connector.

    Args:
        config: Preview connector and timing settings.
        frame_provider: Zero-argument callable returning the most recent JPEG
            frame bytes, or ``None`` when no preview frame is available.
        backend_factory: Optional preview backend factory.
        logger: Optional logger receiving non-fatal preview warnings.
        poll_interval_s: Poll interval in seconds for the local frame provider.
    """

    def __init__(
        self,
        config: PreviewDisplayConfig,
        frame_provider: Callable[[], bytes | None],
        backend_factory: Callable[[PreviewDisplayConfig], Any] | None = None,
        logger: logging.Logger | None = None,
        poll_interval_s: float = 1.0 / 60.0,
        max_consecutive_errors_before_reinit: int = 3,
        runtime_retry_backoff_s: float = 1.0,
    ) -> None:
        self.config = config
        self._frame_provider = frame_provider
        self._backend_factory = backend_factory or _PykmsPreviewBackend
        self._logger = logger or logging.getLogger(__name__)
        self._poll_interval_s = float(poll_interval_s)
        self._max_consecutive_errors_before_reinit = max(1, int(max_consecutive_errors_before_reinit))
        self._runtime_retry_backoff_s = max(0.0, float(runtime_retry_backoff_s))
        self._backend: Any | None = None
        self._renderer: PreviewRenderer | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._consecutive_errors = 0
        self._next_init_allowed_s = 0.0
        self._last_error_phase: str | None = None
        self._last_error_message: str | None = None

    def start(self) -> "DirectJpegPreviewViewer":
        """Start the preview loop in a daemon thread."""

        self._ensure_runtime()
        if self._thread is not None and self._thread.is_alive():
            return self
        self._thread = threading.Thread(
            target=self.run,
            kwargs={"stop_event": self._stop_event},
            name="drm-direct-preview-viewer",
            daemon=True,
        )
        self._thread.start()
        return self

    def run(self, stop_event: threading.Event | None = None) -> None:
        """Run the local preview loop in the current thread."""

        local_stop = stop_event or self._stop_event
        last_frame_token: int | None = None
        while not local_stop.is_set():
            now_s = time.monotonic()
            if self._renderer is None:
                if now_s < self._next_init_allowed_s:
                    time.sleep(self._poll_interval_s)
                    continue
                try:
                    self._ensure_runtime()
                    last_frame_token = None
                except Exception as exc:
                    self._handle_runtime_error(exc, phase="init", now_s=now_s)
                    time.sleep(self._poll_interval_s)
                    continue
            try:
                frame_bytes = self._frame_provider()
                if frame_bytes is not None:
                    frame_token = id(frame_bytes)
                    if frame_token != last_frame_token:
                        self._renderer.submit_jpeg_frame(frame_bytes, received_time_s=time.monotonic())
                        last_frame_token = frame_token
                self._renderer.render_pending(time.monotonic())
                self._consecutive_errors = 0
            except Exception as exc:
                self._handle_runtime_error(exc, phase="render", now_s=time.monotonic())
            time.sleep(self._poll_interval_s)

    def close(self) -> None:
        """Stop the preview thread and release the backend."""

        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._teardown_runtime()

    def state_dict(self) -> dict[str, Any]:
        """Return one JSON-serializable preview runtime state snapshot.

        Returns:
            dict[str, Any]: Preview state including connector, whether a backend
            is currently active, lightweight DRM diagnostics, and the latest
            non-fatal runtime error information when available.
        """

        diagnostics: dict[str, Any] = {}
        if self._backend is not None and hasattr(self._backend, "diagnostics"):
            diagnostics = dict(self._backend.diagnostics())
        return {
            "preview_connector": self.config.connector,
            "preview_active": self._backend is not None and self._renderer is not None,
            "drm_diagnostics": diagnostics,
            "consecutive_errors": int(self._consecutive_errors),
            "last_error_phase": self._last_error_phase,
            "last_error_message": self._last_error_message,
        }

    def _ensure_runtime(self) -> None:
        if self._renderer is not None:
            return
        backend = self._backend_factory(self.config)
        runtime_config = self.config
        backend_resolution = getattr(backend, "resolution_px", None)
        if backend_resolution is not None:
            runtime_config = replace(self.config, resolution_px=tuple(backend_resolution))
        self._backend = backend
        self._renderer = PreviewRenderer(config=runtime_config, backend=backend)

    def _handle_runtime_error(self, exc: Exception, *, phase: str, now_s: float) -> None:
        """Track errors, throttle warning logs, and reinitialize backend as needed.

        Args:
            exc: Runtime exception raised by preview init/render logic.
            phase: Failing phase label, either ``"init"`` or ``"render"``.
            now_s: Current monotonic time in seconds.
        """

        self._last_error_phase = str(phase)
        self._last_error_message = str(exc)
        self._consecutive_errors += 1
        if self._should_log_consecutive_error(self._consecutive_errors):
            self._logger.warning(
                "direct preview %s error on %s (count=%d): %s",
                phase,
                self.config.connector,
                self._consecutive_errors,
                exc,
            )
        if self._consecutive_errors < self._max_consecutive_errors_before_reinit:
            return
        self._consecutive_errors = 0
        self._next_init_allowed_s = float(now_s) + self._runtime_retry_backoff_s
        self._teardown_runtime()

    def _teardown_runtime(self) -> None:
        """Close backend resources and clear preview runtime state."""

        if self._backend is not None and hasattr(self._backend, "close"):
            self._backend.close()
        self._backend = None
        self._renderer = None

    @staticmethod
    def _should_log_consecutive_error(count: int) -> bool:
        """Return ``True`` for the first and power-of-two repeated errors."""

        if count <= 0:
            return False
        return (count & (count - 1)) == 0


def start_preview_viewer_from_env(
    port: int,
    backend_factory: Callable[[PreviewDisplayConfig], Any] | None = None,
    opener: Callable[[str, float], Any] | None = None,
    logger: logging.Logger | None = None,
) -> DrmPreviewViewer | None:
    """Create and start the local DRM preview viewer from environment settings.

    Args:
        port: HTTP camera service port exposing ``/stream.mjpg``.
        backend_factory: Optional backend constructor override.
        opener: Optional MJPEG stream opener override.
        logger: Optional logger for non-fatal startup failures.

    Returns:
        DrmPreviewViewer | None: Running viewer on success, otherwise ``None``.
    """

    if not _env_truthy(os.environ.get("CAMERA_PREVIEW_DRM_ENABLE", "1")):
        return None

    config = PreviewDisplayConfig(
        connector=os.environ.get("CAMERA_PREVIEW_DRM_CONNECTOR", "HDMI-A-2").strip() or "HDMI-A-2",
        resolution_px=(640, 480),
        stream_url=f"http://127.0.0.1:{int(port)}/stream.mjpg",
        max_preview_hz=float(os.environ.get("CAMERA_PREVIEW_DRM_MAX_HZ", "15.0")),
        stall_timeout_s=float(os.environ.get("CAMERA_PREVIEW_STALL_TIMEOUT_S", "0.5")),
    )
    viewer = DrmPreviewViewer(
        config=config,
        backend_factory=backend_factory,
        opener=opener,
        logger=logger,
    )
    try:
        return viewer.start()
    except (PreviewConnectorUnavailable, RuntimeError) as exc:
        if logger is not None:
            logger.warning("preview viewer disabled: %s", exc)
        return None


class _PykmsPreviewBackend:
    """DRM/KMS preview backend using double-buffered RGB framebuffers."""

    def __init__(self, config: PreviewDisplayConfig) -> None:
        try:
            import pykms  # type: ignore
        except ImportError as exc:
            raise RuntimeError("DRM preview backend requires python3-kms++ / pykms") from exc

        self._pykms = pykms
        self.connector = config.connector
        self.card = pykms.Card()
        self.res = pykms.ResourceManager(self.card)
        try:
            self.conn = self.res.reserve_connector(self.connector)
        except Exception as exc:
            raise PreviewConnectorUnavailable(f"preview connector {self.connector} is unavailable") from exc
        self.crtc = self.res.reserve_crtc(self.conn)
        self.mode = self.conn.get_default_mode()
        self.resolution_px = (int(self.mode.hdisplay), int(self.mode.vdisplay))
        self._selector = selectors.DefaultSelector()
        self._selector.register(self.card.fd, selectors.EVENT_READ)
        self._plane = self.res.reserve_primary_plane(self.crtc)
        self._mode_set = False
        self._front_index = 0
        self._last_commit_stage: str | None = None
        self._last_commit_error: str | None = None
        self._last_request_summary: dict[str, Any] = {}
        self._framebuffers = [
            pykms.DumbFramebuffer(self.card, self.resolution_px[0], self.resolution_px[1], "XR24"),
            pykms.DumbFramebuffer(self.card, self.resolution_px[0], self.resolution_px[1], "XR24"),
        ]

    def display_frame(self, frame_rgb: np.ndarray) -> None:
        """Display one RGB preview frame.

        Args:
            frame_rgb: ``uint8`` RGB frame with shape ``(height_px, width_px, 3)``.
        """

        height_px, width_px, channels = frame_rgb.shape
        if channels != 3:
            raise ValueError("preview frame must have shape (height_px, width_px, 3)")
        if (width_px, height_px) != self.resolution_px:
            raise ValueError("preview frame size must match the DRM connector mode")

        framebuffer = self._framebuffers[self._front_index ^ 1]
        self._write_rgb_framebuffer(frame_rgb, framebuffer)
        self._flip(framebuffer)
        self._front_index ^= 1

    def display_black(self) -> None:
        """Display a black frame on the preview connector."""

        width_px, height_px = self.resolution_px
        black = np.zeros((height_px, width_px, 3), dtype=np.uint8)
        self.display_frame(black)

    def close(self) -> None:
        """Release the DRM preview backend resources."""

        try:
            self._selector.close()
        finally:
            try:
                self.card.disable_planes()
            except Exception:
                return None

    def diagnostics(self) -> dict[str, Any]:
        """Return a lightweight DRM resource snapshot for preview debugging.

        Returns:
            dict[str, Any]: JSON-serializable preview diagnostics containing the
            requested connector, reserved DRM object identifiers, and the most
            recent commit-stage outcome.
        """

        return {
            "backend": "drm_preview",
            "requested_connector": str(self.connector),
            "reserved_connector_id": int(getattr(self.conn, "id", -1)),
            "reserved_connector_name": str(
                getattr(self.conn, "fullname", self.connector)
            ),
            "reserved_crtc_id": int(getattr(self.crtc, "id", -1)),
            "reserved_plane_id": int(getattr(self._plane, "id", -1)),
            "mode_set_done": bool(self._mode_set),
            "last_commit_stage": self._last_commit_stage,
            "last_commit_error": self._last_commit_error,
            "last_request": dict(self._last_request_summary),
        }

    def _write_rgb_framebuffer(self, frame_rgb: np.ndarray, framebuffer: Any) -> None:
        """Copy one RGB frame into an XR24 dumb framebuffer.

        Args:
            frame_rgb: ``uint8`` RGB frame with shape ``(height_px, width_px, 3)``.
            framebuffer: pykms dumb framebuffer.
        """

        width_px, height_px = self.resolution_px
        mapped = framebuffer.map(0)
        pixels = np.frombuffer(mapped, dtype=np.uint8).reshape(height_px, width_px, 4)
        pixels[:, :, 0] = frame_rgb[:, :, 2]
        pixels[:, :, 1] = frame_rgb[:, :, 1]
        pixels[:, :, 2] = frame_rgb[:, :, 0]
        pixels[:, :, 3] = 0

    def _flip(self, framebuffer: Any) -> None:
        """Present one framebuffer on the preview connector."""

        if not self._mode_set:
            self._last_commit_stage = "modeset"
            mode_blob = self.mode.to_blob(self.card)
            plane_properties = {
                "FB_ID": framebuffer.id,
                "CRTC_ID": self.crtc.id,
                "SRC_X": 0 << 16,
                "SRC_Y": 0 << 16,
                "SRC_W": framebuffer.width << 16,
                "SRC_H": framebuffer.height << 16,
                "CRTC_X": 0,
                "CRTC_Y": 0,
                "CRTC_W": self.mode.hdisplay,
                "CRTC_H": self.mode.vdisplay,
            }
            self._last_request_summary = {
                "commit_kind": "atomic",
                "allow_modeset": True,
                "framebuffer_id": int(framebuffer.id),
                "object_properties": {
                    "connector": {"CRTC_ID": int(self.crtc.id)},
                    "crtc": {"ACTIVE": 1, "MODE_ID": int(mode_blob.id)},
                    "plane": dict(plane_properties),
                },
            }
            req = self._pykms.AtomicReq(self.card)
            req.add(self.conn, "CRTC_ID", self.crtc.id)
            req.add(self.crtc, {"ACTIVE": 1, "MODE_ID": mode_blob.id})
            req.add(
                self._plane,
                plane_properties,
            )
            ret = req.commit(allow_modeset=True)
            if ret < 0:
                self._last_commit_error = f"preview atomic mode set failed with {ret}"
                raise RuntimeError(self._last_commit_error)
            self._last_commit_error = None
            self._mode_set = True
            return

        self._last_commit_stage = "page_flip"
        self._last_request_summary = {
            "commit_kind": "atomic",
            "allow_modeset": False,
            "framebuffer_id": int(framebuffer.id),
            "object_properties": {
                "crtc_primary_plane": {"FB_ID": int(framebuffer.id)},
            },
        }
        req = self._pykms.AtomicReq(self.card)
        req.add(self.crtc.primary_plane, "FB_ID", framebuffer.id)
        ret = req.commit()
        if ret < 0:
            self._last_commit_error = f"preview atomic page flip failed with {ret}"
            raise RuntimeError(self._last_commit_error)
        self._last_commit_error = None
        self._wait_for_flip_complete(timeout_s=0.2)

    def _wait_for_flip_complete(self, timeout_s: float) -> None:
        """Wait for one DRM flip-complete event."""

        deadline = time.monotonic() + float(timeout_s)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for preview page-flip completion")
            events = self._selector.select(remaining)
            if not events:
                continue
            for _key, _mask in events:
                for event in self.card.read_events():
                    if event.type == self._pykms.DrmEventType.FLIP_COMPLETE:
                        return


def _decode_jpeg_rgb(jpeg_bytes: bytes) -> np.ndarray:
    """Decode one JPEG byte string into an RGB image array.

    Args:
        jpeg_bytes: JPEG-encoded image bytes.

    Returns:
        np.ndarray: ``uint8`` RGB array with shape ``(height_px, width_px, 3)``.
    """

    with Image.open(BytesIO(jpeg_bytes)) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def _letterbox_rgb_frame(frame_rgb: np.ndarray, output_size_px: tuple[int, int]) -> np.ndarray:
    """Resize an RGB frame into a black letterboxed output canvas.

    Args:
        frame_rgb: ``uint8`` RGB frame with shape ``(height_px, width_px, 3)``.
        output_size_px: Output size as ``(width_px, height_px)``.

    Returns:
        np.ndarray: Letterboxed ``uint8`` RGB frame with shape
            ``(output_height_px, output_width_px, 3)``.
    """

    output_width_px, output_height_px = output_size_px
    input_height_px, input_width_px, channels = frame_rgb.shape
    if channels != 3:
        raise ValueError("frame_rgb must have shape (height_px, width_px, 3)")
    if output_width_px <= 0 or output_height_px <= 0:
        raise ValueError("output_size_px values must be > 0")

    scale = min(output_width_px / input_width_px, output_height_px / input_height_px)
    resized_width_px = max(1, int(round(input_width_px * scale)))
    resized_height_px = max(1, int(round(input_height_px * scale)))

    resized_image = Image.fromarray(frame_rgb, mode="RGB").resize(
        (resized_width_px, resized_height_px),
        Image.Resampling.BILINEAR,
    )
    canvas = np.zeros((output_height_px, output_width_px, 3), dtype=np.uint8)
    offset_x_px = (output_width_px - resized_width_px) // 2
    offset_y_px = (output_height_px - resized_height_px) // 2
    canvas[
        offset_y_px : offset_y_px + resized_height_px,
        offset_x_px : offset_x_px + resized_width_px,
        :,
    ] = np.asarray(resized_image, dtype=np.uint8)
    return canvas


def _env_truthy(value: str) -> bool:
    """Interpret a typical environment truthy/falsey string."""

    return value.strip().lower() not in {"0", "false", "no", "off", ""}
