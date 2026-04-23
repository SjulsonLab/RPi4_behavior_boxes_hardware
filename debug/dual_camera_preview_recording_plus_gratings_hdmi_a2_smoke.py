"""Dual-output smoke for dual-camera recording with preview and drifting gratings."""

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
    from debug.dual_camera_preview_recording_hdmi_a1_smoke import (
        DualCameraPreviewRecordingSmokeFailure,
    )
    from debug.repo_imports import prepare_repo_imports, resolve_repo_root
    from debug.shared_drm_debug import SharedDrmController
    from debug.shared_dual_camera_recording_source import SharedDualCameraRecordingSource
except ModuleNotFoundError:
    from camera_setup_preview_plus_gratings_hdmi_a2_smoke import (
        _average,
        _select_stimulus_frame,
        compile_setup_grating_stimuli,
    )
    from display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from dual_camera_preview_recording_hdmi_a1_smoke import (
        DualCameraPreviewRecordingSmokeFailure,
    )
    from repo_imports import prepare_repo_imports, resolve_repo_root
    from shared_drm_debug import SharedDrmController
    from shared_dual_camera_recording_source import SharedDualCameraRecordingSource


def run_dual_camera_preview_recording_plus_gratings_hdmi_a2_smoke(
    *,
    output_root: Path,
    preview_camera_id: str = "camera0",
    recording_camera_id: str = "camera1",
    duration_s: float = 5.0,
    preview_connector: str = "HDMI-A-1",
    stimulus_connector: str = "HDMI-A-2",
    resolution_px: tuple[int, int] = (1024, 600),
    frame_rate_hz: float = 30.0,
    sensor_mode: int = 0,
    request_mode: str = "next",
    preview_overlay_enabled: bool = True,
    recording_overlay_enabled: bool = True,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    dual_source_factory: Callable[..., Any] = SharedDualCameraRecordingSource,
    shared_controller_factory: Callable[..., Any] = SharedDrmController,
    compile_stimuli_fn: Callable[..., dict[str, Any]] = compile_setup_grating_stimuli,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Run a dual-camera smoke with preview, recording, and drifting gratings.

    Data contracts:

    - ``resolution_px`` is ``(width_px, height_px)`` in display pixels for the
      preview output.
    - ``dual_source_factory`` must return an object exposing:
      - ``capture_frame_for_preview() -> frame``
      - ``release_frame(frame) -> None``
      - optional ``diagnostics() -> dict[str, object]``
      - ``close() -> None``
    - ``shared_controller_factory`` must return an object exposing shared DRM
      ``preview`` and ``stimulus`` outputs where:
      - ``preview.display_dmabuf_frame(frame) -> None``
      - ``stimulus.display_gray_frame(frame_gray_u8) -> None``

    Args:
        output_root: Directory under which both cameras' recording artifacts are
            stored.
        preview_camera_id: Semantic identifier for the previewed camera.
        recording_camera_id: Semantic identifier for the recording-only camera.
        duration_s: Requested smoke duration in seconds.
        preview_connector: DRM connector name for the preview output.
        stimulus_connector: DRM connector name for the drifting-grating output.
        resolution_px: Requested preview resolution as ``(width_px, height_px)``.
        frame_rate_hz: Requested acquisition frame rate in Hz for both cameras.
        sensor_mode: Picamera2 sensor mode index applied to both cameras.
        request_mode: Preview request-selection policy for the preview camera.
        preview_overlay_enabled: Whether overlay text is enabled on the
            previewed/recorded camera.
        recording_overlay_enabled: Whether overlay text is enabled on the
            recording-only camera.
        repo_root: Optional explicit repository root path.
        env: Optional environment mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.
        require_mode: Zero-argument callable validating headless console mode.
        dual_source_factory: Factory returning the dual-camera recording helper.
        shared_controller_factory: Factory returning a shared DRM controller.
        compile_stimuli_fn: Callable compiling shared-DRM stimuli for the
            stimulus output.
        monotonic_fn: Monotonic clock callable returning seconds.

    Returns:
        dict[str, object]: JSON-serializable summary with preview timing, both
        cameras' recording artifacts, and stimulus metrics.
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
        "preview_camera_id": str(preview_camera_id),
        "recording_camera_id": str(recording_camera_id),
        "preview_connector": str(preview_connector),
        "stimulus_connector": str(stimulus_connector),
        "camera_preview_transport": "dmabuf",
        "preview_target_fps": float(frame_rate_hz),
        "sensor_mode": int(sensor_mode),
        "request_mode": str(request_mode),
        "preview_overlay_enabled": bool(preview_overlay_enabled),
        "recording_overlay_enabled": bool(recording_overlay_enabled),
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    dual_source = None
    controller = None
    current_frame = None
    frame_count = 0
    stimulus_frame_update_count = 0
    capture_time_total_s = 0.0
    display_time_total_s = 0.0
    stimulus_display_time_total_s = 0.0
    failure_stage = "dual_source_init"
    started_s: float | None = None

    try:
        dual_source = dual_source_factory(
            output_root=output_root,
            preview_camera_id=str(preview_camera_id),
            recording_camera_id=str(recording_camera_id),
            resolution_px=resolution_px,
            frame_rate_hz=float(frame_rate_hz),
            sensor_mode=int(sensor_mode),
            request_mode=str(request_mode),
            preview_overlay_enabled=bool(preview_overlay_enabled),
            recording_overlay_enabled=bool(recording_overlay_enabled),
        )
        if hasattr(dual_source, "diagnostics"):
            summary.update(dual_source.diagnostics())
            summary["preview_camera_id"] = str(preview_camera_id)
            summary["recording_camera_id"] = str(recording_camera_id)

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
            frame = dual_source.capture_frame_for_preview()
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
                dual_source.release_frame(frame)
                raise
            display_time_total_s += max(0.0, monotonic_fn() - display_started_s)
            if current_frame is not None:
                dual_source.release_frame(current_frame)
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
                "display_time_total_s": float(display_time_total_s),
                "stimulus_display_time_total_s": float(stimulus_display_time_total_s),
                "capture_time_avg_s": _average(capture_time_total_s, frame_count),
                "display_time_avg_s": _average(display_time_total_s, frame_count),
                "stimulus_display_time_avg_s": _average(
                    stimulus_display_time_total_s,
                    stimulus_frame_update_count,
                ),
                "preview_drm_diagnostics": controller.preview.diagnostics(),
                "stimulus_drm_diagnostics": controller.stimulus.diagnostics(),
            }
        )
        if hasattr(dual_source, "diagnostics"):
            summary.update(dual_source.diagnostics())
            summary["preview_camera_id"] = str(preview_camera_id)
            summary["recording_camera_id"] = str(recording_camera_id)
        return summary
    except Exception as exc:
        summary["failure_stage"] = failure_stage
        summary["preview_frame_count"] = int(frame_count)
        summary["capture_time_total_s"] = float(capture_time_total_s)
        summary["display_time_total_s"] = float(display_time_total_s)
        summary["stimulus_display_time_total_s"] = float(stimulus_display_time_total_s)
        summary["stimulus_frame_update_count"] = int(stimulus_frame_update_count)
        if dual_source is not None and hasattr(dual_source, "diagnostics"):
            summary.update(dual_source.diagnostics())
            summary["preview_camera_id"] = str(preview_camera_id)
            summary["recording_camera_id"] = str(recording_camera_id)
        if controller is not None:
            try:
                diagnostics = controller.diagnostics()
                summary["preview_drm_diagnostics"] = diagnostics["preview"]
                summary["stimulus_drm_diagnostics"] = diagnostics["stimulus"]
            except Exception:
                pass
        raise DualCameraPreviewRecordingSmokeFailure(
            f"{type(exc).__name__}: {exc}",
            summary=summary,
        ) from exc
    finally:
        if dual_source is not None and current_frame is not None:
            dual_source.release_frame(current_frame)
        if controller is not None:
            controller.close()
        if dual_source is not None:
            dual_source.close()


