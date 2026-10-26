"""Immutable, validated calibration/runtime settings; pins come from the manifest."""

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Mapping

import yaml

from box_runtime.io_manifest import BoxProfileManifest


class ConfigurationError(ValueError):
    """Invalid treadmill configuration, before any hardware is claimed."""


@dataclass(frozen=True)
class TreadmillConfig:
    """Scalar configuration: BCM pin numbers, mm/cycle, seconds, Hz and ns.

    Integer sizes are event/record counts; sign is +/-1 in encoder coordinates.
    Calibration/criticality fields are Booleans, provenance and bias are strings.
    Maximum transition Hz may be None until measured for production acceptance.
    """

    a_bcm_pin: int
    b_bcm_pin: int
    mm_per_encoder_cycle: float
    locomotion_sign: int
    calibration_verified: bool
    calibration_source: str
    calibration_note: str
    gpio_bias: str
    debounce_period_us: int
    kernel_event_buffer_size: int
    read_batch_size: int
    speed_timeout_s: float
    continuous_log_rate_hz: float
    continuous_log_flush_interval_s: float
    diagnostic_ring_buffer_size: int
    diagnostic_event_queue_size: int
    heartbeat_interval_s: float
    heartbeat_failure_timeout_s: float
    processing_lag_warning_ns: int
    startup_timeout_s: float
    shutdown_timeout_s: float
    treadmill_required: bool
    continuous_logger_required: bool
    expected_max_transition_rate_hz: float | None


def load_config(
    session_info: Mapping[str, object], manifest: BoxProfileManifest
) -> TreadmillConfig | None:
    """Merge defaults and overrides, returning validated config or disabled None.

    Args:
        session_info: Session mapping with Boolean treadmill and optional settings.
        manifest: Resolved profile, the sole authority for A/B BCM numbers.

    Returns:
        TreadmillConfig | None: Frozen scalar settings, or None when disabled.

    Raises:
        ConfigurationError: Invalid fields/profile. No GPIO or processes start.
    """
    enabled = session_info.get("treadmill", False)
    if type(enabled) is not bool:
        raise ConfigurationError("treadmill must be a Boolean")
    if not enabled:
        return None
    if manifest.profile_name != "head_fixed":
        raise ConfigurationError("Enabled treadmill requires the head_fixed profile")
    for key in (
        "treadmill_speed_hz",
        "treadmill_wheel_diameter_cm",
        "treadmill_pulses_per_rotation",
    ):
        if key in session_info:
            raise ConfigurationError(
                f"Retired {key}: migrate to treadmill_config; mm_per_encoder_cycle = "
                "pi * (10 * diameter_cm) / cycles_per_revolution; logging rate is continuous_log_rate_hz"
            )
    try:
        pins = [manifest.inputs[name].pin for name in ("treadmill_1", "treadmill_2")]
    except KeyError as exc:
        raise ConfigurationError("Manifest must provide both treadmill lines") from exc
    if any(type(pin) is not int or pin < 0 for pin in pins) or pins[0] == pins[1]:
        raise ConfigurationError(
            "Manifest treadmill pins must be distinct nonnegative BCM integers"
        )
    defaults = yaml.safe_load(
        Path(__file__).with_name("defaults.yaml").read_text(encoding="ascii")
    )
    overrides = session_info.get("treadmill_config", {})
    if not isinstance(overrides, Mapping):
        raise ConfigurationError("treadmill_config must be a mapping")
    unknown = set(overrides) - set(defaults)
    if unknown:
        raise ConfigurationError(
            f"Unknown treadmill_config keys: {sorted(unknown, key=str)}"
        )
    values = {**defaults, **overrides}
    integer_fields = {
        "debounce_period_us",
        "kernel_event_buffer_size",
        "read_batch_size",
        "diagnostic_ring_buffer_size",
        "diagnostic_event_queue_size",
        "processing_lag_warning_ns",
    }
    boolean_fields = {
        "calibration_verified",
        "treadmill_required",
        "continuous_logger_required",
    }
    for name, value in values.items():
        if name in boolean_fields:
            valid = type(value) is bool
        elif name in ("calibration_source", "calibration_note"):
            valid = isinstance(value, str) and bool(value.strip())
        elif name == "gpio_bias":
            valid = value in ("pull_up", "pull_down", "disabled")
        elif name == "locomotion_sign":
            valid = type(value) is int and value in (-1, 1)
        elif name == "expected_max_transition_rate_hz" and value is None:
            valid = True
        elif name in integer_fields:
            valid = type(value) is int and (
                value >= 0 if name == "debounce_period_us" else value > 0
            )
        else:
            valid = type(value) in (int, float) and math.isfinite(value) and value > 0
        if not valid:
            raise ConfigurationError(f"Invalid {name}: {value!r}")
    if values["heartbeat_failure_timeout_s"] <= values["heartbeat_interval_s"]:
        raise ConfigurationError(
            "heartbeat_failure_timeout_s must exceed heartbeat_interval_s"
        )
    return TreadmillConfig(a_bcm_pin=pins[0], b_bcm_pin=pins[1], **values)
