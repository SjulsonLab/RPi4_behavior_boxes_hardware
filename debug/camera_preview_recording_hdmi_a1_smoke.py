"""Single-output HDMI-A-1 smoke for dmabuf preview plus H.264 recording."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping

try:
    from debug.display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from debug.repo_imports import prepare_repo_imports, resolve_repo_root
    from debug.shared_camera_recording_dmabuf_source import SharedCameraRecordingDmabufSource
    from debug.shared_drm_debug import SharedDrmPreviewBackend
except ModuleNotFoundError:
    from display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from repo_imports import prepare_repo_imports, resolve_repo_root
    from shared_camera_recording_dmabuf_source import SharedCameraRecordingDmabufSource
    from shared_drm_debug import SharedDrmPreviewBackend


class CameraPreviewRecordingSmokeFailure(RuntimeError):
    """Raised when the preview+recording smoke fails after partial startup.

    Attributes:
        summary: JSON-serializable failure summary containing the latest timing
            metrics and recording diagnostics gathered before the failure.
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


def run_camera_preview_recording_hdmi_a1_smoke(
    *,
    output_root: Path,
    camera_id: str = "camera0",
    duration_s: float = 5.0,
    preview_connector: str = "HDMI-A-1",
    resolution_px: tuple[int, int] = (1024, 600),
    frame_rate_hz: float = 30.0,
    sensor_mode: int = 1,
    request_mode: str = "next",
    overlay_enabled: bool = True,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    frame_source_factory: Callable[..., Any] = SharedCameraRecordingDmabufSource,
    preview_backend_factory: Callable[..., Any] = SharedDrmPreviewBackend,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Run a one-screen dmabuf preview + H.264 recording smoke.

    Data contracts:

    - ``resolution_px`` is ``(width_px, height_px)`` in display pixels.
    - ``frame_source_factory`` must return an object exposing:
      - ``capture_frame_for_preview() -> frame``
      - ``release_frame(frame) -> None``
      - optional ``diagnostics() -> dict[str, object]``
      - ``close() -> None``
    - ``preview_backend_factory`` must return an object exposing:
      - ``display_dmabuf_frame(frame) -> None``
      - optional ``diagnostics() -> dict[str, object]``
      - ``close() -> None``
      - it may accept ``frame_release_fn=...`` to own the displayed-request
        lifetime instead of the smoke loop releasing frames eagerly

    Args:
        output_root: Directory under which recording outputs may be stored.
        camera_id: Semantic camera identifier such as ``"camera0"`` or
            ``"camera1"`` passed directly to the recording dmabuf source and
            used to name the output artifacts.
        duration_s: Requested preview duration in seconds.
        preview_connector: DRM connector name for the preview output.
        resolution_px: Requested output resolution as ``(width_px, height_px)``.
        frame_rate_hz: Requested preview and recording frame rate in Hz.
        sensor_mode: Picamera2 sensor mode index used for preview and recording.
        request_mode: Preview request-selection policy. ``"next"`` is the
            default because it behaved best in the shared-DRM dmabuf path.
        overlay_enabled: Whether the recording callback overlays elapsed time,
            Unix time, and instantaneous frame rate on the luma plane.
        repo_root: Optional explicit repository root path.
        env: Optional environment mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.
        require_mode: Zero-argument callable validating headless console mode.
        frame_source_factory: Factory returning a recording-capable dmabuf
            camera source.
        preview_backend_factory: Factory returning a dmabuf-capable preview
            backend.
        monotonic_fn: Monotonic clock callable returning seconds.

    Returns:
        dict[str, object]: JSON-serializable summary with preview timing,
        recording paths, and DRM diagnostics.
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

    camera_label = str(camera_id)
    video_path = output_root / f"{camera_label}_preview_recording_output.h264"
    timestamp_csv_path = output_root / f"{camera_label}_preview_recording_timestamp.csv"

    summary: dict[str, object] = {
        "camera_id": camera_label,
        "preview_connector": str(preview_connector),
        "preview_target_fps": float(frame_rate_hz),
        "sensor_mode": int(sensor_mode),
        "request_mode": str(request_mode),
        "overlay_enabled": bool(overlay_enabled),
        "video_path": str(video_path),
        "timestamp_csv_path": str(timestamp_csv_path),
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    frame_source = None
    preview_backend = None
    frame_count = 0
    capture_time_total_s = 0.0
    display_time_total_s = 0.0
    failure_stage = "frame_source_init"
    started_s: float | None = None

    try:
        frame_source = frame_source_factory(
            camera_id=camera_label,
            resolution_px=resolution_px,
            video_path=video_path,
            timestamp_csv_path=timestamp_csv_path,
            frame_rate_hz=float(frame_rate_hz),
            sensor_mode=int(sensor_mode),
            request_mode=str(request_mode),
            overlay_enabled=bool(overlay_enabled),
        )
        if hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
            summary["camera_id"] = camera_label

        failure_stage = "preview_backend_init"
        preview_backend = preview_backend_factory(
            connector=str(preview_connector),
            resolution_px=tuple(resolution_px),
            frame_rate_hz=float(frame_rate_hz),
            frame_release_fn=frame_source.release_frame,
        )
        if hasattr(preview_backend, "diagnostics"):
            summary["preview_drm_diagnostics"] = preview_backend.diagnostics()

        started_s = monotonic_fn()
        deadline_s = started_s + max(0.0, float(duration_s))
        while True:
            now_s = monotonic_fn()
            if frame_count > 0 and now_s >= deadline_s:
                break

            failure_stage = "preview_loop_capture"
            capture_started_s = monotonic_fn()
            frame = frame_source.capture_frame_for_preview()
            capture_time_total_s += max(0.0, monotonic_fn() - capture_started_s)

            failure_stage = "preview_loop_display"
            display_started_s = monotonic_fn()
            preview_backend.display_dmabuf_frame(frame)
            display_time_total_s += max(0.0, monotonic_fn() - display_started_s)

            frame_count += 1

        ended_s = monotonic_fn()
        elapsed_s = max(0.0, ended_s - (started_s if started_s is not None else ended_s))
        fps_achieved = float(frame_count) / elapsed_s if elapsed_s > 0.0 else 0.0
        summary.update(
            {
                "status": "ok",
                "preview_frame_count": int(frame_count),
                "preview_elapsed_s": float(elapsed_s),
                "preview_fps_achieved": float(fps_achieved),
                "capture_time_total_s": float(capture_time_total_s),
                "display_time_total_s": float(display_time_total_s),
                "capture_time_avg_s": _average(capture_time_total_s, frame_count),
                "display_time_avg_s": _average(display_time_total_s, frame_count),
            }
        )
        if hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
            summary["camera_id"] = camera_label
        if hasattr(preview_backend, "diagnostics"):
            summary["preview_drm_diagnostics"] = preview_backend.diagnostics()
        return summary
    except Exception as exc:
        summary["failure_stage"] = failure_stage
        summary["preview_frame_count"] = int(frame_count)
        summary["capture_time_total_s"] = float(capture_time_total_s)
        summary["display_time_total_s"] = float(display_time_total_s)
        if frame_source is not None and hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
            summary["camera_id"] = camera_label
        if preview_backend is not None and hasattr(preview_backend, "diagnostics"):
            summary["preview_drm_diagnostics"] = preview_backend.diagnostics()
        raise CameraPreviewRecordingSmokeFailure(
            f"{type(exc).__name__}: {exc}",
            summary=summary,
        ) from exc
    finally:
        if preview_backend is not None:
            preview_backend.close()
        if frame_source is not None:
            frame_source.close()


def main(argv: list[str] | None = None) -> int:
    """Run the HDMI-A-1 preview+recording smoke as a CLI command.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Zero on success, nonzero on failure.
    """

    parser = argparse.ArgumentParser(
        description="Run a single-output dmabuf camera preview+recording smoke on HDMI-A-1."
    )
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/behavbox_debug"))
    parser.add_argument("--camera-id", type=str, default="camera0")
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--preview-connector", type=str, default="HDMI-A-1")
    parser.add_argument("--frame-rate-hz", type=float, default=30.0)
    parser.add_argument("--sensor-mode", type=int, default=1)
    parser.add_argument("--request-mode", choices=("latest", "next"), default="next")
    parser.add_argument("--no-overlay", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_camera_preview_recording_hdmi_a1_smoke(
            output_root=args.output_root,
            camera_id=args.camera_id,
            duration_s=args.duration_s,
            preview_connector=args.preview_connector,
            frame_rate_hz=args.frame_rate_hz,
            sensor_mode=args.sensor_mode,
            request_mode=args.request_mode,
            overlay_enabled=not args.no_overlay,
            repo_root=args.repo_root,
        )
    except HeadlessDisplayModeError as exc:
        print(exc, file=sys.stderr)
        return 2
    except CameraPreviewRecordingSmokeFailure as exc:
        print(f"Camera preview recording smoke failed: {exc}", file=sys.stderr)
        for key, value in exc.summary.items():
            print(f"{key}: {value}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Camera preview recording smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Camera preview recording smoke passed.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
