"""Headless drifting-grating smoke for the supported HDMI-A-2 topology."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping

try:
    from debug.display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from debug.repo_imports import prepare_repo_imports, resolve_repo_root
except ModuleNotFoundError:
    from display_mode_guard import HeadlessDisplayModeError, require_headless_console_mode
    from repo_imports import prepare_repo_imports, resolve_repo_root


def build_visual_grating_session_info(
    *,
    output_root: Path,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
) -> dict[str, object]:
    """Build a stimulus-only session targeting HDMI-A-2 through DRM.

    Args:
        output_root: Directory under which the smoke-run session directory is created.
        repo_root: Optional explicit BehavBox repository root path.
        env: Optional environment-variable mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.

    Returns:
        dict[str, object]: Session configuration mapping for ``VisualStim``.
    """

    resolved_repo_root = resolve_repo_root(
        repo_root_arg=repo_root,
        env=env,
        script_path=Path(__file__),
        home_dir=home_dir,
    )
    stimuli_root = resolved_repo_root / "box_runtime" / "visual_stimuli"
    session_dir = output_root / "visual_grating_hdmi_a2_smoke"
    return {
        "external_storage": str(output_root),
        "dir_name": str(session_dir),
        "visual_stimulus": True,
        "visual_display_backend": "drm",
        "visual_display_connector": "HDMI-A-2",
        "gray_level": 127,
        "vis_gratings": [
            str(stimuli_root / "go_grating.yaml"),
            str(stimuli_root / "nogo_grating.yaml"),
        ],
    }


def run_visual_grating_hdmi_a2_smoke(
    *,
    output_root: Path,
    hold_s: float = 1.0,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    visual_factory: Callable[..., Any] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Run one bounded headless grating smoke on HDMI-A-2.

    Args:
        output_root: Directory under which the smoke-run session directory is created.
        hold_s: Time in seconds to leave each grating visible.
        repo_root: Optional explicit BehavBox repository root path.
        env: Optional environment-variable mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.
        require_mode: Zero-argument callable validating headless mode.
        visual_factory: Optional factory returning a ``VisualStim``-compatible
            object. When omitted, the real runtime is imported after repo-root
            resolution.
        sleep_fn: Sleep callable used between grating presentations.

    Returns:
        dict[str, object]: JSON-serializable smoke summary including connector,
        backend, and runtime metrics when available.
    """

    mode_status = require_mode()
    prepare_repo_imports(
        repo_root_arg=repo_root,
        env=env,
        script_path=Path(__file__),
        home_dir=home_dir,
    )
    visual_factory = visual_factory or _import_visual_stim()
    output_root.mkdir(parents=True, exist_ok=True)
    session_info = build_visual_grating_session_info(
        output_root=output_root,
        repo_root=repo_root,
        env=env,
        home_dir=home_dir,
    )
    visual = visual_factory(session_info)
    summary: dict[str, object] = {
        "visual_backend": "drm",
        "visual_connector": "HDMI-A-2",
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    try:
        visual.show_grating("go_grating")
        sleep_fn(float(hold_s))
        visual.show_grating("nogo_grating")
        sleep_fn(float(hold_s))

        runtime = getattr(visual, "_runtime", None)
        if runtime is not None and hasattr(runtime, "get_metrics"):
            metrics = runtime.get_metrics()
        else:
            metrics = dict(getattr(visual, "_metrics", {}))
        summary.update(
            {
                "status": "ok",
                "play_count": int(metrics.get("play_count", 0)),
                "current_label": metrics.get("current_label"),
                "timing_entries": len(list(metrics.get("timing_log", []))),
            }
        )
        return summary
    finally:
        visual.close()


def _import_visual_stim() -> Callable[..., Any]:
    """Import the real visual runtime after repo-root resolution.

    Returns:
        Callable[..., Any]: ``VisualStim`` constructor compatible with
        ``run_visual_grating_hdmi_a2_smoke``.
    """

    from box_runtime.visual_stimuli.visualstim import VisualStim

    return VisualStim


def main(argv: list[str] | None = None) -> int:
    """Run the grating smoke as a command-line tool.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Zero on success, nonzero on failure.
    """

    parser = argparse.ArgumentParser(description="Run a headless drifting-grating smoke on HDMI-A-2.")
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/behavbox_debug"))
    parser.add_argument("--hold-s", type=float, default=1.0)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_visual_grating_hdmi_a2_smoke(
            output_root=args.output_root,
            hold_s=args.hold_s,
            repo_root=args.repo_root,
        )
    except HeadlessDisplayModeError as exc:
        print(exc, file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Visual grating smoke failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Visual grating smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Visual grating smoke passed.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
