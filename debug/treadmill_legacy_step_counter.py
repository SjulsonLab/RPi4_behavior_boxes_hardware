"""Collect legacy A-rising treadmill counts until the operator presses Ctrl-C."""

from __future__ import annotations

import signal
from typing import Any, Callable

try:
    from debug.treadmill_decoder_legacy import (
        ENC_A_PIN,
        ENC_B_PIN,
        MM_PER_COUNT,
        EncoderState,
        TreadmillDecoder,
    )
except ModuleNotFoundError:
    from treadmill_decoder_legacy import (
        ENC_A_PIN,
        ENC_B_PIN,
        MM_PER_COUNT,
        EncoderState,
        TreadmillDecoder,
    )


def collect_counts_until_interrupted(
    *,
    decoder_factory: Callable[..., Any] = TreadmillDecoder,
    wait_for_interrupt: Callable[[], None] = signal.pause,
    a_pin: int = ENC_A_PIN,
    b_pin: int = ENC_B_PIN,
    mm_per_count: float = MM_PER_COUNT,
) -> EncoderState:
    """Collect legacy count diagnostics until Ctrl-C.

    Args:
        decoder_factory: Callable accepting A/B BCM pin numbers and the legacy
            scaled calibration value, and returning a decoder-like object.
        wait_for_interrupt: Zero-argument blocking callable that raises
            ``KeyboardInterrupt`` for Ctrl-C.
        a_pin: Adjustable channel-A BCM GPIO pin number.
        b_pin: Adjustable channel-B BCM GPIO pin number.
        mm_per_count: Legacy calibration in millimeters times 1,000,000 per
            count. The diagnostic does not report physical-unit conversions.

    Returns:
        EncoderState: Final frozen decoder snapshot. ``counts`` is the signed,
        dimensionless legacy count; ``edge_count_total`` is the number of
        accepted A-rising callbacks; ``missed_callback_count`` is the number
        of callbacks rejected following a GPIO read failure.

    Raises:
        Exception: Propagates decoder construction, lifecycle, snapshot, and
            unexpected wait errors after attempting appropriate cleanup.
    """

    decoder = decoder_factory(int(a_pin), int(b_pin), float(mm_per_count))
    try:
        decoder.zero()
        decoder.start()
        try:
            while True:
                wait_for_interrupt()
        except KeyboardInterrupt:
            pass
        finally:
            decoder.stop()
        return decoder.snapshot()
    finally:
        decoder.close()


def main(
    *,
    decoder_factory: Callable[..., Any] = TreadmillDecoder,
    wait_for_interrupt: Callable[[], None] = signal.pause,
    output_fn: Callable[[str], Any] = print,
) -> int:
    """Run the legacy treadmill count diagnostic.

    Args:
        decoder_factory: Optional decoder constructor used for GPIO acquisition.
        wait_for_interrupt: Zero-argument blocking callable used to await
            Ctrl-C.
        output_fn: Callable receiving each final count line as text.

    Returns:
        int: Process exit status; zero after a normal Ctrl-C stop.
    """

    state = collect_counts_until_interrupted(
        decoder_factory=decoder_factory,
        wait_for_interrupt=wait_for_interrupt,
    )
    output_fn(f"Final legacy treadmill counts: {state.counts}")
    output_fn(f"A-rising callbacks: {state.edge_count_total}")
    output_fn(f"Missed callbacks: {state.missed_callback_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
