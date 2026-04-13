from __future__ import annotations

import subprocess

import pytest

from debug.display_mode_guard import (
    HeadlessDisplayModeError,
    collect_headless_mode_status,
    require_headless_console_mode,
)


def _completed(command: list[str], *, returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    """Build one completed-process test double.

    Args:
        command: Command vector.
        returncode: Process exit code.
        stdout: Captured standard output text.
        stderr: Captured standard error text.

    Returns:
        subprocess.CompletedProcess[str]: Completed process record.
    """

    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def test_collect_headless_mode_status_fails_when_lightdm_is_active() -> None:
    """The mode guard should reject an active desktop manager."""

    def fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command == ["systemctl", "is-active", "lightdm"]:
            return _completed(command, returncode=0, stdout="active\n")
        if command == ["tty"]:
            return _completed(command, returncode=0, stdout="/dev/tty1\n")
        raise AssertionError(command)

    status = collect_headless_mode_status(runner=fake_runner, env={})

    assert status.ok is False
    assert status.lightdm_state == "active"
    assert any("lightdm" in reason for reason in status.reasons)


def test_collect_headless_mode_status_fails_when_display_env_is_present() -> None:
    """The mode guard should reject desktop display environment variables."""

    def fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command == ["systemctl", "is-active", "lightdm"]:
            return _completed(command, returncode=3, stdout="inactive\n")
        if command == ["tty"]:
            return _completed(command, returncode=1, stderr="not a tty\n")
        raise AssertionError(command)

    status = collect_headless_mode_status(
        runner=fake_runner,
        env={"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"},
    )

    assert status.ok is False
    assert status.display == ":0"
    assert status.wayland_display == "wayland-0"
    assert any("DISPLAY" in reason for reason in status.reasons)
    assert any("WAYLAND_DISPLAY" in reason for reason in status.reasons)


def test_collect_headless_mode_status_succeeds_in_headless_mode() -> None:
    """The mode guard should accept compositor-free headless mode."""

    def fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command == ["systemctl", "is-active", "lightdm"]:
            return _completed(command, returncode=3, stdout="inactive\n")
        if command == ["tty"]:
            return _completed(command, returncode=0, stdout="/dev/tty1\n")
        raise AssertionError(command)

    status = collect_headless_mode_status(runner=fake_runner, env={})

    assert status.ok is True
    assert status.lightdm_state == "inactive"
    assert status.tty == "/dev/tty1"
    assert status.reasons == ()


def test_require_headless_console_mode_raises_with_clear_reason() -> None:
    """The strict guard should raise one descriptive error on wrong mode."""

    def fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command == ["systemctl", "is-active", "lightdm"]:
            return _completed(command, returncode=0, stdout="active\n")
        if command == ["tty"]:
            return _completed(command, returncode=1, stderr="not a tty\n")
        raise AssertionError(command)

    with pytest.raises(HeadlessDisplayModeError, match="lightdm"):
        require_headless_console_mode(runner=fake_runner, env={"DISPLAY": ":0"})
