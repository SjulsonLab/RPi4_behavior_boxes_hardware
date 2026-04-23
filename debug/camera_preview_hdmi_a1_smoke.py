"""Headless camera-preview smoke for the supported HDMI-A-1 topology."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping

try:
    from debug.display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from debug.repo_imports import prepare_repo_imports
except ModuleNotFoundError:
    from display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from repo_imports import prepare_repo_imports


def build_camera_preview_session_info(
    *,
    output_root: Path,
    camera_id: str = "camera0",
) -> dict[str, object]:
    """Build a preview-only camera session targeting HDMI-A-1.

    Args:
        output_root: Directory under which the smoke-run session directory is created.
        camera_id: Semantic camera identifier string such as ``"camera0"``.

    Returns:
        dict[str, object]: Session configuration mapping for
        ``LocalCameraRuntime``.
    """

    normalized_camera_id = str(camera_id)
    session_dir = output_root / "camera_preview_hdmi_a1_smoke"
    return {
        "external_storage": str(output_root),
        "dir_name": str(session_dir),
        "camera_enabled": True,
        "camera_ids": [normalized_camera_id],
        "camera_preview_modes": {normalized_camera_id: "drm_local"},
        "camera_preview_connector": "HDMI-A-1",
        "camera_recording_enabled": False,
        "visual_stimulus": False,
    }


def run_camera_preview_hdmi_a1_smoke(
    *,
    output_root: Path,
    duration_s: float = 5.0,
    camera_id: str = "camera0",
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    runtime_factory: Callable[..., Any] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Run one bounded headless camera-preview smoke on HDMI-A-1.

    Args:
        output_root: Directory under which the smoke-run session directory is created.
        duration_s: Preview duration in seconds.
        camera_id: Semantic camera identifier string such as ``"camera0"``.
        repo_root: Optional explicit BehavBox repository root path.
        env: Optional environment-variable mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.
        require_mode: Zero-argument callable validating headless mode.
        runtime_factory: Optional factory returning a ``LocalCameraRuntime``-compatible
            object. When omitted, the real runtime is imported after repo-root
            resolution.
        sleep_fn: Sleep callable used for the bounded preview interval.

    Returns:
        dict[str, object]: JSON-serializable smoke summary including connector
        and preview state.
    """

    mode_status = require_mode()
    prepare_repo_imports(
        repo_root_arg=repo_root,
        env=env,
        script_path=Path(__file__),
        home_dir=home_dir,
    )
    runtime_factory = runtime_factory or _import_camera_runtime()
    output_root.mkdir(parents=True, exist_ok=True)
    session_info = build_camera_preview_session_info(output_root=output_root, camera_id=camera_id)
    runtime = runtime_factory(str(camera_id), session_info)
    summary: dict[str, object] = {
        "camera_id": str(camera_id),
        "preview_connector": "HDMI-A-1",
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    try:
        runtime.prepare()
        runtime.start_preview()
        active_state = runtime.state_dict()
        sleep_fn(float(duration_s))
        runtime.stop_preview()
        stopped_state = runtime.state_dict()
        summary.update(
            {
                "status": "ok",
                "preview_active_before_stop": bool(active_state.get("preview_active", False)),
                "preview_active_after_stop": bool(stopped_state.get("preview_active", False)),
                "storage_root": str(active_state.get("storage_root", "")),
                "preview_drm_diagnostics": dict(active_state.get("drm_diagnostics", {})),
                "preview_last_error_phase": active_state.get("preview_last_error_phase"),
                "preview_last_error_message": active_state.get("preview_last_error_message"),
            }
        )
        return summary
    finally:
        runtime.close()


def _import_camera_runtime() -> Callable[..., Any]:
    """Import the real camera runtime after repo-root resolution.

    Returns:
        Callable[..., Any]: ``LocalCameraRuntime`` constructor compatible with
        ``run_camera_preview_hdmi_a1_smoke``.
    """

    from box_runtime.video_recording.local_camera_runtime import LocalCameraRuntime

    return LocalCameraRuntime


def main(argv: list[str] | None = None) -> int:
    """Run the camera-preview smoke as a command-line tool.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Zero on success, nonzero on failure.
    """

    parser = argparse.ArgumentParser(description="Run a headless camera preview smoke on HDMI-A-1.")
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/behavbox_debug"))
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--camera-id", default="camera0")
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_camera_preview_hdmi_a1_smoke(
            output_root=args.output_root,
            duration_s=args.duration_s,
            camera_id=args.camera_id,
            repo_root=args.repo_root,
        )
    except HeadlessDisplayModeError as exc:
        print(exc, file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Camera preview smoke failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Camera preview smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Camera preview smoke passed.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
