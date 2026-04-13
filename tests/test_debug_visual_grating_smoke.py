from __future__ import annotations

from pathlib import Path

import pytest

from debug.display_mode_guard import HeadlessModeStatus
from debug.visual_grating_hdmi_a2_smoke import (
    VisualGratingSmokeFailure,
    build_visual_grating_session_info,
    run_visual_grating_hdmi_a2_smoke,
)


def test_build_visual_grating_session_info_targets_drm_hdmi_a2(tmp_path: Path) -> None:
    """Grating smoke config should target the supported DRM stimulus connector."""

    session_info = build_visual_grating_session_info(output_root=tmp_path)

    assert session_info["visual_stimulus"] is True
    assert session_info["visual_display_backend"] == "drm"
    assert session_info["visual_display_connector"] == "HDMI-A-2"
    assert len(session_info["vis_gratings"]) == 2
    assert any(str(path).endswith("go_grating.yaml") for path in session_info["vis_gratings"])
    assert any(str(path).endswith("nogo_grating.yaml") for path in session_info["vis_gratings"])


def test_visual_grating_smoke_aborts_before_runtime_when_mode_guard_fails(tmp_path: Path) -> None:
    """The grating smoke should not touch the visual runtime in the wrong mode."""

    class ShouldNotConstruct:
        def __init__(self, session_info: dict[str, object]) -> None:
            raise AssertionError("visual runtime should not be constructed")

    def fail_guard() -> HeadlessModeStatus:
        raise RuntimeError("wrong mode")

    with pytest.raises(RuntimeError, match="wrong mode"):
        run_visual_grating_hdmi_a2_smoke(
            output_root=tmp_path,
            require_mode=fail_guard,
            visual_factory=ShouldNotConstruct,
        )


def test_visual_grating_smoke_shows_go_then_nogo_and_collects_metrics(tmp_path: Path) -> None:
    """The grating smoke should request the two default gratings in order."""

    calls: list[str] = []

    class FakeVisualStim:
        def __init__(self, session_info: dict[str, object]) -> None:
            self.session_info = session_info
            self._runtime = self
            self._metrics = {
                "play_count": 0,
                "current_label": "gray",
                "timing_log": [],
                "drm_diagnostics": {
                    "requested_connector": "HDMI-A-2",
                    "reserved_crtc_id": 104,
                    "reserved_plane_id": 93,
                    "last_request": {
                        "allow_modeset": False,
                        "framebuffer_id": 77,
                    },
                },
            }

        def show_grating(self, grating_name: str) -> None:
            calls.append(grating_name)
            self._metrics["play_count"] += 1
            self._metrics["current_label"] = grating_name
            self._metrics["timing_log"].append({"label": grating_name})

        def get_metrics(self) -> dict[str, object]:
            return dict(self._metrics)

        def close(self) -> None:
            calls.append("close")

    summary = run_visual_grating_hdmi_a2_smoke(
        output_root=tmp_path,
        hold_s=0.0,
        require_mode=lambda: HeadlessModeStatus(
            ok=True,
            lightdm_state="inactive",
            display=None,
            wayland_display=None,
            tty="/dev/tty1",
            reasons=(),
        ),
        visual_factory=FakeVisualStim,
        sleep_fn=lambda _seconds: None,
    )

    assert calls == ["go_grating", "nogo_grating", "close"]
    assert summary["visual_backend"] == "drm"
    assert summary["visual_connector"] == "HDMI-A-2"
    assert summary["play_count"] == 2
    assert summary["visual_drm_diagnostics"]["requested_connector"] == "HDMI-A-2"
    assert summary["visual_drm_diagnostics"]["last_request"]["framebuffer_id"] == 77


def test_visual_grating_smoke_reports_diagnostics_when_visual_init_fails(tmp_path: Path) -> None:
    """Visual-init failures should preserve DRM diagnostics in a structured summary."""

    class FakeVisualInitError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("atomic mode set failed with -13")
            self.diagnostics = {
                "requested_connector": "HDMI-A-2",
                "reserved_crtc_id": 104,
                "reserved_plane_id": 93,
                "last_request": {
                    "allow_modeset": True,
                    "framebuffer_id": 793,
                },
            }

    class FailingVisualStim:
        def __init__(self, session_info: dict[str, object]) -> None:
            raise FakeVisualInitError()

    with pytest.raises(VisualGratingSmokeFailure) as exc_info:
        run_visual_grating_hdmi_a2_smoke(
            output_root=tmp_path,
            hold_s=0.0,
            require_mode=lambda: HeadlessModeStatus(
                ok=True,
                lightdm_state="inactive",
                display=None,
                wayland_display=None,
                tty="/dev/tty1",
                reasons=(),
            ),
            visual_factory=FailingVisualStim,
            sleep_fn=lambda _seconds: None,
        )

    summary = exc_info.value.summary
    assert summary["failure_stage"] == "visual_init"
    assert summary["visual_drm_diagnostics"]["requested_connector"] == "HDMI-A-2"
    assert summary["visual_drm_diagnostics"]["last_request"]["framebuffer_id"] == 793
