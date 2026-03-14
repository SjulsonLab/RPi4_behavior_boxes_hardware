"""Persistent audio runtime for BehavBox cue playback."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading
import time
from typing import Protocol

import numpy as np

from box_runtime.audio.assets import (
    LoadedSound,
    PlaybackRender,
    build_loaded_sound,
    generate_white_noise,
    predicted_peak_overshoot_db,
    render_playback_frames,
)
from box_runtime.audio.importer import (
    AudioPaths,
    CueImporter,
    DEFAULT_REFERENCE_RMS,
    DEFAULT_SAMPLE_RATE_HZ,
)
from box_runtime.audio.latency import PyAlsaCaptureDevice, measure_loopback_latency

try:
    import alsaaudio
except Exception:  # pragma: no cover - exercised on Raspberry Pi hardware
    alsaaudio = None


LOGGER = logging.getLogger(__name__)


class PlaybackBackend(Protocol):
    """Protocol for stereo playback backends."""

    sample_rate_hz: int

    def write_frames(self, frames: np.ndarray, stop_requested) -> int:
        """Write stereo playback frames.

        Args:
            frames: Stereo ``int16`` array of shape ``(num_frames, 2)``.
            stop_requested: Zero-argument callable returning whether playback
                should stop early.

        Returns:
            Number of stereo frames consumed.
        """

    def close(self) -> None:
        """Close the backend and release resources."""


@dataclass(frozen=True)
class PlaybackRequest:
    """Playback request stored by the runtime worker.

    Args:
        name: Cue identifier.
        render: Rendered stereo playback buffer.
    """

    name: str
    render: PlaybackRender


class NullPlaybackBackend:
    """Fallback backend that raises on playback when ALSA is unavailable."""

    def __init__(self, sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ):
        self.sample_rate_hz = int(sample_rate_hz)

    def write_frames(self, frames: np.ndarray, stop_requested) -> int:
        raise RuntimeError("Audio playback is unavailable because pyalsaaudio is not installed.")

    def close(self) -> None:
        return None


class RecordingPlaybackBackend:
    """Mock playback backend that records submitted stereo buffers.

    Args:
        sample_rate_hz: Playback sampling rate in hertz.
        chunk_frames: Number of stereo frames consumed per worker iteration.
        chunk_sleep_s: Optional per-chunk sleep used to emulate slower devices.
    """

    def __init__(self, sample_rate_hz: int, chunk_frames: int = 256, chunk_sleep_s: float = 0.0):
        self.sample_rate_hz = int(sample_rate_hz)
        self.chunk_frames = int(chunk_frames)
        self.chunk_sleep_s = float(chunk_sleep_s)
        self.play_calls: list[np.ndarray] = []

    def write_frames(self, frames: np.ndarray, stop_requested) -> int:
        """Record the played stereo frames.

        Args:
            frames: Stereo ``int16`` array of shape ``(num_frames, 2)``.
            stop_requested: Zero-argument callable returning whether playback
                should stop early.

        Returns:
            Number of stereo frames consumed.
        """

        consumed = 0
        chunks: list[np.ndarray] = []
        total_frames = int(frames.shape[0])
        while consumed < total_frames:
            if stop_requested():
                break
            stop = min(consumed + self.chunk_frames, total_frames)
            chunks.append(frames[consumed:stop].copy())
            consumed = stop
            if self.chunk_sleep_s > 0:
                time.sleep(self.chunk_sleep_s)
        self.play_calls.append(np.vstack(chunks) if chunks else np.empty((0, 2), dtype=np.int16))
        return consumed

    def close(self) -> None:
        return None


class PyAlsaAudioPlaybackBackend:
    """Persistent ALSA playback backend for stereo PCM audio."""

    def __init__(
        self,
        device_name: str = "default",
        sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
        period_size_frames: int = 256,
    ):
        if alsaaudio is None:  # pragma: no cover - exercised on Raspberry Pi hardware
            raise RuntimeError("pyalsaaudio is not installed; ALSA playback is unavailable.")
        self._pcm = alsaaudio.PCM(alsaaudio.PCM_PLAYBACK, alsaaudio.PCM_NORMAL, device_name)
        self._pcm.setchannels(2)
        self._pcm.setrate(sample_rate_hz)
        self._pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
        self.period_size_frames = int(self._pcm.setperiodsize(period_size_frames))
        self.sample_rate_hz = int(sample_rate_hz)

    def write_frames(self, frames: np.ndarray, stop_requested) -> int:
        consumed = 0
        total_frames = int(frames.shape[0])
        while consumed < total_frames:
            if stop_requested():
                break

            actual_end = min(consumed + self.period_size_frames, total_frames)
            actual_chunk = frames[consumed:actual_end]
            if actual_chunk.shape[0] < self.period_size_frames:
                padded = np.zeros((self.period_size_frames, 2), dtype=np.int16)
                padded[:actual_chunk.shape[0]] = actual_chunk
                chunk = padded
            else:
                chunk = actual_chunk

            self._pcm.write(chunk.tobytes(order="C"))
            consumed = actual_end
        return consumed

    def close(self) -> None:
        self._pcm.close()


class SoundRuntime:
    """Persistent playback runtime for imported and generated BehavBox cues.

    Args:
        paths: Filesystem layout for source and canonical cue files.
        backend: Optional playback backend. If omitted, a real ALSA backend is
            created when possible.
        device_name: ALSA device string for playback and capture.
        sample_rate_hz: Desired sampling rate in hertz.
        period_size_frames: Period size in stereo frames for ALSA playback.
        ramp_duration_s: Ramp duration in seconds applied to all cue edges.
        reference_rms: Canonical RMS amplitude used for imports and built-in
            white noise generation.
    """

    def __init__(
        self,
        paths: AudioPaths,
        backend: PlaybackBackend | None = None,
        device_name: str = "default",
        sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
        period_size_frames: int = 256,
        ramp_duration_s: float = 0.002,
        reference_rms: float = DEFAULT_REFERENCE_RMS,
    ):
        self.paths = paths
        self.paths.ensure_directories()
        self.importer = CueImporter(
            paths=paths,
            target_sample_rate_hz=sample_rate_hz,
            reference_rms=reference_rms,
        )
        self.sample_rate_hz = int(sample_rate_hz)
        self.reference_rms = float(reference_rms)
        self.ramp_duration_s = float(ramp_duration_s)
        self.device_name = str(device_name)
        self.period_size_frames = int(period_size_frames)
        self.backend = backend or self._build_backend()
        self.loaded_sounds: dict[str, LoadedSound] = {}
        self._condition = threading.Condition()
        self._pending_request: tuple[int, PlaybackRequest] | None = None
        self._active_token = 0
        self._stop_token = 0
        self._busy = False
        self._shutdown = False
        self._worker = threading.Thread(target=self._worker_main, name="behavbox-sound", daemon=True)
        self._worker.start()

    def import_wav_file(
        self,
        source_name: str,
        cue_name: str | None = None,
        overwrite: bool = False,
        max_duration_s: float = 10.0,
        allow_longer: bool = False,
    ) -> Path:
        """Import a raw source WAV into the local canonical cue directory."""

        return self.importer.import_wav_file(
            source_name=source_name,
            cue_name=cue_name,
            overwrite=overwrite,
            max_duration_s=max_duration_s,
            allow_longer=allow_longer,
        )

    def load_sound(self, name: str) -> LoadedSound:
        """Load a canonical cue into RAM by name.

        Args:
            name: Cue basename with or without ``.wav`` suffix.

        Returns:
            LoadedSound prepared for playback.
        """

        cue = self.importer.load_canonical_waveform(name)
        loaded = build_loaded_sound(
            name=Path(name).stem,
            waveform_mono=cue.waveform_mono,
            sample_rate_hz=cue.sample_rate_hz,
            ramp_duration_s=self.ramp_duration_s,
        )
        self.loaded_sounds[loaded.name] = loaded
        return loaded

    def loaded_sound_names(self) -> list[str]:
        """Return the sorted list of loaded cue names."""

        return sorted(self.loaded_sounds)

    def clear_sounds(self) -> None:
        """Release all loaded cues from RAM."""

        self.loaded_sounds.clear()

    def register_white_noise(self, name: str, duration_s: float, seed: int = 0) -> LoadedSound:
        """Register a generated white-noise cue directly in memory.

        Args:
            name: Cue identifier.
            duration_s: Cue duration in seconds.
            seed: Deterministic random seed for waveform generation.

        Returns:
            LoadedSound prepared for playback.
        """

        waveform = generate_white_noise(
            duration_s=float(duration_s),
            sample_rate_hz=self.sample_rate_hz,
            rms=self.reference_rms,
            seed=int(seed),
        )
        loaded = build_loaded_sound(
            name=Path(name).stem,
            waveform_mono=waveform,
            sample_rate_hz=self.sample_rate_hz,
            ramp_duration_s=self.ramp_duration_s,
        )
        self.loaded_sounds[loaded.name] = loaded
        return loaded

    def play_sound(
        self,
        name: str,
        side: str = "both",
        gain_db: float = 0.0,
        duration_s: float | None = None,
    ) -> None:
        """Queue a cue for playback.

        Args:
            name: Loaded cue name.
            side: Playback side, one of ``"left"``, ``"right"``, or ``"both"``.
            gain_db: Playback gain in decibels.
            duration_s: Requested duration in seconds, or ``None`` to use the
                cue duration.
        """

        cue_key = Path(name).stem
        if cue_key not in self.loaded_sounds:
            raise KeyError(f"Sound '{cue_key}' is not loaded. Call load_sound() first.")
        render = render_playback_frames(
            self.loaded_sounds[cue_key],
            side=side,
            gain_db=gain_db,
            duration_s=duration_s,
        )
        request = PlaybackRequest(name=cue_key, render=render)
        with self._condition:
            next_token = self._active_token + 1
            self._active_token = next_token
            self._pending_request = (next_token, request)
            self._busy = True
            self._condition.notify_all()

    def stop_sound(self) -> None:
        """Interrupt the currently playing cue, if any."""

        with self._condition:
            self._stop_token = max(self._stop_token, self._active_token + 1)
            self._active_token = max(self._active_token, self._stop_token)
            self._condition.notify_all()

    def start_sound_calibration(self, side: str = "both", gain_db: float = 0.0) -> None:
        """Start long-running white-noise playback for hardware calibration."""

        calibration_name = "__white_noise_calibration__"
        if calibration_name not in self.loaded_sounds:
            waveform = generate_white_noise(
                duration_s=1.0,
                sample_rate_hz=self.sample_rate_hz,
                rms=self.reference_rms,
                seed=0,
            )
            self.loaded_sounds[calibration_name] = build_loaded_sound(
                name=calibration_name,
                waveform_mono=waveform,
                sample_rate_hz=self.sample_rate_hz,
                ramp_duration_s=self.ramp_duration_s,
            )
        self.play_sound(calibration_name, side=side, gain_db=gain_db, duration_s=3600.0)

    def stop_sound_calibration(self) -> None:
        """Stop the long-running calibration noise."""

        self.stop_sound()

    def wait_until_idle(self, timeout_s: float) -> bool:
        """Wait until playback becomes idle.

        Args:
            timeout_s: Maximum wait duration in seconds.

        Returns:
            ``True`` if the runtime became idle before timeout, otherwise
            ``False``.
        """

        deadline = time.time() + timeout_s
        with self._condition:
            while self._busy or self._pending_request is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            return True

    def measure_sound_latency(
        self,
        name: str,
        side: str = "both",
        gain_db: float = 0.0,
        repeats: int = 3,
    ) -> list[float]:
        """Measure loopback latency for a loaded cue.

        Args:
            name: Loaded cue name.
            side: Playback side.
            gain_db: Playback gain in decibels.
            repeats: Number of repeated measurements.

        Returns:
            List of latency values in milliseconds.
        """

        cue_key = Path(name).stem
        if cue_key not in self.loaded_sounds:
            raise KeyError(f"Sound '{cue_key}' is not loaded. Call load_sound() first.")
        render = render_playback_frames(
            self.loaded_sounds[cue_key],
            side=side,
            gain_db=gain_db,
            duration_s=None,
        )
        reference_mono = np.max(
            np.abs(render.frames_int16.astype(np.float32) / 32767.0),
            axis=1,
        )

        latencies_ms: list[float] = []
        for _ in range(int(repeats)):
            capture = PyAlsaCaptureDevice(
                device_name=self.device_name,
                sample_rate_hz=self.sample_rate_hz,
                period_size_frames=self.period_size_frames,
            )
            try:
                measurement = measure_loopback_latency(
                    capture_device=capture,
                    play_callback=lambda: self.play_sound(cue_key, side=side, gain_db=gain_db),
                    played_waveform_mono=reference_mono,
                    sample_rate_hz=self.sample_rate_hz,
                )
                self.wait_until_idle(timeout_s=5.0)
                latencies_ms.append(measurement.latency_ms)
            finally:
                capture.close()
        return latencies_ms

    def close(self) -> None:
        """Shut down the playback worker and close the backend."""

        with self._condition:
            self._shutdown = True
            self._condition.notify_all()
        self._worker.join(timeout=2.0)
        self.backend.close()

    def _build_backend(self) -> PlaybackBackend:
        if str(os.environ.get("BEHAVBOX_MOCK_AUDIO", "0")).strip().lower() in {"1", "true", "yes", "on"}:
            LOGGER.info("Using recording audio backend because BEHAVBOX_MOCK_AUDIO is enabled.")
            return RecordingPlaybackBackend(sample_rate_hz=self.sample_rate_hz)
        if alsaaudio is None:  # pragma: no cover - exercised on Raspberry Pi hardware
            LOGGER.warning("pyalsaaudio is unavailable; using null audio backend.")
            return NullPlaybackBackend(sample_rate_hz=self.sample_rate_hz)
        return PyAlsaAudioPlaybackBackend(
            device_name=self.device_name,
            sample_rate_hz=self.sample_rate_hz,
            period_size_frames=self.period_size_frames,
        )

    def _worker_main(self) -> None:
        while True:
            with self._condition:
                while not self._shutdown and self._pending_request is None:
                    self._condition.wait()
                if self._shutdown:
                    self._busy = False
                    self._condition.notify_all()
                    return
                token, request = self._pending_request
                self._pending_request = None

            try:
                self.backend.write_frames(
                    request.render.frames_int16,
                    stop_requested=lambda: self._interrupt_requested(token),
                )
            finally:
                self._log_clipping_if_needed(request)
                with self._condition:
                    if self._pending_request is None:
                        self._busy = False
                        self._condition.notify_all()

    def _interrupt_requested(self, token: int) -> bool:
        with self._condition:
            if self._shutdown:
                return True
            if self._stop_token > token:
                return True
            if self._pending_request is not None and self._pending_request[0] > token:
                return True
            return False

    def _log_clipping_if_needed(self, request: PlaybackRequest) -> None:
        render = request.render
        overshoot_db = predicted_peak_overshoot_db(render.predicted_peak_abs)
        clipped_percent = 100.0 * render.clipped_fraction
        if overshoot_db < 1.0 and render.clipped_fraction < 0.001:
            return
        LOGGER.warning(
            "Audio cue '%s' clipped approximately %.3f%% of output samples (predicted peak overshoot %.2f dB).",
            request.name,
            clipped_percent,
            overshoot_db,
        )
