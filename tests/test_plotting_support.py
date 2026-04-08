from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from box_runtime.behavior.plotting_support import (
    DesktopSessionStatus,
    PlottingDependencyStatus,
    detect_desktop_session,
    detect_plotting_dependencies,
    probe_plotting_window,
)


def test_detect_plotting_dependencies_reports_missing_pygame_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing pygame should be reported without importing heavy plotting modules.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the spec lookup.
    """

    def fake_find_spec(name: str):
        if name == "pygame":
            return None
        return object()

    status = detect_plotting_dependencies(spec_finder=fake_find_spec)

    assert isinstance(status, PlottingDependencyStatus)
    assert status.ok is False
    assert status.missing_modules == ("pygame",)


def test_detect_plotting_dependencies_reports_missing_matplotlib_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing matplotlib should be reported without raising.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the spec lookup.
    """

    def fake_find_spec(name: str):
        if name == "matplotlib":
            return None
        return object()

    status = detect_plotting_dependencies(spec_finder=fake_find_spec)

    assert status.ok is False
    assert status.missing_modules == ("matplotlib",)


def test_detect_desktop_session_reports_missing_display_context() -> None:
    """Desktop plotting should report missing display environment cleanly."""

    status = detect_desktop_session({})

    assert isinstance(status, DesktopSessionStatus)
    assert status.ok is False
    assert status.display_env is None
    assert "DISPLAY" in status.reason or "WAYLAND_DISPLAY" in status.reason


def test_detect_desktop_session_accepts_display_variable() -> None:
    """An X11-style DISPLAY variable should satisfy desktop-session detection."""

    status = detect_desktop_session({"DISPLAY": ":0"})

    assert status.ok is True
    assert status.display_env == "DISPLAY=:0"


def test_detect_desktop_session_accepts_wayland_variable() -> None:
    """A Wayland display variable should satisfy desktop-session detection."""

    status = detect_desktop_session({"WAYLAND_DISPLAY": "wayland-0"})

    assert status.ok is True
    assert status.display_env == "WAYLAND_DISPLAY=wayland-0"


def test_probe_plotting_window_timeout_returns_clean_failure(tmp_path: Path) -> None:
    """The plotting probe should fail cleanly on subprocess timeout.

    Args:
        tmp_path: Temporary repo-root stand-in for the subprocess invocation.
    """

    def fake_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    status = probe_plotting_window(
        repo_root=tmp_path,
        timeout_s=0.1,
        env={"DISPLAY": ":0"},
        runner=fake_runner,
    )

    assert status.ok is False
    assert "timed out" in (status.failure_reason or "")


def test_probe_plotting_window_returns_clean_backend_error(tmp_path: Path) -> None:
    """A failing subprocess should surface the backend error text.

    Args:
        tmp_path: Temporary repo-root stand-in for the subprocess invocation.
    """

    def fake_runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout="",
            stderr="backend exploded",
        )

    status = probe_plotting_window(
        repo_root=tmp_path,
        timeout_s=0.1,
        env={"DISPLAY": ":0"},
        runner=fake_runner,
    )

    assert status.ok is False
    assert "backend exploded" in (status.failure_reason or "")


def test_probe_plotting_window_succeeds_with_mocked_subprocess(tmp_path: Path) -> None:
    """A successful probe should return an OK result from stdout.

    Args:
        tmp_path: Temporary repo-root stand-in for the subprocess invocation.
    """

    def fake_runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"ok": true, "backend": "pygame_matplotlib"}\n',
            stderr="",
        )

    status = probe_plotting_window(
        repo_root=tmp_path,
        timeout_s=0.1,
        env={"DISPLAY": ":0"},
        runner=fake_runner,
    )

    assert status.ok is True
    assert status.backend == "pygame_matplotlib"
