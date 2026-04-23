"""Run a visual-only workflow with browser camera preview plus drifting gratings."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sample_tasks.head_fixed_gonogo.visual_only_mode import (
    build_visual_only_lightdm_plan,
    build_visual_only_session_info,
    camera_monitor_url,
    ensure_camera_service_running,
)


def _run_lightdm_action(action: str, *, dry_run: bool) -> None:
    """Run one ``systemctl`` action on ``lightdm`` with optional dry-run mode.

    Data contracts:

    - ``action``:
      String service action, expected to be ``"start"`` or ``"stop"``.
    - ``dry_run``:
      ``True`` to print the command without executing it.

    Returns:
        None.
    """

    if action not in {"start", "stop"}:
        raise ValueError(f"Unsupported lightdm action {action!r}")
    command = ["sudo", "systemctl", action, "lightdm"]
    print(" ".join(command))
    if not dry_run:
        subprocess.run(command, check=True)


def _parse_args() -> argparse.Namespace:
    """Parse the visual-only launcher command line.

    Returns:
        argparse.Namespace: Parsed CLI arguments.
    """

    parser = argparse.ArgumentParser(
        description="Run browser camera preview plus drifting gratings without BehavBox task logic."
    )
    parser.add_argument("--visual-connector", default="HDMI-A-1", help="DRM connector for drifting gratings.")
    parser.add_argument(
        "--grating",
        choices=["go_grating", "nogo_grating"],
        default="go_grating",
        help="Grating spec name to loop continuously.",
    )
    parser.add_argument(
        "--camera-service-port",
        type=int,
        default=8000,
        help="HTTP port for the browser camera preview service.",
    )
    parser.add_argument(
        "--camera-monitor-host",
        default="127.0.0.1",
        help="Host or IP that the operator browser should use for the camera monitor page.",
    )
    parser.add_argument(
        "--camera-storage-root",
        default=str(Path.home() / "behvideos"),
        help="Storage root passed to the camera service if it must be started.",
    )
    parser.add_argument("--output-root", default="tmp_visual_runs", help="Directory root for visual-only outputs.")
    parser.add_argument("--session-tag", default="visual_only_session", help="Basename for the visual-only session.")
    parser.add_argument(
        "--grating-interval-s",
        type=float,
        default=0.25,
        help="Delay between consecutive grating presentations in seconds.",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="Number of grating presentations before exit; 0 means run until interrupted.",
    )
    parser.add_argument(
        "--keep-lightdm",
        action="store_true",
        help="Do not stop lightdm before attempting DRM grating output.",
    )
    parser.add_argument(
        "--no-restore-lightdm",
        action="store_true",
        help="Do not restart lightdm after the launcher exits.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the planned actions without launching visuals.")
    return parser.parse_args()


def main() -> int:
    """Run a visual-only camera-preview plus drifting-grating session.

    Returns:
        int: Process exit code, zero on clean completion.
    """

    args = _parse_args()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    storage_root = Path(args.camera_storage_root).expanduser().resolve()
    lightdm_plan = build_visual_only_lightdm_plan(
        stop_for_grating=not bool(args.keep_lightdm),
        restore_after=not bool(args.no_restore_lightdm),
    )
    planned_monitor_url = camera_monitor_url(host=args.camera_monitor_host, port=args.camera_service_port)

    if args.dry_run:
        print(f"Camera monitor URL: {planned_monitor_url}")
        for action in lightdm_plan["before"]:
            _run_lightdm_action(action, dry_run=True)
        print(
            "Planned grating loop:",
            {
                "grating": args.grating,
                "visual_connector": args.visual_connector,
                "cycles": args.cycles,
                "grating_interval_s": float(args.grating_interval_s),
            },
        )
        for action in lightdm_plan["after"]:
            print(f"sudo systemctl {action} lightdm  # planned restore")
        return 0

    monitor_url, started_camera_service = ensure_camera_service_running(
        port=int(args.camera_service_port),
        monitor_host=str(args.camera_monitor_host),
        storage_root=storage_root,
    )
    print(f"Camera monitor URL: {monitor_url}")
    if started_camera_service:
        print("Started the camera service for this run.")
    else:
        print("Reusing an existing camera service.")

    for action in lightdm_plan["before"]:
        _run_lightdm_action(action, dry_run=False)

    from box_runtime.visual_stimuli.visualstim import VisualStim

    session_info = build_visual_only_session_info(
        output_root,
        args.session_tag,
        visual_connector=str(args.visual_connector),
        grating_names=[str(args.grating)],
    )
    visual = VisualStim(session_info)
    completed_cycles = 0
    target_cycles = max(0, int(args.cycles))
    try:
        print(
            f"Displaying {args.grating} on {args.visual_connector}. "
            "Press Ctrl-C to stop."
        )
        while target_cycles == 0 or completed_cycles < target_cycles:
            visual.show_grating(str(args.grating))
            completed_cycles += 1
            time.sleep(float(args.grating_interval_s))
    except KeyboardInterrupt:
        print("Interrupted; closing visuals.")
    finally:
        visual.close()
        for action in lightdm_plan["after"]:
            try:
                _run_lightdm_action(action, dry_run=False)
            except Exception as exc:  # pragma: no cover - best-effort restore path
                print(f"Warning: failed to restore lightdm via '{action}': {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
