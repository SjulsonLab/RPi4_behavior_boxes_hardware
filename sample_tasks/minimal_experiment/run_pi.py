"""Run the minimal tutorial experiment on a headless Raspberry Pi over SSH."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from box_runtime.behavior.behavbox import BehavBox
from sample_tasks.common.runner import TaskRunner
from sample_tasks.minimal_experiment.session_config import build_headless_pi_session_info
from sample_tasks.minimal_experiment import task as minimal_task


def configure_headless_pi_environment() -> None:
    """Configure environment variables for a real headless Pi run.

    The real Pi runner should not force mock mode. It also should not try to
    auto-start the browser mock UI.
    """

    os.environ.pop("BEHAVBOX_FORCE_MOCK", None)
    os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"


def main() -> int:
    """Run the minimal experiment on a headless Raspberry Pi.

    Returns:
    - ``exit_code``: zero on clean completion
    """

    parser = argparse.ArgumentParser(description="Run the minimal experiment on a headless Raspberry Pi.")
    parser.add_argument("--output-root", default="~/behavbox_runs", help="Directory root for task outputs on the Pi.")
    parser.add_argument("--session-tag", default="minimal_experiment_session", help="Basename for the session directory.")
    parser.add_argument("--max-duration-s", type=float, default=30.0, help="Maximum session duration in seconds.")
    parser.add_argument("--reward-on-response", action="store_true", help="Deliver reward when the response event is detected.")
    args = parser.parse_args()

    configure_headless_pi_environment()

    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    session_info = build_headless_pi_session_info(output_root, args.session_tag)
    print("This is the headless Raspberry Pi workflow.")
    print("Run it over SSH. Responses come from the physical box inputs, not the browser mock UI.")

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
