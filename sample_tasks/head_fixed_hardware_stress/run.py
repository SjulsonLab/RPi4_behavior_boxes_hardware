"""CLI entrypoint for the head-fixed hardware stress task."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.mock_hw.server import ensure_server_running
from sample_tasks.common.runner import TaskRunner
from sample_tasks.head_fixed_gonogo.output_root import resolve_output_root
from sample_tasks.head_fixed_hardware_stress.session_config import build_session_info
from sample_tasks.head_fixed_hardware_stress import task as stress_task


def _build_task_config(args: argparse.Namespace) -> dict[str, object]:
    """Build one task-config mapping from parsed CLI arguments.

    Data contracts:
    - ``args``: ``argparse.Namespace`` from ``main``.
    - Returns ``dict[str, object]`` accepted by ``prepare_task``.
    """

    return {
        "max_trials": int(args.max_trials),
        "max_duration_s": float(args.max_duration_s),
        "reward_every_n_trials": int(args.reward_every_n_trials),
        "reward_outputs": list(args.reward_output_order),
        "reward_size_ul": float(args.reward_size_ul),
    }


def main() -> int:
    """Run the hardware stress task until completion or interruption.

    Data contracts:
    - Returns integer process exit code, ``0`` on clean completion.
    """

    parser = argparse.ArgumentParser(description="Run the head-fixed hardware stress task.")
    parser.add_argument(
        "--output-root",
        default=None,
        help="Directory root for task outputs. When omitted on the Pi, prefer /mnt/behavbox_ssd/head_fixed_gonogo_runs if mounted.",
    )
    parser.add_argument("--session-tag", default="head_fixed_hardware_stress_session", help="Basename for the session directory.")
    parser.add_argument("--max-trials", type=int, default=20, help="Maximum number of completed trials before stopping.")
    parser.add_argument("--max-duration-s", type=float, default=600.0, help="Maximum session duration in seconds.")
    parser.add_argument("--reward-every-n-trials", type=int, default=5, help="Deliver serial rewards every N completed trials.")
    parser.add_argument(
        "--reward-output-order",
        nargs="+",
        default=["reward_left", "reward_right", "reward_center", "reward_4"],
        help="Ordered reward outputs to actuate serially on reward trials.",
    )
    parser.add_argument("--reward-size-ul", type=float, default=50.0, help="Reward size in microliters for each solenoid pulse.")
    args = parser.parse_args()

    os.environ.setdefault("BEHAVBOX_FORCE_MOCK", "1")
    os.environ.setdefault("BEHAVBOX_MOCK_UI_AUTOSTART", "1")

    output_root = resolve_output_root(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    session_info = build_session_info(output_root, args.session_tag)
    mock_url = ensure_server_running()
    print(f"Mock hardware UI: {mock_url}")
    print("This task ignores lick input and only uses cue, visual stimulus, treadmill, and scheduled rewards.")

    runner = TaskRunner(
        box=BehavBox(session_info),
        task=stress_task,
        task_config=_build_task_config(args),
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

