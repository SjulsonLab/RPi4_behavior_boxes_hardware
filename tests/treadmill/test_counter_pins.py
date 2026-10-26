"""Standalone BCM pin selection without acquiring GPIO or starting children."""

from copy import deepcopy
from dataclasses import replace

import pytest

from box_runtime import io_manifest
from box_runtime.input.treadmill.config import TreadmillConfig, load_config
from box_runtime.input.treadmill.decoder import Decoder, EdgeEvent
from box_runtime.input.treadmill.runtime import HealthReport
from box_runtime.input.treadmill.state import TreadmillState
from debug import treadmill_counter


class CounterRuntime:
    """Small runtime stand-in used only to inspect the CLI/config boundary."""

    def __init__(self, settings: TreadmillConfig) -> None:
        """Store scalar settings and an idle state; create no external resources."""
        self.config = settings
        self.state = TreadmillState(health="READY")
        self.first_failure_diagnostic = None
        self.started = False
        self.closed = False

    def start(self) -> None:
        """Record successful startup; return None without hardware access."""
        self.started = True

    def snapshot(self) -> TreadmillState:
        """Return scalar mm/mm/s/ns/count state, marking orderly close as STOPPED."""
        return (
            replace(self.state, health="STOPPED", acquisition_available=False)
            if self.closed
            else self.state
        )

    def health_report(self) -> HealthReport:
        """Return immutable health with the state's dimensionless integrity flag."""
        return HealthReport(
            "STOPPED" if self.closed else self.state.health,
            self.state.integrity_valid,
            not self.closed,
            None,
            observed_health_codes=self.state.observed_acquisition_health_codes,
        )

    def close(self) -> None:
        """Record cleanup; return None without external resources."""
        self.closed = True


def interrupt(seconds: float) -> None:
    """Represent Ctrl-C during a wait measured in seconds; raise KeyboardInterrupt."""
    raise KeyboardInterrupt


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ([], (13, 16)),
        (["--a-pin", "23", "--b-pin", "24"], (23, 24)),
        (["--a-pin", "23"], (23, 16)),
        (["--b-pin", "24"], (13, 24)),
        (["--a-pin", "24", "--b-pin", "23"], (24, 23)),
        (["--a-pin", "0", "--b-pin", "27"], (0, 27)),
    ],
)
def test_counter_pin_selection_reaches_runtime_and_readiness(
    arguments, expected, capsys
):
    instances = []

    def factory(settings):
        """Capture the selected BCM numbers without constructing a real runtime."""
        instance = CounterRuntime(settings)
        instances.append(instance)
        return instance

    assert (
        treadmill_counter.main(arguments, runtime_factory=factory, wait_fn=interrupt)
        == 0
    )
    instance = instances[0]
    assert (instance.config.a_bcm_pin, instance.config.b_bcm_pin) == expected
    assert instance.started and instance.closed
    assert f"BCM{expected[0]}/{expected[1]}" in capsys.readouterr().out


def test_omitted_cli_pins_follow_the_supplied_manifest(monkeypatch):
    original = io_manifest.load_box_profile("head_fixed")
    inputs = dict(original.inputs)
    inputs["treadmill_1"] = replace(inputs["treadmill_1"], pin=2)
    inputs["treadmill_2"] = replace(inputs["treadmill_2"], pin=3)
    alternate = replace(original, inputs=inputs)

    def profile(name):
        """Return a test head-fixed manifest with different default BCM pins."""
        assert name == "head_fixed"
        return alternate

    def factory(settings):
        """Assert defaults come from the manifest rather than copied literals."""
        assert (settings.a_bcm_pin, settings.b_bcm_pin) == (2, 3)
        return CounterRuntime(settings)

    monkeypatch.setattr(io_manifest, "load_box_profile", profile)
    assert treadmill_counter.main([], runtime_factory=factory, wait_fn=interrupt) == 0


@pytest.mark.parametrize(
    "arguments,message",
    [
        (["--a-pin", "-1"], "BCM"),
        (["--b-pin", "28"], "BCM"),
        (["--a-pin", "40"], "BCM"),
        (["--a-pin", "23.0"], "integer"),
        (["--b-pin", "true"], "integer"),
        (["--a-pin", "23", "--b-pin", "23"], "different"),
        (["--a-pin", "16"], "different"),
        (["--a-pin", "9"], "reserved"),
        (["--b-pin", "9"], "reserved"),
    ],
)
def test_invalid_cli_pins_fail_before_runtime_construction(arguments, message, capsys):
    def factory(settings):
        """Fail the test if invalid pins reach runtime construction."""
        pytest.fail("Invalid pins reached runtime construction")

    with pytest.raises(SystemExit) as error:
        treadmill_counter.main(arguments, runtime_factory=factory)
    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_standalone_override_does_not_mutate_manifest_or_normal_config():
    manifest = io_manifest.load_box_profile("head_fixed")
    before = deepcopy(manifest)
    assert (
        treadmill_counter.main(
            ["--a-pin", "23", "--b-pin", "24"],
            runtime_factory=CounterRuntime,
            wait_fn=interrupt,
        )
        == 0
    )
    assert io_manifest.load_box_profile("head_fixed") == before
    normal = load_config({"treadmill": True}, manifest)
    assert (normal.a_bcm_pin, normal.b_bcm_pin) == (13, 16)


def test_failure_context_labels_the_cli_selected_pin(capsys):
    def factory(settings):
        """Inject a pure same-level failure into a stub using the CLI config."""
        instance = CounterRuntime(settings)
        decoder = Decoder(settings)
        decoder.process_event(EdgeEvent(1, "A", "falling", 1, 1), 2)
        instance.state = decoder.snapshot(2)
        instance.first_failure_diagnostic = decoder.first_failure_diagnostic
        return instance

    assert (
        treadmill_counter.main(
            ["--a-pin", "23", "--b-pin", "24"],
            runtime_factory=factory,
            wait_fn=interrupt,
        )
        == 1
    )
    assert "Channel A / BCM23: falling" in capsys.readouterr().out


def test_counter_help_explains_bcm_pin_arguments(capsys):
    with pytest.raises(SystemExit) as error:
        treadmill_counter.main(["--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--a-pin" in output and "--b-pin" in output and "BCM" in output
