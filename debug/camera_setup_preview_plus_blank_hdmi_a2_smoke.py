"""Dual-output setup preview smoke with camera on HDMI-A-1 and blank HDMI-A-2."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping

try:
    from debug.display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from debug.repo_imports import prepare_repo_imports, resolve_repo_root
    from debug.shared_camera_frame_source import SharedCameraFrameSource
    from debug.shared_drm_debug import SharedDrmController
except ModuleNotFoundError:
    from display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from repo_imports import prepare_repo_imports, resolve_repo_root
    from shared_camera_frame_source import SharedCameraFrameSource
    from shared_drm_debug import SharedDrmController


class CameraSetupPreviewPlusBlankSmokeFailure(RuntimeError):
    """Raised when the dual-output setup preview smoke fails after partial startup.

    Attributes:
        summary: JSON-serializable failure summary with timing and DRM state.
    """

    def __init__(self, message: str, *, summary: dict[str, object]) -> None:
        super().__init__(message)
        self.summary = dict(summary)


def _average(total_s: float, count: int) -> float:
    """Return a safe average timing value.

    Args:
        total_s: Aggregated time in seconds.
        count: Number of contributing iterations.

    Returns:
        float: Average time in seconds, or ``0.0`` when ``count == 0``.
    """

    return float(total_s) / float(count) if int(count) > 0 else 0.0


def run_camera_setup_preview_plus_blank_hdmi_a2_smoke(
    *,
    output_root: Path,
    duration_s: float = 5.0,
    preview_connector: str = "HDMI-A-1",
    blank_connector: str = "HDMI-A-2",
    resolution_px: tuple[int, int] = (1024, 600),
    preview_source_mode: str = "rgb_main",
    blank_gray_level_u8: int = 127,
    frame_rate_hz: float = 30.0,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    frame_source_factory: Callable[..., Any] = SharedCameraFrameSource,
    shared_controller_factory: Callable[..., Any] = SharedDrmController,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Run a dual-output setup preview smoke with a blank second screen.

    Args:
        output_root: Directory under which smoke outputs may be stored.
        duration_s: Requested preview loop duration in seconds.
        preview_connector: DRM connector name for the camera preview output.
        blank_connector: DRM connector name for the blank second output.
        resolution_px: Preview output resolution as ``(width_px, height_px)``.
        preview_source_mode: Camera preview source mode passed through to
            ``SharedCameraFrameSource``.
        blank_gray_level_u8: Blank-screen gray value in uint8 display units.
        frame_rate_hz: Target preview loop rate in frames per second.
        repo_root: Optional explicit repository root path.
        env: Optional environment-variable mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.
        require_mode: Zero-argument callable validating headless console mode.
        frame_source_factory: Factory returning a
            ``SharedCameraFrameSource``-compatible object.
        shared_controller_factory: Factory returning a
            ``SharedDrmController``-compatible object.
        monotonic_fn: Monotonic clock callable returning seconds.
        sleep_fn: Sleep callable used to target the preview loop rate.

    Returns:
        dict[str, object]: JSON-serializable smoke summary with timing metrics.
    """

    mode_status = require_mode()
    resolved_repo_root = resolve_repo_root(
        repo_root_arg=repo_root,
        env=env,
        script_path=Path(__file__),
        home_dir=home_dir,
    )
    prepare_repo_imports(
        repo_root_arg=resolved_repo_root,
        env=env,
        script_path=Path(__file__),
        home_dir=home_dir,
    )
    output_root.mkdir(parents=True, exist_ok=True)

    summary: dict[str, object] = {
        "preview_connector": str(preview_connector),
        "blank_connector": str(blank_connector),
        "blank_gray_level_u8": int(blank_gray_level_u8),
        "camera_preview_source_mode": str(preview_source_mode),
        "preview_target_fps": float(frame_rate_hz),
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    frame_source = None
    controller = None
    frame_count = 0
    capture_time_total_s = 0.0
    frame_prepare_time_total_s = 0.0
    display_time_total_s = 0.0
    failure_stage = "frame_source_init"
    started_s: float | None = None
    try:
        frame_source = frame_source_factory(
            camera_id="camera0",
            resolution_px=resolution_px,
            acquisition_resolution_px=resolution_px,
            preview_stream_resolution_px=resolution_px,
            preview_source_mode=str(preview_source_mode),
            frame_rate_hz=float(frame_rate_hz),
        )
        if hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())

        failure_stage = "shared_controller_init"
        controller = shared_controller_factory(
            preview_connector=str(preview_connector),
            stimulus_connector=str(blank_connector),
        )

        failure_stage = "blank_display"
        controller.stimulus.display_gray(int(blank_gray_level_u8))
        summary["preview_drm_diagnostics"] = controller.preview.diagnostics()
        summary["blank_drm_diagnostics"] = controller.stimulus.diagnostics()

        started_s = monotonic_fn()
        deadline_s = started_s + max(0.0, float(duration_s))
        interval_s = 1.0 / max(float(frame_rate_hz), 1.0)

        while True:
            now_s = monotonic_fn()
            if frame_count > 0 and now_s >= deadline_s:
                break

            failure_stage = "preview_loop_capture"
            capture_started_s = monotonic_fn()
            source_frame = frame_source.capture_source_frame()
            capture_time_total_s += max(0.0, monotonic_fn() - capture_started_s)

            failure_stage = "preview_loop_prepare"
            prepare_started_s = monotonic_fn()
            preview_frame = frame_source.prepare_preview_frame(source_frame)
            frame_prepare_time_total_s += max(0.0, monotonic_fn() - prepare_started_s)

            failure_stage = "preview_loop_display"
            display_started_s = monotonic_fn()
            controller.preview.display_rgb_frame(preview_frame)
            display_time_total_s += max(0.0, monotonic_fn() - display_started_s)

            frame_count += 1

            remaining_s = deadline_s - monotonic_fn()
            if remaining_s <= 0.0:
                break
            sleep_fn(min(interval_s, remaining_s))

        elapsed_s = max(0.0, monotonic_fn() - (started_s if started_s is not None else monotonic_fn()))
        fps_achieved = float(frame_count) / elapsed_s if elapsed_s > 0.0 else 0.0
        summary.update(
            {
                "status": "ok",
                "preview_frame_count": int(frame_count),
                "preview_elapsed_s": float(elapsed_s),
                "preview_fps_achieved": float(fps_achieved),
                "capture_time_total_s": float(capture_time_total_s),
                "frame_prepare_time_total_s": float(frame_prepare_time_total_s),
                "display_time_total_s": float(display_time_total_s),
                "capture_time_avg_s": _average(capture_time_total_s, frame_count),
                "frame_prepare_time_avg_s": _average(frame_prepare_time_total_s, frame_count),
                "display_time_avg_s": _average(display_time_total_s, frame_count),
                "preview_drm_diagnostics": controller.preview.diagnostics(),
                "blank_drm_diagnostics": controller.stimulus.diagnostics(),
            }
        )
        if hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
        return summary
    except Exception as exc:
        summary["failure_stage"] = failure_stage
        summary["preview_frame_count"] = int(frame_count)
        summary["capture_time_total_s"] = float(capture_time_total_s)
        summary["frame_prepare_time_total_s"] = float(frame_prepare_time_total_s)
        summary["display_time_total_s"] = float(display_time_total_s)
        if frame_source is not None and hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
        if controller is not None:
            diagnostics = controller.diagnostics()
            summary["preview_drm_diagnostics"] = diagnostics.get("preview", {})
            summary["blank_drm_diagnostics"] = diagnostics.get("stimulus", {})
        raise CameraSetupPreviewPlusBlankSmokeFailure(
            f"{type(exc).__name__}: {exc}",
            summary=summary,
        ) from exc
    finally:
        if controller is not None:
            controller.close()
        if frame_source is not None:
            frame_source.close()


