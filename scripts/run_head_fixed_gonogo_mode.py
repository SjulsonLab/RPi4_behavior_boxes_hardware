"""Run head-fixed go/no-go with an explicit desktop/experiment display mode."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sample_tasks.head_fixed_gonogo.display_mode import (
    apply_display_mode_overrides,
    build_lightdm_action_plan,
)


def _run_lightdm_action(action: str, *, dry_run: bool) -> None:
    """Run one ``systemctl`` action on ``lightdm`` with optional dry-run mode.

    Args:
        action: Service action string, expected ``"start"`` or ``"stop"``.
        dry_run: When ``True``, only print the command.
    """

    if action not in {"start", "stop"}:
        raise ValueError(f"Unsupported lightdm action {action!r}")
    command = ["sudo", "systemctl", action, "lightdm"]
    print(" ".join(command))
    if not dry_run:
        subprocess.run(command, check=True)


def _build_task_config(args: argparse.Namespace) -> dict[str, Any]:
    """Build one task-config mapping from parsed CLI arguments."""

    return {
        "max_trials": int(args.max_trials),
        "max_duration_s": float(args.max_duration_s),
        "fake_mouse_enabled": bool(args.fake_mouse),
        "fake_mouse_seed": int(args.fake_mouse_seed),
    }


def main() -> int:
    """Run one mode-aware head-fixed go/no-go session.

    Returns:
        int: Process exit code, zero on clean completion.
    """

    parser = argparse.ArgumentParser(description="Run head-fixed go/no-go in desktop or experiment mode.")
    parser.add_argument("--display-mode", choices=["desktop", "experiment"], default="desktop")
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands without running a session.")
    parser.add_argument("--output-root", default="tmp_task_runs", help="Directory root for task outputs.")
    parser.add_argument("--session-tag", default="head_fixed_gonogo_session", help="Basename for the session directory.")
    parser.add_argument("--max-trials", type=int, default=20, help="Maximum number of completed trials before stopping.")
    parser.add_argument("--max-duration-s", type=float, default=600.0, help="Maximum session duration in seconds.")
    parser.add_argument("--fake-mouse", action="store_true", help="Enable the seeded fake mouse.")
    parser.add_argument("--fake-mouse-seed", type=int, default=0, help="Seed for fake-mouse behavior.")
    args = parser.parse_args()

    lightdm_plan = build_lightdm_action_plan(args.display_mode)
    for action in lightdm_plan["before"]:
        _run_lightdm_action(action, dry_run=bool(args.dry_run))

    if args.dry_run:
        for action in lightdm_plan["after"]:
            print(f"sudo systemctl {action} lightdm  # planned restore")
        print("Dry run complete; no session executed.")
        return 0

    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    from sample_tasks.head_fixed_gonogo.session_config import build_session_info

    session_info = build_session_info(output_root, args.session_tag)
    session_info = apply_display_mode_overrides(session_info, mode=args.display_mode)
    from box_runtime.behavior.behavbox import BehavBox
    from box_runtime.mock_hw.server import ensure_server_running
    from sample_tasks.common.runner import TaskRunner
    from sample_tasks.head_fixed_gonogo.fake_mouse import build_fake_mouse_step_hook
    from sample_tasks.head_fixed_gonogo.plot_state import build_plot_step_hook
    from sample_tasks.head_fixed_gonogo import task as gonogo_task

    os.environ.setdefault("BEHAVBOX_MOCK_UI_AUTOSTART", "1")

    mock_url = ensure_server_running()
    print(f"Mock hardware UI: {mock_url}")
    print("Use the generic mock UI and pulse lick_3 to send the center response.")
    print(f"Display mode: {args.display_mode}; preview modes: {session_info.get('camera_preview_modes')}")

    step_hooks = [build_plot_step_hook(history_limit=64)]
    if args.fake_mouse:
        step_hooks.insert(0, build_fake_mouse_step_hook(seed=int(args.fake_mouse_seed)))

    runner = TaskRunner(
        box=BehavBox(session_info),
        task=gonogo_task,
        task_config=_build_task_config(args),
        step_hooks=step_hooks,
    )

    try:
        try:
            final_state = runner.run()
        except KeyboardInterrupt:
            runner.stop(reason="keyboard_interrupt")
            final_state = runner.finalize()
        print(f"Final task state written to: {Path(session_info['dir_name']) / 'final_task_state.json'}")
        print(final_state)
        return 0
    finally:
        for action in lightdm_plan["after"]:
            try:
                _run_lightdm_action(action, dry_run=False)
            except Exception as exc:  # pragma: no cover - best-effort restore path
                print(f"Warning: failed to restore lightdm via '{action}': {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
