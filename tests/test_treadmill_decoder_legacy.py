"""Tests for the A-rising legacy treadmill decoder."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from debug import treadmill_decoder_legacy


class FakeGpio:
    """Small RPi.GPIO double supporting two input pins and edge callbacks."""

    BCM = 11
    IN = 1
    PUD_UP = 22
    RISING = 31

    def __init__(self) -> None:
        """Initialize fake GPIO state and call records.

        Returns:
            None.
        """

        self.mode: int | None = None
        self.pin_values: dict[int, int] = {}
        self.input_error_pins: set[int] = set()
        self.setup_calls: list[tuple[int, int, int]] = []
        self.event_calls: list[tuple[int, int]] = []
        self.callbacks: dict[int, Callable[..., None]] = {}
        self.removed_pins: list[int] = []
        self.cleaned_pins: list[int] = []
        self.warnings_enabled: bool | None = None

    def setwarnings(self, enabled: bool) -> None:
        """Record whether GPIO warnings are enabled and return ``None``."""

        self.warnings_enabled = bool(enabled)

    def getmode(self) -> int | None:
        """Return the configured fake GPIO numbering mode."""

        return self.mode

    def setmode(self, mode: int) -> None:
        """Store one integer GPIO numbering mode and return ``None``."""

        self.mode = int(mode)

    def setup(self, pin: int, direction: int, *, pull_up_down: int) -> None:
        """Record one input-pin setup operation.

        Args:
            pin: BCM GPIO pin number.
            direction: Fake GPIO direction constant.
            pull_up_down: Fake pull-resistor constant.

        Returns:
            None.
        """

        self.setup_calls.append((int(pin), int(direction), int(pull_up_down)))
        self.pin_values.setdefault(int(pin), 1)

    def add_event_detect(
        self,
        pin: int,
        edge: int,
        *,
        callback: Callable[..., None],
    ) -> None:
        """Record one edge registration and retain its callback."""

        self.event_calls.append((int(pin), int(edge)))
        self.callbacks[int(pin)] = callback

    def input(self, pin: int) -> int:
        """Return one dimensionless digital level, or raise a configured error."""

        pin = int(pin)
        if pin in self.input_error_pins:
            raise RuntimeError(f"input failed for BCM{pin}")
        return int(self.pin_values[pin])

    def remove_event_detect(self, pin: int) -> None:
        """Record removal of edge detection from one BCM pin."""

        self.removed_pins.append(int(pin))

    def cleanup(self, pin: int) -> None:
        """Record release of one BCM pin and return ``None``."""

        self.cleaned_pins.append(int(pin))

    def trigger_a_rising(self, a_pin: int) -> None:
        """Invoke the registered callback for one simulated A-rising edge."""

        self.callbacks[int(a_pin)](int(a_pin))


@pytest.fixture
def fake_gpio(monkeypatch: pytest.MonkeyPatch) -> FakeGpio:
    """Install and return a fake RPi.GPIO-compatible module.

    Args:
        monkeypatch: Pytest fixture used to replace the module GPIO boundary.

    Returns:
        FakeGpio: Configurable fake GPIO backend.
    """

    gpio = FakeGpio()
    monkeypatch.setattr(treadmill_decoder_legacy, "GPIO", gpio)
    return gpio


def _make_decoder(fake_gpio: FakeGpio) -> treadmill_decoder_legacy.TreadmillDecoder:
    """Construct a decoder on the configured adjustable BCM pins.

    Args:
        fake_gpio: Installed fake GPIO backend.

    Returns:
        TreadmillDecoder: Initialized legacy decoder with dimensionless counts.
    """

    del fake_gpio
    return treadmill_decoder_legacy.TreadmillDecoder(
        treadmill_decoder_legacy.ENC_A_PIN,
        treadmill_decoder_legacy.ENC_B_PIN,
        treadmill_decoder_legacy.MM_PER_COUNT,
    )


def test_decoder_defaults_to_new_treadmill_pins_and_a_rising_only(
    fake_gpio: FakeGpio,
) -> None:
    """The adjustable defaults should use BCM13/16 and one A-rising callback."""

    decoder = _make_decoder(fake_gpio)

    assert treadmill_decoder_legacy.ENC_A_PIN == 13
    assert treadmill_decoder_legacy.ENC_B_PIN == 16
    assert fake_gpio.mode == fake_gpio.BCM
    assert fake_gpio.warnings_enabled is False
    assert fake_gpio.setup_calls == [
        (13, fake_gpio.IN, fake_gpio.PUD_UP),
        (16, fake_gpio.IN, fake_gpio.PUD_UP),
    ]
    assert fake_gpio.event_calls == [(13, fake_gpio.RISING)]

    decoder.close()


def test_older_teensy_count_signs_and_callback_total(fake_gpio: FakeGpio) -> None:
    """Equal A/B should add one; unequal A/B should subtract one."""

    decoder = _make_decoder(fake_gpio)
    decoder.start()

    fake_gpio.pin_values[decoder.a_pin] = 1
    fake_gpio.pin_values[decoder.b_pin] = 1
    fake_gpio.trigger_a_rising(decoder.a_pin)
    positive_state = decoder.snapshot()

    fake_gpio.pin_values[decoder.b_pin] = 0
    fake_gpio.trigger_a_rising(decoder.a_pin)
    net_state = decoder.snapshot()

    assert positive_state.counts == 1
    assert positive_state.edge_count_total == 1
    assert net_state.counts == 0
    assert net_state.edge_count_total == 2

    decoder.close()


def test_callback_before_start_is_ignored(fake_gpio: FakeGpio) -> None:
    """A-rising edges should not change any count while disarmed."""

    decoder = _make_decoder(fake_gpio)
    fake_gpio.trigger_a_rising(decoder.a_pin)

    state = decoder.snapshot()
    assert state.counts == 0
    assert state.edge_count_total == 0

    decoder.close()


def test_gpio_read_failure_only_increments_missed_callback_count(
    fake_gpio: FakeGpio,
) -> None:
    """A failed level read should not invent a legacy treadmill count."""

    decoder = _make_decoder(fake_gpio)
    decoder.start()
    fake_gpio.input_error_pins.add(decoder.a_pin)

    fake_gpio.trigger_a_rising(decoder.a_pin)

    state = decoder.snapshot()
    assert state.counts == 0
    assert state.edge_count_total == 0
    assert state.missed_callback_count == 1

    decoder.close()


def test_zero_clears_count_diagnostics(fake_gpio: FakeGpio) -> None:
    """Zero should clear signed, observed-callback, and missed-callback counts."""

    decoder = _make_decoder(fake_gpio)
    decoder.start()
    fake_gpio.trigger_a_rising(decoder.a_pin)
    fake_gpio.input_error_pins.add(decoder.a_pin)
    fake_gpio.trigger_a_rising(decoder.a_pin)

    decoder.zero()

    state = decoder.snapshot()
    assert state.counts == 0
    assert state.edge_count_total == 0
    assert state.missed_callback_count == 0

    decoder.close()


def test_close_unregisters_callback_and_releases_owned_pins(
    fake_gpio: FakeGpio,
) -> None:
    """Close should release edge detection and exactly the two encoder pins."""

    decoder = _make_decoder(fake_gpio)

    decoder.close()

    assert fake_gpio.removed_pins == [decoder.a_pin]
    assert fake_gpio.cleaned_pins == [decoder.a_pin, decoder.b_pin]