def main(argv: list[str] | None = None) -> int:
    """Run the dual-output setup preview smoke as a CLI command.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Zero on success, nonzero on failure.
    """

    parser = argparse.ArgumentParser(
        description="Run a dual-output setup preview smoke with blank HDMI-A-2."
    )
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/behavbox_debug"))
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--preview-connector", type=str, default="HDMI-A-1")
    parser.add_argument("--blank-connector", type=str, default="HDMI-A-2")
    parser.add_argument("--blank-gray-level", type=int, default=127)
    parser.add_argument("--preview-source-mode", choices=("rgb_main", "yuv_lores"), default="rgb_main")
    parser.add_argument("--frame-rate-hz", type=float, default=30.0)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_camera_setup_preview_plus_blank_hdmi_a2_smoke(
            output_root=args.output_root,
            duration_s=args.duration_s,
            preview_connector=args.preview_connector,
            blank_connector=args.blank_connector,
            blank_gray_level_u8=args.blank_gray_level,
            preview_source_mode=args.preview_source_mode,
            frame_rate_hz=args.frame_rate_hz,
            repo_root=args.repo_root,
        )
    except HeadlessDisplayModeError as exc:
        print(exc, file=sys.stderr)
        return 2
    except CameraSetupPreviewPlusBlankSmokeFailure as exc:
        print(f"Camera setup preview plus blank smoke failed: {exc}", file=sys.stderr)
        for key, value in exc.summary.items():
            print(f"{key}: {value}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Camera setup preview plus blank smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Camera setup preview plus blank smoke passed.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
