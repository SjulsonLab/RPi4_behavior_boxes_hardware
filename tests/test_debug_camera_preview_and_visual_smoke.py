from __future__ import annotations

from pathlib import Path

import pytest

from debug.camera_preview_and_visual_smoke import run_camera_preview_and_visual_smoke
from debug.display_mode_guard import HeadlessModeStatus


def test_combined_smoke_aborts_before_runtime_when_mode_guard_fails(tmp_path: Path) -> None:
    """Wrong display mode should prevent both preview and stimulus startup.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """

    class ShouldNotConstructCamera:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("camera runtime should not be constructed")

    class ShouldNotConstructVisual:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("visual runtime should not be constructed")

    with pytest.raises(RuntimeError, match="wrong mode"):
        run_camera_preview_and_visual_smoke(
            output_root=tmp_path,
            require_mode=lambda: (_ for _ in ()).throw(RuntimeError("wrong mode")),
            camera_runtime_factory=ShouldNotConstructCamera,
            visual_factory=ShouldNotConstructVisual,
        )


def test_combined_smoke_starts_camera_before_visual_and_reports_connectors(tmp_path: Path) -> None:
    """Camera preview should start before the visual path and report the fixed topology.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """

    calls: list[str] = []

    class FakeCameraRuntime:
        def __init__(self, camera_id: str, session_info: dict[str, object]) -> None:
            self.camera_id = camera_id
            self.session_info = session_info
            self.preview_active = False

        def prepare(self) -> None:
            calls.append("camera_prepare")

        def start_preview(self) -> None:
            calls.append("camera_start_preview")
            self.preview_active = True

        def stop_preview(self) -> None:
            calls.append("camera_stop_preview")
            self.preview_active = False

        def state_dict(self) -> dict[str, object]:
            return {
                "preview_active": self.preview_active,
                "storage_root": str(tmp_path / "camera_storage"),
                "drm_diagnostics": {
                    "backend": "drm_preview",
                    "requested_connector": "HDMI-A-1",
                    "reserved_crtc_id": 22,
                    "last_request": {
                        "allow_modeset": True,
                        "framebuffer_id": 44,
                    },
                },
            }

        def close(self) -> None:
            calls.append("camera_close")

    class FakeVisualStim:
        def __init__(self, session_info: dict[str, object]) -> None:
            self.session_info = session_info
            self._metrics = {
                "play_count": 0,
                "current_label": "gray",
                "timing_log": [],
                "drm_diagnostics": {
                    "backend": "drm_visual",
                    "requested_connector": "HDMI-A-2",
                    "reserved_crtc_id": 52,
                    "last_request": {
                        "allow_modeset": True,
                        "framebuffer_id": 77,
                    },
                },
            }
            calls.append("visual_init")

        def show_grating(self, grating_name: str) -> None:
            calls.append(f"visual_show_{grating_name}")
            self._metrics["play_count"] += 1
            self._metrics["current_label"] = grating_name
            self._metrics["timing_log"].append({"label": grating_name})

        def close(self) -> None:
            calls.append("visual_close")

    summary = run_camera_preview_and_visual_smoke(
        output_root=tmp_path,
        overlap_s=0.0,
        require_mode=lambda: HeadlessModeStatus(
            ok=True,
            lightdm_state="inactive",
            display=None,
            wayland_display=None,
            tty="/dev/tty1",
            reasons=(),
        ),
        camera_runtime_factory=FakeCameraRuntime,
        visual_factory=FakeVisualStim,
        sleep_fn=lambda _seconds: None,
        repo_root=tmp_path,
        env={},
        home_dir=tmp_path,
    )

    assert calls == [
        "camera_prepare",
        "camera_start_preview",
        "visual_init",
        "visual_show_go_grating",
        "visual_show_nogo_grating",
        "visual_close",
        "camera_stop_preview",
        "camera_close",
    ]
    assert summary["preview_connector"] == "HDMI-A-1"
    assert summary["visual_connector"] == "HDMI-A-2"
    assert summary["preview_active_after_start"] is True
    assert summary["preview_active_after_stop"] is False
    assert summary["visual_play_count"] == 2
    assert summary["preview_drm_diagnostics"]["reserved_crtc_id"] == 22
    assert summary["visual_drm_diagnostics"]["reserved_crtc_id"] == 52
    assert summary["preview_drm_diagnostics"]["last_request"]["framebuffer_id"] == 44
    assert summary["visual_drm_diagnostics"]["last_request"]["framebuffer_id"] == 77


