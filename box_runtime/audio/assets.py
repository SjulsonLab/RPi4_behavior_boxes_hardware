"""In-memory cue preparation for BehavBox audio playback."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class LoadedSound:
    """Preloaded canonical cue ready for playback rendering.

    Args:
        name: Cue identifier.
        waveform_mono: Mono waveform with shape ``(num_frames,)`` and
            normalized amplitude units in ``[-1.0, 1.0]``.
        sample_rate_hz: Sampling rate in hertz.
        routed_base: Mapping from side label to stereo waveform arrays with
            shape ``(num_frames, 2)`` and normalized amplitude units.
        routed_loop: Mapping from side label to loop-safe stereo waveform arrays
            with shape ``(num_frames, 2)`` and normalized amplitude units.
        peak_abs: Peak absolute amplitude of the canonical mono waveform.
    """

    name: str
    waveform_mono: np.ndarray
    sample_rate_hz: int
    routed_base: dict[str, np.ndarray]
    routed_loop: dict[str, np.ndarray]
    peak_abs: float


@dataclass(frozen=True)
class PlaybackRender:
    """Rendered stereo playback buffer and clipping metadata.

    Args:
        frames_int16: Stereo playback buffer of shape ``(num_frames, 2)`` in
            signed 16-bit PCM sample units.
        predicted_peak_abs: Predicted peak absolute normalized amplitude before
            clipping.
        clipped_samples: Number of individual channel samples predicted to clip.
        total_samples: Total number of individual channel samples in the output
            buffer.
    """

    frames_int16: np.ndarray
    predicted_peak_abs: float
    clipped_samples: int
    total_samples: int

    @property
    def clipped_fraction(self) -> float:
        """Return the fraction of output samples that would clip."""

        if self.total_samples == 0:
            return 0.0
        return float(self.clipped_samples) / float(self.total_samples)


def build_loaded_sound(
    name: str,
    waveform_mono: np.ndarray,
    sample_rate_hz: int,
    ramp_duration_s: float,
) -> LoadedSound:
    """Build routed stereo buffers for one canonical cue.

    Args:
        name: Cue identifier.
        waveform_mono: Mono waveform of shape ``(num_frames,)``.
        sample_rate_hz: Sampling rate in hertz.
        ramp_duration_s: Endpoint ramp duration in seconds.

    Returns:
        LoadedSound with precomputed stereo routing.
    """

    ramp_frames = max(1, int(round(ramp_duration_s * sample_rate_hz)))
    base_mono = _apply_endpoint_ramps(waveform_mono.astype(np.float32, copy=False), ramp_frames)
    loop_mono = _apply_endpoint_ramps(base_mono, ramp_frames)
    routed_base = {side: _route_to_stereo(base_mono, side) for side in ("left", "right", "both")}
    routed_loop = {side: _route_to_stereo(loop_mono, side) for side in ("left", "right", "both")}
    peak_abs = float(np.max(np.abs(waveform_mono))) if waveform_mono.size else 0.0
    return LoadedSound(
        name=name,
        waveform_mono=waveform_mono.astype(np.float32, copy=False),
        sample_rate_hz=int(sample_rate_hz),
        routed_base=routed_base,
        routed_loop=routed_loop,
        peak_abs=peak_abs,
    )


def generate_white_noise(
    duration_s: float,
    sample_rate_hz: int,
    rms: float,
    seed: int = 0,
) -> np.ndarray:
    """Generate deterministic mono white noise.

    Args:
        duration_s: Desired duration in seconds.
        sample_rate_hz: Sampling rate in hertz.
        rms: Target RMS amplitude in normalized units.
        seed: Random seed for reproducibility.

    Returns:
        Mono ``float32`` waveform of shape ``(num_frames,)``.
    """

    frame_count = max(1, int(round(duration_s * sample_rate_hz)))
    random_source = np.random.default_rng(seed)
    waveform = random_source.standard_normal(frame_count, dtype=np.float32)
    current_rms = float(np.sqrt(np.mean(np.square(waveform, dtype=np.float32))))
    if current_rms > 0:
        waveform = waveform * (float(rms) / current_rms)
    return waveform.astype(np.float32, copy=False)


def render_playback_frames(
    sound: LoadedSound,
    side: str,
    gain_db: float,
    duration_s: float | None,
) -> PlaybackRender:
    """Render a stereo playback buffer for one cue request.

    Args:
        sound: Preloaded cue.
        side: One of ``"left"``, ``"right"``, or ``"both"``.
        gain_db: Playback gain in decibels.
        duration_s: Requested playback duration in seconds, or ``None`` to use
            the cue duration.

    Returns:
        PlaybackRender containing the stereo PCM buffer and clipping metadata.
    """

    if side not in sound.routed_base:
        raise ValueError(f"Unsupported playback side: {side}")

    if duration_s is None:
        target_frames = int(sound.routed_base[side].shape[0])
    else:
        target_frames = max(0, int(round(duration_s * sound.sample_rate_hz)))

    if target_frames == 0:
        return PlaybackRender(
            frames_int16=np.empty((0, 2), dtype=np.int16),
            predicted_peak_abs=0.0,
            clipped_samples=0,
            total_samples=0,
        )

    base = sound.routed_base[side]
    loop = sound.routed_loop[side]
    if target_frames <= base.shape[0]:
        stereo = base[:target_frames].copy()
    else:
        repeat_count = target_frames // loop.shape[0]
        remainder = target_frames % loop.shape[0]
        chunks = [loop] * repeat_count
        if remainder:
            chunks.append(loop[:remainder])
        stereo = np.vstack(chunks).astype(np.float32, copy=False)

    linear_gain = db_to_linear(gain_db)
    scaled = stereo * linear_gain
    clipped_mask = np.abs(scaled) > 1.0
    clipped_samples = int(np.count_nonzero(clipped_mask))
    total_samples = int(scaled.size)
    predicted_peak_abs = float(np.max(np.abs(scaled))) if scaled.size else 0.0
    clipped = np.clip(scaled, -1.0, 1.0)
    frames_int16 = np.round(clipped * 32767.0).astype(np.int16)
    return PlaybackRender(
        frames_int16=frames_int16,
        predicted_peak_abs=predicted_peak_abs,
        clipped_samples=clipped_samples,
        total_samples=total_samples,
    )


def db_to_linear(gain_db: float) -> float:
    """Convert gain in decibels to a linear multiplier."""

    return float(math.pow(10.0, float(gain_db) / 20.0))


def predicted_peak_overshoot_db(predicted_peak_abs: float) -> float:
    """Compute peak overshoot above full scale in decibels.

    Args:
        predicted_peak_abs: Predicted pre-clipped peak amplitude.

    Returns:
        Overshoot above full scale in decibels. Returns ``0.0`` if the peak is
        at or below full scale.
    """

    if predicted_peak_abs <= 1.0:
        return 0.0
    return 20.0 * math.log10(predicted_peak_abs)


def _route_to_stereo(waveform_mono: np.ndarray, side: str) -> np.ndarray:
    stereo = np.zeros((waveform_mono.shape[0], 2), dtype=np.float32)
    if side == "left":
        stereo[:, 0] = waveform_mono
    elif side == "right":
        stereo[:, 1] = waveform_mono
    elif side == "both":
        stereo[:, 0] = waveform_mono
        stereo[:, 1] = waveform_mono
    else:
        raise ValueError(f"Unsupported stereo routing side: {side}")
    return stereo


def _apply_endpoint_ramps(waveform: np.ndarray, ramp_frames: int) -> np.ndarray:
    if waveform.size == 0:
        return waveform.astype(np.float32, copy=False)
    ramp_frames = max(1, min(int(ramp_frames), waveform.shape[0] // 2 or 1))
    output = waveform.astype(np.float32, copy=True)
    if waveform.shape[0] == 1:
        output[0] = 0.0
        return output
    ramp_up = np.linspace(0.0, 1.0, ramp_frames, endpoint=False, dtype=np.float32)
    ramp_down = np.linspace(1.0, 0.0, ramp_frames, endpoint=False, dtype=np.float32)
    output[:ramp_frames] *= ramp_up
    output[-ramp_frames:] *= ramp_down
    return output
