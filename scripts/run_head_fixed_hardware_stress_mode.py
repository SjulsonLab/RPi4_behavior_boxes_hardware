"""Run head-fixed hardware stress with an explicit desktop/experiment display mode."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from box_runtime.behavior.behavbox import BehavBox
from box_runtime.mock_hw.server import ensure_server_running
from sample_tasks.common.runner import TaskRunner
from sample_tasks.head_fixed_gonogo.display_mode import (
    apply_display_mode_overrides,
    build_lightdm_action_plan,
)
from sample_tasks.head_fixed_gonogo.output_root import resolve_output_root
from sample_tasks.head_fixed_hardware_stress.run import _build_task_config
from sample_tasks.head_fixed_hardware_stress.session_config import build_session_info
from sample_tasks.head_fixed_hardware_stress import task as stress_task
from scripts.run_head_fixed_gonogo_mode import _run_lightdm_action


def run_audio_preflight(
    box,
    *,
    enabled: bool,
    measure_latency: bool,
    cue_name: str = "stress_audio_preflight",
    cue_duration_s: float = 0.25,
    side: str = "both",
    gain_db: float = 0.0,
    latency_repeats: int = 3,
) -> dict[str, object]:
    """Optionally play and measure one short cue before the main task starts.

    Data contracts:
    - ``box``: prepared BehavBox runtime exposing cue registration, playback,
      and optional ``measure_sound_latency``.
    - ``enabled``: ``bool`` controlling whether any preflight playback occurs.
    - ``measure_latency``: ``bool`` requesting loopback latency measurement.
    - ``cue_name``: ``str`` unique cue identifier for the generated noise cue.
    - ``cue_duration_s``: cue duration in seconds as ``float``.
    - ``side``: playback routing side string accepted by ``BehavBox``.
    - ``gain_db``: playback gain in decibels as ``float``.
    - ``latency_repeats``: positive integer number of latency repeats.
    - Returns a JSON-serializable ``dict[str, object]`` summarizing the
      preflight outcome.
    """

    if not enabled:
        return {"enabled": False}

    box.register_noise_cue(cue_name, duration_s=float(cue_duration_s), seed=99)
    box.play_sound(cue_name, side=side, gain_db=float(gain_db))
    sound_runtime = getattr(box, "sound_runtime", None)
    if sound_runtime is not None and hasattr(sound_runtime, "wait_until_idle"):
        sound_runtime.wait_until_idle(timeout_s=5.0)

    latencies_ms = None
    latency_error = None
    if measure_latency:
        try:
            latencies_ms = list(
                box.measure_sound_latency(
                    cue_name,
                    side=side,
                    gain_db=float(gain_db),
                    repeats=max(1, int(latency_repeats)),
                )
            )
        except Exception as exc:  # pragma: no cover - exercised on hardware
            latency_error = str(exc)

    return {
        "enabled": True,
        "audio_device": box.session_info.get("audio_device"),
        "latencies_ms": latencies_ms,
        "latency_error": latency_error,
    }


def main() -> int:
    """Run one mode-aware head-fixed hardware stress session.

    Data contracts:
    - Returns integer process exit code, ``0`` on clean completion.
    """

    parser = argparse.ArgumentParser(description="Run the head-fixed hardware stress task in desktop or experiment mode.")
    parser.add_argument("--display-mode", choices=["desktop", "experiment"], default="experiment")
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands without running a session.")
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
    parser.add_argument("--audio-preflight", action="store_true", help="Play one short cue before the task starts.")
    parser.add_argument(
        "--measure-audio-latency",
        action="store_true",
        help="Measure cue playback latency after the optional preflight playback.",
    )
    parser.add_argument(
        "--audio-latency-repeats",
        type=int,
        default=3,
        help="Number of latency repeats to request when --measure-audio-latency is enabled.",
    )
    args = parser.parse_args()

    lightdm_plan = build_lightdm_action_plan(args.display_mode)
    for action in lightdm_plan["before"]:
        _run_lightdm_action(action, dry_run=bool(args.dry_run))

    if args.dry_run:
        for action in lightdm_plan["after"]:
            print(f"sudo systemctl {action} lightdm  # planned restore")
        print("Dry run complete; no session executed.")
        return 0

    output_root = resolve_output_root(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    session_info = build_session_info(output_root, args.session_tag)
    session_info = apply_display_mode_overrides(session_info, mode=args.display_mode)

    os.environ.setdefault("BEHAVBOX_MOCK_UI_AUTOSTART", "1")
    mock_url = ensure_server_running()
    print(f"Mock hardware UI: {mock_url}")
    print(f"Display mode: {args.display_mode}; preview modes: {session_info.get('camera_preview_modes')}")
    print(f"Audio device: {session_info.get('audio_device')}; mock_audio={session_info.get('mock_audio')}")

    runner = TaskRunner(
        box=BehavBox(session_info),
        task=stress_task,
        task_config=_build_task_config(args),
    )

    try:
        try:
            runner.prepare()
            preflight_result = run_audio_preflight(
                runner.box,
                enabled=bool(args.audio_preflight),
                measure_latency=bool(args.measure_audio_latency),
                latency_repeats=int(args.audio_latency_repeats),
            )
            if preflight_result.get("enabled"):
                print(f"Audio preflight: {preflight_result}")
                if preflight_result.get("latency_error"):
                    print(f"Audio latency measurement warning: {preflight_result['latency_error']}")
                time.sleep(0.25)
            runner.start()
            while runner.step():
                time.sleep(0.01)
            final_state = runner.finalize()
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
