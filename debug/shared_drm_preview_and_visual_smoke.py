"""Shared-DRM debug smoke using one card owner for preview and stimulus."""

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
    from debug.shared_drm_debug import SharedDrmController, make_placeholder_preview_frame
except ModuleNotFoundError:
    from display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from repo_imports import prepare_repo_imports, resolve_repo_root
    from shared_camera_frame_source import SharedCameraFrameSource
    from shared_drm_debug import SharedDrmController, make_placeholder_preview_frame


class SharedDrmSmokeFailure(RuntimeError):
    """Raised when the shared-DRM smoke fails after partial initialization.

    Attributes:
        summary: JSON-serializable failure summary with the latest shared-DRM
            diagnostics.
    """

    def __init__(self, message: str, *, summary: dict[str, object]) -> None:
        super().__init__(message)
        self.summary = dict(summary)


def compile_shared_drm_stimuli(
    *,
    repo_root: Path,
    resolution_px: tuple[int, int],
    refresh_hz: float,
    degrees_subtended: float = 80.0,
) -> dict[str, Any]:
    """Load and compile the default go/nogo gratings for shared-DRM playback.

    Args:
        repo_root: BehavBox repository root directory.
        resolution_px: Stimulus output resolution as ``(width_px, height_px)``.
        refresh_hz: Stimulus output refresh rate in Hz.
        degrees_subtended: Horizontal display extent in visual degrees.

    Returns:
        dict[str, Any]: Mapping from canonical stimulus names to compiled
        gratings.
    """

    from box_runtime.visual_stimuli.visual_runtime import compile_grating, load_grating_spec

    stimuli_root = repo_root / "box_runtime" / "visual_stimuli"
    compiled: dict[str, Any] = {}
    for filename in ("go_grating.yaml", "nogo_grating.yaml"):
        spec = load_grating_spec(stimuli_root / filename)
        compiled[spec.name] = compile_grating(
            spec=spec,
            resolution_px=resolution_px,
            refresh_hz=float(refresh_hz),
            degrees_subtended=float(degrees_subtended),
        )
    return compiled


