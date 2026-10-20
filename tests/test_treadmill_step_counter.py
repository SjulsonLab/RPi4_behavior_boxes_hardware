"""Tests for the standalone treadmill step-count diagnostic."""

from __future__ import annotations

from typing import Callable

import pytest

from debug import treadmill_step_counter


class FakeEncoder:
    """Minimal encoder double with a signed, dimensionless step count."""

    def __init__(self, initial_steps: int = 99) -> None:
        """Create an open fake encoder.

        Args:
            initial_steps: Initial signed, dimensionless step count.

        Returns:
            None.
        """

        self.steps = int(initial_steps)
        self.closed = False

    def close(self) -> None:
        """Mark the fake GPIO resource closed and return ``None``."""

        self.closed = True


def _recording_encoder_factory(
    calls: list[tuple[int, int, int]],
    encoder: FakeEncoder,
) -> Callable[..., FakeEncoder]:
    """Build a factory that records BCM pins and the GPIO Zero step limit.

    Args:
        calls: Mutable list receiving ``(a_pin, b_pin, max_steps)`` tuples.
        encoder: Encoder instance returned by the factory.

    Returns:
        Callable[..., FakeEncoder]: Factory compatible with
        ``gpiozero.RotaryEncoder`` construction.
    """

    def factory(a_pin: int, b_pin: int, *, max_steps: int) -> FakeEncoder:
        """Record integer constructor arguments and return the fake encoder."""

        calls.append((int(a_pin), int(b_pin), int(max_steps)))
        return encoder

    return factory


def test_collect_uses_active_pins_resets_count_and_closes_on_ctrl_c() -> None:
    """Collection should return signed steps accumulated after a zero reset."""

    calls: list[tuple[int, int, int]] = []
    encoder = FakeEncoder(initial_steps=99)

    def interrupt_after_steps() -> None:
        """Simulate five negative decoded cycles followed by Ctrl-C."""

        assert encoder.steps == 0
        encoder.steps = -5
        raise KeyboardInterrupt

    final_steps = treadmill_step_counter.collect_steps_until_interrupted(
        encoder_factory=_recording_encoder_factory(calls, encoder),
        wait_for_interrupt=interrupt_after_steps,
    )

    assert calls == [(13, 16, 0)]
    assert final_steps == -5
    assert encoder.closed is True


def test_collect_continues_when_wait_returns_without_ctrl_c() -> None:
    """A handled non-SIGINT signal should not terminate collection."""

    encoder = FakeEncoder()
    wait_call_count = 0

    def return_once_then_interrupt() -> None:
        """Return once, then simulate two positive cycles and Ctrl-C."""

        nonlocal wait_call_count
        wait_call_count += 1
        if wait_call_count == 2:
            encoder.steps = 2
            raise KeyboardInterrupt

    final_steps = treadmill_step_counter.collect_steps_until_interrupted(
        encoder_factory=_recording_encoder_factory([], encoder),
        wait_for_interrupt=return_once_then_interrupt,
    )

    assert wait_call_count == 2
    assert final_steps == 2
    assert encoder.closed is True


def test_collect_closes_encoder_and_propagates_unexpected_wait_error() -> None:
    """Unexpected acquisition errors should remain visible after cleanup."""

    encoder = FakeEncoder()

    def fail_wait() -> None:
        """Raise a representative non-interrupt acquisition failure."""

        raise RuntimeError("wait failed")

    with pytest.raises(RuntimeError, match="wait failed"):
        treadmill_step_counter.collect_steps_until_interrupted(
            encoder_factory=_recording_encoder_factory([], encoder),
            wait_for_interrupt=fail_wait,
        )

    assert encoder.closed is True


def test_main_prints_only_the_final_signed_step_count() -> None:
    """The command should emit one final result rather than per-cycle output."""

    encoder = FakeEncoder()
    output_lines: list[str] = []

    def interrupt_after_steps() -> None:
        """Set the final signed count and simulate Ctrl-C."""

        encoder.steps = -1000
        raise KeyboardInterrupt

    exit_code = treadmill_step_counter.main(
        encoder_factory=_recording_encoder_factory([], encoder),
        wait_for_interrupt=interrupt_after_steps,
        output_fn=output_lines.append,
    )

    assert exit_code == 0
    assert output_lines == ["Final treadmill steps: -1000"]
