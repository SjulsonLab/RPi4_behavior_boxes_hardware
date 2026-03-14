"""Cue import and canonical audio asset loading.

This module owns the file-layout contract for BehavBox sounds.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from math import gcd
from pathlib import Path
from typing import Final

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly


LOGGER = logging.getLogger(__name__)
DEFAULT_SAMPLE_RATE_HZ: Final[int] = 48_000
DEFAULT_REFERENCE_RMS: Final[float] = 0.1


@dataclass(frozen=True)
class AudioPaths:
    """Filesystem layout for BehavBox audio cues.

    Args:
        tracked_sounds_dir: Directory containing tracked canonical mono WAV
            files. Files have shape ``(num_frames,)`` and units of normalized
            audio amplitude in ``[-1.0, 1.0]`` before PCM conversion.
        local_source_dir: Gitignored directory containing raw source WAV files.
        local_sounds_dir: Gitignored directory containing canonical cues
            generated during local bench work.
    """

    tracked_sounds_dir: Path
    local_source_dir: Path
    local_sounds_dir: Path

    def ensure_directories(self) -> None:
        """Create any missing directories in the audio layout.

        Returns:
            ``None``.
        """

        self.tracked_sounds_dir.mkdir(parents=True, exist_ok=True)
        self.local_source_dir.mkdir(parents=True, exist_ok=True)
        self.local_sounds_dir.mkdir(parents=True, exist_ok=True)

    def resolve_source_path(self, source_name: str) -> Path:
        """Resolve a raw source cue name under ``local_source_dir``.

        Args:
            source_name: Cue basename with or without the ``.wav`` suffix.

        Returns:
            Resolved WAV file path under ``local_source_dir``.
        """

        source = Path(source_name)
        filename = source.name if source.suffix else f"{source.name}.wav"
        return self.local_source_dir / filename

    def resolve_output_path(self, cue_name: str) -> Path:
        """Resolve canonical output path under ``local_sounds_dir``.

        Args:
            cue_name: Cue basename with or without the ``.wav`` suffix.

        Returns:
            Canonical output WAV file path under ``local_sounds_dir``.
        """

        cue = Path(cue_name)
        filename = cue.name if cue.suffix else f"{cue.name}.wav"
        return self.local_sounds_dir / filename

    def resolve_canonical_path(self, cue_name: str) -> Path:
        """Resolve a canonical cue from local or tracked directories.

        Args:
            cue_name: Cue basename with or without the ``.wav`` suffix.

        Returns:
            First matching canonical WAV path, preferring
            ``local_sounds_dir`` over ``tracked_sounds_dir``.

        Raises:
            FileNotFoundError: If the cue does not exist in either directory.
        """

        cue = Path(cue_name)
        filename = cue.name if cue.suffix else f"{cue.name}.wav"
        local_path = self.local_sounds_dir / filename
        if local_path.exists():
            return local_path
        tracked_path = self.tracked_sounds_dir / filename
        if tracked_path.exists():
            return tracked_path
        raise FileNotFoundError(f"Canonical cue not found: {filename}")


@dataclass(frozen=True)
class CanonicalWaveform:
    """Canonical mono cue representation.

    Args:
        waveform_mono: One-dimensional ``float32`` array of shape
            ``(num_frames,)`` with normalized amplitude units in ``[-1.0, 1.0]``.
        sample_rate_hz: Sampling rate in hertz.
        peak_abs: Peak absolute amplitude in normalized units.
        rms: Root mean square amplitude in normalized units.
    """

    waveform_mono: np.ndarray
    sample_rate_hz: int
    peak_abs: float
    rms: float


class CueImporter:
    """Import raw WAV files into the canonical BehavBox cue format.

    Args:
        paths: Filesystem layout for raw and canonical cue files.
        target_sample_rate_hz: Target sample rate in hertz.
        reference_rms: Canonical RMS amplitude used to normalize imported
            cues.
    """

    def __init__(
        self,
        paths: AudioPaths,
        target_sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
        reference_rms: float = DEFAULT_REFERENCE_RMS,
    ):
        self.paths = paths
        self.target_sample_rate_hz = int(target_sample_rate_hz)
        self.reference_rms = float(reference_rms)
        self.paths.ensure_directories()

    def import_wav_file(
        self,
        source_name: str,
        cue_name: str | None = None,
        overwrite: bool = False,
        max_duration_s: float = 10.0,
        allow_longer: bool = False,
    ) -> Path:
        """Import a source WAV file into the canonical cue directory.

        Args:
            source_name: Source basename resolved under ``local_source_dir``.
            cue_name: Optional canonical basename. If omitted, defaults to the
                source stem.
            overwrite: Whether an existing canonical cue may be replaced.
            max_duration_s: Maximum allowed imported duration in seconds when
                ``allow_longer`` is ``False``.
            allow_longer: If ``True``, preserve full source duration.

        Returns:
            Path to the canonical cue written under ``local_sounds_dir``.
        """

        source_path = self.paths.resolve_source_path(source_name)
        if not source_path.exists():
            raise FileNotFoundError(f"Source cue not found: {source_path}")

        output_stem = cue_name or Path(source_name).stem
        output_path = self.paths.resolve_output_path(output_stem)
        if output_path.exists() and not overwrite:
            raise FileExistsError(
                f"Canonical cue already exists: {output_path.name}. "
                "Pass overwrite=True to replace it."
            )

        sample_rate_hz, raw = wavfile.read(source_path)
        waveform = _wav_to_float32(raw)
        if waveform.ndim == 2:
            waveform = waveform.mean(axis=1, dtype=np.float32)

        if not allow_longer:
            max_source_frames = int(round(max_duration_s * sample_rate_hz))
            if waveform.shape[0] > max_source_frames:
                LOGGER.warning(
                    "Source cue '%s' was truncated to %.3f s; pass allow_longer=True to keep the full file.",
                    source_path.name,
                    max_duration_s,
                )
                waveform = waveform[:max_source_frames]

        waveform = _resample_mono(waveform, int(sample_rate_hz), self.target_sample_rate_hz)
        waveform = waveform.astype(np.float32, copy=False)
        if waveform.size:
            waveform = waveform - np.mean(waveform, dtype=np.float64)
        waveform = _normalize_rms(waveform, target_rms=self.reference_rms)
        wavfile.write(output_path, self.target_sample_rate_hz, _float32_to_int16(waveform))
        return output_path

    def load_canonical_waveform(self, cue_name: str) -> CanonicalWaveform:
        """Load a canonical cue from local or tracked storage.

        Args:
            cue_name: Canonical cue basename with or without ``.wav`` suffix.

        Returns:
            CanonicalWaveform with mono ``float32`` samples.
        """

        cue_path = self.paths.resolve_canonical_path(cue_name)
        sample_rate_hz, raw = wavfile.read(cue_path)
        waveform = _wav_to_float32(raw)
        if waveform.ndim == 2:
            waveform = waveform.mean(axis=1, dtype=np.float32)
        waveform = waveform.astype(np.float32, copy=False)
        peak_abs = float(np.max(np.abs(waveform))) if waveform.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(waveform, dtype=np.float32)))) if waveform.size else 0.0
        return CanonicalWaveform(
            waveform_mono=waveform,
            sample_rate_hz=int(sample_rate_hz),
            peak_abs=peak_abs,
            rms=rms,
        )


def _wav_to_float32(samples: np.ndarray) -> np.ndarray:
    """Convert WAV sample arrays to normalized ``float32`` audio.

    Args:
        samples: Array of shape ``(num_frames,)`` or
            ``(num_frames, num_channels)`` in integer or floating-point WAV
            units.

    Returns:
        Array with the same shape in normalized amplitude units.
    """

    if np.issubdtype(samples.dtype, np.floating):
        return samples.astype(np.float32, copy=False)
    if samples.dtype == np.int16:
        return samples.astype(np.float32) / 32768.0
    if samples.dtype == np.int32:
        return samples.astype(np.float32) / 2147483648.0
    if samples.dtype == np.uint8:
        return (samples.astype(np.float32) - 128.0) / 128.0
    raise TypeError(f"Unsupported WAV dtype: {samples.dtype}")


def _float32_to_int16(waveform: np.ndarray) -> np.ndarray:
    """Convert normalized mono audio to signed 16-bit PCM samples.

    Args:
        waveform: Array of shape ``(num_frames,)`` with normalized amplitude.

    Returns:
        ``int16`` array of shape ``(num_frames,)``.
    """

    clipped = np.clip(waveform, -1.0, 1.0)
    return np.round(clipped * 32767.0).astype(np.int16)


def _resample_mono(
    waveform: np.ndarray,
    source_rate_hz: int,
    target_rate_hz: int,
) -> np.ndarray:
    """Resample a mono waveform with polyphase filtering.

    Args:
        waveform: One-dimensional mono waveform of shape ``(num_frames,)``.
        source_rate_hz: Original sampling rate in hertz.
        target_rate_hz: Target sampling rate in hertz.

    Returns:
        Resampled mono waveform of shape ``(num_target_frames,)``.
    """

    if source_rate_hz == target_rate_hz:
        return waveform.astype(np.float32, copy=False)
    divisor = gcd(source_rate_hz, target_rate_hz)
    up = target_rate_hz // divisor
    down = source_rate_hz // divisor
    return resample_poly(waveform, up, down).astype(np.float32, copy=False)


def _normalize_rms(waveform: np.ndarray, target_rms: float) -> np.ndarray:
    """Scale waveform RMS to a target amplitude.

    Args:
        waveform: Mono waveform of shape ``(num_frames,)``.
        target_rms: Target normalized RMS amplitude.

    Returns:
        RMS-normalized waveform of shape ``(num_frames,)``.
    """

    if waveform.size == 0:
        return waveform.astype(np.float32, copy=False)
    current_rms = float(np.sqrt(np.mean(np.square(waveform, dtype=np.float32))))
    if current_rms <= 1e-12:
        return waveform.astype(np.float32, copy=False)
    gain = float(target_rms) / current_rms
    return (waveform * gain).astype(np.float32, copy=False)
