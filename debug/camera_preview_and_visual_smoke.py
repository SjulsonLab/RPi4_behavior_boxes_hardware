"""Headless combined camera-preview and visual-stimulus smoke for one-Pi topology."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping

try:
    from debug.camera_preview_hdmi_a1_smoke import build_camera_preview_session_info
    from debug.display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from debug.repo_imports import prepare_repo_imports
    from debug.visual_grating_hdmi_a2_smoke import build_visual_grating_session_info
except ModuleNotFoundError:
    from camera_preview_hdmi_a1_smoke import build_camera_preview_session_info
    from display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from repo_imports import prepare_repo_imports
    from visual_grating_hdmi_a2_smoke import build_visual_grating_session_info


class CombinedSmokeFailure(RuntimeError):
    """Raised when the combined smoke fails after partial runtime startup.

    Attributes:
        summary: JSON-serializable failure summary with the latest preview and
            visual diagnostic snapshots.
    """

    def __init__(self, message: str, *, summary: dict[str, object]) -> None:
        super().__init__(message)
        self.summary = dict(summary)


def run_camera_preview_and_visual_smoke(
    *,
    output_root: Path,
    overlap_s: float = 3.0,
    camera_id: str = "camera0",
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    camera_runtime_factory: Callable[..., Any] | None = None,
    visual_factory: Callable[..., Any] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Run the camera-first combined preview and grating smoke.

    Args:
        output_root: Directory under which the smoke-run output directories are created.
        overlap_s: Time in seconds to hold each grating while preview remains active.
        camera_id: Semantic camera identifier such as ``"camera0"``.
        repo_root: Optional explicit BehavBox repository root path.
        env: Optional environment-variable mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.
        require_mode: Zero-argument callable validating headless mode.
        camera_runtime_factory: Optional ``LocalCameraRuntime``-compatible constructor.
        visual_factory: Optional ``VisualStim``-compatible constructor.
        sleep_fn: Sleep callable used during the bounded overlap intervals.

    Returns:
        dict[str, object]: JSON-serializable smoke summary including connector
        targets, preview state, and visual playback metrics.
    """

    mode_status = require_mode()
    prepare_repo_imports(
        repo_root_arg=repo_root,
        env=env,
        script_path=Path(__file__),
        home_dir=home_dir,
    )
    camera_runtime_factory = camera_runtime_factory or _import_camera_runtime()
    visual_factory = visual_factory or _import_visual_stim()

    output_root.mkdir(parents=True, exist_ok=True)
    camera_session_info = build_camera_preview_session_info(output_root=output_root, camera_id=camera_id)
    visual_session_info = build_visual_grating_session_info(
        output_root=output_root,
        repo_root=repo_root,
        env=env,
        home_dir=home_dir,
    )

    camera_runtime = camera_runtime_factory(str(camera_id), camera_session_info)
    preview_started = False
    visual = None
    summary: dict[str, object] = {
        "camera_id": str(camera_id),
        "preview_connector": "HDMI-A-1",
        "visual_connector": "HDMI-A-2",
        "visual_backend": "drm",
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    failure_stage = "camera_prepare"
    try:
        camera_runtime.prepare()
        failure_stage = "camera_start_preview"
        camera_runtime.start_preview()
        preview_started = True
        preview_state = dict(camera_runtime.state_dict())
        summary.update(
            {
                "preview_active_after_start": bool(preview_state.get("preview_active", False)),
                "preview_storage_root": str(preview_state.get("storage_root", "")),
                "preview_drm_diagnostics": dict(preview_state.get("drm_diagnostics", {})),
                "preview_last_error_phase": preview_state.get("preview_last_error_phase"),
                "preview_last_error_message": preview_state.get("preview_last_error_message"),
            }
        )

        failure_stage = "visual_init"
        visual = visual_factory(visual_session_info)
        failure_stage = "visual_playback"
        visual.show_grating("go_grating")
        sleep_fn(float(overlap_s))
        visual.show_grating("nogo_grating")
        sleep_fn(float(overlap_s))

        runtime = getattr(visual, "_runtime", None)
        if runtime is not None and hasattr(runtime, "get_metrics"):
            metrics = runtime.get_metrics()
        else:
            metrics = dict(getattr(visual, "_metrics", {}))
        summary.update(
            {
                "status": "ok",
                "visual_play_count": int(metrics.get("play_count", 0)),
                "visual_current_label": metrics.get("current_label"),
                "visual_timing_entries": len(list(metrics.get("timing_log", []))),
                "visual_drm_diagnostics": dict(metrics.get("drm_diagnostics", {})),
            }
        )
        return summary
    except Exception as exc:
        summary["failure_stage"] = failure_stage
        preview_state = dict(camera_runtime.state_dict()) if preview_started else {}
        if preview_state:
            summary["preview_drm_diagnostics"] = dict(preview_state.get("drm_diagnostics", {}))
            summary["preview_last_error_phase"] = preview_state.get("preview_last_error_phase")
            summary["preview_last_error_message"] = preview_state.get("preview_last_error_message")
        if visual is not None:
            runtime = getattr(visual, "_runtime", None)
            visual_metrics: dict[str, object] = {}
            if runtime is not None:
                try:
                    visual_metrics = dict(runtime.get_metrics())
                except Exception:
                    visual_metrics = {"drm_diagnostics": dict(getattr(runtime, "_drm_diagnostics", {}))}
            else:
                visual_metrics = dict(getattr(visual, "_metrics", {}))
            summary["visual_drm_diagnostics"] = dict(visual_metrics.get("drm_diagnostics", {}))
        elif hasattr(exc, "diagnostics"):
            summary["visual_drm_diagnostics"] = dict(getattr(exc, "diagnostics", {}))
        raise CombinedSmokeFailure(
            f"{type(exc).__name__}: {exc}",
            summary=summary,
        ) from exc
    finally:
        if visual is not None:
            visual.close()
        if preview_started:
            try:
                camera_runtime.stop_preview()
            finally:
                stopped_state = dict(camera_runtime.state_dict())
                summary["preview_active_after_stop"] = bool(
                    stopped_state.get("preview_active", False)
                )
        camera_runtime.close()


def _import_camera_runtime() -> Callable[..., Any]:
    """Import the real camera runtime after repo-root resolution.

    Returns:
        Callable[..., Any]: ``LocalCameraRuntime`` constructor compatible with
        ``run_camera_preview_and_visual_smoke``.
    """

    from box_runtime.video_recording.local_camera_runtime import LocalCameraRuntime

    return LocalCameraRuntime


def _import_visual_stim() -> Callable[..., Any]:
    """Import the real visual runtime after repo-root resolution.

    Returns:
        Callable[..., Any]: ``VisualStim`` constructor compatible with
        ``run_camera_preview_and_visual_smoke``.
    """

    from box_runtime.visual_stimuli.visualstim import VisualStim

    return VisualStim


def main(argv: list[str] | None = None) -> int:
    """Run the combined smoke as a command-line tool.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Zero on success, nonzero on failure.
    """

    parser = argparse.ArgumentParser(
        description="Run a headless camera-preview plus drifting-grating smoke."
    )
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/behavbox_debug"))
    parser.add_argument("--overlap-s", type=float, default=3.0)
    parser.add_argument("--camera-id", default="camera0")
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_camera_preview_and_visual_smoke(
            output_root=args.output_root,
            overlap_s=args.overlap_s,
            camera_id=args.camera_id,
            repo_root=args.repo_root,
        )
    except HeadlessDisplayModeError as exc:
        print(exc, file=sys.stderr)
        return 2
    except CombinedSmokeFailure as exc:
        print(f"Combined camera/visual smoke failed: {exc}", file=sys.stderr)
        for key, value in exc.summary.items():
            print(f"{key}: {value}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Combined camera/visual smoke failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Combined camera/visual smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Combined camera/visual smoke passed.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
