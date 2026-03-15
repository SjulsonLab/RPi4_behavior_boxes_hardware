"""Manual GPIO output debug helper for the active OutputService."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from box_runtime.behavior.behavbox import BehavBox


def _session_info(base_dir: Path) -> dict:
    """Build a minimal session configuration for manual output testing."""

    return {
        "external_storage": str(base_dir),
        "basename": "pump_debug",
        "dir_name": str(base_dir / "pump_debug"),
        "mouse_name": "debug",
        "datetime": time.strftime("%Y-%m-%d_%H%M%S"),
        "box_name": "debug_box",
        "reward_size": 20,
        "key_reward_amount": 20,
        "calibration_coefficient": {
            "1": [0.0, 0.01],
            "2": [0.0, 0.01],
            "3": [0.0, 0.01],
            "4": [0.0, 0.01],
        },
        "air_duration": 0.05,
        "vacuum_duration": 0.05,
        "visual_stimulus": False,
        "treadmill": False,
        "box_profile": "head_fixed",
    }


def main(argv: list[str]) -> int:
    """Pulse one named output from the active profile."""

    if len(argv) < 2:
        raise SystemExit("usage: pump_debug.py <reward_left|reward_right|reward_center|reward_4|airpuff|vacuum>")
    output_name = str(argv[1])
    box = BehavBox(_session_info(Path.cwd()))
    box.prepare_session()
    try:
        if output_name.startswith("reward_"):
            box.deliver_reward(output_name, reward_size_ul=20)
        else:
            box.pulse_output(output_name)
        time.sleep(1.0)
    finally:
        box.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
