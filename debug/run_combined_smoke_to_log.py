"""Run the combined camera/visual smoke and mirror output into a timestamped log."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


def build_log_path(log_dir: Path, now: datetime) -> Path:
    """Build the log-file path for one combined-smoke console run.

    Args:
        log_dir: Directory path where log files are stored.
        now: Wall-clock timestamp used to generate the log filename.

    Returns:
        Path: Timestamped log-file path of the form
        ``combined_smoke_YYYYMMDD_HHMMSS.log`` inside ``log_dir``.
    """

    timestamp = now.strftime("%Y%m%d_%H%M%S")
    return log_dir / f"combined_smoke_{timestamp}.log"


def build_combined_smoke_command(script_path: Path, overlap_s: float) -> list[str]:
    """Build the subprocess argv for the combined headless smoke.

    Args:
        script_path: Absolute or relative path to the combined smoke script.
        overlap_s: Overlap duration in seconds between preview and stimulus.

    Returns:
        list[str]: Subprocess argv for ``uv run python <script> --overlap-s <value>``.
    """

    return [
        "uv",
        "run",
        "python",
        str(script_path),
        "--overlap-s",
        str(float(overlap_s)),
    ]


def stream_command_to_log(
    command: Sequence[str],
    *,
    log_path: Path,
    env: dict[str, str],
    cwd: Path | None = None,
) -> int:
    """Run one command, echo stdout/stderr, and save the same stream to a log.

    Args:
        command: Subprocess argv sequence.
        log_path: Output log-file path.
        env: Environment mapping passed to the child process.
        cwd: Optional working-directory path for the child process.

    Returns:
        int: Child-process return code.
    """

    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(cwd) if cwd is not None else None,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log_file.write(line)
        return process.wait()


def main(argv: list[str] | None = None) -> int:
    """Run the combined smoke and save a timestamped console log.

    Args:
        argv: Optional command-line arguments excluding the executable name.

    Returns:
        int: Child-process exit code from the combined smoke script.
    """

    parser = argparse.ArgumentParser(
        description="Run the combined camera/visual smoke and save a console log."
    )
    parser.add_argument("--overlap-s", type=float, default=1.0)
    parser.add_argument(
        "--script-path",
        type=Path,
        default=Path.home() / "debugging_commands" / "camera_preview_and_visual_smoke.py",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path.home() / "debugging_commands" / "logs",
    )
    args = parser.parse_args(argv)

    log_dir = args.log_dir.expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = build_log_path(log_dir, datetime.now())

    child_env = os.environ.copy()
    child_env.pop("DISPLAY", None)
    child_env.pop("WAYLAND_DISPLAY", None)
    child_env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{child_env.get('PATH', '')}"
    child_env["UV_CACHE_DIR"] = "/tmp/uv-cache"

    command = build_combined_smoke_command(
        script_path=args.script_path.expanduser(),
        overlap_s=args.overlap_s,
    )

    print(f"Saving combined smoke log to: {log_path}")
    print("Running command:", " ".join(command))
    return_code = stream_command_to_log(command, log_path=log_path, env=child_env)
    print(f"Combined smoke exit code: {return_code}")
    print(f"Saved log: {log_path}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
