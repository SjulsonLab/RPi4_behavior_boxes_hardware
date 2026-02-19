#!/usr/bin/env python3
"""Launch BehavBox in cross-platform mock mode and expose the mock web UI."""

import os
import sys
import time
import tempfile
from pathlib import Path


def _build_session_info(run_dir: Path) -> dict:
    timestamp = time.strftime("%Y-%m-%d_%H%M%S")
    return {
        "external_storage": str(run_dir),
        "basename": f"mock_session_{timestamp}",
        "dir_name": str(run_dir / "session_output"),
        "mouse_name": "mock_mouse",
        "datetime": timestamp,
        "box_name": "mock_headfixed_box",
        "reward_size": 50,
        "key_reward_amount": 50,
        "calibration_coefficient": {
            "1": [0.0, 0.01],
            "2": [0.0, 0.01],
            "3": [0.0, 0.01],
            "4": [0.0, 0.01],
        },
        "air_duration": 0.02,
        "vacuum_duration": 0.02,
        "visual_stimulus": True,
        "vis_gratings": [],
        "gray_level": 0.5,
        "treadmill": False,
    }


def main() -> int:
    # Force cross-platform mock mode for the launcher.
    os.environ.setdefault("BEHAVBOX_FORCE_MOCK", "1")
    os.environ.setdefault("BEHAVBOX_MOCK_UI_AUTOSTART", "1")

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from essential.mock_hw.server import ensure_server_running
    from essential.behavbox import BehavBox

    run_dir = Path(tempfile.mkdtemp(prefix="behavbox_mock_"))
    session_info = _build_session_info(run_dir)

    ui_url = ensure_server_running()
    print(f"Mock hardware UI: {ui_url}")
    print(f"Session output dir: {session_info['dir_name']}")

    box = BehavBox(session_info)
    print("BehavBox mock started. Press Ctrl-C to stop.")

    try:
        while True:
            # If pygame is available, keep keyboard simulation responsive.
            try:
                box.check_keybd()
            except Exception:
                pass
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("Stopping mock BehavBox...")
        try:
            box.flipper.close()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