def main(argv: list[str] | None = None) -> int:
    """Run the dual-camera preview+recording+gratings smoke as a CLI command.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Zero on success, nonzero on failure.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Run a dual-camera smoke with camera0 preview+recording, camera1 recording-only, "
            "and drifting gratings on HDMI-A-2."
        )
    )
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/behavbox_debug"))
    parser.add_argument("--preview-camera-id", type=str, default="camera0")
    parser.add_argument("--recording-camera-id", type=str, default="camera1")
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--preview-connector", type=str, default="HDMI-A-1")
    parser.add_argument("--stimulus-connector", type=str, default="HDMI-A-2")
    parser.add_argument("--frame-rate-hz", type=float, default=30.0)
    parser.add_argument("--sensor-mode", type=int, default=0)
    parser.add_argument("--request-mode", choices=("latest", "next"), default="next")
    parser.add_argument("--no-preview-overlay", action="store_true")
    parser.add_argument("--no-recording-overlay", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_dual_camera_preview_recording_plus_gratings_hdmi_a2_smoke(
            output_root=args.output_root,
            preview_camera_id=args.preview_camera_id,
            recording_camera_id=args.recording_camera_id,
            duration_s=args.duration_s,
            preview_connector=args.preview_connector,
            stimulus_connector=args.stimulus_connector,
            frame_rate_hz=args.frame_rate_hz,
            sensor_mode=args.sensor_mode,
            request_mode=args.request_mode,
            preview_overlay_enabled=not args.no_preview_overlay,
            recording_overlay_enabled=not args.no_recording_overlay,
            repo_root=args.repo_root,
        )
    except HeadlessDisplayModeError as exc:
        print(exc, file=sys.stderr)
        return 2
    except DualCameraPreviewRecordingSmokeFailure as exc:
        print(f"Dual-camera preview recording plus gratings smoke failed: {exc}", file=sys.stderr)
        for key, value in exc.summary.items():
            print(f"{key}: {value}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Dual-camera preview recording plus gratings smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Dual-camera preview recording plus gratings smoke passed.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