def test_combined_smoke_stops_preview_if_visual_startup_fails(tmp_path: Path) -> None:
    """Partial startup should still tear down the active camera preview cleanly.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """

    calls: list[str] = []

    class FakeCameraRuntime:
        def __init__(self, camera_id: str, session_info: dict[str, object]) -> None:
            self.preview_active = False

        def prepare(self) -> None:
            calls.append("camera_prepare")

        def start_preview(self) -> None:
            calls.append("camera_start_preview")
            self.preview_active = True

        def stop_preview(self) -> None:
            calls.append("camera_stop_preview")
            self.preview_active = False

        def state_dict(self) -> dict[str, object]:
            return {
                "preview_active": self.preview_active,
                "drm_diagnostics": {
                    "backend": "drm_preview",
                    "requested_connector": "HDMI-A-1",
                    "reserved_plane_id": 33,
                },
            }

        def close(self) -> None:
            calls.append("camera_close")

    class FailingVisualStim:
        def __init__(self, session_info: dict[str, object]) -> None:
            calls.append("visual_init")

        def show_grating(self, grating_name: str) -> None:
            calls.append(f"visual_show_{grating_name}")
            raise RuntimeError("visual startup failed")

        def close(self) -> None:
            calls.append("visual_close")

    with pytest.raises(RuntimeError, match="visual startup failed") as exc_info:
        run_camera_preview_and_visual_smoke(
            output_root=tmp_path,
            overlap_s=0.0,
            require_mode=lambda: HeadlessModeStatus(
                ok=True,
                lightdm_state="inactive",
                display=None,
                wayland_display=None,
                tty="/dev/tty1",
                reasons=(),
            ),
            camera_runtime_factory=FakeCameraRuntime,
            visual_factory=FailingVisualStim,
            sleep_fn=lambda _seconds: None,
            repo_root=tmp_path,
            env={},
            home_dir=tmp_path,
        )

    assert calls == [
        "camera_prepare",
        "camera_start_preview",
        "visual_init",
        "visual_show_go_grating",
        "visual_close",
        "camera_stop_preview",
        "camera_close",
    ]
    error_summary = getattr(exc_info.value, "summary", {})
    assert error_summary["failure_stage"] == "visual_playback"
    assert error_summary["preview_drm_diagnostics"]["reserved_plane_id"] == 33


def test_combined_smoke_reports_visual_diagnostics_when_visual_init_fails(tmp_path: Path) -> None:
    """Visual init failures should still surface visual-side DRM diagnostics.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """

    class FakeCameraRuntime:
        def __init__(self, camera_id: str, session_info: dict[str, object]) -> None:
            self.preview_active = False

        def prepare(self) -> None:
            return None

        def start_preview(self) -> None:
            self.preview_active = True

        def stop_preview(self) -> None:
            self.preview_active = False

        def state_dict(self) -> dict[str, object]:
            return {
                "preview_active": self.preview_active,
                "drm_diagnostics": {
                    "backend": "drm_preview",
                    "requested_connector": "HDMI-A-1",
                },
            }

        def close(self) -> None:
            return None

    class VisualInitFailure(RuntimeError):
        def __init__(self) -> None:
            super().__init__("visual init failed")
            self.diagnostics = {
                "backend": "drm_visual",
                "requested_connector": "HDMI-A-2",
                "reserved_crtc_id": 52,
                "last_commit_stage": "modeset",
                "last_commit_error": "atomic mode set failed with -13",
            }

    class FailingVisualFactory:
        def __init__(self, session_info: dict[str, object]) -> None:
            raise VisualInitFailure()

    with pytest.raises(RuntimeError, match="visual init failed") as exc_info:
        run_camera_preview_and_visual_smoke(
            output_root=tmp_path,
            overlap_s=0.0,
            require_mode=lambda: HeadlessModeStatus(
                ok=True,
                lightdm_state="inactive",
                display=None,
                wayland_display=None,
                tty="/dev/tty1",
                reasons=(),
            ),
            camera_runtime_factory=FakeCameraRuntime,
            visual_factory=FailingVisualFactory,
            sleep_fn=lambda _seconds: None,
            repo_root=tmp_path,
            env={},
            home_dir=tmp_path,
        )

    error_summary = getattr(exc_info.value, "summary", {})
    assert error_summary["failure_stage"] == "visual_init"
    assert error_summary["visual_drm_diagnostics"]["requested_connector"] == "HDMI-A-2"
    assert error_summary["visual_drm_diagnostics"]["reserved_crtc_id"] == 52


def test_combined_smoke_accepts_explicit_repo_root_outside_repo_tree(tmp_path: Path) -> None:
    """Copied scripts should work when an explicit repo root is provided.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """

    repo_root = tmp_path / "repo"
    (repo_root / "box_runtime").mkdir(parents=True)

    class FakeCameraRuntime:
        def __init__(self, camera_id: str, session_info: dict[str, object]) -> None:
            self.preview_active = False

        def prepare(self) -> None:
            return None

        def start_preview(self) -> None:
            self.preview_active = True

        def stop_preview(self) -> None:
            self.preview_active = False

        def state_dict(self) -> dict[str, object]:
            return {
                "preview_active": self.preview_active,
                "drm_diagnostics": {
                    "backend": "drm_preview",
                    "requested_connector": "HDMI-A-1",
                },
            }

        def close(self) -> None:
            return None

    class FakeVisualStim:
        def __init__(self, session_info: dict[str, object]) -> None:
            self._metrics = {
                "play_count": 0,
                "current_label": "gray",
                "timing_log": [],
                "drm_diagnostics": {
                    "backend": "drm_visual",
                    "requested_connector": "HDMI-A-2",
                },
            }

        def show_grating(self, grating_name: str) -> None:
            self._metrics["play_count"] += 1
            self._metrics["current_label"] = grating_name
            self._metrics["timing_log"].append({"label": grating_name})

        def close(self) -> None:
            return None

    summary = run_camera_preview_and_visual_smoke(
        output_root=tmp_path / "output",
        overlap_s=0.0,
        require_mode=lambda: HeadlessModeStatus(
            ok=True,
            lightdm_state="inactive",
            display=None,
            wayland_display=None,
            tty="/dev/tty1",
            reasons=(),
        ),
        camera_runtime_factory=FakeCameraRuntime,
        visual_factory=FakeVisualStim,
        sleep_fn=lambda _seconds: None,
        repo_root=repo_root,
        env={},
        home_dir=tmp_path / "home",
    )

    assert summary["status"] == "ok"
    assert summary["preview_drm_diagnostics"]["requested_connector"] == "HDMI-A-1"
    assert summary["visual_drm_diagnostics"]["requested_connector"] == "HDMI-A-2"
