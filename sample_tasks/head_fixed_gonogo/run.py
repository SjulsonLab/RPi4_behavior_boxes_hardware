"""CLI entrypoint for the reference head-fixed go/no-go task."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]


def ensure_repo_root_on_sys_path(
    *,
    script_path: Path | str | None = None,
    path_list: list[str] | None = None,
) -> Path:
    """Ensure the repository root is importable for direct script execution.

    Args:
        script_path: Optional path to this script. When omitted, uses
            ``__file__`` for the current module.
        path_list: Optional mutable ``sys.path``-style list to update in place.

    Returns:
        Path: Absolute repository-root path that was ensured on ``path_list``.
    """

    resolved_script = Path(script_path if script_path is not None else __file__).resolve()
    repo_root = resolved_script.parents[2]
    sys_path = sys.path if path_list is None else path_list
    repo_root_text = str(repo_root)
    if repo_root_text not in sys_path:
        sys_path.insert(0, repo_root_text)
    return repo_root


ensure_repo_root_on_sys_path(script_path=__file__)

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.mock_hw.server import ensure_server_running
from sample_tasks.common.runner import TaskRunner
from sample_tasks.head_fixed_gonogo.fake_mouse import build_fake_mouse_step_hook
from sample_tasks.head_fixed_gonogo.output_root import (
    detect_raspberry_pi_host,
    resolve_output_root,
)
from sample_tasks.head_fixed_gonogo.plot_state import build_plot_step_hook
from sample_tasks.head_fixed_gonogo.session_config import build_session_info
from sample_tasks.head_fixed_gonogo import task as gonogo_task


def resolve_plain_entrypoint_output_root(
    cli_output_root: str | os.PathLike[str] | None,
    *,
    is_raspberry_pi_host_fn=detect_raspberry_pi_host,
    ssd_mount_path: Path = Path("/mnt/behavbox_ssd"),
    ssd_is_ready_fn=None,
) -> Path:
    """Resolve the plain entrypoint output root while preserving mock runtime.

    Args:
        cli_output_root: Explicit CLI output-root path or ``None``.
        is_raspberry_pi_host_fn: Zero-argument callable answering whether the
            underlying host is a Raspberry Pi, independent of mock mode.
        ssd_mount_path: Expected SSD mountpoint path.
        ssd_is_ready_fn: Optional predicate indicating whether the SSD mount is
            ready to use.

    Returns:
        Path: Absolute output-root path for the session directory tree.
    """

    return resolve_output_root(
        cli_output_root,
        is_raspberry_pi_fn=is_raspberry_pi_host_fn,
        ssd_mount_path=ssd_mount_path,
        ssd_is_ready_fn=ssd_is_ready_fn,
    )


def main() -> int:
    """Run the sample task until completion or manual interruption.

    Returns:
    - ``exit_code``: zero on clean completion.
    """

    parser = argparse.ArgumentParser(description="Run the reference head-fixed go/no-go task.")
    parser.add_argument(
        "--output-root",
        default=None,
        help="Directory root for task outputs. When omitted on the Pi, prefer /mnt/behavbox_ssd/head_fixed_gonogo_runs if mounted.",
    )
    parser.add_argument("--session-tag", default="head_fixed_gonogo_session", help="Basename for the session directory.")
    parser.add_argument("--max-trials", type=int, default=20, help="Maximum number of completed trials before stopping.")
    parser.add_argument("--max-duration-s", type=float, default=600.0, help="Maximum session duration in seconds.")
    parser.add_argument("--fake-mouse", action="store_true", help="Enable the seeded fake mouse.")
    parser.add_argument("--fake-mouse-seed", type=int, default=0, help="Seed for fake-mouse behavior.")
    args = parser.parse_args()

    os.environ.setdefault("BEHAVBOX_FORCE_MOCK", "1")
    os.environ.setdefault("BEHAVBOX_MOCK_UI_AUTOSTART", "1")

    output_root = resolve_plain_entrypoint_output_root(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    session_info = build_session_info(output_root, args.session_tag)
    mock_url = ensure_server_running()
    print(f"Mock hardware UI: {mock_url}")
    print("Use the generic mock UI and pulse lick_3 to send the center response.")

    step_hooks = [build_plot_step_hook(history_limit=64)]
    if args.fake_mouse:
        step_hooks.insert(0, build_fake_mouse_step_hook(seed=int(args.fake_mouse_seed)))

    runner = TaskRunner(
        box=BehavBox(session_info),
        task=gonogo_task,
        task_config={
            "max_trials": int(args.max_trials),
            "max_duration_s": float(args.max_duration_s),
            "fake_mouse_enabled": bool(args.fake_mouse),
            "fake_mouse_seed": int(args.fake_mouse_seed),
        },
        step_hooks=step_hooks,
    )

    try:
        final_state = runner.run()
    except KeyboardInterrupt:
        runner.stop(reason="keyboard_interrupt")
        final_state = runner.finalize()
    print(f"Final task state written to: {Path(session_info['dir_name']) / 'final_task_state.json'}")
    print(final_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
