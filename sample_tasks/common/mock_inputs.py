"""Programmatic mock-input helpers for local sample-task testing."""

from __future__ import annotations

from box_runtime.mock_hw.registry import REGISTRY


class MockInputInjector:
    """Inject mock GPIO events through the canonical registry path.

    Data contracts:
    - ``label``: registry label string such as ``"lick_3"``
    - ``duration_ms``: pulse duration in milliseconds as ``int``
    """

    def press(self, label: str) -> None:
        """Press one mock input by label."""

        REGISTRY.press_input(label=str(label), source="injector")

    def release(self, label: str) -> None:
        """Release one mock input by label."""

        REGISTRY.release_input(label=str(label), source="injector")

    def pulse(self, label: str, duration_ms: int = 30) -> None:
        """Pulse one mock input by label.

        Args:
            label: Registry label string.
            duration_ms: Pulse duration in milliseconds.
        """

        REGISTRY.pulse_input(label=str(label), duration_ms=int(duration_ms), source="injector")