def run_shared_drm_preview_and_visual_smoke(
    *,
    output_root: Path,
    hold_s: float = 1.0,
    preview_mode: str = "placeholder",
    preview_source_mode: str = "rgb_main",
    preview_frame_rate_hz: float = 30.0,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    controller_factory: Callable[..., Any] = SharedDrmController,
    frame_source_factory: Callable[..., Any] = SharedCameraFrameSource,
    compile_stimuli_fn: Callable[..., dict[str, Any]] = compile_shared_drm_stimuli,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Run the shared-DRM placeholder-preview plus grating smoke.

    Args:
        output_root: Directory under which smoke outputs may be stored.
        hold_s: Additional hold time in seconds between grating presentations.
        preview_mode: Preview source mode, either ``"placeholder"`` or
            ``"live_camera0"``.
        preview_source_mode: Live camera preview source mode. ``"rgb_main"``
            captures from the main RGB stream, while ``"yuv_lores"`` uses the
            optional YUV low-resolution stream.
        preview_frame_rate_hz: Target shared preview update rate in frames per
            second for live camera mode.
        repo_root: Optional explicit repository root path.
        env: Optional environment-variable mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.
        require_mode: Zero-argument callable validating headless console mode.
        controller_factory: Factory returning a ``SharedDrmController``-compatible
            object.
        frame_source_factory: Factory returning a frame source compatible with
            ``SharedCameraFrameSource``.
        compile_stimuli_fn: Callable compiling shared-DRM stimuli for the
            stimulus output.
        sleep_fn: Sleep callable used between stimulus presentations.
        monotonic_fn: Monotonic clock callable returning seconds.

    Returns:
        dict[str, object]: JSON-serializable smoke summary.
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
        "preview_connector": "HDMI-A-1",
        "visual_connector": "HDMI-A-2",
        "preview_mode": str(preview_mode),
        "camera_preview_source_mode": str(preview_source_mode),
        "preview_target_fps": float(preview_frame_rate_hz),
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    controller = None
    frame_source = None
    preview_frame_count = 0
    preview_started_monotonic_s: float | None = None
    failure_stage = "controller_init"
    try:
        controller = controller_factory(
            preview_connector="HDMI-A-1",
            stimulus_connector="HDMI-A-2",
        )
        if preview_mode == "placeholder":
            failure_stage = "preview_placeholder"
            preview_frame = make_placeholder_preview_frame(controller.preview.resolution_px)
        elif preview_mode == "live_camera0":
            failure_stage = "preview_frame_source_init"
            frame_source = frame_source_factory(
                camera_id="camera0",
                resolution_px=controller.preview.resolution_px,
                acquisition_resolution_px=controller.preview.resolution_px,
                preview_stream_resolution_px=controller.preview.resolution_px,
                preview_source_mode=str(preview_source_mode),
                frame_rate_hz=float(preview_frame_rate_hz),
            )
            summary.update(
                {
                    "camera_preview_source_mode": getattr(
                        frame_source,
                        "preview_source_mode",
                        preview_source_mode,
                    ),
                    "camera_acquisition_resolution_px": getattr(
                        frame_source,
                        "acquisition_resolution_px",
                        controller.preview.resolution_px,
                    ),
                    "preview_stream_resolution_px": getattr(
                        frame_source,
                        "preview_stream_resolution_px",
                        controller.preview.resolution_px,
                    ),
                    "preview_frame_resolution_px": getattr(
                        frame_source,
                        "preview_rgb_resolution_px",
                        controller.preview.resolution_px,
                    ),
                }
            )
            failure_stage = "preview_capture"
            preview_frame = frame_source.capture_rgb_frame()
            summary["preview_frame_resolution_px"] = getattr(
                frame_source,
                "preview_rgb_resolution_px",
                (int(preview_frame.shape[1]), int(preview_frame.shape[0])),
            )
        else:
            raise ValueError(f"unsupported preview_mode {preview_mode!r}")

        failure_stage = "preview_display"
        controller.preview.display_rgb_frame(preview_frame)
        preview_frame_count += 1
        preview_started_monotonic_s = monotonic_fn()
        summary["preview_drm_diagnostics"] = controller.preview.diagnostics()

        failure_stage = "stimulus_compile"
        compiled_stimuli = compile_stimuli_fn(
            repo_root=resolved_repo_root,
            resolution_px=controller.stimulus.resolution_px,
            refresh_hz=controller.stimulus.refresh_hz,
        )

        failure_stage = "stimulus_gray"
        controller.stimulus.display_gray(127)

        failure_stage = "stimulus_playback"
        controller.stimulus.play_grating("go_grating", compiled_stimuli["go_grating"])
        preview_frame_count = _update_preview_during_hold(
            controller=controller,
            frame_source=frame_source,
            hold_s=float(hold_s),
            preview_frame_rate_hz=float(preview_frame_rate_hz),
            sleep_fn=sleep_fn,
            preview_frame_count=preview_frame_count,
            monotonic_fn=monotonic_fn,
        )
        controller.stimulus.play_grating("nogo_grating", compiled_stimuli["nogo_grating"])
        preview_frame_count = _update_preview_during_hold(
            controller=controller,
            frame_source=frame_source,
            hold_s=float(hold_s),
            preview_frame_rate_hz=float(preview_frame_rate_hz),
            sleep_fn=sleep_fn,
            preview_frame_count=preview_frame_count,
            monotonic_fn=monotonic_fn,
        )

        preview_elapsed_s = (
            max(0.0, monotonic_fn() - preview_started_monotonic_s)
            if preview_started_monotonic_s is not None
            else 0.0
        )
        preview_fps_achieved = (
            float(preview_frame_count) / preview_elapsed_s
            if preview_elapsed_s > 0.0
            else 0.0
        )

        summary.update(
            {
                "status": "ok",
                "preview_frame_count": int(preview_frame_count),
                "preview_elapsed_s": float(preview_elapsed_s),
                "preview_fps_achieved": float(preview_fps_achieved),
                "preview_drm_diagnostics": controller.preview.diagnostics(),
                "visual_drm_diagnostics": controller.stimulus.diagnostics(),
            }
        )
        if frame_source is not None and hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
        return summary
    except Exception as exc:
        summary["failure_stage"] = failure_stage
        if controller is not None:
            diagnostics = controller.diagnostics()
            summary["preview_drm_diagnostics"] = diagnostics.get("preview", {})
            summary["visual_drm_diagnostics"] = diagnostics.get("stimulus", {})
        raise SharedDrmSmokeFailure(
            f"{type(exc).__name__}: {exc}",
            summary=summary,
        ) from exc
    finally:
        if frame_source is not None:
            frame_source.close()
        if controller is not None:
            controller.close()


def _update_preview_during_hold(
    *,
    controller: Any,
    frame_source: Any,
    hold_s: float,
    preview_frame_rate_hz: float,
    sleep_fn: Callable[[float], None],
    preview_frame_count: int,
    monotonic_fn: Callable[[], float],
) -> int:
    """Update shared preview frames repeatedly during one hold interval.

    Args:
        controller: Shared DRM controller exposing ``preview.display_rgb_frame``.
        frame_source: Optional live frame source, or ``None`` for placeholder mode.
        hold_s: Hold interval in seconds.
        preview_frame_rate_hz: Target preview update rate in frames per second.
        sleep_fn: Sleep callable used between updates.
        preview_frame_count: Running preview frame count before this hold interval.
        monotonic_fn: Monotonic clock callable returning seconds.

    Returns:
        int: Updated preview frame count after this hold interval.
    """

    frame_count = int(preview_frame_count)
    if frame_source is None:
        sleep_fn(float(hold_s))
        return frame_count

    interval_s = 1.0 / max(float(preview_frame_rate_hz), 1.0)
    deadline = monotonic_fn() + max(0.0, float(hold_s))
    while True:
        now = monotonic_fn()
        if now >= deadline:
            break
        controller.preview.display_rgb_frame(frame_source.capture_rgb_frame())
        frame_count += 1
        remaining_s = deadline - monotonic_fn()
        if remaining_s <= 0.0:
            break
        sleep_fn(min(interval_s, remaining_s))
    return frame_count


def main(argv: list[str] | None = None) -> int:
    """Run the shared-DRM smoke as a command-line tool.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Zero on success, nonzero on failure.
    """

    parser = argparse.ArgumentParser(
        description="Run a shared-DRM placeholder-preview plus grating smoke."
    )
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/behavbox_debug"))
    parser.add_argument("--hold-s", type=float, default=1.0)
    parser.add_argument(
        "--preview-mode",
        choices=("placeholder", "live_camera0"),
        default="placeholder",
    )
    parser.add_argument(
        "--preview-source-mode",
        choices=("rgb_main", "yuv_lores"),
        default="rgb_main",
    )
    parser.add_argument("--preview-frame-rate-hz", type=float, default=30.0)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_shared_drm_preview_and_visual_smoke(
            output_root=args.output_root,
            hold_s=args.hold_s,
            preview_mode=args.preview_mode,
            preview_source_mode=args.preview_source_mode,
            preview_frame_rate_hz=args.preview_frame_rate_hz,
            repo_root=args.repo_root,
        )
    except HeadlessDisplayModeError as exc:
        print(exc, file=sys.stderr)
        return 2
    except SharedDrmSmokeFailure as exc:
        print(f"Shared DRM smoke failed: {exc}", file=sys.stderr)
        for key, value in exc.summary.items():
            print(f"{key}: {value}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Shared DRM smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Shared DRM smoke passed.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
