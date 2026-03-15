"""Run the minimal tutorial experiment locally in mock mode."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.mock_hw.server import ensure_server_running
from sample_tasks.common.runner import TaskRunner
from sample_tasks.minimal_experiment.session_config import build_mock_session_info
from sample_tasks.minimal_experiment import task as minimal_task


def configure_mock_environment() -> None:
    """Configure environment variables for local mock-mode tutorial runs."""

    os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
    os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "1"


def main() -> int:
    """Run the minimal experiment locally with the browser mock UI.

    Returns:
    - ``exit_code``: zero on clean completion
    """

    parser = argparse.ArgumentParser(description="Run the minimal experiment locally in mock mode.")
    parser.add_argument("--output-root", default="tmp_task_runs", help="Directory root for task outputs.")
    parser.add_argument("--session-tag", default="minimal_experiment_session", help="Basename for the session directory.")
    parser.add_argument("--max-duration-s", type=float, default=30.0, help="Maximum session duration in seconds.")
    parser.add_argument("--reward-on-response", action="store_true", help="Deliver reward when the response event is detected.")
    args = parser.parse_args()

    configure_mock_environment()

    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    session_info = build_mock_session_info(output_root, args.session_tag)
    mock_url = ensure_server_running()
    print(f"Mock hardware UI: {mock_url}")
    print("This is the local mock workflow. Open the browser UI and pulse lick_3 to respond.")

    runner = TaskRunner(
        box=BehavBox(session_info),
        task=minimal_task,
        task_config={
            "max_duration_s": float(args.max_duration_s),
            "reward_on_response": bool(args.reward_on_response),
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

