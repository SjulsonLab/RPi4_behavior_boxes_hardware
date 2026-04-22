"""Single-output camera setup preview smoke for HDMI-A-1 with timing metrics."""

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
except ModuleNotFoundError:
    from display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from repo_imports import prepare_repo_imports, resolve_repo_root
    from shared_camera_frame_source import SharedCameraFrameSource


class CameraSetupPreviewSmokeFailure(RuntimeError):
    """Raised when the camera setup preview smoke fails after partial startup.

    Attributes:
        summary: JSON-serializable failure summary with the latest available
            timing and display diagnostics.
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


def run_camera_setup_preview_hdmi_a1_smoke(
    *,
    output_root: Path,
    duration_s: float = 5.0,
    preview_connector: str = "HDMI-A-1",
    resolution_px: tuple[int, int] = (1024, 600),
    preview_source_mode: str = "rgb_main",
    frame_rate_hz: float = 30.0,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    frame_source_factory: Callable[..., Any] = SharedCameraFrameSource,
    preview_backend_factory: Callable[..., Any] | None = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Run a single-output HDMI-A-1 camera setup preview smoke.

    Args:
        output_root: Directory under which smoke outputs may be stored.
        duration_s: Requested preview loop duration in seconds.
        preview_connector: DRM connector name for the setup preview display.
        resolution_px: Preview output resolution as ``(width_px, height_px)``.
        preview_source_mode: Camera preview source mode passed through to
            ``SharedCameraFrameSource``.
        frame_rate_hz: Target preview loop rate in frames per second.
        repo_root: Optional explicit repository root path.
        env: Optional environment-variable mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.
        require_mode: Zero-argument callable validating headless console mode.
        frame_source_factory: Factory returning a
            ``SharedCameraFrameSource``-compatible object.
        preview_backend_factory: Optional factory returning a display backend
            with ``display_frame()``, ``diagnostics()``, and ``close()``.
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

    if preview_backend_factory is None:
        from box_runtime.video_recording.drm_preview_viewer import (
            PreviewDisplayConfig,
            _PykmsPreviewBackend,
        )

        def _default_preview_backend_factory(**kwargs: object) -> object:
            config = PreviewDisplayConfig(
                connector=str(kwargs["connector"]),
                resolution_px=tuple(kwargs["resolution_px"]),  # type: ignore[arg-type]
                stream_url="",
                max_preview_hz=float(kwargs["frame_rate_hz"]),
                stall_timeout_s=0.5,
            )
            return _PykmsPreviewBackend(config)

        preview_backend_factory = _default_preview_backend_factory

    summary: dict[str, object] = {
        "preview_connector": str(preview_connector),
        "camera_preview_source_mode": str(preview_source_mode),
        "preview_target_fps": float(frame_rate_hz),
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    frame_source = None
    preview_backend = None
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

        failure_stage = "preview_backend_init"
        preview_backend = preview_backend_factory(
            connector=str(preview_connector),
            resolution_px=tuple(resolution_px),
            frame_rate_hz=float(frame_rate_hz),
        )
        if hasattr(preview_backend, "diagnostics"):
            summary["preview_drm_diagnostics"] = preview_backend.diagnostics()

        started_s = monotonic_fn()
        deadline_s = started_s + max(0.0, float(duration_s))
        interval_s = 1.0 / max(float(frame_rate_hz), 1.0)

        while True:
            now_s = monotonic_fn()
            if frame_count > 0 and now_s >= deadline_s:
                break

            capture_started_s = monotonic_fn()
            source_frame = frame_source.capture_source_frame()
            capture_time_total_s += max(0.0, monotonic_fn() - capture_started_s)

            prepare_started_s = monotonic_fn()
            preview_frame = frame_source.prepare_preview_frame(source_frame)
            frame_prepare_time_total_s += max(0.0, monotonic_fn() - prepare_started_s)

            display_started_s = monotonic_fn()
            preview_backend.display_frame(preview_frame)
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
            }
        )
        if hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
        if hasattr(preview_backend, "diagnostics"):
            summary["preview_drm_diagnostics"] = preview_backend.diagnostics()
        return summary
    except Exception as exc:
        summary["failure_stage"] = failure_stage
        summary["preview_frame_count"] = int(frame_count)
        summary["capture_time_total_s"] = float(capture_time_total_s)
        summary["frame_prepare_time_total_s"] = float(frame_prepare_time_total_s)
        summary["display_time_total_s"] = float(display_time_total_s)
        if frame_source is not None and hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
        if preview_backend is not None and hasattr(preview_backend, "diagnostics"):
            summary["preview_drm_diagnostics"] = preview_backend.diagnostics()
        raise CameraSetupPreviewSmokeFailure(
            f"{type(exc).__name__}: {exc}",
            summary=summary,
        ) from exc
    finally:
        if preview_backend is not None:
            preview_backend.close()
        if frame_source is not None:
            frame_source.close()


def main(argv: list[str] | None = None) -> int:
    """Run the HDMI-A-1 setup preview smoke as a CLI command.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Zero on success, nonzero on failure.
    """

    parser = argparse.ArgumentParser(
        description="Run a single-output camera setup preview smoke on HDMI-A-1."
    )
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/behavbox_debug"))
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--preview-connector", type=str, default="HDMI-A-1")
    parser.add_argument("--preview-source-mode", choices=("rgb_main", "yuv_lores"), default="rgb_main")
    parser.add_argument("--frame-rate-hz", type=float, default=30.0)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_camera_setup_preview_hdmi_a1_smoke(
            output_root=args.output_root,
            duration_s=args.duration_s,
            preview_connector=args.preview_connector,
            preview_source_mode=args.preview_source_mode,
            frame_rate_hz=args.frame_rate_hz,
            repo_root=args.repo_root,
        )
    except HeadlessDisplayModeError as exc:
        print(exc, file=sys.stderr)
        return 2
    except CameraSetupPreviewSmokeFailure as exc:
        print(f"Camera setup preview smoke failed: {exc}", file=sys.stderr)
        for key, value in exc.summary.items():
            print(f"{key}: {value}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Camera setup preview smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Camera setup preview smoke passed.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
