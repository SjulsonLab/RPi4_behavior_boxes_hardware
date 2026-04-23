"""Tests for the visual-only camera plus drifting-grating launcher helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sample_tasks.head_fixed_gonogo.visual_only_mode import (
    build_camera_service_environment,
    build_camera_service_start_command,
    build_visual_only_lightdm_plan,
    build_visual_only_session_info,
    camera_monitor_url,
    camera_service_status_url,
    ensure_camera_service_running,
)


def test_build_visual_only_session_info_targets_requested_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Visual-only session config should preserve the requested visual connector.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        None.
    """

    monkeypatch.setattr(
        "sample_tasks.head_fixed_gonogo.visual_only_mode._host_looks_like_raspberry_pi",
        lambda: True,
    )
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        session_info = build_visual_only_session_info(
            Path(tmp),
            "visual_only_run",
            visual_connector="HDMI-A-1",
            grating_names=["go_grating"],
        )

    assert session_info["visual_display_backend"] == "drm"
    assert session_info["visual_display_connector"] == "HDMI-A-1"
    assert session_info["vis_gratings"] == [
        str(repo_root / "box_runtime" / "visual_stimuli" / "go_grating.yaml")
    ]


def test_build_visual_only_lightdm_plan_stops_and_restores_when_requested() -> None:
    """Dedicated grating mode should stop lightdm before and restore after.

    Returns:
        None.
    """

    plan = build_visual_only_lightdm_plan(stop_for_grating=True, restore_after=True)

    assert plan["before"] == ["stop"]
    assert plan["after"] == ["start"]


def test_build_camera_service_environment_sets_port_and_storage_root() -> None:
    """Camera service env should expose the requested port and storage root.

    Returns:
        None.
    """

    env = build_camera_service_environment(
        {"PATH": "/usr/bin"},
        port=8123,
        storage_root=Path("/tmp/camera-storage"),
    )

    assert env["PATH"] == "/usr/bin"
    assert env["CAMERA_SERVICE_PORT"] == "8123"
    assert env["CAMERA_STORAGE_ROOT"] == "/tmp/camera-storage"


def test_build_camera_service_start_command_uses_module_entrypoint() -> None:
    """Camera service should start through the module entrypoint.

    Returns:
        None.
    """

    assert build_camera_service_start_command() == [
        "python3",
        "-m",
        "box_runtime.video_recording.http_camera_service",
    ]


def test_camera_service_urls_use_requested_host_and_port() -> None:
    """Status and monitor URLs should use the configured host and port.

    Returns:
        None.
    """

    assert camera_service_status_url(port=8123) == "http://127.0.0.1:8123/api/status"
    assert camera_monitor_url(host="10.0.0.5", port=8123) == "http://10.0.0.5:8123/monitor"


def test_ensure_camera_service_running_reuses_existing_service() -> None:
    """Healthy service should be reused without starting a new process.

    Returns:
        None.
    """

    launched: list[tuple[list[str], dict[str, str]]] = []

    def fake_status_loader(url: str):
        del url
        return {"status": "ok"}

    def fake_launcher(command: list[str], env: dict[str, str]):
        launched.append((command, env))
        return object()

    monitor_url, started_here = ensure_camera_service_running(
        port=8000,
        monitor_host="10.49.98.223",
        storage_root=Path("/tmp/camera-storage"),
        status_loader=fake_status_loader,
        process_launcher=fake_launcher,
    )

    assert monitor_url == "http://10.49.98.223:8000/monitor"
    assert started_here is False
    assert launched == []


def test_ensure_camera_service_running_starts_service_after_initial_failure() -> None:
    """Unhealthy service should trigger a start and wait for a healthy status.

    Returns:
        None.
    """

    status_calls: list[str] = []
    launched: list[tuple[list[str], dict[str, str]]] = []
    statuses = iter(
        [
            RuntimeError("connection refused"),
            {"status": "ok"},
        ]
    )

    def fake_status_loader(url: str):
        status_calls.append(url)
        result = next(statuses)
        if isinstance(result, Exception):
            raise result
        return result

    def fake_launcher(command: list[str], env: dict[str, str]):
        launched.append((command, env))
        return object()

    monitor_url, started_here = ensure_camera_service_running(
        port=8000,
        monitor_host="10.49.98.223",
        storage_root=Path("/tmp/camera-storage"),
        status_loader=fake_status_loader,
        process_launcher=fake_launcher,
        sleep=lambda _delay_s: None,
        startup_attempts=1,
    )

    assert monitor_url == "http://10.49.98.223:8000/monitor"
    assert started_here is True
    assert len(status_calls) == 2
    assert launched[0][0] == [
        "python3",
        "-m",
        "box_runtime.video_recording.http_camera_service",
    ]
    assert launched[0][1]["CAMERA_STORAGE_ROOT"] == "/tmp/camera-storage"


def test_ensure_camera_service_running_raises_if_service_never_becomes_healthy() -> None:
    """Launcher should fail fast if the camera service never becomes healthy.

    Returns:
        None.
    """

    def fake_status_loader(url: str):
        del url
        raise RuntimeError("connection refused")

    with pytest.raises(RuntimeError, match="camera service did not become healthy"):
        ensure_camera_service_running(
            port=8000,
            monitor_host="10.49.98.223",
            status_loader=fake_status_loader,
            process_launcher=lambda command, env: object(),
            sleep=lambda _delay_s: None,
            startup_attempts=2,
        )
