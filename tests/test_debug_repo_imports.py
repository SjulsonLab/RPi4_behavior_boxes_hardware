from __future__ import annotations

from pathlib import Path

import pytest

from debug.repo_imports import resolve_repo_root


def _make_repo_root(path: Path) -> Path:
    """Create the minimal directory shape required for repo-root detection.

    Args:
        path: Candidate repository root directory.

    Returns:
        Path: The created repository root.
    """

    (path / "box_runtime").mkdir(parents=True)
    return path


def test_resolve_repo_root_prefers_explicit_argument(tmp_path: Path) -> None:
    """Explicit repo-root arguments should take precedence over all fallbacks.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """

    explicit_root = _make_repo_root(tmp_path / "explicit_repo")
    env_root = _make_repo_root(tmp_path / "env_repo")

    resolved = resolve_repo_root(
        repo_root_arg=explicit_root,
        env={"BEHAVBOX_REPO_ROOT": str(env_root)},
        script_path=tmp_path / "standalone" / "camera_preview_hdmi_a1_smoke.py",
        home_dir=tmp_path / "home",
    )

    assert resolved == explicit_root.resolve()


def test_resolve_repo_root_uses_environment_when_argument_missing(tmp_path: Path) -> None:
    """Environment repo-root settings should be honored when no argument is provided.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """

    env_root = _make_repo_root(tmp_path / "env_repo")

    resolved = resolve_repo_root(
        repo_root_arg=None,
        env={"BEHAVBOX_REPO_ROOT": str(env_root)},
        script_path=tmp_path / "standalone" / "camera_preview_hdmi_a1_smoke.py",
        home_dir=tmp_path / "home",
    )

    assert resolved == env_root.resolve()


def test_resolve_repo_root_uses_repo_parent_when_script_lives_under_debug(tmp_path: Path) -> None:
    """Scripts running from the repo debug directory should resolve their parent repo root.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """

    repo_root = _make_repo_root(tmp_path / "repo")
    script_path = repo_root / "debug" / "camera_preview_hdmi_a1_smoke.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("# placeholder\n", encoding="utf-8")

    resolved = resolve_repo_root(
        repo_root_arg=None,
        env={},
        script_path=script_path,
        home_dir=tmp_path / "home",
    )

    assert resolved == repo_root.resolve()


def test_resolve_repo_root_falls_back_to_home_checkout(tmp_path: Path) -> None:
    """Copied scripts should fall back to the standard home-directory checkout.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """

    home_dir = tmp_path / "home"
    fallback_root = _make_repo_root(home_dir / "RPi4_behavior_boxes_hardware")

    resolved = resolve_repo_root(
        repo_root_arg=None,
        env={},
        script_path=tmp_path / "debugging_commands" / "camera_preview_hdmi_a1_smoke.py",
        home_dir=home_dir,
    )

    assert resolved == fallback_root.resolve()


def test_resolve_repo_root_raises_clear_error_when_no_candidate_is_valid(tmp_path: Path) -> None:
    """Repo-root detection should explain what paths it checked on failure.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """

    with pytest.raises(ValueError, match="Unable to locate BehavBox repository root"):
        resolve_repo_root(
            repo_root_arg=None,
            env={},
            script_path=tmp_path / "debugging_commands" / "camera_preview_hdmi_a1_smoke.py",
            home_dir=tmp_path / "home",
        )
