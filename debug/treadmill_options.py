"""Shared pin/duration options for the two standalone treadmill diagnostics."""

from __future__ import annotations

import argparse
from dataclasses import replace
import math
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from box_runtime.input.treadmill.config import TreadmillConfig

try:
    from debug.repo_imports import prepare_repo_imports
except ModuleNotFoundError:
    from repo_imports import prepare_repo_imports


def parse_counter_options(
    argv: list[str] | None, description: str
) -> tuple[argparse.Namespace, TreadmillConfig]:
    """Parse standalone options and return args plus a frozen bench-only config.

    Args:
        argv: CLI strings, or None for the process arguments.
        description: Human-readable purpose shown by --help.

    Returns:
        tuple[Namespace, TreadmillConfig]: duration_s is positive finite seconds
        or None. Config pins are distinct, unreserved BCM GPIO numbers in 0..27;
        omitted channels follow the head-fixed manifest. Other settings retain
        production defaults and physical units. The cached manifest is unchanged.

    Raises:
        SystemExit: Help (0) or invalid options (2), before any source is created.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--duration-s", type=_duration, help="otherwise run until Ctrl-C"
    )
    for channel in ("a", "b"):
        parser.add_argument(
            f"--{channel}-pin",
            type=_bcm_pin,
            metavar="BCM",
            help=f"standalone channel {channel.upper()} BCM GPIO number; defaults to the head-fixed manifest",
        )
    args = parser.parse_args(argv)
    prepare_repo_imports(script_path=Path(__file__))
    from box_runtime.io_manifest import load_box_profile
    from box_runtime.input.treadmill.config import load_config

    manifest = load_box_profile("head_fixed")
    settings = load_config(
        {"treadmill": True, "treadmill_config": {"treadmill_required": True}}, manifest
    )
    a_pin = settings.a_bcm_pin if args.a_pin is None else args.a_pin
    b_pin = settings.b_bcm_pin if args.b_pin is None else args.b_pin
    if a_pin == b_pin:
        parser.error("Channel A and B must select different BCM GPIO pins")
    for pin in (a_pin, b_pin):
        if pin in manifest.reserved:
            parser.error(f"GPIO{pin} is reserved: {manifest.reserved[pin].notes}")
    return args, replace(settings, a_bcm_pin=a_pin, b_bcm_pin=b_pin)


def _duration(value: str) -> float:
    """Parse CLI text as positive finite seconds; return float or parser error."""
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be positive finite seconds")
    return seconds


def _bcm_pin(value: str) -> int:
    """Parse CLI text as integer BCM GPIO 0..27; raise parser error if invalid."""
    try:
        pin = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "BCM GPIO must be an integer from 0 to 27"
        ) from exc
    if not 0 <= pin <= 27:
        raise argparse.ArgumentTypeError("BCM GPIO must be in the range 0 to 27")
    return pin
