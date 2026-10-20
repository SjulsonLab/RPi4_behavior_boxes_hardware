"""Tests for the standalone legacy treadmill count diagnostic."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from debug import treadmill_legacy_step_counter


class FakeLegacyDecoder:
    """Legacy decoder double with call-order and final-count tracking."""

    def __init__(self, a_pin: int, b_pin: int, mm_per_count: float) -> None:
        """Record construction values and initialize dimensionless counts.

        Args:
            a_pin: Channel-A BCM pin number.
            b_pin: Channel-B BCM pin number.
            mm_per_count: Legacy scaled calibration value; unused by this fake.

        Returns:
            None.
        """

        self.a_pin = int(a_pin)
        self.b_pin = int(b_pin)
        self.mm_per_count = float(mm_per_count)
        self.calls: list[str] = []
        self.counts = 99
        self.edge_count_total = 77
        self.missed_callback_count = 3

    def zero(self) -> None:
        """Reset all dimensionless fake count fields and record the call."""

        self.calls.append("zero")
        self.counts = 0
        self.edge_count_total = 0
        self.missed_callback_count = 0

    def start(self) -> None:
        """Record arming of the fake decoder and return ``None``."""

        self.calls.append("start")

    def stop(self) -> None:
        """Record disarming of the fake decoder and return ``None``."""

        self.calls.append("stop")

    def snapshot(self) -> SimpleNamespace:
        """Return an immutable-by-convention copy of current count fields."""

        self.calls.append("snapshot")
        return SimpleNamespace(
            counts=int(self.counts),
            edge_count_total=int(self.edge_count_total),
            missed_callback_count=int(self.missed_callback_count),
        )

    def close(self) -> None:
        """Record release of fake GPIO resources and return ``None``."""

        self.calls.append("close")


def test_collection_uses_adjustable_pins_and_freezes_counts_before_snapshot() -> None:
    """Ctrl-C should stop, snapshot, and close after collecting legacy counts."""

    factory_calls: list[tuple[int, int, float]] = []
    decoder: FakeLegacyDecoder | None = None

    def decoder_factory(
        a_pin: int,
        b_pin: int,
        mm_per_count: float,
    ) -> FakeLegacyDecoder:
        """Construct and retain one fake legacy decoder."""

        nonlocal decoder
        factory_calls.append((int(a_pin), int(b_pin), float(mm_per_count)))
        decoder = FakeLegacyDecoder(a_pin, b_pin, mm_per_count)
        return decoder

    def interrupt_after_counts() -> None:
        """Populate final counts and simulate operator Ctrl-C."""

        assert decoder is not None
        decoder.counts = 125
        decoder.edge_count_total = 127
        decoder.missed_callback_count = 2
        raise KeyboardInterrupt

    state = treadmill_legacy_step_counter.collect_counts_until_interrupted(
        decoder_factory=decoder_factory,
        wait_for_interrupt=interrupt_after_counts,
    )

    assert factory_calls == [(13, 16, 410950.0)]
    assert decoder is not None
    assert decoder.calls == ["zero", "start", "stop", "snapshot", "close"]
    assert state.counts == 125
    assert state.edge_count_total == 127
    assert state.missed_callback_count == 2


def test_collection_closes_decoder_and_propagates_unexpected_wait_error() -> None:
    """Unexpected wait failures should disarm and close the decoder."""

    decoder = FakeLegacyDecoder(13, 16, 410950.0)

    def fail_wait() -> None:
        """Raise one representative non-interrupt acquisition error."""

        raise RuntimeError("wait failed")

    with pytest.raises(RuntimeError, match="wait failed"):
        treadmill_legacy_step_counter.collect_counts_until_interrupted(
            decoder_factory=lambda *_args, **_kwargs: decoder,
            wait_for_interrupt=fail_wait,
        )

    assert decoder.calls == ["zero", "start", "stop", "close"]


def test_main_prints_only_final_count_diagnostics() -> None:
    """The command should print three final count lines and no live updates."""

    decoder = FakeLegacyDecoder(13, 16, 410950.0)
    output_lines: list[str] = []

    def interrupt_after_counts() -> None:
        """Populate signed and diagnostic counts before simulated Ctrl-C."""

        decoder.counts = -1000
        decoder.edge_count_total = 1000
        decoder.missed_callback_count = 0
        raise KeyboardInterrupt

    exit_code = treadmill_legacy_step_counter.main(
        decoder_factory=lambda *_args, **_kwargs: decoder,
        wait_for_interrupt=interrupt_after_counts,
        output_fn=output_lines.append,
    )

    assert exit_code == 0
    assert output_lines == [
        "Final legacy treadmill counts: -1000",
        "A-rising callbacks: 1000",
        "Missed callbacks: 0",
    ]
