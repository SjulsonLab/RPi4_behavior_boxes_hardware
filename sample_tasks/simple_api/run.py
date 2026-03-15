"""Unified launcher for user-authored simple-task files."""

from __future__ import annotations

import argparse
import os
import platform
from pathlib import Path

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.mock_hw.server import ensure_server_running
from sample_tasks.common.runner import TaskRunner
from sample_tasks.simple_api.loader import load_task_from_file
from sample_tasks.simple_api.session_config import build_session_info


def detect_actual_raspberry_pi() -> bool:
    """Detect Raspberry Pi hardware without honoring mock override variables."""

    machine = platform.machine().lower()
    if not any(token in machine for token in ("arm", "aarch")):
        return False
    try:
        model = Path("/proc/device-tree/model").read_text(encoding="utf-8").lower()
    except Exception:
        return False
    return "raspberry pi" in model


def resolve_run_mode(requested_mode: str, detector=detect_actual_raspberry_pi) -> str:
    """Resolve the requested launcher mode to ``mock`` or ``pi``."""

    clean_mode = str(requested_mode).strip().lower()
    if clean_mode not in {"auto", "mock", "pi"}:
        raise ValueError(f"unsupported mode: {requested_mode!r}")
    if clean_mode == "auto":
        return "pi" if detector() else "mock"
    return clean_mode


def configure_environment_for_mode(requested_mode: str, detector=detect_actual_raspberry_pi) -> str:
    """Apply environment overrides for the resolved run mode.

    Args:
    - ``requested_mode``: one of ``auto``, ``mock``, or ``pi``
    - ``detector``: zero-argument callable returning whether the host is a Pi

    Returns:
    - ``resolved_mode``: concrete mode string, ``mock`` or ``pi``
    """

    resolved_mode = resolve_run_mode(requested_mode, detector=detector)
    if resolved_mode == "mock":
        os.environ["BEHAVBOX_FORCE_MOCK"] = "1"
        os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "1"
        return resolved_mode
    if not detector():
        raise RuntimeError("Pi mode requires a Raspberry Pi-compatible environment.")
    os.environ.pop("BEHAVBOX_FORCE_MOCK", None)
    os.environ["BEHAVBOX_MOCK_UI_AUTOSTART"] = "0"
    return resolved_mode


def build_session_info_for_mode(task, output_root: Path, session_tag: str, mode: str) -> dict[str, object]:
    """Build session-info for the chosen launcher mode."""

    resolved_mode = resolve_run_mode(mode)
    return build_session_info(
        Path(output_root).resolve(),
        session_tag,
        protocol_name=str(getattr(task, "PROTOCOL_NAME", "simple_task")),
        box_profile=str(getattr(task, "BOX_PROFILE", "head_fixed")),
        mock_audio=(resolved_mode == "mock"),
    )


def main(argv: list[str] | None = None) -> int:
    """Run one user-authored simple-task file."""

    parser = argparse.ArgumentParser(description="Run a simple task file with auto-detected or explicit mode.")
    parser.add_argument("task_file", help="Path to a Python file defining TASK.")
    parser.add_argument("--mode", default="auto", choices=["auto", "mock", "pi"], help="Run mode selection.")
    parser.add_argument("--output-root", default=None, help="Directory root for task outputs.")
    parser.add_argument("--session-tag", default=None, help="Basename for the session directory.")
    parser.add_argument("--max-duration-s", type=float, default=None, help="Maximum session duration in seconds.")
    args = parser.parse_args(argv)

    task = load_task_from_file(args.task_file)
    resolved_mode = configure_environment_for_mode(args.mode)

    default_output_root = "tmp_task_runs" if resolved_mode == "mock" else "~/behavbox_runs"
    output_root = Path(args.output_root or default_output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    session_tag = str(args.session_tag or f"{getattr(task, 'PROTOCOL_NAME', 'simple_task')}_session")
    session_info = build_session_info_for_mode(task, output_root=output_root, session_tag=session_tag, mode=resolved_mode)

    if resolved_mode == "mock":
        mock_url = ensure_server_running()
        print(f"Local mock workflow: open {mock_url} and use the browser UI for inputs.")
    else:
        print("Headless Raspberry Pi workflow: run this over SSH and use the physical box inputs.")

    task_config: dict[str, object] = {}
    if args.max_duration_s is not None:
        task_config["max_duration_s"] = float(args.max_duration_s)

    runner = TaskRunner(
        box=BehavBox(session_info),
        task=task,
        task_config=task_config,
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
