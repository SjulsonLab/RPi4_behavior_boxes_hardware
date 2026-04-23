"""Shared-DRM camera source that combines dmabuf preview with H.264 recording."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Callable

import numpy as np

try:
    from debug.shared_camera_dmabuf_source import SharedCameraDmabufSource
except ModuleNotFoundError:
    from shared_camera_dmabuf_source import SharedCameraDmabufSource


@dataclass(frozen=True)
class TimestampSample:
    """One recorded frame-timestamp sample.

    Attributes:
        sensor_timestamp_ns: Camera sensor timestamp in nanoseconds from
            Picamera2/libcamera metadata.
        frame_duration_us: Frame duration in microseconds from Picamera2
            metadata.
        unix_timestamp_s: Wall-clock Unix timestamp in seconds from
            ``time.time()``.
    """

    sensor_timestamp_ns: int
    frame_duration_us: int
    unix_timestamp_s: float


class FastTextOverlayRenderer:
    """Render cached numeric text into the luma plane of a preview frame.

    Data contracts:

    - ``frame_y`` must be a writable ``uint8`` NumPy array with shape
      ``(height_px, width_px)`` representing the Y/luma plane of the camera
      image in display pixel coordinates.
    - ``elapsed_s`` and ``unix_timestamp_s`` are scalar seconds.
    - ``frame_rate_hz`` is a scalar frame rate in Hz.
    """

    FONT_SCALE = 0.8
    THICKNESS = 2
    CHARACTERS = "0123456789.-"

    def __init__(self, *, cv2_module: Any | None = None) -> None:
        if cv2_module is None:
            try:
                import cv2  # type: ignore
            except ImportError as exc:
                raise RuntimeError("fast overlay rendering requires cv2 on this host") from exc
            cv2_module = cv2

        self._cv2 = cv2_module
        self._glyph_cache = self._build_glyph_cache()

    def _build_glyph_cache(self) -> dict[str, np.ndarray]:
        """Build grayscale glyph images for the small numeric character set.

        Returns:
            dict[str, np.ndarray]: Mapping from character to ``uint8`` glyph
            image with shape ``(glyph_height_px, glyph_width_px)``.
        """

        glyph_cache: dict[str, np.ndarray] = {}
        for char in self.CHARACTERS:
            glyph = np.zeros((40, 30), dtype=np.uint8)
            self._cv2.putText(
                glyph,
                char,
                (2, 30),
                self._cv2.FONT_HERSHEY_SIMPLEX,
                self.FONT_SCALE,
                255,
                self.THICKNESS,
            )
            glyph_cache[char] = glyph
        return glyph_cache

    def _draw_text_fast(self, frame_y: np.ndarray, text: str, *, x: int, y: int) -> None:
        """Draw cached text into a luma plane in-place.

        Args:
            frame_y: Writable luma plane with shape ``(height_px, width_px)``.
            text: Text string restricted to glyphs in ``CHARACTERS``.
            x: Horizontal origin in pixels.
            y: Baseline location in pixels.

        Returns:
            None.
        """

        offset_px = 0
        for char in str(text):
            glyph = self._glyph_cache.get(char)
            if glyph is None:
                offset_px += 15
                continue
            height_px, width_px = glyph.shape
            y0 = max(int(y) - height_px, 0)
            y1 = min(int(y), frame_y.shape[0])
            x0 = int(x) + offset_px
            x1 = min(x0 + width_px, frame_y.shape[1])
            if y1 > y0 and x1 > x0:
                glyph_y0 = height_px - (y1 - y0)
                glyph_x1 = x1 - x0
                frame_y[y0:y1, x0:x1] = np.maximum(
                    frame_y[y0:y1, x0:x1],
                    glyph[glyph_y0:height_px, :glyph_x1],
                )
            offset_px += width_px + 2

    def draw_overlay(
        self,
        frame_y: np.ndarray,
        *,
        elapsed_s: float,
        unix_timestamp_s: float,
        frame_rate_hz: float,
    ) -> None:
        """Draw elapsed time, Unix time, and frame rate into the luma plane.

        Args:
            frame_y: Writable luma plane with shape ``(height_px, width_px)``.
            elapsed_s: Seconds elapsed since the first recorded sensor
                timestamp.
            unix_timestamp_s: Unix wall-clock timestamp in seconds.
            frame_rate_hz: Instantaneous frame rate in Hz derived from metadata.

        Returns:
            None.
        """

        self._draw_text_fast(frame_y, f"{float(elapsed_s):.3f}", x=10, y=45)
        self._draw_text_fast(frame_y, f"{float(unix_timestamp_s):.6f}", x=10, y=90)
        self._draw_text_fast(frame_y, f"{float(frame_rate_hz):.1f}", x=10, y=135)


class SharedCameraRecordingDmabufSource(SharedCameraDmabufSource):
    """Dmabuf preview source that also records H.264 video and timestamp CSV data.

    Data contracts:

    - ``video_path`` is a filesystem path for the raw H.264 bitstream.
    - ``timestamp_csv_path`` is a filesystem path for the timestamp CSV.
    - ``sensor_mode`` is the Picamera2 sensor mode index used for recording and
      preview. The default is ``1`` because this is the first higher-resolution
      smoke target for recording.
    - ``request_mode`` follows the same semantics as
      :class:`SharedCameraDmabufSource` and defaults to ``"next"``.
    - ``overlay_enabled`` controls whether the metadata callback modifies the
      luma plane in-place to draw elapsed time, Unix time, and instantaneous
      frame rate.
    - Timestamp CSV rows always contain three columns in this order:
      ``SensorTimestamp_ns,FrameDuration_us,UnixTimestamp_s``.
    """

    DEFAULT_BITRATE_BPS = 30_000_000

    def __init__(
        self,
        *,
        camera_id: str,
        resolution_px: tuple[int, int],
        video_path: Path | str,
        timestamp_csv_path: Path | str,
        frame_rate_hz: float = 30.0,
        sensor_mode: int = 1,
        request_mode: str = "next",
        pixel_format: str = "XBGR8888",
        overlay_enabled: bool = True,
        bitrate_bps: int = DEFAULT_BITRATE_BPS,
        picamera2_factory: Callable[..., Any] | None = None,
        encoder_factory: Callable[..., Any] | None = None,
        file_output_factory: Callable[..., Any] | None = None,
        overlay_renderer_factory: Callable[[], Any] | None = None,
        mapped_array_factory: Callable[..., Any] | None = None,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.video_path = Path(video_path)
        self.timestamp_csv_path = Path(timestamp_csv_path)
        self.overlay_enabled = bool(overlay_enabled)
        self.bitrate_bps = int(bitrate_bps)
        self._recording_started = False
        self._recording_encoder = None
        self._recording_output = None
        self._timestamp_samples: list[TimestampSample] = []
        self._overlay_renderer = None
        self._mapped_array_factory = mapped_array_factory
        self._time_fn = time_fn

        if encoder_factory is None:
            try:
                from picamera2.encoders import H264Encoder  # type: ignore
            except ImportError as exc:
                raise RuntimeError("recording dmabuf source requires Picamera2 encoders on this host") from exc
            encoder_factory = H264Encoder
        if file_output_factory is None:
            try:
                from picamera2.outputs import FileOutput  # type: ignore
            except ImportError as exc:
                raise RuntimeError("recording dmabuf source requires Picamera2 outputs on this host") from exc
            file_output_factory = FileOutput

        if self.overlay_enabled:
            if overlay_renderer_factory is None:
                overlay_renderer_factory = lambda: FastTextOverlayRenderer()
            if self._mapped_array_factory is None:
                try:
                    from picamera2 import MappedArray  # type: ignore
                except ImportError as exc:
                    raise RuntimeError("overlay-enabled recording dmabuf source requires Picamera2 MappedArray") from exc
                self._mapped_array_factory = MappedArray
            self._overlay_renderer = overlay_renderer_factory()

        self._encoder_factory = encoder_factory
        self._file_output_factory = file_output_factory

        super().__init__(
            camera_id=camera_id,
            resolution_px=resolution_px,
            frame_rate_hz=frame_rate_hz,
            sensor_mode=sensor_mode,
            request_mode=request_mode,
            pixel_format=pixel_format,
            picamera2_factory=picamera2_factory,
        )

        self.video_path.parent.mkdir(parents=True, exist_ok=True)
        self.timestamp_csv_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._recording_encoder = self._encoder_factory(bitrate=self.bitrate_bps)
            self._recording_output = self._file_output_factory(str(self.video_path))
            self._picam2.pre_callback = self._append_timestamp
            self._picam2.start_encoder(
                self._recording_encoder,
                self._recording_output,
                name="main",
            )
            self._recording_started = True
        except Exception:
            self.close()
            raise

    def _append_timestamp(self, request: Any) -> None:
        """Append timestamp metadata and optionally overlay it onto the luma plane.

        Args:
            request: Picamera2 completed request exposing ``get_metadata()`` and
                the mapped main image stream.

        Returns:
            None.
        """

        meta = request.get_metadata()
        sensor_timestamp_ns = int(meta.get("SensorTimestamp", 0))
        frame_duration_us = int(meta.get("FrameDuration", 0))
        unix_timestamp_s = float(self._time_fn())
        sample = TimestampSample(
            sensor_timestamp_ns=sensor_timestamp_ns,
            frame_duration_us=frame_duration_us,
            unix_timestamp_s=unix_timestamp_s,
        )
        self._timestamp_samples.append(sample)

        if not self.overlay_enabled or self._overlay_renderer is None or self._mapped_array_factory is None:
            return

        elapsed_s = 0.0
        if len(self._timestamp_samples) > 1:
            elapsed_s = (
                float(sensor_timestamp_ns) - float(self._timestamp_samples[0].sensor_timestamp_ns)
            ) / 1e9
        frame_rate_hz = (1e6 / float(frame_duration_us)) if frame_duration_us > 0 else 0.0

        with self._mapped_array_factory(request, "main") as mapped:
            frame = mapped.array
            frame_y = frame[:, :, 0] if getattr(frame, "ndim", 2) == 3 else frame
            self._overlay_renderer.draw_overlay(
                frame_y,
                elapsed_s=elapsed_s,
                unix_timestamp_s=unix_timestamp_s,
                frame_rate_hz=frame_rate_hz,
            )

    def _flush_timestamps(self) -> None:
        """Write accumulated timestamp samples to CSV.

        Returns:
            None.
        """

        with self.timestamp_csv_path.open("w", encoding="utf-8") as handle:
            handle.write("SensorTimestamp_ns,FrameDuration_us,UnixTimestamp_s\n")
            for sample in self._timestamp_samples:
                handle.write(
                    f"{sample.sensor_timestamp_ns},{sample.frame_duration_us},{sample.unix_timestamp_s}\n"
                )

    def diagnostics(self) -> dict[str, object]:
        """Return a JSON-serializable recording-source summary.

        Returns:
            dict[str, object]: Camera source metadata including recording paths
            and overlay settings.
        """

        diagnostics = super().diagnostics()
        diagnostics.update(
            {
                "overlay_enabled": self.overlay_enabled,
                "video_path": str(self.video_path),
                "timestamp_csv_path": str(self.timestamp_csv_path),
                "bitrate_bps": self.bitrate_bps,
                "timestamp_sample_count": len(self._timestamp_samples),
            }
        )
        return diagnostics

    def close(self) -> None:
        """Stop recording, flush timestamps, and close the underlying camera.

        Returns:
            None.
        """

        if getattr(self, "_closed", False):
            return
        try:
            self._picam2.pre_callback = None
        except Exception:
            pass
        if self._recording_started:
            try:
                self._picam2.stop_encoder(self._recording_encoder)
            except Exception:
                pass
            self._recording_started = False
        if self._recording_output is not None:
            try:
                close_output = getattr(self._recording_output, "close", None)
                if callable(close_output):
                    close_output()
            except Exception:
                pass
        try:
            self._flush_timestamps()
        except Exception:
            pass
        super().close()
