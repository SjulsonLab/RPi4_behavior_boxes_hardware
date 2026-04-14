"""Shared-DRM camera frame source with separate acquisition and preview streams."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np


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


def _letterbox_rgb_frame_local(
    frame_rgb: np.ndarray,
    output_size_px: tuple[int, int],
) -> np.ndarray:
    """Resize an RGB frame into a black letterboxed output canvas.

    Args:
        frame_rgb: ``uint8`` RGB frame with shape ``(height_px, width_px, 3)``.
        output_size_px: Output size as ``(width_px, height_px)``.

    Returns:
        np.ndarray: Letterboxed ``uint8`` RGB frame with shape
        ``(output_height_px, output_width_px, 3)``.
    """

    output_width_px, output_height_px = (int(output_size_px[0]), int(output_size_px[1]))
    input_height_px, input_width_px, channels = frame_rgb.shape
    if channels != 3:
        raise ValueError("frame_rgb must have shape (height_px, width_px, 3)")
    if output_width_px <= 0 or output_height_px <= 0:
        raise ValueError("output_size_px values must be > 0")

    scale = min(output_width_px / input_width_px, output_height_px / input_height_px)
    resized_width_px = max(1, int(round(input_width_px * scale)))
    resized_height_px = max(1, int(round(input_height_px * scale)))

    src_x = np.minimum(
        (np.arange(resized_width_px, dtype=np.float64) * input_width_px / resized_width_px).astype(np.int64),
        input_width_px - 1,
    )
    src_y = np.minimum(
        (np.arange(resized_height_px, dtype=np.float64) * input_height_px / resized_height_px).astype(np.int64),
        input_height_px - 1,
    )
    resized = frame_rgb[src_y[:, None], src_x[None, :], :]

    canvas = np.zeros((output_height_px, output_width_px, 3), dtype=np.uint8)
    offset_x_px = (output_width_px - resized_width_px) // 2
    offset_y_px = (output_height_px - resized_height_px) // 2
    canvas[
        offset_y_px : offset_y_px + resized_height_px,
        offset_x_px : offset_x_px + resized_width_px,
        :,
    ] = resized
    return canvas


class SharedCameraFrameSource:
    """Pull preview RGB frames from one local camera without creating a preview client.

    Data contracts:

    - ``camera_id``: Semantic camera identifier string such as ``"camera0"``.
    - ``resolution_px``: Final preview output size as ``(width_px, height_px)``.
    - ``acquisition_resolution_px``: Main camera stream size as
      ``(width_px, height_px)``.
    - ``preview_stream_resolution_px``: Low-resolution YUV preview stream size
      as ``(width_px, height_px)``.
    - ``preview_source_mode``: Preview frame source strategy. ``"rgb_main"``
      captures from the main RGB stream. ``"yuv_lores"`` captures from the
      YUV low-resolution stream and converts it to RGB.
    - ``capture_rgb_frame()`` returns ``uint8`` RGB frames with shape
      ``(height_px, width_px, 3)``.
    """

    def __init__(
        self,
        *,
        camera_id: str,
        resolution_px: tuple[int, int],
        acquisition_resolution_px: tuple[int, int] | None = None,
        preview_stream_resolution_px: tuple[int, int] | None = None,
        preview_source_mode: str = "rgb_main",
        frame_rate_hz: float = 5.0,
        picamera2_factory: Callable[..., Any] | None = None,
        yuv420_to_rgb_fn: Callable[[np.ndarray, tuple[int, int]], np.ndarray] | None = None,
    ) -> None:
        if picamera2_factory is None:
            try:
                from picamera2 import Picamera2  # type: ignore
            except ImportError as exc:
                raise RuntimeError("shared camera frame source requires Picamera2 on this host") from exc
            picamera2_factory = Picamera2
        if preview_source_mode not in {"rgb_main", "yuv_lores"}:
            raise ValueError(f"unsupported preview_source_mode {preview_source_mode!r}")
        if preview_source_mode == "yuv_lores" and yuv420_to_rgb_fn is None:
            try:
                from picamera2.converters import YUV420_to_RGB  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "shared camera frame source requires picamera2.converters.YUV420_to_RGB"
                ) from exc
            yuv420_to_rgb_fn = YUV420_to_RGB

        self.camera_id = str(camera_id)
        self.camera_num = _camera_num_from_id(self.camera_id)
        self.resolution_px = (int(resolution_px[0]), int(resolution_px[1]))
        self.preview_source_mode = str(preview_source_mode)
        self.acquisition_resolution_px = (
            (int(acquisition_resolution_px[0]), int(acquisition_resolution_px[1]))
            if acquisition_resolution_px is not None
            else self.resolution_px
        )
        self.preview_stream_resolution_px = self._normalize_preview_stream_resolution(
            preview_stream_resolution_px
            if preview_stream_resolution_px is not None
            else self.acquisition_resolution_px
        )
        self.frame_rate_hz = float(frame_rate_hz)
        self._picamera2_factory = picamera2_factory
        self._yuv420_to_rgb_fn = yuv420_to_rgb_fn
        self.preview_rgb_resolution_px = (
            self.acquisition_resolution_px
            if self.preview_source_mode == "rgb_main"
            else (
                self.preview_stream_resolution_px[0] // 2,
                self.preview_stream_resolution_px[1] // 2,
            )
        )
        self._picam2 = self._picamera2_factory(camera_num=self.camera_num)
        self._closed = False
        try:
            configuration = self._picam2.create_video_configuration(
                main={"size": self.acquisition_resolution_px, "format": "RGB888"},
                lores=(
                    {"size": self.preview_stream_resolution_px, "format": "YUV420"}
                    if self.preview_source_mode == "yuv_lores"
                    else None
                ),
                controls={"FrameRate": self.frame_rate_hz},
            )
            self._picam2.configure(configuration)
            self._picam2.start()
        except Exception:
            self.close()
            raise

    # Helper methods
    def _normalize_preview_stream_resolution(
        self,
        preview_stream_resolution_px: tuple[int, int],
    ) -> tuple[int, int]:
        """Normalize low-resolution stream dimensions for YUV420 capture.

        Args:
            preview_stream_resolution_px: Requested low-resolution stream size as
                ``(width_px, height_px)``.

        Returns:
            tuple[int, int]: Even-valued low-resolution stream size as
            ``(width_px, height_px)``.
        """

        width_px = max(2, int(preview_stream_resolution_px[0]))
        height_px = max(2, int(preview_stream_resolution_px[1]))
        width_px -= width_px % 2
        height_px -= height_px % 2
        return (width_px, height_px)

    def capture_rgb_frame(self) -> np.ndarray:
        """Capture one RGB frame from the configured camera.

        Returns:
            np.ndarray: ``uint8`` RGB frame with shape
            ``(height_px, width_px, 3)``.
        """

        if self.preview_source_mode == "rgb_main":
            frame = np.asarray(self._picam2.capture_array("main"), dtype=np.uint8)
        else:
            frame_yuv = np.asarray(self._picam2.capture_array("lores"), dtype=np.uint8)
            frame = np.asarray(
                self._yuv420_to_rgb_fn(frame_yuv.reshape(-1), self.preview_stream_resolution_px),
                dtype=np.uint8,
            )
        if frame.ndim != 3:
            raise RuntimeError(f"expected RGB frame with 3 dimensions, got shape {frame.shape!r}")
        if frame.shape[2] == 4:
            frame = frame[:, :, :3]
        if frame.shape[2] != 3:
            raise RuntimeError(f"expected 3 RGB channels, got shape {frame.shape!r}")
        self.preview_rgb_resolution_px = (int(frame.shape[1]), int(frame.shape[0]))
        output_height_px = int(self.resolution_px[1])
        output_width_px = int(self.resolution_px[0])
        if frame.shape[:2] != (output_height_px, output_width_px):
            frame = _letterbox_rgb_frame_local(frame, self.resolution_px)
        return np.ascontiguousarray(frame, dtype=np.uint8)

    def diagnostics(self) -> dict[str, object]:
        """Return the frame-source configuration as a JSON-serializable summary.

        Returns:
            dict[str, object]: Configuration summary including acquisition and
            preview stream sizes in pixels.
        """

        return {
            "camera_id": self.camera_id,
            "camera_preview_source_mode": self.preview_source_mode,
            "acquisition_resolution_px": self.acquisition_resolution_px,
            "preview_stream_resolution_px": self.preview_stream_resolution_px,
            "preview_frame_resolution_px": self.preview_rgb_resolution_px,
            "output_resolution_px": self.resolution_px,
            "frame_rate_hz": self.frame_rate_hz,
        }

    def close(self) -> None:
        """Stop and close the underlying camera object.

        Returns:
            None.
        """

        if self._closed:
            return
        self._closed = True
        if not hasattr(self, "_picam2") or self._picam2 is None:
            return
        try:
            try:
                self._picam2.stop()
            except Exception:
                pass
        finally:
            try:
                self._picam2.close()
            finally:
                self._picam2 = None
