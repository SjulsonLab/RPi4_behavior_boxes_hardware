from __future__ import annotations

from pathlib import Path

import pytest

from debug.camera_preview_hdmi_a1_smoke import (
    build_camera_preview_session_info,
    run_camera_preview_hdmi_a1_smoke,
)
from debug.display_mode_guard import HeadlessModeStatus


def test_build_camera_preview_session_info_targets_hdmi_a1(tmp_path: Path) -> None:
    """Camera smoke config should target the supported preview connector."""

    session_info = build_camera_preview_session_info(output_root=tmp_path, camera_id="camera0")

    assert session_info["camera_enabled"] is True
    assert session_info["camera_ids"] == ["camera0"]
    assert session_info["camera_preview_modes"] == {"camera0": "drm_local"}
    assert session_info["camera_preview_connector"] == "HDMI-A-1"
    assert session_info["camera_recording_enabled"] is False
    assert session_info["visual_stimulus"] is False


def test_camera_preview_smoke_aborts_before_runtime_when_mode_guard_fails(tmp_path: Path) -> None:
    """The camera smoke should not touch the runtime in the wrong mode."""

    class ShouldNotConstruct:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("runtime should not be constructed")

    def fail_guard() -> HeadlessModeStatus:
        raise RuntimeError("wrong mode")

    with pytest.raises(RuntimeError, match="wrong mode"):
        run_camera_preview_hdmi_a1_smoke(
            output_root=tmp_path,
            require_mode=fail_guard,
            runtime_factory=ShouldNotConstruct,
        )


def test_camera_preview_smoke_runs_preview_lifecycle_in_order(tmp_path: Path) -> None:
    """The camera smoke should prepare, preview, stop, and close in order."""

    calls: list[str] = []

    class FakeRuntime:
        def __init__(self, camera_id: str, session_info: dict[str, object]) -> None:
            self.camera_id = camera_id
            self.session_info = session_info
            self.is_preview_active = False

        def prepare(self) -> None:
            calls.append("prepare")

        def start_preview(self) -> None:
            calls.append("start_preview")
            self.is_preview_active = True

        def stop_preview(self) -> None:
            calls.append("stop_preview")
            self.is_preview_active = False

        def state_dict(self) -> dict[str, object]:
            return {
                "camera_id": self.camera_id,
                "preview_mode": self.session_info["camera_preview_modes"],
                "preview_connector": self.session_info["camera_preview_connector"],
                "preview_active": self.is_preview_active,
                "drm_diagnostics": {
                    "requested_connector": "HDMI-A-1",
                    "reserved_crtc_id": 92,
                    "reserved_plane_id": 81,
                    "last_request": {
                        "allow_modeset": True,
                        "framebuffer_id": 44,
                    },
                },
                "preview_last_error_phase": None,
                "preview_last_error_message": None,
            }

        def close(self) -> None:
            calls.append("close")

    summary = run_camera_preview_hdmi_a1_smoke(
        output_root=tmp_path,
        duration_s=0.0,
        require_mode=lambda: HeadlessModeStatus(
            ok=True,
            lightdm_state="inactive",
            display=None,
            wayland_display=None,
            tty="/dev/tty1",
            reasons=(),
        ),
        runtime_factory=FakeRuntime,
        sleep_fn=lambda _seconds: None,
    )

    assert calls == ["prepare", "start_preview", "stop_preview", "close"]
    assert summary["camera_id"] == "camera0"
    assert summary["preview_connector"] == "HDMI-A-1"
    assert summary["preview_drm_diagnostics"]["requested_connector"] == "HDMI-A-1"
    assert summary["preview_drm_diagnostics"]["last_request"]["framebuffer_id"] == 44
    assert summary["preview_last_error_phase"] is None
