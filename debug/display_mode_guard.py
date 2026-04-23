"""Headless/console-mode guard helpers for DRM display debug scripts.

Data contracts:
- environment mappings: ``dict[str, str]``-like objects containing desktop
  session variables such as ``DISPLAY`` and ``WAYLAND_DISPLAY``.
- command runner: ``subprocess.run``-style callable returning
  ``subprocess.CompletedProcess[str]``.
- return values: ``HeadlessModeStatus`` records summarizing detected desktop
  ownership state and any reasons a DRM-exclusive debug script should refuse to
  run.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
from typing import Callable, Mapping


class HeadlessDisplayModeError(RuntimeError):
    """Raised when a debug script is launched outside the required headless mode."""


@dataclass(frozen=True)
class HeadlessModeStatus:
    """Detected display/session state for headless DRM validation.

    Args:
        ok: Whether the current host state is acceptable for headless DRM use.
        lightdm_state: ``systemctl is-active lightdm`` output, or ``"unknown"``.
        display: ``DISPLAY`` environment variable value when present.
        wayland_display: ``WAYLAND_DISPLAY`` environment variable value when present.
        tty: Diagnostic ``tty`` command output or error text.
        reasons: Ordered failure reasons explaining why the mode is rejected.
    """

    ok: bool
    lightdm_state: str
    display: str | None
    wayland_display: str | None
    tty: str | None
    reasons: tuple[str, ...]

    def describe(self) -> str:
        """Return one human-readable status report for operators.

        Returns:
            str: Multi-line explanation of the detected mode and failure reasons.
        """

        lines = [
            f"lightdm state: {self.lightdm_state}",
            f"DISPLAY: {self.display or '<unset>'}",
            f"WAYLAND_DISPLAY: {self.wayland_display or '<unset>'}",
            f"tty: {self.tty or '<unknown>'}",
        ]
        if self.reasons:
            lines.append("Refusing to continue because:")
            lines.extend(f"- {reason}" for reason in self.reasons)
        else:
            lines.append("Headless DRM mode looks valid.")
        return "\n".join(lines)


def collect_headless_mode_status(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    env: Mapping[str, str] | None = None,
) -> HeadlessModeStatus:
    """Collect desktop-ownership signals for headless DRM debug scripts.

    Args:
        runner: ``subprocess.run``-style callable used to query host state.
        env: Optional environment mapping. Defaults to ``os.environ``.

    Returns:
        HeadlessModeStatus: Collected headless/desktop mode summary.
    """

    current_env = dict(os.environ if env is None else env)
    display = str(current_env.get("DISPLAY", "")).strip() or None
    wayland_display = str(current_env.get("WAYLAND_DISPLAY", "")).strip() or None

    lightdm_result = runner(
        ["systemctl", "is-active", "lightdm"],
        capture_output=True,
        text=True,
        check=False,
    )
    lightdm_state = (
        (lightdm_result.stdout or "").strip()
        or (lightdm_result.stderr or "").strip()
        or "unknown"
    )

    tty_result = runner(
        ["tty"],
        capture_output=True,
        text=True,
        check=False,
    )
    tty_text = (
        (tty_result.stdout or "").strip()
        or (tty_result.stderr or "").strip()
        or None
    )

    reasons: list[str] = []
    if lightdm_state == "active":
        reasons.append("lightdm is active, so the desktop/compositor may own the displays")
    if display is not None:
        reasons.append(f"DISPLAY is set to {display!r}, indicating a desktop/X11 session")
    if wayland_display is not None:
        reasons.append(
            f"WAYLAND_DISPLAY is set to {wayland_display!r}, indicating a Wayland desktop session"
        )

    return HeadlessModeStatus(
        ok=not reasons,
        lightdm_state=lightdm_state,
        display=display,
        wayland_display=wayland_display,
        tty=tty_text,
        reasons=tuple(reasons),
    )


def require_headless_console_mode(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    env: Mapping[str, str] | None = None,
) -> HeadlessModeStatus:
    """Require a compositor-free headless mode before starting DRM checks.

    Args:
        runner: ``subprocess.run``-style callable used to query host state.
        env: Optional environment mapping. Defaults to ``os.environ``.

    Returns:
        HeadlessModeStatus: Accepted headless-mode summary.

    Raises:
        HeadlessDisplayModeError: If desktop/session state is incompatible with
            headless DRM validation.
    """

    status = collect_headless_mode_status(runner=runner, env=env)
    if not status.ok:
        raise HeadlessDisplayModeError(status.describe())
    return status
