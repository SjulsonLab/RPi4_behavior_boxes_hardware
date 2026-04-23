"""Dual-output smoke for dmabuf preview+recording on HDMI-A-1 and gratings on HDMI-A-2."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping

try:
    from debug.camera_setup_preview_plus_gratings_hdmi_a2_smoke import (
        _average,
        _select_stimulus_frame,
        compile_setup_grating_stimuli,
    )
    from debug.display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from debug.repo_imports import prepare_repo_imports, resolve_repo_root
    from debug.shared_camera_recording_dmabuf_source import SharedCameraRecordingDmabufSource
    from debug.shared_drm_debug import SharedDrmController
except ModuleNotFoundError:
    from camera_setup_preview_plus_gratings_hdmi_a2_smoke import (
        _average,
        _select_stimulus_frame,
        compile_setup_grating_stimuli,
    )
    from display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from repo_imports import prepare_repo_imports, resolve_repo_root
    from shared_camera_recording_dmabuf_source import SharedCameraRecordingDmabufSource
    from shared_drm_debug import SharedDrmController


class CameraPreviewRecordingPlusGratingsSmokeFailure(RuntimeError):
    """Raised when the preview+recording+gratings smoke fails.

    Attributes:
        summary: JSON-serializable failure summary with timing, recording, and
            DRM state.
    """

    def __init__(self, message: str, *, summary: dict[str, object]) -> None:
        super().__init__(message)
        self.summary = dict(summary)


def run_camera_preview_recording_plus_gratings_hdmi_a2_smoke(
    *,
    output_root: Path,
    duration_s: float = 5.0,
    preview_connector: str = "HDMI-A-1",
    stimulus_connector: str = "HDMI-A-2",
    resolution_px: tuple[int, int] = (1024, 600),
    preview_source_mode: str = "dmabuf_main",
    frame_rate_hz: float = 30.0,
    sensor_mode: int = 0,
    request_mode: str = "next",
    overlay_enabled: bool = True,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    frame_source_factory: Callable[..., Any] = SharedCameraRecordingDmabufSource,
    shared_controller_factory: Callable[..., Any] = SharedDrmController,
    compile_stimuli_fn: Callable[..., dict[str, Any]] = compile_setup_grating_stimuli,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Run a dual-output smoke with preview+recording and drifting gratings.

    Data contracts:

    - ``resolution_px`` is ``(width_px, height_px)`` in display pixels for the
      preview/recording output.
    - ``frame_source_factory`` must return an object exposing:
      - ``capture_frame_for_preview() -> frame``
      - ``release_frame(frame) -> None``
      - optional ``diagnostics() -> dict[str, object]``
      - ``close() -> None``
    - ``shared_controller_factory`` must return an object exposing shared DRM
      ``preview`` and ``stimulus`` outputs where:
      - ``preview.display_dmabuf_frame(frame) -> None``
      - ``stimulus.display_gray_frame(frame_gray_u8) -> None``

    Args:
        output_root: Directory under which recording outputs may be stored.
        duration_s: Requested smoke duration in seconds.
        preview_connector: DRM connector name for the preview output.
        stimulus_connector: DRM connector name for the stimulus output.
        resolution_px: Requested preview/recording resolution as
            ``(width_px, height_px)``.
        preview_source_mode: Preview source label for this smoke. The recording
            dmabuf preview transport supports only ``"dmabuf_main"``.
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
        shared_controller_factory: Factory returning a shared DRM controller.
        compile_stimuli_fn: Callable compiling shared-DRM stimuli for the
            stimulus output.
        monotonic_fn: Monotonic clock callable returning seconds.

    Returns:
        dict[str, object]: JSON-serializable summary with preview timing,
        recording paths, stimulus metrics, and DRM diagnostics.
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

    if str(preview_source_mode) != "dmabuf_main":
        raise ValueError(
            "camera preview recording plus gratings smoke supports only preview_source_mode='dmabuf_main'"
        )

    video_path = output_root / "camera0_preview_recording_output.h264"
    timestamp_csv_path = output_root / "camera0_preview_recording_timestamp.csv"

    summary: dict[str, object] = {
        "preview_connector": str(preview_connector),
        "stimulus_connector": str(stimulus_connector),
        "camera_preview_source_mode": str(preview_source_mode),
        "camera_preview_transport": "dmabuf",
        "preview_target_fps": float(frame_rate_hz),
        "sensor_mode": int(sensor_mode),
        "request_mode": str(request_mode),
        "overlay_enabled": bool(overlay_enabled),
        "video_path": str(video_path),
        "timestamp_csv_path": str(timestamp_csv_path),
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    frame_source = None
    controller = None
    current_frame = None
    frame_count = 0
    stimulus_frame_update_count = 0
    capture_time_total_s = 0.0
    frame_prepare_time_total_s = 0.0
    display_time_total_s = 0.0
    stimulus_display_time_total_s = 0.0
    failure_stage = "frame_source_init"
    started_s: float | None = None

    try:
        frame_source = frame_source_factory(
            camera_id="camera0",
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

        failure_stage = "shared_controller_init"
        controller = shared_controller_factory(
            preview_connector=str(preview_connector),
            stimulus_connector=str(stimulus_connector),
        )

        failure_stage = "stimulus_compile"
        compiled_stimuli = compile_stimuli_fn(
            repo_root=resolved_repo_root,
            resolution_px=controller.stimulus.resolution_px,
            refresh_hz=controller.stimulus.refresh_hz,
        )
        compiled_go = compiled_stimuli["go_grating"]
        compiled_nogo = compiled_stimuli["nogo_grating"]

        summary["preview_drm_diagnostics"] = controller.preview.diagnostics()
        summary["stimulus_drm_diagnostics"] = controller.stimulus.diagnostics()

        started_s = monotonic_fn()
        deadline_s = started_s + max(0.0, float(duration_s))

        while True:
            now_s = monotonic_fn()
            if frame_count > 0 and now_s >= deadline_s:
                break
            elapsed_s = now_s - started_s

            failure_stage = "preview_loop_capture"
            capture_started_s = monotonic_fn()
            frame = frame_source.capture_frame_for_preview()
            capture_time_total_s += max(0.0, monotonic_fn() - capture_started_s)

            failure_stage = "preview_loop_stimulus_display"
            stimulus_label, stimulus_frame = _select_stimulus_frame(
                elapsed_s=elapsed_s,
                compiled_go=compiled_go,
                compiled_nogo=compiled_nogo,
            )
            stimulus_display_started_s = monotonic_fn()
            controller.stimulus.display_gray_frame(stimulus_frame)
            stimulus_display_time_total_s += max(0.0, monotonic_fn() - stimulus_display_started_s)
            stimulus_frame_update_count += 1

            failure_stage = "preview_loop_display"
            display_started_s = monotonic_fn()
            try:
                controller.preview.display_dmabuf_frame(frame)
            except Exception:
                frame_source.release_frame(frame)
                raise
            display_time_total_s += max(0.0, monotonic_fn() - display_started_s)
            if current_frame is not None:
                frame_source.release_frame(current_frame)
            current_frame = frame
            frame_count += 1

            summary["current_stimulus_name"] = stimulus_label

        elapsed_s = max(0.0, monotonic_fn() - (started_s if started_s is not None else monotonic_fn()))
        fps_achieved = float(frame_count) / elapsed_s if elapsed_s > 0.0 else 0.0
        summary.update(
            {
                "status": "ok",
                "preview_frame_count": int(frame_count),
                "stimulus_frame_update_count": int(stimulus_frame_update_count),
                "preview_elapsed_s": float(elapsed_s),
                "preview_fps_achieved": float(fps_achieved),
                "capture_time_total_s": float(capture_time_total_s),
                "frame_prepare_time_total_s": float(frame_prepare_time_total_s),
                "display_time_total_s": float(display_time_total_s),
                "stimulus_display_time_total_s": float(stimulus_display_time_total_s),
                "capture_time_avg_s": _average(capture_time_total_s, frame_count),
                "frame_prepare_time_avg_s": _average(frame_prepare_time_total_s, frame_count),
                "display_time_avg_s": _average(display_time_total_s, frame_count),
                "stimulus_display_time_avg_s": _average(
                    stimulus_display_time_total_s,
                    stimulus_frame_update_count,
                ),
                "preview_drm_diagnostics": controller.preview.diagnostics(),
                "stimulus_drm_diagnostics": controller.stimulus.diagnostics(),
            }
        )
        if hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
        return summary
    except Exception as exc:
        summary["failure_stage"] = failure_stage
        summary["preview_frame_count"] = int(frame_count)
        summary["stimulus_frame_update_count"] = int(stimulus_frame_update_count)
        summary["capture_time_total_s"] = float(capture_time_total_s)
        summary["frame_prepare_time_total_s"] = float(frame_prepare_time_total_s)
        summary["display_time_total_s"] = float(display_time_total_s)
        summary["stimulus_display_time_total_s"] = float(stimulus_display_time_total_s)
        if frame_source is not None and hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
        if controller is not None:
            diagnostics = controller.diagnostics()
            summary["preview_drm_diagnostics"] = diagnostics.get("preview", {})
            summary["stimulus_drm_diagnostics"] = diagnostics.get("stimulus", {})
        raise CameraPreviewRecordingPlusGratingsSmokeFailure(
            f"{type(exc).__name__}: {exc}",
            summary=summary,
        ) from exc
    finally:
        if current_frame is not None and frame_source is not None:
            frame_source.release_frame(current_frame)
        if controller is not None:
            controller.close()
        if frame_source is not None:
            frame_source.close()


def main(argv: list[str] | None = None) -> int:
    """Run the dual-output preview+recording+gratings smoke as a CLI command.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Zero on success, nonzero on failure.
    """

    parser = argparse.ArgumentParser(
        description="Run a dual-output dmabuf preview+recording smoke with drifting gratings on HDMI-A-2."
    )
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/behavbox_debug"))
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--preview-connector", type=str, default="HDMI-A-1")
    parser.add_argument("--stimulus-connector", type=str, default="HDMI-A-2")
    parser.add_argument("--preview-source-mode", choices=("dmabuf_main",), default="dmabuf_main")
    parser.add_argument("--frame-rate-hz", type=float, default=30.0)
    parser.add_argument("--sensor-mode", type=int, default=0)
    parser.add_argument("--request-mode", choices=("latest", "next"), default="next")
    parser.add_argument("--no-overlay", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_camera_preview_recording_plus_gratings_hdmi_a2_smoke(
            output_root=args.output_root,
            duration_s=args.duration_s,
            preview_connector=args.preview_connector,
            stimulus_connector=args.stimulus_connector,
            preview_source_mode=args.preview_source_mode,
            frame_rate_hz=args.frame_rate_hz,
            sensor_mode=args.sensor_mode,
            request_mode=args.request_mode,
            overlay_enabled=not args.no_overlay,
            repo_root=args.repo_root,
        )
    except HeadlessDisplayModeError as exc:
        print(exc, file=sys.stderr)
        return 2
    except CameraPreviewRecordingPlusGratingsSmokeFailure as exc:
        print(f"Camera preview recording plus gratings smoke failed: {exc}", file=sys.stderr)
        for key, value in exc.summary.items():
            print(f"{key}: {value}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Camera preview recording plus gratings smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Camera preview recording plus gratings smoke passed.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
