"""Helpers for locating the BehavBox repo before importing project modules.

Data contracts:
    - ``repo_root_arg``: Optional ``pathlib.Path`` or path-like string pointing
      at the repository root directory. The directory must contain
      ``box_runtime``.
    - ``env``: Mapping from environment-variable names to strings. When
      omitted, ``os.environ`` is used.
    - ``script_path``: Optional path to the currently executing script file.
      Used to detect repo-local ``debug`` scripts.
    - ``home_dir``: Optional home-directory path used for the
      ``~/RPi4_behavior_boxes_hardware`` fallback.
    - Return values are absolute ``pathlib.Path`` objects pointing at the repo
      root. ``prepare_repo_imports`` mutates ``sys.path`` in-place and returns
      the resolved root path.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Mapping


def _candidate_is_repo_root(path: Path) -> bool:
    """Return whether a directory looks like the BehavBox repository root.

    Args:
        path: Candidate directory path.

    Returns:
        bool: ``True`` when the candidate contains ``box_runtime``.
    """

    normalized_path = Path(path).expanduser().resolve()
    return normalized_path.is_dir() and (normalized_path / "box_runtime").is_dir()


def resolve_repo_root(
    *,
    repo_root_arg: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    script_path: Path | str | None = None,
    home_dir: Path | str | None = None,
) -> Path:
    """Resolve the BehavBox repository root for standalone debug scripts.

    Args:
        repo_root_arg: Optional explicit repo-root argument from the CLI.
        env: Optional environment-variable mapping. ``BEHAVBOX_REPO_ROOT`` is
            consulted when no explicit argument is provided.
        script_path: Optional path to the currently executing script.
        home_dir: Optional home-directory path for
            ``~/RPi4_behavior_boxes_hardware`` fallback.

    Returns:
        Path: Absolute repository-root path containing ``box_runtime``.

    Raises:
        ValueError: If no valid repo root can be located.
    """

    effective_env = os.environ if env is None else env
    checked_candidates: list[str] = []

    def _check_candidate(candidate: Path | str | None) -> Path | None:
        if candidate is None:
            return None
        candidate_path = Path(candidate).expanduser().resolve()
        checked_candidates.append(str(candidate_path))
        if _candidate_is_repo_root(candidate_path):
            return candidate_path
        return None

    explicit_match = _check_candidate(repo_root_arg)
    if explicit_match is not None:
        return explicit_match

    env_match = _check_candidate(effective_env.get("BEHAVBOX_REPO_ROOT"))
    if env_match is not None:
        return env_match

    if script_path is not None:
        script_file = Path(script_path).expanduser().resolve()
        for parent in script_file.parents:
            if parent.name == "debug":
                repo_parent = _check_candidate(parent.parent)
                if repo_parent is not None:
                    return repo_parent
                break

    fallback_home = Path.home() if home_dir is None else Path(home_dir).expanduser().resolve()
    fallback_match = _check_candidate(fallback_home / "RPi4_behavior_boxes_hardware")
    if fallback_match is not None:
        return fallback_match

    checked_text = ", ".join(checked_candidates) if checked_candidates else "<none>"
    raise ValueError(
        "Unable to locate BehavBox repository root. "
        "Checked: "
        f"{checked_text}. "
        "Pass --repo-root, set BEHAVBOX_REPO_ROOT, or place the script under the repo debug directory."
    )


def prepare_repo_imports(
    *,
    repo_root_arg: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    script_path: Path | str | None = None,
    home_dir: Path | str | None = None,
) -> Path:
    """Resolve the repo root and prepend it to ``sys.path`` if needed.

    Args:
        repo_root_arg: Optional explicit repo-root argument from the CLI.
        env: Optional environment-variable mapping.
        script_path: Optional path to the currently executing script.
        home_dir: Optional home-directory path for fallback detection.

    Returns:
        Path: Absolute repository-root path inserted into ``sys.path``.
    """

    repo_root = resolve_repo_root(
        repo_root_arg=repo_root_arg,
        env=env,
        script_path=script_path,
        home_dir=home_dir,
    )
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)
    return repo_root
