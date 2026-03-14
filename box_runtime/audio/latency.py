"""Loopback latency estimation utilities for BehavBox audio."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Callable

import numpy as np
from scipy.io import wavfile

try:
    import alsaaudio
except Exception:  # pragma: no cover - exercised on Raspberry Pi hardware
    alsaaudio = None


def estimate_loopback_latency_samples(recorded: np.ndarray, played: np.ndarray) -> int:
    """Estimate onset latency between recorded and played waveforms.

    Args:
        recorded: One-dimensional mono waveform of shape ``(num_recorded_frames,)``.
        played: One-dimensional mono waveform of shape ``(num_played_frames,)``.

    Returns:
        Estimated onset offset in samples from the start of ``recorded``.
    """

    recorded = np.asarray(recorded, dtype=np.float32)
    played = np.asarray(played, dtype=np.float32)
    if recorded.ndim != 1 or played.ndim != 1:
        raise ValueError("estimate_loopback_latency_samples expects one-dimensional inputs.")
    if recorded.size < played.size or played.size == 0:
        raise ValueError("Recorded waveform must be at least as long as the played waveform.")
    correlation = np.correlate(recorded, played, mode="valid")
    return int(np.argmax(correlation))


@dataclass(frozen=True)
class LoopbackMeasurement:
    """Measured loopback latency for one playback repetition.

    Args:
        offset_samples: Estimated onset offset in samples.
        sample_rate_hz: Sampling rate in hertz.
    """

    offset_samples: int
    sample_rate_hz: int

    @property
    def latency_ms(self) -> float:
        """Return latency in milliseconds."""

        return 1000.0 * float(self.offset_samples) / float(self.sample_rate_hz)


class PyAlsaCaptureDevice:
    """Mono ALSA capture helper for loopback latency measurements."""

    def __init__(
        self,
        device_name: str,
        sample_rate_hz: int,
        period_size_frames: int,
    ):
        if alsaaudio is None:  # pragma: no cover - exercised on Raspberry Pi hardware
            raise RuntimeError("pyalsaaudio is not installed; loopback capture is unavailable.")
        self._pcm = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, alsaaudio.PCM_NORMAL, device_name)
        self._pcm.setchannels(1)
        self._pcm.setrate(sample_rate_hz)
        self._pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
        self.period_size_frames = int(self._pcm.setperiodsize(period_size_frames))
        self.sample_rate_hz = int(sample_rate_hz)
        self.device_name = str(device_name)

    def capture_frames(self, frame_count: int) -> np.ndarray:
        """Capture mono audio for a fixed number of frames.

        Args:
            frame_count: Number of frames to record.

        Returns:
            One-dimensional ``float32`` waveform of shape ``(frame_count,)`` in
            normalized amplitude units.
        """

        chunks: list[np.ndarray] = []
        captured = 0
        try:
            while captured < frame_count:
                length, data = self._pcm.read()
                if length <= 0:
                    continue
                chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                chunks.append(chunk[:length])
                captured += int(length)
            recorded = np.concatenate(chunks)[:frame_count]
            return recorded.astype(np.float32, copy=False)
        except Exception:  # pragma: no cover - exercised on Raspberry Pi hardware
            self.close()
            return self._capture_with_arecord(frame_count)

    def capture_around_playback(
        self,
        play_callback: Callable[[], None],
        total_frames: int,
        pre_roll_frames: int,
    ) -> np.ndarray:
        """Capture audio around a playback trigger.

        Args:
            play_callback: Zero-argument callable that starts playback.
            total_frames: Total number of mono frames to capture.
            pre_roll_frames: Number of captured frames to accumulate before
                calling ``play_callback``.

        Returns:
            One-dimensional ``float32`` waveform of shape ``(total_frames,)``.
        """

        chunks: list[np.ndarray] = []
        captured = 0
        playback_started = False
        try:
            while captured < total_frames:
                length, data = self._pcm.read()
                if length <= 0:
                    continue
                chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                chunk = chunk[:length]
                chunks.append(chunk)
                captured += int(length)
                if not playback_started and captured >= pre_roll_frames:
                    play_callback()
                    playback_started = True
            recorded = np.concatenate(chunks)[:total_frames]
            return recorded.astype(np.float32, copy=False)
        except Exception:  # pragma: no cover - exercised on Raspberry Pi hardware
            self.close()
            return self._capture_with_arecord_streaming(
                play_callback=play_callback,
                total_frames=total_frames,
                pre_roll_frames=pre_roll_frames,
            )

    def close(self) -> None:
        if hasattr(self, "_pcm"):
            self._pcm.close()

    def _capture_with_arecord(self, frame_count: int) -> np.ndarray:
        """Fallback capture path using the `arecord` CLI tool.

        Args:
            frame_count: Number of mono frames to capture.

        Returns:
            One-dimensional ``float32`` waveform of shape ``(frame_count,)``.
        """

        duration_s = max(1, int(np.ceil(frame_count / self.sample_rate_hz)))
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            output_path = Path(handle.name)
        try:
            subprocess.run(
                [
                    "arecord",
                    "-q",
                    "-D",
                    self.device_name,
                    "-f",
                    "S16_LE",
                    "-c",
                    "1",
                    "-r",
                    str(self.sample_rate_hz),
                    "-d",
                    str(duration_s),
                    str(output_path),
                ],
                check=True,
            )
            _, recorded = wavfile.read(output_path)
        finally:
            output_path.unlink(missing_ok=True)
        recorded = recorded.astype(np.float32) / 32768.0
        return recorded[:frame_count]

    def _capture_with_arecord_streaming(
        self,
        play_callback: Callable[[], None],
        total_frames: int,
        pre_roll_frames: int,
    ) -> np.ndarray:
        """Fallback capture path using streaming `arecord` stdout.

        Args:
            play_callback: Zero-argument callable that starts playback.
            total_frames: Total number of mono frames to capture.
            pre_roll_frames: Number of captured frames to accumulate before
                starting playback.

        Returns:
            One-dimensional ``float32`` waveform of shape ``(total_frames,)``.
        """

        bytes_per_frame = 2
        target_bytes = total_frames * bytes_per_frame
        process = subprocess.Popen(
            [
                "arecord",
                "-q",
                "-D",
                self.device_name,
                "-f",
                "S16_LE",
                "-c",
                "1",
                "-r",
                str(self.sample_rate_hz),
                "-t",
                "raw",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        playback_started = False
        raw_buffer = bytearray()
        try:
            while len(raw_buffer) < target_bytes:
                assert process.stdout is not None
                chunk = process.stdout.read(self.period_size_frames * bytes_per_frame)
                if not chunk:
                    break
                raw_buffer.extend(chunk)
                if not playback_started and len(raw_buffer) >= pre_roll_frames * bytes_per_frame:
                    play_callback()
                    playback_started = True
        finally:
            process.terminate()
            process.wait(timeout=2.0)
        recorded = np.frombuffer(bytes(raw_buffer[:target_bytes]), dtype=np.int16).astype(np.float32) / 32768.0
        return recorded[:total_frames]


def measure_loopback_latency(
    capture_device: PyAlsaCaptureDevice,
    play_callback: Callable[[], None],
    played_waveform_mono: np.ndarray,
    sample_rate_hz: int,
    pre_roll_s: float = 0.5,
    post_roll_s: float = 0.25,
) -> LoopbackMeasurement:
    """Capture loopback audio around a playback trigger and estimate latency.

    Args:
        capture_device: Open mono capture device.
        play_callback: Zero-argument callable that triggers playback.
        played_waveform_mono: Reference mono waveform with shape
            ``(num_played_frames,)``.
        sample_rate_hz: Sampling rate in hertz.
        pre_roll_s: Lead-in capture window in seconds before playback trigger.
        post_roll_s: Tail capture window in seconds after the reference cue.

    Returns:
        LoopbackMeasurement for the captured repetition.
    """

    played_waveform_mono = np.asarray(played_waveform_mono, dtype=np.float32)
    pre_roll_frames = int(round(pre_roll_s * sample_rate_hz))
    post_roll_frames = int(round(post_roll_s * sample_rate_hz))
    total_frames = pre_roll_frames + post_roll_frames + int(played_waveform_mono.shape[0])
    recorded = capture_device.capture_around_playback(
        play_callback=play_callback,
        total_frames=total_frames,
        pre_roll_frames=pre_roll_frames,
    )
    if recorded.shape[0] < played_waveform_mono.shape[0]:
        raise TimeoutError("Timed out waiting for loopback capture.")
    offset = estimate_loopback_latency_samples(recorded, played_waveform_mono)
    # Subtract the intentional pre-roll offset.
    offset -= pre_roll_frames
    return LoopbackMeasurement(offset_samples=offset, sample_rate_hz=sample_rate_hz)
