"""Count signed treadmill quadrature cycles until the operator presses Ctrl-C.

The diagnostic uses the same ``gpiozero.RotaryEncoder`` wrapper and BCM pin
mapping as the active BehavBox treadmill path. Its only result is the signed,
dimensionless number of complete quadrature cycles decoded after startup.
"""

from __future__ import annotations

from pathlib import Path
import signal
from typing import Any, Callable

try:
    from debug.repo_imports import prepare_repo_imports
except ModuleNotFoundError:
    from repo_imports import prepare_repo_imports


TREADMILL_A_BCM_PIN = 13
TREADMILL_B_BCM_PIN = 16


def collect_steps_until_interrupted(
    *,
    encoder_factory: Callable[..., Any],
    wait_for_interrupt: Callable[[], None] = signal.pause,
) -> int:
    """Collect signed decoded treadmill cycles until Ctrl-C.

    Args:
        encoder_factory: Callable accepting integer BCM A/B pins and the
            keyword ``max_steps`` and returning an encoder-like object with a
            writable integer ``steps`` attribute and ``close()`` method.
        wait_for_interrupt: Zero-argument blocking callable. It returns
            ``None`` for handled non-SIGINT signals and raises
            ``KeyboardInterrupt`` for Ctrl-C.

    Returns:
        int: Signed number of complete quadrature cycles decoded since the
        counter was reset. The count is dimensionless; positive and negative
        values represent opposite encoder directions.

    Raises:
        Exception: Propagates encoder construction and unexpected wait errors
            after closing an encoder that was constructed successfully.
    """

    encoder = encoder_factory(
        TREADMILL_A_BCM_PIN,
        TREADMILL_B_BCM_PIN,
        max_steps=0,
    )
    try:
        encoder.steps = 0
        while True:
            wait_for_interrupt()
    except KeyboardInterrupt:
        return int(encoder.steps)
    finally:
        encoder.close()


def _import_rotary_encoder() -> Callable[..., Any]:
    """Import and return the active project RotaryEncoder constructor.

    Returns:
        Callable[..., Any]: Project wrapper around
        ``gpiozero.RotaryEncoder``.
    """

    from box_runtime.behavior.gpio_backend import RotaryEncoder

    return RotaryEncoder


def main(
    *,
    encoder_factory: Callable[..., Any] | None = None,
    wait_for_interrupt: Callable[[], None] = signal.pause,
    output_fn: Callable[[str], Any] = print,
) -> int:
    """Run the treadmill step-count diagnostic.

    Args:
        encoder_factory: Optional GPIO Zero-compatible encoder constructor.
            When omitted, the active project wrapper is imported.
        wait_for_interrupt: Zero-argument blocking callable used to await
            Ctrl-C.
        output_fn: Callable receiving the one-line final count as text.

    Returns:
        int: Process exit status; zero after a normal Ctrl-C stop.
    """

    if encoder_factory is None:
        prepare_repo_imports(script_path=Path(__file__))
        encoder_factory = _import_rotary_encoder()

    final_steps = collect_steps_until_interrupted(
        encoder_factory=encoder_factory,
        wait_for_interrupt=wait_for_interrupt,
    )
    output_fn(f"Final treadmill steps: {final_steps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
