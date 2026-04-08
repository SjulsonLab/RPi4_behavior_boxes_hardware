"""Desktop plotting capability helpers for BehavBox.

Data contracts:
- environment mappings: ``dict[str, str]``-like objects containing desktop
  session variables such as ``DISPLAY`` or ``WAYLAND_DISPLAY``.
- repository root: ``Path`` pointing at the checkout used for subprocess probes.
- subprocess probe output: one JSON object with keys including ``ok``,
  ``backend``, ``display_env``, and ``failure_reason``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class PlottingDependencyStatus:
    """Availability of Python plotting dependencies.

    Args:
        ok: Whether all required modules appear importable.
        missing_modules: Missing module names in deterministic order.
        reason: Human-readable summary string.
    """

    ok: bool
    missing_modules: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class DesktopSessionStatus:
    """Desktop-session availability for plotting.

    Args:
        ok: Whether a desktop display environment is visible.
        display_env: The chosen display descriptor string, if any.
        reason: Human-readable summary string.
    """

    ok: bool
    display_env: str | None
    reason: str


@dataclass(frozen=True)
class PlottingProbeStatus:
    """Result of a one-shot plotting probe.

    Args:
        ok: Whether the probe completed successfully.
        backend: Plotting backend label when available.
        display_env: The display environment used by the probe.
        failure_reason: Human-readable failure string when ``ok`` is ``False``.
    """

    ok: bool
    backend: str | None
    display_env: str | None
    failure_reason: str | None


def detect_plotting_dependencies(
    spec_finder: Callable[[str], Any] | None = None,
) -> PlottingDependencyStatus:
    """Check whether lightweight plotting dependencies appear importable.

    Args:
        spec_finder: Optional ``find_spec``-style callable used for tests.

    Returns:
        PlottingDependencyStatus: Missing-module summary without importing the
        plotting backend itself.
    """

    finder = spec_finder or importlib.util.find_spec
    missing = []
    for module_name in ("pygame", "matplotlib"):
        if finder(module_name) is None:
            missing.append(module_name)
    if missing:
        return PlottingDependencyStatus(
            ok=False,
            missing_modules=tuple(missing),
            reason=f"missing plotting modules: {', '.join(missing)}",
        )
    return PlottingDependencyStatus(
        ok=True,
        missing_modules=(),
        reason="plotting dependencies are importable",
    )


def detect_desktop_session(env: Mapping[str, str] | None = None) -> DesktopSessionStatus:
    """Check whether a desktop-session display environment is available.

    Args:
        env: Optional environment mapping. Defaults to ``os.environ``.

    Returns:
        DesktopSessionStatus: Desktop availability result.
    """

    current_env = dict(os.environ if env is None else env)
    display = str(current_env.get("DISPLAY", "")).strip()
    wayland_display = str(current_env.get("WAYLAND_DISPLAY", "")).strip()
    if display:
        return DesktopSessionStatus(
            ok=True,
            display_env=f"DISPLAY={display}",
            reason="desktop plotting can use X11 DISPLAY",
        )
    if wayland_display:
        return DesktopSessionStatus(
            ok=True,
            display_env=f"WAYLAND_DISPLAY={wayland_display}",
            reason="desktop plotting can use Wayland display",
        )
    return DesktopSessionStatus(
        ok=False,
        display_env=None,
        reason="desktop plotting requires DISPLAY or WAYLAND_DISPLAY",
    )


def import_plotting_modules():
    """Import and configure the pygame/matplotlib plotting backend.

    Returns:
        tuple: ``(pygame, plt, fg)`` modules configured for the custom backend.
    """

    import pygame
    import pygame.display
    import matplotlib

    matplotlib.use("module://box_runtime.support.pygame_matplotlib.backend_pygame")
    import matplotlib.figure as fg
    import matplotlib.pyplot as plt

    return pygame, plt, fg


def run_plotting_probe_once(env: Mapping[str, str] | None = None) -> PlottingProbeStatus:
    """Run one direct plotting probe in the current process.

    Args:
        env: Optional environment mapping used for desktop detection only.

    Returns:
        PlottingProbeStatus: Probe result for one short-lived plotting window.
    """

    dependency_status = detect_plotting_dependencies()
    if not dependency_status.ok:
        return PlottingProbeStatus(
            ok=False,
            backend=None,
            display_env=None,
            failure_reason=dependency_status.reason,
        )

    desktop_status = detect_desktop_session(env)
    if not desktop_status.ok:
        return PlottingProbeStatus(
            ok=False,
            backend=None,
            display_env=desktop_status.display_env,
            failure_reason=desktop_status.reason,
        )

    pygame = None
    plt = None
    main_display = None
    figure = None
    try:
        pygame, plt, _ = import_plotting_modules()
        pygame.init()
        main_display = pygame.display.set_mode((64, 64))
        figure, axes = plt.subplots(1, 1)
        axes.plot([0, 1], [0, 1])
        figure.canvas.draw()
        main_display.blit(figure, (0, 0))
        pygame.display.update()
        return PlottingProbeStatus(
            ok=True,
            backend="pygame_matplotlib",
            display_env=desktop_status.display_env,
            failure_reason=None,
        )
    except Exception as exc:
        return PlottingProbeStatus(
            ok=False,
            backend=None,
            display_env=desktop_status.display_env,
            failure_reason=str(exc),
        )
    finally:
        try:
            if plt is not None and figure is not None:
                plt.close(figure)
        except Exception:
            pass
        try:
            if pygame is not None:
                pygame.display.quit()
                pygame.quit()
        except Exception:
            pass


def probe_plotting_window(
    repo_root: Path,
    timeout_s: float = 10.0,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> PlottingProbeStatus:
    """Run the plotting probe in a subprocess so hangs cannot wedge the caller.

    Args:
        repo_root: Repository root path added to ``sys.path``.
        timeout_s: Subprocess timeout in seconds.
        env: Optional environment mapping forwarded to the subprocess.
        runner: Optional ``subprocess.run``-style callable.

    Returns:
        PlottingProbeStatus: Probe result parsed from subprocess output.
    """

    repo_path = Path(repo_root).resolve()
    subprocess_env = dict(os.environ)
    if env is not None:
        subprocess_env.update(env)
    probe_code = (
        "import json; "
        "from box_runtime.behavior.plotting_support import run_plotting_probe_once; "
        "print(json.dumps(run_plotting_probe_once().__dict__, sort_keys=True))"
    )
    try:
        completed = runner(
            [sys.executable, "-c", probe_code],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=subprocess_env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return PlottingProbeStatus(
            ok=False,
            backend=None,
            display_env=None,
            failure_reason=f"plotting probe timed out after {timeout_s:.1f} s",
        )

    if completed.returncode != 0:
        return PlottingProbeStatus(
            ok=False,
            backend=None,
            display_env=None,
            failure_reason=(completed.stderr or "").strip() or f"probe exited with code {completed.returncode}",
        )

    try:
        payload = json.loads(_final_nonempty_stdout_line(completed.stdout or ""))
    except Exception as exc:
        return PlottingProbeStatus(
            ok=False,
            backend=None,
            display_env=None,
            failure_reason=f"failed to parse plotting probe output: {exc}",
        )
    return PlottingProbeStatus(
        ok=bool(payload.get("ok", False)),
        backend=payload.get("backend"),
        display_env=payload.get("display_env"),
        failure_reason=payload.get("failure_reason"),
    )


def require_plotting_ready(
    repo_root: Path,
    timeout_s: float = 10.0,
    env: Mapping[str, str] | None = None,
) -> PlottingProbeStatus:
    """Require the plotting subprocess probe to succeed.

    Args:
        repo_root: Repository root path for the subprocess probe.
        timeout_s: Subprocess timeout in seconds.
        env: Optional environment mapping forwarded to the subprocess.

    Returns:
        PlottingProbeStatus: Successful probe result.

    Raises:
        RuntimeError: If plotting is not ready.
    """

    status = probe_plotting_window(repo_root=repo_root, timeout_s=timeout_s, env=env)
    if not status.ok:
        raise RuntimeError(status.failure_reason or "desktop plotting probe failed")
    return status


def plotting_status_dict(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Return a lightweight JSON-serializable plotting capability snapshot.

    Args:
        env: Optional environment mapping used for desktop detection.

    Returns:
        dict[str, object]: Lightweight dependency and desktop-session status.
    """

    dependency_status = detect_plotting_dependencies()
    desktop_status = detect_desktop_session(env)
    return {
        "dependencies_ok": dependency_status.ok,
        "missing_modules": list(dependency_status.missing_modules),
        "desktop_session_ok": desktop_status.ok,
        "display_env": desktop_status.display_env,
        "reason": None if (dependency_status.ok and desktop_status.ok) else (
            dependency_status.reason if not dependency_status.ok else desktop_status.reason
        ),
    }


def _final_nonempty_stdout_line(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""

