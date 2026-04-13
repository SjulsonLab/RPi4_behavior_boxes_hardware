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
    from debug.shared_drm_debug import SharedDrmController, make_placeholder_preview_frame
except ModuleNotFoundError:
    from display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from repo_imports import prepare_repo_imports, resolve_repo_root
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
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    controller_factory: Callable[..., Any] = SharedDrmController,
    compile_stimuli_fn: Callable[..., dict[str, Any]] = compile_shared_drm_stimuli,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Run the shared-DRM placeholder-preview plus grating smoke.

    Args:
        output_root: Directory under which smoke outputs may be stored.
        hold_s: Additional hold time in seconds between grating presentations.
        repo_root: Optional explicit repository root path.
        env: Optional environment-variable mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.
        require_mode: Zero-argument callable validating headless console mode.
        controller_factory: Factory returning a ``SharedDrmController``-compatible
            object.
        compile_stimuli_fn: Callable compiling shared-DRM stimuli for the
            stimulus output.
        sleep_fn: Sleep callable used between stimulus presentations.

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
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    controller = None
    failure_stage = "controller_init"
    try:
        controller = controller_factory(
            preview_connector="HDMI-A-1",
            stimulus_connector="HDMI-A-2",
        )
        failure_stage = "preview_placeholder"
        placeholder_frame = make_placeholder_preview_frame(controller.preview.resolution_px)
        controller.preview.display_rgb_frame(placeholder_frame)
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
        sleep_fn(float(hold_s))
        controller.stimulus.play_grating("nogo_grating", compiled_stimuli["nogo_grating"])
        sleep_fn(float(hold_s))

        summary.update(
            {
                "status": "ok",
                "preview_drm_diagnostics": controller.preview.diagnostics(),
                "visual_drm_diagnostics": controller.stimulus.diagnostics(),
            }
        )
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
        if controller is not None:
            controller.close()


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
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_shared_drm_preview_and_visual_smoke(
            output_root=args.output_root,
            hold_s=args.hold_s,
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
