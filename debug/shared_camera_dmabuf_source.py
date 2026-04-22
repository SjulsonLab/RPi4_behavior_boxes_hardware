"""Shared-DRM camera source that exposes Picamera2 completed requests as dmabuf frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


def _camera_num_from_id(camera_id: str) -> int:
    """Convert a semantic camera id like ``camera0`` into a zero-based index.

    Args:
        camera_id: Semantic camera identifier string.

    Returns:
        int: Zero-based Picamera2 camera index.
    """

    text = str(camera_id).strip().lower()
    if not text.startswith("camera"):
        raise ValueError(f"camera_id must look like 'camera0', got {camera_id!r}")
    return int(text.removeprefix("camera"))


@dataclass
class CameraDmabufFrame:
    """Display-ready camera frame backed by one Picamera2 completed request.

    Attributes:
        request: Picamera2 completed request object that must remain alive until
            the display code has finished using the dmabuf-backed framebuffer.
        stream_name: Picamera2 display-stream name, typically ``"main"``.
        pixel_format: Camera stream pixel format string such as ``"XBGR8888"``.
        width_px: Frame width in pixels.
        height_px: Frame height in pixels.
        plane_fds: Tuple of dmabuf file descriptors, one per framebuffer plane.
        strides_bytes: Tuple of per-plane strides in bytes.
        offsets_bytes: Tuple of per-plane byte offsets from the corresponding
            dmabuf file descriptor.
        buffer_key: Stable framebuffer-cache key. When the underlying camera
            buffer object is hashable, this is that object. Otherwise it is a
            structural tuple derived from the frame's dmabuf metadata.
    """

    request: Any
    stream_name: str
    pixel_format: str
    width_px: int
    height_px: int
    plane_fds: tuple[int, ...]
    strides_bytes: tuple[int, ...]
    offsets_bytes: tuple[int, ...]
    buffer_key: object
    _released: bool = False


class SharedCameraDmabufSource:
    """Capture Picamera2 completed requests without creating a separate preview client.

    Data contracts:

    - ``camera_id``: Semantic camera identifier string such as ``"camera0"``.
    - ``resolution_px``: Main display stream size as ``(width_px, height_px)``.
    - ``sensor_mode``: Picamera2 sensor mode index used for the camera's sensor
      configuration. The default is ``0`` for preview-performance testing.
    - ``request_mode``: Preview request-selection policy. ``"latest"`` asks
      Picamera2 for a post-now completed request using ``flush=True``.
      ``"next"`` consumes the next available completed request in order.
    - ``capture_frame()`` returns a ``CameraDmabufFrame`` whose dmabuf metadata
      is suitable for import into a DRM framebuffer.
    - ``capture_latest_frame()`` requests the next post-"now" completed request
      from Picamera2 by using ``capture_request(flush=True)``. This favors a
      fresher operator preview over consuming every frame in order.
    - ``capture_frame_for_preview()`` follows the configured ``request_mode``.
    - ``CameraDmabufFrame.buffer_key`` is a stable cache key for imported DRM
      framebuffers that reference the same underlying camera buffer.
    - ``release_frame(frame)`` must be called exactly once for every captured
      frame that was returned successfully, unless ``close()`` releases it.
    """

    def __init__(
        self,
        *,
        camera_id: str,
        resolution_px: tuple[int, int],
        frame_rate_hz: float = 30.0,
        sensor_mode: int = 0,
        request_mode: str = "next",
        pixel_format: str = "XBGR8888",
        picamera2_factory: Callable[..., Any] | None = None,
    ) -> None:
        if picamera2_factory is None:
            try:
                from picamera2 import Picamera2  # type: ignore
            except ImportError as exc:
                raise RuntimeError("shared camera dmabuf source requires Picamera2 on this host") from exc
            picamera2_factory = Picamera2

        self.camera_id = str(camera_id)
        self.camera_num = _camera_num_from_id(self.camera_id)
        self.resolution_px = (int(resolution_px[0]), int(resolution_px[1]))
        self.frame_rate_hz = float(frame_rate_hz)
        self.sensor_mode = int(sensor_mode)
        self.frame_duration_us = int(1e6 / max(self.frame_rate_hz, 1.0))
        self.request_mode = str(request_mode)
        if self.request_mode not in {"latest", "next"}:
            raise ValueError(f"request_mode must be 'latest' or 'next', got {request_mode!r}")
        self.pixel_format = str(pixel_format)
        self._picamera2_factory = picamera2_factory
        self._picam2 = self._picamera2_factory(camera_num=self.camera_num)
        self._closed = False
        self._outstanding_frames: list[CameraDmabufFrame] = []
        try:
            sensor_config: dict[str, object] = {}
            sensor_modes = getattr(self._picam2, "sensor_modes", None)
            if sensor_modes is not None:
                if self.sensor_mode < 0 or self.sensor_mode >= len(sensor_modes):
                    raise ValueError(
                        f"sensor_mode {self.sensor_mode} is out of range for available sensor modes"
                    )
                selected_mode = sensor_modes[self.sensor_mode]
                sensor_config = {
                    "output_size": tuple(selected_mode["size"]),
                    "bit_depth": int(selected_mode["bit_depth"]),
                }
            configuration = self._picam2.create_video_configuration(
                main={"size": self.resolution_px, "format": self.pixel_format},
                sensor=sensor_config,
                controls={
                    "FrameRate": self.frame_rate_hz,
                    "FrameDurationLimits": (self.frame_duration_us, self.frame_duration_us),
                },
                display="main",
                encode="main",
            )
            align_configuration = getattr(self._picam2, "align_configuration", None)
            if callable(align_configuration):
                align_configuration(configuration)
            self._picam2.configure(configuration)
            self._picam2.start()
        except Exception:
            self.close()
            raise

    def _make_buffer_key(
        self,
        *,
        buffer: Any,
        pixel_format: str,
        width_px: int,
        height_px: int,
        plane_fds: tuple[int, ...],
        strides_bytes: tuple[int, ...],
        offsets_bytes: tuple[int, ...],
    ) -> object:
        """Build a stable framebuffer-cache key for one camera buffer.

        Args:
            buffer: Picamera2/libcamera buffer object for the display stream.
            pixel_format: Pixel format string such as ``"XBGR8888"``.
            width_px: Frame width in pixels.
            height_px: Frame height in pixels.
            plane_fds: Per-plane dmabuf file descriptors.
            strides_bytes: Per-plane byte strides.
            offsets_bytes: Per-plane byte offsets.

        Returns:
            object: Hashable cache key identifying the underlying camera buffer.
        """

        try:
            hash(buffer)
            return buffer
        except TypeError:
            return (
                str(pixel_format),
                int(width_px),
                int(height_px),
                tuple(int(fd) for fd in plane_fds),
                tuple(int(stride) for stride in strides_bytes),
                tuple(int(offset) for offset in offsets_bytes),
            )

    def _capture_frame(self, *, flush: object | None) -> CameraDmabufFrame:
        """Fetch one completed request and expose its display-stream dmabuf metadata.

        Returns:
            CameraDmabufFrame: Display-ready metadata for one completed request.

        Raises:
            RuntimeError: If the completed request does not expose a configured
                display stream or the stream is unsupported by this helper.
        """

        if flush is None:
            request = self._picam2.capture_request()
        else:
            request = self._picam2.capture_request(flush=flush)
        try:
            stream_name = request.config.get("display")
            if not isinstance(stream_name, str) or not stream_name:
                raise RuntimeError("completed request does not expose a display stream")
            stream = request.stream_map[stream_name]
            cfg = stream.configuration
            pixel_format = str(cfg.pixel_format)
            width_px = int(cfg.size.width)
            height_px = int(cfg.size.height)
            stride_bytes = int(cfg.stride)
            buffer = request.request.buffers[stream]
            plane_fds = (int(buffer.planes[0].fd),)
            strides_bytes = (stride_bytes,)
            offsets_bytes = (0,)

            if pixel_format not in {"XBGR8888", "XRGB8888", "RGB888", "BGR888"}:
                raise RuntimeError(f"unsupported dmabuf pixel format {pixel_format!r}")

            frame = CameraDmabufFrame(
                request=request,
                stream_name=stream_name,
                pixel_format=pixel_format,
                width_px=width_px,
                height_px=height_px,
                plane_fds=plane_fds,
                strides_bytes=strides_bytes,
                offsets_bytes=offsets_bytes,
                buffer_key=self._make_buffer_key(
                    buffer=buffer,
                    pixel_format=pixel_format,
                    width_px=width_px,
                    height_px=height_px,
                    plane_fds=plane_fds,
                    strides_bytes=strides_bytes,
                    offsets_bytes=offsets_bytes,
                ),
            )
            self._outstanding_frames.append(frame)
            return frame
        except Exception:
            release_request = getattr(request, "release", None)
            if callable(release_request):
                release_request()
            raise

    def capture_frame(self) -> CameraDmabufFrame:
        """Fetch the next available completed request.

        Returns:
            CameraDmabufFrame: Display-ready metadata for one completed request.
        """

        return self._capture_frame(flush=None)

    def capture_latest_frame(self) -> CameraDmabufFrame:
        """Fetch the next completed request newer than the current time.

        Returns:
            CameraDmabufFrame: Display-ready metadata for a fresh completed
            request chosen for operator-preview responsiveness.
        """

        return self._capture_frame(flush=True)

    def capture_frame_for_preview(self) -> CameraDmabufFrame:
        """Fetch one preview frame according to the configured request mode.

        Returns:
            CameraDmabufFrame: Display-ready metadata for one completed request.
        """

        if self.request_mode == "latest":
            return self.capture_latest_frame()
        return self.capture_frame()

    def release_frame(self, frame: CameraDmabufFrame) -> None:
        """Release one previously captured completed request back to Picamera2.

        Args:
            frame: Captured dmabuf frame returned by ``capture_frame()``.

        Returns:
            None.
        """

        if frame._released:
            return
        frame.request.release()
        frame._released = True
        if frame in self._outstanding_frames:
            self._outstanding_frames.remove(frame)

    def diagnostics(self) -> dict[str, object]:
        """Return a JSON-serializable source summary.

        Returns:
            dict[str, object]: Camera source metadata including configured output
            size and requested frame rate.
        """

        return {
            "camera_id": self.camera_id,
            "frame_rate_hz": self.frame_rate_hz,
            "sensor_mode": self.sensor_mode,
            "frame_duration_us": self.frame_duration_us,
            "request_mode": self.request_mode,
            "output_resolution_px": self.resolution_px,
            "pixel_format": self.pixel_format,
        }

    def close(self) -> None:
        """Release outstanding requests and stop the camera.

        Returns:
            None.
        """

        if self._closed:
            return
        self._closed = True
        for frame in list(self._outstanding_frames):
            self.release_frame(frame)
        try:
            self._picam2.stop()
        except Exception:
            pass
        try:
            self._picam2.close()
        except Exception:
            pass
