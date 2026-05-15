from __future__ import annotations

import os
from pathlib import Path

from sample_tasks.head_fixed_gonogo.output_root import resolve_output_root

os.environ.setdefault("BEHAVBOX_MOCK_UI_AUTOSTART", "0")
from sample_tasks.head_fixed_gonogo.run import (
    ensure_repo_root_on_sys_path,
    resolve_plain_entrypoint_output_root,
)


def test_resolve_output_root_prefers_ssd_mount_on_pi(tmp_path: Path) -> None:
    """Pi runs should default to the SSD mount when it is available.

    Data contracts:

    - ``tmp_path`` is a pytest-managed temporary directory.
    - the resolved output root is returned as an absolute ``Path``.
    """

    ssd_mount = tmp_path / "behavbox_ssd"
    ssd_mount.mkdir()

    resolved = resolve_output_root(
        None,
        is_raspberry_pi_fn=lambda: True,
        ssd_mount_path=ssd_mount,
        ssd_is_ready_fn=lambda path: path == ssd_mount,
    )

    assert resolved == (ssd_mount / "head_fixed_gonogo_runs").resolve()


def test_resolve_output_root_falls_back_when_ssd_mount_is_unavailable(tmp_path: Path) -> None:
    """Pi runs should fall back to the local default when the SSD is unavailable.

    Data contracts:

    - ``tmp_path`` is a pytest-managed temporary directory.
    - the fallback root is returned as an absolute ``Path``.
    """

    resolved = resolve_output_root(
        None,
        is_raspberry_pi_fn=lambda: True,
        ssd_mount_path=tmp_path / "missing_ssd",
        fallback_output_root=tmp_path / "tmp_task_runs",
        ssd_is_ready_fn=lambda _path: False,
    )

    assert resolved == (tmp_path / "tmp_task_runs").resolve()


def test_resolve_output_root_preserves_explicit_cli_override(tmp_path: Path) -> None:
    """Explicit CLI output roots should override the Pi SSD default.

    Data contracts:

    - ``tmp_path`` is a pytest-managed temporary directory.
    - explicit roots are returned as absolute ``Path`` objects.
    """

    explicit_root = tmp_path / "custom_runs"
    ssd_mount = tmp_path / "behavbox_ssd"
    ssd_mount.mkdir()

    resolved = resolve_output_root(
        str(explicit_root),
        is_raspberry_pi_fn=lambda: True,
        ssd_mount_path=ssd_mount,
        ssd_is_ready_fn=lambda path: path == ssd_mount,
    )

    assert resolved == explicit_root.resolve()


def test_plain_entrypoint_bootstraps_repo_root_for_direct_script_execution(tmp_path: Path) -> None:
    """The plain entrypoint should add the repo root to ``sys.path`` when needed.

    Data contracts:

    - ``tmp_path`` is a pytest-managed temporary directory.
    - ``path_list`` is a mutable ``sys.path``-style list of strings.
    """

    repo_root = tmp_path / "repo_root"
    script_path = repo_root / "sample_tasks" / "head_fixed_gonogo" / "run.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("# placeholder", encoding="utf-8")
    path_list = ["existing_entry"]

    resolved_root = ensure_repo_root_on_sys_path(
        script_path=script_path,
        path_list=path_list,
    )

    assert resolved_root == repo_root.resolve()
    assert path_list[0] == str(repo_root.resolve())


def test_plain_entrypoint_prefers_ssd_on_pi_even_when_runtime_is_mock(tmp_path: Path) -> None:
    """The plain entrypoint should still prefer the SSD when running on a Pi host.

    Data contracts:

    - ``tmp_path`` is a pytest-managed temporary directory.
    - the resolved output root is returned as an absolute ``Path``.
    """

    ssd_mount = tmp_path / "behavbox_ssd"
    ssd_mount.mkdir()

    resolved = resolve_plain_entrypoint_output_root(
        None,
        is_raspberry_pi_host_fn=lambda: True,
        ssd_mount_path=ssd_mount,
        ssd_is_ready_fn=lambda path: path == ssd_mount,
    )

    assert resolved == (ssd_mount / "head_fixed_gonogo_runs").resolve()
