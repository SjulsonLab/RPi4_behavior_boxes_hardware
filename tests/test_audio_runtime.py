import logging
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from box_runtime.audio.importer import AudioPaths, CueImporter
from box_runtime.audio.latency import estimate_loopback_latency_samples
from box_runtime.audio.runtime import SoundRuntime


def _write_wav(
    path: Path,
    samples: np.ndarray,
    sample_rate_hz: int,
) -> None:
    """Write a floating-point waveform to a signed 16-bit PCM WAV file.

    Args:
        path: Destination file path.
        samples: Array of shape ``(num_frames,)`` for mono or
            ``(num_frames, num_channels)`` for multi-channel audio, with
            floating-point values nominally in ``[-1.0, 1.0]``.
        sample_rate_hz: Sampling rate in hertz.
    """

    clipped = np.clip(samples, -1.0, 1.0)
    pcm = np.round(clipped * 32767.0).astype(np.int16)
    channels = 1 if pcm.ndim == 1 else pcm.shape[1]
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate_hz)
        handle.writeframes(pcm.tobytes())


class RecordingBackend:
    """Fake playback backend that records submitted buffers for tests.

    Args:
        sample_rate_hz: Playback sampling rate in hertz.
        chunk_frames: Number of stereo frames consumed per worker iteration.
        chunk_sleep_s: Delay per chunk in seconds to emulate a slower device.
    """

    def __init__(self, sample_rate_hz: int, chunk_frames: int = 256, chunk_sleep_s: float = 0.0):
        self.sample_rate_hz = sample_rate_hz
        self.chunk_frames = chunk_frames
        self.chunk_sleep_s = chunk_sleep_s
        self.play_calls: list[np.ndarray] = []

    def write_frames(self, frames: np.ndarray, stop_requested) -> int:
        """Record the played frames.

        Args:
            frames: Stereo array of shape ``(num_frames, 2)`` with ``int16``
                samples.
            stop_requested: Zero-argument callable returning whether playback
                should stop early.

        Returns:
            Number of stereo frames actually consumed.
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
        if chunks:
            self.play_calls.append(np.vstack(chunks))
        else:
            self.play_calls.append(np.empty((0, 2), dtype=np.int16))
        return consumed

    def close(self) -> None:
        """Release backend resources.

        Returns:
            ``None``.
        """


@pytest.fixture
def audio_paths(tmp_path: Path) -> AudioPaths:
    """Construct isolated audio directories for tests.

    Args:
        tmp_path: Pytest temporary directory fixture.

    Returns:
        AudioPaths rooted under the temporary directory.
    """

    tracked = tmp_path / "sounds"
    raw = tmp_path / "local_source_wavs"
    local = tmp_path / "local_sounds"
    tracked.mkdir()
    raw.mkdir()
    local.mkdir()
    return AudioPaths(
        tracked_sounds_dir=tracked,
        local_source_dir=raw,
        local_sounds_dir=local,
    )


def test_import_truncates_long_file_and_warns(audio_paths: AudioPaths, caplog: pytest.LogCaptureFixture) -> None:
    importer = CueImporter(audio_paths)
    sample_rate_hz = 48000
    long_samples = np.full(sample_rate_hz * 12, 0.25, dtype=np.float32)
    _write_wav(audio_paths.local_source_dir / "long.wav", long_samples, sample_rate_hz)

    with caplog.at_level(logging.WARNING):
        imported_path = importer.import_wav_file("long")

    assert imported_path.name == "long.wav"
    assert "allow_longer=True" in caplog.text
    imported = importer.load_canonical_waveform("long")
    assert imported.waveform_mono.shape == (sample_rate_hz * 10,)


def test_import_preserves_long_file_when_allow_longer(audio_paths: AudioPaths) -> None:
    importer = CueImporter(audio_paths)
    sample_rate_hz = 48000
    long_samples = np.linspace(-0.5, 0.5, sample_rate_hz * 12, dtype=np.float32)
    _write_wav(audio_paths.local_source_dir / "long.wav", long_samples, sample_rate_hz)

    importer.import_wav_file("long", cue_name="long_full", allow_longer=True)
    imported = importer.load_canonical_waveform("long_full")
    assert imported.waveform_mono.shape == (sample_rate_hz * 12,)


def test_import_downmixes_and_removes_dc_offset(audio_paths: AudioPaths) -> None:
    importer = CueImporter(audio_paths)
    sample_rate_hz = 48000
    frames = sample_rate_hz // 2
    left = np.full(frames, 0.7, dtype=np.float32)
    right = np.full(frames, -0.1, dtype=np.float32)
    stereo = np.column_stack([left, right])
    _write_wav(audio_paths.local_source_dir / "stereo.wav", stereo, sample_rate_hz)

    importer.import_wav_file("stereo")
    imported = importer.load_canonical_waveform("stereo")

    assert imported.waveform_mono.ndim == 1
    assert abs(float(imported.waveform_mono.mean())) < 1e-4


def test_load_sound_only_caches_requested_name(audio_paths: AudioPaths) -> None:
    importer = CueImporter(audio_paths)
    sample_rate_hz = 48000
    _write_wav(audio_paths.local_source_dir / "cue_a.wav", np.zeros(sample_rate_hz // 10, dtype=np.float32), sample_rate_hz)
    _write_wav(audio_paths.local_source_dir / "cue_b.wav", np.ones(sample_rate_hz // 10, dtype=np.float32) * 0.1, sample_rate_hz)
    importer.import_wav_file("cue_a")
    importer.import_wav_file("cue_b")

    runtime = SoundRuntime(audio_paths, backend=RecordingBackend(sample_rate_hz))
    runtime.load_sound("cue_a")

    assert sorted(runtime.loaded_sound_names()) == ["cue_a"]
    runtime.clear_sounds()
    assert runtime.loaded_sound_names() == []
    runtime.close()


def test_lookup_prefers_local_sounds_over_tracked(audio_paths: AudioPaths) -> None:
    sample_rate_hz = 48000
    _write_wav(audio_paths.tracked_sounds_dir / "buzzer.wav", np.ones(sample_rate_hz // 20, dtype=np.float32) * 0.05, sample_rate_hz)
    _write_wav(audio_paths.local_sounds_dir / "buzzer.wav", np.ones(sample_rate_hz // 20, dtype=np.float32) * 0.25, sample_rate_hz)

    importer = CueImporter(audio_paths)
    imported = importer.load_canonical_waveform("buzzer")
    assert pytest.approx(float(np.max(np.abs(imported.waveform_mono))), rel=1e-3) == 0.25


def test_play_sound_loops_to_requested_duration(audio_paths: AudioPaths) -> None:
    importer = CueImporter(audio_paths)
    sample_rate_hz = 48000
    frames = sample_rate_hz // 10
    _write_wav(audio_paths.local_source_dir / "short.wav", np.linspace(-0.25, 0.25, frames, dtype=np.float32), sample_rate_hz)
    importer.import_wav_file("short")

    backend = RecordingBackend(sample_rate_hz)
    runtime = SoundRuntime(audio_paths, backend=backend)
    runtime.load_sound("short")
    runtime.play_sound("short", side="both", duration_s=0.35)
    runtime.wait_until_idle(timeout_s=2.0)

    played = backend.play_calls[-1]
    assert played.shape == (int(round(0.35 * sample_rate_hz)), 2)
    runtime.close()


def test_play_sound_truncates_to_requested_duration(audio_paths: AudioPaths) -> None:
    importer = CueImporter(audio_paths)
    sample_rate_hz = 48000
    frames = sample_rate_hz // 2
    _write_wav(audio_paths.local_source_dir / "longer.wav", np.linspace(-0.5, 0.5, frames, dtype=np.float32), sample_rate_hz)
    importer.import_wav_file("longer")

    backend = RecordingBackend(sample_rate_hz)
    runtime = SoundRuntime(audio_paths, backend=backend)
    runtime.load_sound("longer")
    runtime.play_sound("longer", side="left", duration_s=0.1)
    runtime.wait_until_idle(timeout_s=2.0)

    played = backend.play_calls[-1]
    assert played.shape == (int(round(0.1 * sample_rate_hz)), 2)
    assert np.all(played[:, 1] == 0)
    runtime.close()


def test_play_sound_interrupts_current_playback(audio_paths: AudioPaths) -> None:
    importer = CueImporter(audio_paths)
    sample_rate_hz = 48000
    frames = sample_rate_hz
    _write_wav(audio_paths.local_source_dir / "a.wav", np.ones(frames, dtype=np.float32) * 0.1, sample_rate_hz)
    _write_wav(audio_paths.local_source_dir / "b.wav", np.ones(frames // 4, dtype=np.float32) * -0.1, sample_rate_hz)
    importer.import_wav_file("a")
    importer.import_wav_file("b")

    backend = RecordingBackend(sample_rate_hz, chunk_frames=128, chunk_sleep_s=0.001)
    runtime = SoundRuntime(audio_paths, backend=backend)
    runtime.load_sound("a")
    runtime.load_sound("b")
    runtime.play_sound("a", duration_s=0.5)
    time.sleep(0.01)
    runtime.play_sound("b", duration_s=0.1)
    runtime.wait_until_idle(timeout_s=3.0)

    assert len(backend.play_calls) >= 2
    assert backend.play_calls[0].shape[0] < int(round(0.5 * sample_rate_hz))
    assert backend.play_calls[-1].shape[0] == int(round(0.1 * sample_rate_hz))
    runtime.close()


def test_stop_sound_terminates_playback_early(audio_paths: AudioPaths) -> None:
    importer = CueImporter(audio_paths)
    sample_rate_hz = 48000
    _write_wav(audio_paths.local_source_dir / "tone.wav", np.ones(sample_rate_hz, dtype=np.float32) * 0.1, sample_rate_hz)
    importer.import_wav_file("tone")

    backend = RecordingBackend(sample_rate_hz, chunk_frames=128, chunk_sleep_s=0.001)
    runtime = SoundRuntime(audio_paths, backend=backend)
    runtime.load_sound("tone")
    runtime.play_sound("tone", duration_s=0.6)
    time.sleep(0.01)
    runtime.stop_sound()
    runtime.wait_until_idle(timeout_s=3.0)

    assert backend.play_calls[-1].shape[0] < int(round(0.6 * sample_rate_hz))
    runtime.close()


def test_runtime_logs_significant_clipping_with_percentage_after_launch(
    audio_paths: AudioPaths,
    caplog: pytest.LogCaptureFixture,
) -> None:
    importer = CueImporter(audio_paths)
    sample_rate_hz = 48000
    time_axis = np.arange(sample_rate_hz // 5, dtype=np.float32) / sample_rate_hz
    hot_wave = 0.9 * np.sin(2 * np.pi * 880.0 * time_axis, dtype=np.float32)
    _write_wav(audio_paths.local_source_dir / "hot.wav", hot_wave, sample_rate_hz)
    importer.import_wav_file("hot")

    backend = RecordingBackend(sample_rate_hz)
    runtime = SoundRuntime(audio_paths, backend=backend)
    runtime.load_sound("hot")
    with caplog.at_level(logging.WARNING):
        runtime.play_sound("hot", gain_db=24.0)
        runtime.wait_until_idle(timeout_s=2.0)

    assert "clip" in caplog.text.lower()
    assert "%" in caplog.text
    runtime.close()


def test_estimate_loopback_latency_samples_recovers_known_offset() -> None:
    played = np.zeros(4096, dtype=np.float32)
    played[512:768] = 1.0
    recorded = np.zeros(5000, dtype=np.float32)
    offset = 321
    recorded[offset:offset + played.shape[0]] += played

    estimated = estimate_loopback_latency_samples(recorded, played)
    assert estimated == offset
