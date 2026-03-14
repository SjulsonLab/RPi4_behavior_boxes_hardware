"""CLI entrypoint for the reference head-fixed go/no-go task."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.mock_hw.server import ensure_server_running
from sample_tasks.common.runner import TaskRunner
from sample_tasks.head_fixed_gonogo import task as gonogo_task


def _build_session_info(output_root: Path, session_tag: str) -> dict:
    """Build one runnable session configuration for the sample task.

    Args:
    - ``output_root``: Directory under which the session directory is created.
    - ``session_tag``: Human-readable session basename.

    Returns:
    - ``session_info``: mapping consumed by ``BehavBox``.
    """

    timestamp = time.strftime("%Y-%m-%d_%H%M%S")
    session_dir = output_root / session_tag
    return {
        "external_storage": str(output_root),
        "basename": session_tag,
        "dir_name": str(session_dir),
        "mouse_name": "mock_mouse",
        "datetime": timestamp,
        "box_name": "head_fixed_gonogo",
        "reward_size": 50,
        "key_reward_amount": 50,
        "calibration_coefficient": {
            "1": [0.0, 0.01],
            "2": [0.0, 0.01],
            "3": [0.0, 0.01],
            "4": [0.0, 0.01],
        },
        "air_duration": 0.01,
        "vacuum_duration": 0.01,
        "visual_stimulus": False,
        "treadmill": False,
        "input_profile": "head_fixed",
        "mock_audio": True,
    }


def main() -> int:
    """Run the sample task until completion or manual interruption.

    Returns:
    - ``exit_code``: zero on clean completion.
    """

    parser = argparse.ArgumentParser(description="Run the reference head-fixed go/no-go task.")
    parser.add_argument("--output-root", default="tmp_task_runs", help="Directory root for task outputs.")
    parser.add_argument("--session-tag", default="head_fixed_gonogo_session", help="Basename for the session directory.")
    parser.add_argument("--max-trials", type=int, default=20, help="Maximum number of completed trials before stopping.")
    parser.add_argument("--max-duration-s", type=float, default=600.0, help="Maximum session duration in seconds.")
    args = parser.parse_args()

    os.environ.setdefault("BEHAVBOX_FORCE_MOCK", "1")
    os.environ.setdefault("BEHAVBOX_MOCK_UI_AUTOSTART", "1")

    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    session_info = _build_session_info(output_root, args.session_tag)
    mock_url = ensure_server_running()
    print(f"Mock hardware UI: {mock_url}")
    print("Use the generic mock UI and pulse lick_3 to send the center response.")

    runner = TaskRunner(
        box=BehavBox(session_info),
        task=gonogo_task,
        task_config={
            "max_trials": int(args.max_trials),
            "max_duration_s": float(args.max_duration_s),
        },
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
