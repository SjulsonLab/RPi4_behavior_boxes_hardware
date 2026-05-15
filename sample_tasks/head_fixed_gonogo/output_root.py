"""Shared output-root resolution helpers for head-fixed go/no-go launchers.

Data contracts:

- explicit CLI output-root paths always override platform-specific defaults.
- on Raspberry Pi, the default prefers the mounted SSD root
  ``/mnt/behavbox_ssd/head_fixed_gonogo_runs`` when that mount is ready.
- otherwise the fallback output root is used.
"""

from __future__ import annotations

import os
from pathlib import Path
import platform
from typing import Callable


def detect_raspberry_pi() -> bool:
    """Return whether the current host should be treated as a Raspberry Pi.

    Returns:
        bool: ``True`` when the project GPIO backend identifies this host as a
        Raspberry Pi, else ``False``.
    """

    from box_runtime.behavior.gpio_backend import is_raspberry_pi

    return bool(is_raspberry_pi())


def detect_raspberry_pi_host() -> bool:
    """Return whether the underlying host looks like a Raspberry Pi.

    Unlike :func:`detect_raspberry_pi`, this helper ignores mock-mode
    environment overrides and answers only the host-hardware question.

    Returns:
        bool: ``True`` when the current machine appears to be a Raspberry Pi.
    """

    machine = platform.machine().lower()
    if not any(token in machine for token in ("arm", "aarch")):
        return False

    try:
        with open("/proc/device-tree/model", "r", encoding="utf-8") as stream:
            model = stream.read().lower()
        return "raspberry pi" in model
    except Exception:
        return False


def resolve_output_root(
    cli_output_root: str | os.PathLike[str] | None,
    *,
    is_raspberry_pi_fn: Callable[[], bool] = detect_raspberry_pi,
    ssd_mount_path: Path = Path("/mnt/behavbox_ssd"),
    ssd_subdir_name: str = "head_fixed_gonogo_runs",
    fallback_output_root: Path = Path("tmp_task_runs"),
    ssd_is_ready_fn: Callable[[Path], bool] | None = None,
) -> Path:
    """Resolve the session output root for one launcher invocation.

    Args:
        cli_output_root: Explicit CLI output-root path or ``None``.
        is_raspberry_pi_fn: Zero-argument detector for Raspberry Pi hosts.
        ssd_mount_path: Expected SSD mountpoint path.
        ssd_subdir_name: Session-output subdirectory name under the SSD mount.
        fallback_output_root: Fallback path used when the SSD is not available.
        ssd_is_ready_fn: Optional predicate indicating whether the SSD mount is
            ready to use.

    Returns:
        Path: Absolute output-root path for the session directory tree.
    """

    if cli_output_root is not None:
        return Path(cli_output_root).expanduser().resolve()

    mount_path = Path(ssd_mount_path).expanduser()
    if ssd_is_ready_fn is None:
        ssd_is_ready_fn = lambda path: path.exists() and path.is_dir() and path.is_mount()

    if is_raspberry_pi_fn() and ssd_is_ready_fn(mount_path):
        return (mount_path / str(ssd_subdir_name)).resolve()

    return Path(fallback_output_root).expanduser().resolve()
