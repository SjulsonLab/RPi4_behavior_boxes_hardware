from __future__ import annotations

from scripts.run_head_fixed_hardware_stress_mode import run_audio_preflight


class _FakeSoundRuntime:
    """Minimal sound-runtime double for launcher audio preflight tests.

    Data contracts:
    - ``wait_calls``: ``list[float]`` timeout values passed to
      ``wait_until_idle``.
    """

    def __init__(self) -> None:
        self.wait_calls: list[float] = []

    def wait_until_idle(self, timeout_s: float = 5.0) -> bool:
        self.wait_calls.append(float(timeout_s))
        return True


class _FakePreflightBox:
    """Minimal BehavBox double covering the audio preflight surface area.

    Data contracts:
    - ``session_info``: ``dict[str, object]`` with optional ``audio_device``.
    - ``registered``: ``list[tuple[str, float, int]]`` of registered cues.
    - ``played``: ``list[tuple[str, str, float]]`` of playback calls.
    - ``latency_calls``: ``list[tuple[str, str, float, int]]`` of latency
      requests made through ``measure_sound_latency``.
    """

    def __init__(self, *, audio_device: str | None = "plughw:CARD=Device,DEV=0") -> None:
        self.session_info = {"audio_device": audio_device}
        self.registered: list[tuple[str, float, int]] = []
        self.played: list[tuple[str, str, float]] = []
        self.latency_calls: list[tuple[str, str, float, int]] = []
        self.sound_runtime = _FakeSoundRuntime()

    def register_noise_cue(self, name: str, duration_s: float, seed: int = 0) -> None:
        self.registered.append((str(name), float(duration_s), int(seed)))

    def play_sound(self, name: str, side: str = "both", gain_db: float = 0.0, duration_s: float | None = None) -> None:
        del duration_s
        self.played.append((str(name), str(side), float(gain_db)))

    def measure_sound_latency(
        self,
        name: str,
        side: str = "both",
        gain_db: float = 0.0,
        repeats: int = 3,
    ) -> list[float]:
        self.latency_calls.append((str(name), str(side), float(gain_db), int(repeats)))
        return [11.0, 12.5, 10.5]


def test_run_audio_preflight_registers_and_plays_noise_cue() -> None:
    box = _FakePreflightBox()

    result = run_audio_preflight(
        box,
        enabled=True,
        measure_latency=False,
    )

    assert box.registered == [("stress_audio_preflight", 0.25, 99)]
    assert box.played == [("stress_audio_preflight", "both", 0.0)]
    assert box.sound_runtime.wait_calls == [5.0]
    assert result["audio_device"] == "plughw:CARD=Device,DEV=0"
    assert result["latencies_ms"] is None


def test_run_audio_preflight_measures_latency_when_requested() -> None:
    box = _FakePreflightBox()

    result = run_audio_preflight(
        box,
        enabled=True,
        measure_latency=True,
        latency_repeats=4,
    )

    assert box.latency_calls == [("stress_audio_preflight", "both", 0.0, 4)]
    assert result["latencies_ms"] == [11.0, 12.5, 10.5]


def test_run_audio_preflight_skips_work_when_disabled() -> None:
    box = _FakePreflightBox(audio_device=None)

    result = run_audio_preflight(
        box,
        enabled=False,
        measure_latency=True,
    )

    assert box.registered == []
    assert box.played == []
    assert box.latency_calls == []
    assert box.sound_runtime.wait_calls == []
    assert result == {"enabled": False}
