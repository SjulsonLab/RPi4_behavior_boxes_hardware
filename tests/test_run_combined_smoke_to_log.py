from __future__ import annotations

from datetime import datetime
from pathlib import Path

from debug.run_combined_smoke_to_log import (
    build_combined_smoke_command,
    build_log_path,
)


def test_build_log_path_uses_timestamped_log_filename(tmp_path: Path) -> None:
    """The console wrapper should create timestamped combined-smoke log files.

    Args:
        tmp_path: Pytest-provided temporary directory path.

    Returns:
        None: Assertions validate the generated log-path shape.
    """

    now = datetime(2026, 4, 13, 20, 5, 6)

    log_path = build_log_path(tmp_path, now)

    assert log_path.parent == tmp_path
    assert log_path.name == "combined_smoke_20260413_200506.log"


def test_build_combined_smoke_command_targets_debugging_commands_script() -> None:
    """The wrapper should invoke the combined smoke script with overlap seconds.

    Returns:
        None: Assertions validate the subprocess argument vector.
    """

    script_path = Path("/home/pi/debugging_commands/camera_preview_and_visual_smoke.py")

    command = build_combined_smoke_command(script_path=script_path, overlap_s=1.5)

    assert command == [
        "uv",
        "run",
        "python",
        str(script_path),
        "--overlap-s",
        "1.5",
    ]
