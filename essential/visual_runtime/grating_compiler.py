"""Precompute drifting grating frames for low-latency presentation."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .grating_specs import GratingSpec


@dataclass(frozen=True)
class CompiledGrating:
    """Precomputed grayscale frames ready for display.

    Attributes:
        spec: Source grating specification.
        frames: ``uint8`` array with shape ``(n_frames, height_px, width_px)``.
            Axis order is time, vertical pixel index, horizontal pixel index.
            Pixel units are grayscale values in ``[0, 255]``.
        resolution_px: Display resolution as ``(width_px, height_px)``.
        refresh_hz: Display refresh rate in Hz used to quantize frame count.
        degrees_subtended: Horizontal display extent in visual degrees.
        frame_interval_s: Duration of one frame in seconds, equal to
            ``1 / refresh_hz``.
    """

    spec: GratingSpec
    frames: np.ndarray
    resolution_px: tuple[int, int]
    refresh_hz: float
    degrees_subtended: float
    frame_interval_s: float

    @property
    def frame_count(self) -> int:
        """Return the number of precomputed frames."""

        return int(self.frames.shape[0])


def compile_grating(
    spec: GratingSpec,
    resolution_px: tuple[int, int] | None,
    refresh_hz: float,
    degrees_subtended: float | None,
) -> CompiledGrating:
    """Compile a drifting grating specification into precomputed frames.

    Args:
        spec: Validated grating parameters.
        resolution_px: Output resolution as ``(width_px, height_px)``. If
            ``None``, ``spec.resolution_px`` must be present.
        refresh_hz: Display refresh rate in Hz used to quantize frame count.
        degrees_subtended: Horizontal extent of the display in visual degrees.
            If ``None``, ``spec.degrees_subtended`` must be present.

    Returns:
        CompiledGrating: Precomputed frame stack and its display metadata.
    """

    if refresh_hz <= 0:
        raise ValueError("refresh_hz must be > 0")

    if resolution_px is None:
        resolution_px = spec.resolution_px
    if resolution_px is None:
        raise ValueError("resolution_px must be provided either in the session or the JSON spec")

    if degrees_subtended is None:
        degrees_subtended = spec.degrees_subtended
    if degrees_subtended is None:
        raise ValueError("degrees_subtended must be provided either in the session or the JSON spec")
    if degrees_subtended <= 0:
        raise ValueError("degrees_subtended must be > 0")

    width_px, height_px = resolution_px
    frame_count = max(1, int(round(spec.duration_s * refresh_hz)))
    frame_interval_s = 1.0 / refresh_hz

    horizontal_deg = float(degrees_subtended)
    vertical_deg = horizontal_deg * float(height_px) / float(width_px)
    x_deg = np.linspace(
        -horizontal_deg / 2.0,
        horizontal_deg / 2.0,
        num=width_px,
        endpoint=False,
        dtype=np.float32,
    )
    y_deg = np.linspace(
        -vertical_deg / 2.0,
        vertical_deg / 2.0,
        num=height_px,
        endpoint=False,
        dtype=np.float32,
    )
    angle_rad = math.radians(spec.angle_deg)
    projection_deg = (
        np.cos(angle_rad, dtype=np.float32) * x_deg[np.newaxis, :]
        + np.sin(angle_rad, dtype=np.float32) * y_deg[:, np.newaxis]
    )
    spatial_phase = (2.0 * np.pi * spec.spatial_freq_cpd * projection_deg).astype(np.float32, copy=False)

    background = float(spec.background_gray_u8)
    amplitude = float(spec.contrast) * min(background, 255.0 - background)
    wave_buffer = np.empty((height_px, width_px), dtype=np.float32)
    frame_buffer = np.empty((height_px, width_px), dtype=np.float32)
    frames = np.empty((frame_count, height_px, width_px), dtype=np.uint8)

    for frame_index in range(frame_count):
        temporal_phase = np.float32(2.0 * np.pi * spec.temporal_freq_hz * frame_index * frame_interval_s)
        np.subtract(spatial_phase, temporal_phase, out=wave_buffer)
        np.sin(wave_buffer, out=wave_buffer)
        if spec.waveform == "square":
            np.sign(wave_buffer, out=wave_buffer)
            wave_buffer[wave_buffer == 0.0] = 1.0
        np.multiply(wave_buffer, amplitude, out=frame_buffer)
        np.add(frame_buffer, background, out=frame_buffer)
        np.rint(frame_buffer, out=frame_buffer)
        np.clip(frame_buffer, 0.0, 255.0, out=frame_buffer)
        frames[frame_index] = frame_buffer.astype(np.uint8, copy=False)

    return CompiledGrating(
        spec=spec,
        frames=frames,
        resolution_px=(width_px, height_px),
        refresh_hz=float(refresh_hz),
        degrees_subtended=float(degrees_subtended),
        frame_interval_s=frame_interval_s,
    )
