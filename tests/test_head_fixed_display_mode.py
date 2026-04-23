"""Tests for head-fixed go/no-go display-mode orchestration helpers."""

from __future__ import annotations

import pytest

from sample_tasks.head_fixed_gonogo.display_mode import (
    apply_display_mode_overrides,
    build_lightdm_action_plan,
)


def test_apply_display_mode_overrides_sets_qt_preview_for_desktop_mode() -> None:
    """Desktop mode should force Qt local preview while keeping visuals enabled.

    Returns:
        None.
    """

    session_info = {
        "camera_ids": ["camera0"],
        "camera_preview_modes": {"camera0": "drm_local"},
        "visual_stimulus": True,
    }

    updated = apply_display_mode_overrides(session_info, mode="desktop")

    assert updated is not session_info
    assert updated["camera_preview_modes"] == {"camera0": "qt_local"}
    assert updated["camera_preview_display"] == ":0"
    assert updated["visual_display_backend"] == "xwindow"
    assert updated["visual_stimulus"] is True


def test_apply_display_mode_overrides_sets_drm_preview_for_experiment_mode() -> None:
    """Experiment mode should force DRM local preview for camera0.

    Returns:
        None.
    """

    session_info = {
        "camera_ids": ["camera0"],
        "camera_preview_modes": {"camera0": "qt_local"},
    }

    updated = apply_display_mode_overrides(session_info, mode="experiment")

    assert updated["camera_preview_modes"] == {"camera0": "drm_local"}
    assert updated["visual_display_backend"] == "drm"


def test_apply_display_mode_overrides_rejects_unknown_mode() -> None:
    """Unknown display modes should fail fast with ``ValueError``."""

    with pytest.raises(ValueError, match="Unsupported display mode"):
        apply_display_mode_overrides({"camera_ids": ["camera0"]}, mode="invalid")


def test_build_lightdm_action_plan_for_desktop_mode() -> None:
    """Desktop mode should ensure lightdm is started and not stopped.

    Returns:
        None.
    """

    plan = build_lightdm_action_plan("desktop")

    assert plan["before"] == ["start"]
    assert plan["after"] == []


def test_build_lightdm_action_plan_for_experiment_mode() -> None:
    """Experiment mode should stop lightdm before and restore after run.

    Returns:
        None.
    """

    plan = build_lightdm_action_plan("experiment")

    assert plan["before"] == ["stop"]
    assert plan["after"] == ["start"]
