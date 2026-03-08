"""Validation helpers for YAML visual stimulus specifications."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping
import yaml


@dataclass(frozen=True)
class GratingSpec:
    """Validated drifting grating parameters loaded from YAML.

    Attributes:
        path: Source YAML file path.
        name: User-facing stimulus identifier.
        duration_s: Stimulus duration in seconds.
        angle_deg: Drift orientation in degrees.
        spatial_freq_cpd: Spatial frequency in cycles per degree.
        temporal_freq_hz: Temporal frequency in cycles per second.
        contrast: Unitless contrast in the range [0, 1].
        background_gray_u8: Neutral gray value in uint8 display units [0, 255].
        waveform: Waveform type, either ``"sine"`` or ``"square"``.
        resolution_px: Optional explicit stimulus resolution as ``(width_px, height_px)``.
        degrees_subtended: Optional horizontal display extent in visual degrees.
    """

    path: Path
    name: str
    duration_s: float
    angle_deg: float
    spatial_freq_cpd: float
    temporal_freq_hz: float
    contrast: float
    background_gray_u8: int
    waveform: str
    resolution_px: tuple[int, int] | None = None
    degrees_subtended: float | None = None


def load_grating_spec(path: str | Path) -> GratingSpec:
    """Load and validate a drifting grating specification from YAML.

    Args:
        path: Filesystem path to a YAML document describing one stimulus.

    Returns:
        GratingSpec: Parsed stimulus parameters with validated units and ranges.

    Raises:
        ValueError: If required fields are missing or violate the documented
            ranges and types.
    """

    spec_path = Path(path).expanduser().resolve()
    if spec_path.suffix.lower() == ".json":
        raise ValueError(
            "JSON grating specs are no longer supported; convert this file to YAML (.yaml or .yml)"
        )
    if spec_path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("grating spec path must end with .yaml or .yml")

    try:
        payload = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML grating spec: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise ValueError(f"grating spec must be a YAML mapping, got {type(payload).__name__}")

    name = _require_nonempty_string(payload, "name")
    duration_s = _require_positive_float(payload, "duration_s")
    angle_deg = _require_finite_float(payload, "angle_deg")
    spatial_freq_cpd = _require_positive_float(payload, "spatial_freq_cpd")
    temporal_freq_hz = _require_nonnegative_float(payload, "temporal_freq_hz")
    contrast = _require_float_in_range(payload, "contrast", 0.0, 1.0)
    background_gray_u8 = _require_int_in_range(payload, "background_gray_u8", 0, 255)
    waveform = _require_nonempty_string(payload, "waveform").lower()
    if waveform not in {"sine", "square"}:
        raise ValueError("waveform must be 'sine' or 'square'")

    resolution_px = _optional_resolution(payload.get("resolution_px"))
    degrees_subtended = _optional_positive_float(payload.get("degrees_subtended"), "degrees_subtended")

    return GratingSpec(
        path=spec_path,
        name=name,
        duration_s=duration_s,
        angle_deg=angle_deg,
        spatial_freq_cpd=spatial_freq_cpd,
        temporal_freq_hz=temporal_freq_hz,
        contrast=contrast,
        background_gray_u8=background_gray_u8,
        waveform=waveform,
        resolution_px=resolution_px,
        degrees_subtended=degrees_subtended,
    )


def _require_nonempty_string(payload: Mapping[str, Any], key: str) -> str:
    """Validate a required non-empty string field.

    Args:
        payload: YAML mapping parsed into Python objects.
        key: Required mapping key.

    Returns:
        str: Trimmed string value.
    """

    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _require_positive_float(payload: Mapping[str, Any], key: str) -> float:
    """Validate a required strictly positive float field.

    Args:
        payload: YAML mapping parsed into Python objects.
        key: Required mapping key.

    Returns:
        float: Positive finite numeric value.
    """

    value = _require_finite_float(payload, key)
    if value <= 0.0:
        raise ValueError(f"{key} must be > 0")
    return value


def _require_nonnegative_float(payload: Mapping[str, Any], key: str) -> float:
    """Validate a required non-negative float field.

    Args:
        payload: YAML mapping parsed into Python objects.
        key: Required mapping key.

    Returns:
        float: Non-negative finite numeric value.
    """

    value = _require_finite_float(payload, key)
    if value < 0.0:
        raise ValueError(f"{key} must be >= 0")
    return value


def _require_finite_float(payload: Mapping[str, Any], key: str) -> float:
    """Validate a required finite numeric field.

    Args:
        payload: YAML mapping parsed into Python objects.
        key: Required mapping key.

    Returns:
        float: Finite numeric value.
    """

    value = payload.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} must be numeric")
    value_f = float(value)
    if not math.isfinite(value_f):
        raise ValueError(f"{key} must be finite")
    return value_f


def _require_float_in_range(
    payload: Mapping[str, Any],
    key: str,
    min_value: float,
    max_value: float,
) -> float:
    """Validate a required float constrained to an inclusive range.

    Args:
        payload: YAML mapping parsed into Python objects.
        key: Required mapping key.
        min_value: Inclusive lower bound.
        max_value: Inclusive upper bound.

    Returns:
        float: Numeric value in the requested range.
    """

    value = _require_finite_float(payload, key)
    if value < min_value or value > max_value:
        raise ValueError(f"{key} must be between {min_value} and {max_value}")
    return value


def _require_int_in_range(payload: Mapping[str, Any], key: str, min_value: int, max_value: int) -> int:
    """Validate a required integer constrained to an inclusive range.

    Args:
        payload: YAML mapping parsed into Python objects.
        key: Required mapping key.
        min_value: Inclusive lower bound.
        max_value: Inclusive upper bound.

    Returns:
        int: Integer value in the requested range.
    """

    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    if value < min_value or value > max_value:
        raise ValueError(f"{key} must be between {min_value} and {max_value}")
    return value


def _optional_resolution(value: Any) -> tuple[int, int] | None:
    """Validate an optional ``[width_px, height_px]`` YAML resolution field.

    Args:
        value: Raw YAML value for ``resolution_px``.

    Returns:
        tuple[int, int] | None: Validated resolution in pixels.
    """

    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("resolution_px must be a two-element array [width_px, height_px]")
    width_px, height_px = value
    if not isinstance(width_px, int) or not isinstance(height_px, int):
        raise ValueError("resolution_px values must be integers")
    if width_px <= 0 or height_px <= 0:
        raise ValueError("resolution_px values must be > 0")
    return (width_px, height_px)


def _optional_positive_float(value: Any, key: str) -> float | None:
    """Validate an optional strictly positive float field.

    Args:
        value: Raw YAML value.
        key: Field name used in validation errors.

    Returns:
        float | None: Positive numeric value or ``None`` if absent.
    """

    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} must be numeric")
    value_f = float(value)
    if not math.isfinite(value_f) or value_f <= 0.0:
        raise ValueError(f"{key} must be > 0")
    return value_f
