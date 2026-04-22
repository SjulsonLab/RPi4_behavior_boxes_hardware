"""Dual-output setup preview smoke with one grating timed by a worker process."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import multiprocessing as mp
import sys
import time
from typing import Any, Callable, Mapping

import numpy as np

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


class CameraSetupPreviewPlusGratingsMultiprocessSmokeFailure(RuntimeError):
    """Raised when the multiprocess preview-plus-grating smoke fails.

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


def compile_setup_go_grating(
    *,
    repo_root: Path,
    resolution_px: tuple[int, int],
    refresh_hz: float,
    duration_s: float,
    degrees_subtended: float = 80.0,
) -> Any:
    """Load and compile the default go grating for the setup smoke.

    Args:
        repo_root: BehavBox repository root directory.
        resolution_px: Stimulus output resolution as ``(width_px, height_px)``.
        refresh_hz: Stimulus output refresh rate in Hz.
        duration_s: Requested drifting-grating duration in seconds.
        degrees_subtended: Horizontal display extent in visual degrees.

    Returns:
        Any: Compiled grating exposing ``frames``, ``frame_interval_s``, and
        ``spec`` metadata.
    """

    from box_runtime.visual_stimuli.visual_runtime import compile_grating, load_grating_spec

    stimuli_root = repo_root / "box_runtime" / "visual_stimuli"
    spec = load_grating_spec(stimuli_root / "go_grating.yaml")
    spec = replace(spec, duration_s=float(duration_s))
    return compile_grating(
        spec=spec,
        resolution_px=resolution_px,
        refresh_hz=float(refresh_hz),
        degrees_subtended=float(degrees_subtended),
    )


def _runtime_context() -> mp.context.BaseContext:
    """Return a multiprocessing context suitable for the current host.

    Returns:
        mp.context.BaseContext: Multiprocessing context using ``fork`` on Linux
        when available, otherwise ``spawn``.
    """

    methods = mp.get_all_start_methods()
    if sys.platform.startswith("linux") and "fork" in methods:
        return mp.get_context("fork")
    return mp.get_context("spawn")


def _grating_worker_entry(
    start_event: Any,
    stop_event: Any,
    active_value: Any,
    frame_index_value: Any,
    update_count_value: Any,
    frame_count: int,
    frame_interval_s: float,
    duration_s: float,
) -> None:
    """Advance a shared grating frame index after one start signal.

    Args:
        start_event: Multiprocessing event set once to begin the grating.
        stop_event: Multiprocessing event used to request worker shutdown.
        active_value: Shared boolean-ish value indicating stimulus activity.
        frame_index_value: Shared integer value containing the current frame index.
        update_count_value: Shared integer value counting distinct frame advances.
        frame_count: Number of precompiled grating frames.
        frame_interval_s: Duration of one grating frame in seconds.
        duration_s: Requested active stimulus duration in seconds.

    Returns:
        None.
    """

    while not stop_event.is_set():
        if start_event.wait(timeout=0.01):
            break
    if stop_event.is_set():
        return

    with active_value.get_lock():
        active_value.value = 1

    started_s = time.monotonic()
    last_index = -1
    safe_interval_s = max(float(frame_interval_s), 1e-6)
    safe_duration_s = max(float(duration_s), 0.0)

    try:
        while not stop_event.is_set():
            elapsed_s = time.monotonic() - started_s
            if elapsed_s >= safe_duration_s:
                break

            frame_index = min(int(elapsed_s / safe_interval_s), max(int(frame_count) - 1, 0))
            if frame_index != last_index:
                with frame_index_value.get_lock():
                    frame_index_value.value = int(frame_index)
                with update_count_value.get_lock():
                    update_count_value.value += 1
                last_index = int(frame_index)
            time.sleep(min(safe_interval_s / 4.0, 0.001))
    finally:
        with active_value.get_lock():
            active_value.value = 0


class MultiprocessGratingWorker:
    """Own one worker process that advances shared grating timing state.

    Args:
        frame_count: Number of precompiled grating frames.
        frame_interval_s: Duration of one frame in seconds.
        duration_s: Requested active stimulus duration in seconds.
        mp_context: Optional multiprocessing context.

    Returns:
        MultiprocessGratingWorker: Handle used by the parent preview loop.
    """

    def __init__(
        self,
        *,
        frame_count: int,
        frame_interval_s: float,
        duration_s: float,
        mp_context: mp.context.BaseContext | None = None,
    ) -> None:
        self._ctx = mp_context or _runtime_context()
        self._start_event = self._ctx.Event()
        self._stop_event = self._ctx.Event()
        self._active_value = self._ctx.Value("b", False)
        self._frame_index_value = self._ctx.Value("i", 0)
        self._update_count_value = self._ctx.Value("i", 0)
        self._started = False
        self._process = self._ctx.Process(
            target=_grating_worker_entry,
            args=(
                self._start_event,
                self._stop_event,
                self._active_value,
                self._frame_index_value,
                self._update_count_value,
                int(frame_count),
                float(frame_interval_s),
                float(duration_s),
            ),
            daemon=True,
        )
        self._process.start()

    def start(self) -> None:
        """Send the single start signal to the worker.

        Returns:
            None.
        """

        if self._started:
            return
        self._started = True
        self._start_event.set()

    def snapshot(self) -> dict[str, object]:
        """Return the current shared worker state.

        Returns:
            dict[str, object]: JSON-serializable state including activity,
            current frame index, and the worker-side update count.
        """

        return {
            "active": bool(self._active_value.value),
            "frame_index": int(self._frame_index_value.value),
            "update_count": int(self._update_count_value.value),
        }

    def diagnostics(self) -> dict[str, object]:
        """Return lightweight worker diagnostics.

        Returns:
            dict[str, object]: JSON-serializable worker-process diagnostics.
        """

        return {
            "worker_pid": self._process.pid,
            "worker_started": bool(self._started),
            "worker_alive": bool(self._process.is_alive()),
        }

    def close(self) -> None:
        """Stop the worker process and release shared resources.

        Returns:
            None.
        """

        self._stop_event.set()
        self._start_event.set()
        if self._process.is_alive():
            self._process.join(timeout=2.0)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=1.0)


def run_camera_setup_preview_plus_gratings_multiprocess_smoke(
    *,
    output_root: Path,
    duration_s: float = 5.0,
    preview_connector: str = "HDMI-A-1",
    stimulus_connector: str = "HDMI-A-2",
    resolution_px: tuple[int, int] = (1024, 600),
    preview_source_mode: str = "rgb_main",
    frame_rate_hz: float = 30.0,
    stimulus_duration_s: float | None = None,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | str | None = None,
    require_mode: Callable[[], Any] = require_headless_console_mode,
    frame_source_factory: Callable[..., Any] = SharedCameraFrameSource,
    shared_controller_factory: Callable[..., Any] = SharedDrmController,
    compile_stimulus_fn: Callable[..., Any] = compile_setup_go_grating,
    worker_factory: Callable[..., Any] = MultiprocessGratingWorker,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Run a dual-output setup preview smoke with one worker-timed grating.

    Args:
        output_root: Directory under which smoke outputs may be stored.
        duration_s: Requested preview loop duration in seconds.
        preview_connector: DRM connector name for the camera preview output.
        stimulus_connector: DRM connector name for the grating output.
        resolution_px: Preview output resolution as ``(width_px, height_px)``.
        preview_source_mode: Camera preview source mode passed through to
            ``SharedCameraFrameSource``.
        frame_rate_hz: Target preview loop rate in frames per second.
        stimulus_duration_s: Optional active grating duration in seconds. When
            ``None``, the smoke uses ``duration_s``.
        repo_root: Optional explicit repository root path.
        env: Optional environment-variable mapping used for repo-root detection.
        home_dir: Optional home-directory path used for repo-root fallback.
        require_mode: Zero-argument callable validating headless console mode.
        frame_source_factory: Factory returning a
            ``SharedCameraFrameSource``-compatible object.
        shared_controller_factory: Factory returning a
            ``SharedDrmController``-compatible object.
        compile_stimulus_fn: Callable compiling one grating for the stimulus
            output.
        worker_factory: Factory returning a worker with ``start``, ``snapshot``,
            and ``close`` methods.
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

    effective_stimulus_duration_s = (
        float(duration_s) if stimulus_duration_s is None else float(stimulus_duration_s)
    )
    summary: dict[str, object] = {
        "preview_connector": str(preview_connector),
        "stimulus_connector": str(stimulus_connector),
        "camera_preview_source_mode": str(preview_source_mode),
        "preview_target_fps": float(frame_rate_hz),
        "stimulus_duration_s": float(effective_stimulus_duration_s),
        "mode_status": getattr(mode_status, "describe", lambda: str(mode_status))(),
    }

    frame_source = None
    controller = None
    worker = None
    frame_count = 0
    stimulus_frame_update_count = 0
    stimulus_worker_update_count = 0
    capture_time_total_s = 0.0
    frame_prepare_time_total_s = 0.0
    display_time_total_s = 0.0
    stimulus_display_time_total_s = 0.0
    failure_stage = "frame_source_init"
    started_s: float | None = None
    stimulus_started = False
    stimulus_was_active = False
    stimulus_reset_to_gray = False
    last_displayed_stimulus_index: int | None = None
    compiled_stimulus = None
    background_gray_u8 = 127

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
            stimulus_connector=str(stimulus_connector),
        )

        failure_stage = "stimulus_compile"
        compiled_stimulus = compile_stimulus_fn(
            repo_root=resolved_repo_root,
            resolution_px=controller.stimulus.resolution_px,
            refresh_hz=controller.stimulus.refresh_hz,
            duration_s=float(effective_stimulus_duration_s),
        )
        background_gray_u8 = int(getattr(compiled_stimulus.spec, "background_gray_u8", 127))
        stimulus_frames = np.asarray(compiled_stimulus.frames, dtype=np.uint8)
        stimulus_frame_count = int(stimulus_frames.shape[0])
        summary["stimulus_name"] = str(getattr(compiled_stimulus.spec, "name", "go_grating"))

        failure_stage = "worker_init"
        worker = worker_factory(
            frame_count=stimulus_frame_count,
            frame_interval_s=float(compiled_stimulus.frame_interval_s),
            duration_s=float(effective_stimulus_duration_s),
        )

        failure_stage = "stimulus_gray_init"
        controller.stimulus.display_gray(background_gray_u8)
        summary["preview_drm_diagnostics"] = controller.preview.diagnostics()
        summary["stimulus_drm_diagnostics"] = controller.stimulus.diagnostics()
        if hasattr(worker, "diagnostics"):
            summary["stimulus_worker_diagnostics"] = worker.diagnostics()

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

            if stimulus_started:
                worker_state = worker.snapshot()
                stimulus_worker_update_count = max(
                    stimulus_worker_update_count,
                    int(worker_state.get("update_count", 0)),
                )
                stimulus_active = bool(worker_state.get("active", False))
                stimulus_index = int(worker_state.get("frame_index", 0))

                if stimulus_active:
                    stimulus_was_active = True
                    if stimulus_index != last_displayed_stimulus_index:
                        failure_stage = "preview_loop_stimulus_display"
                        stimulus_display_started_s = monotonic_fn()
                        controller.stimulus.display_gray_frame(
                            np.asarray(stimulus_frames[stimulus_index], dtype=np.uint8)
                        )
                        stimulus_display_time_total_s += max(
                            0.0, monotonic_fn() - stimulus_display_started_s
                        )
                        stimulus_frame_update_count += 1
                        last_displayed_stimulus_index = stimulus_index
                elif stimulus_was_active and not stimulus_reset_to_gray:
                    failure_stage = "preview_loop_stimulus_display"
                    stimulus_display_started_s = monotonic_fn()
                    controller.stimulus.display_gray(background_gray_u8)
                    stimulus_display_time_total_s += max(
                        0.0, monotonic_fn() - stimulus_display_started_s
                    )
                    stimulus_reset_to_gray = True

            failure_stage = "preview_loop_display"
            display_started_s = monotonic_fn()
            controller.preview.display_rgb_frame(preview_frame)
            display_time_total_s += max(0.0, monotonic_fn() - display_started_s)
            frame_count += 1

            if not stimulus_started:
                failure_stage = "worker_start"
                worker.start()
                stimulus_started = True
                for _ in range(2):
                    worker_state = worker.snapshot()
                    stimulus_worker_update_count = max(
                        stimulus_worker_update_count,
                        int(worker_state.get("update_count", 0)),
                    )
                    stimulus_active = bool(worker_state.get("active", False))
                    stimulus_index = int(worker_state.get("frame_index", 0))
                    if not stimulus_active:
                        continue
                    if stimulus_index != last_displayed_stimulus_index:
                        failure_stage = "preview_loop_stimulus_display"
                        stimulus_display_started_s = monotonic_fn()
                        controller.stimulus.display_gray_frame(
                            np.asarray(stimulus_frames[stimulus_index], dtype=np.uint8)
                        )
                        stimulus_display_time_total_s += max(
                            0.0, monotonic_fn() - stimulus_display_started_s
                        )
                        stimulus_frame_update_count += 1
                        stimulus_was_active = True
                        last_displayed_stimulus_index = stimulus_index
                    break

            remaining_s = deadline_s - monotonic_fn()
            if remaining_s <= 0.0:
                break
            sleep_fn(min(interval_s, remaining_s))

        if worker is not None:
            worker_state = worker.snapshot()
            stimulus_worker_update_count = max(
                stimulus_worker_update_count,
                int(worker_state.get("update_count", 0)),
            )

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
                "stimulus_display_time_total_s": float(stimulus_display_time_total_s),
                "stimulus_frame_update_count": int(stimulus_frame_update_count),
                "stimulus_worker_update_count": int(stimulus_worker_update_count),
                "capture_time_avg_s": _average(capture_time_total_s, frame_count),
                "frame_prepare_time_avg_s": _average(frame_prepare_time_total_s, frame_count),
                "display_time_avg_s": _average(display_time_total_s, frame_count),
                "stimulus_display_time_avg_s": _average(
                    stimulus_display_time_total_s,
                    stimulus_frame_update_count if stimulus_frame_update_count > 0 else 1,
                ),
                "preview_drm_diagnostics": controller.preview.diagnostics(),
                "stimulus_drm_diagnostics": controller.stimulus.diagnostics(),
            }
        )
        if hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
        if worker is not None and hasattr(worker, "diagnostics"):
            summary["stimulus_worker_diagnostics"] = worker.diagnostics()
        return summary
    except Exception as exc:
        summary["failure_stage"] = failure_stage
        summary["preview_frame_count"] = int(frame_count)
        summary["capture_time_total_s"] = float(capture_time_total_s)
        summary["frame_prepare_time_total_s"] = float(frame_prepare_time_total_s)
        summary["display_time_total_s"] = float(display_time_total_s)
        summary["stimulus_display_time_total_s"] = float(stimulus_display_time_total_s)
        summary["stimulus_frame_update_count"] = int(stimulus_frame_update_count)
        summary["stimulus_worker_update_count"] = int(stimulus_worker_update_count)
        if frame_source is not None and hasattr(frame_source, "diagnostics"):
            summary.update(frame_source.diagnostics())
        if controller is not None:
            diagnostics = controller.diagnostics()
            summary["preview_drm_diagnostics"] = diagnostics.get("preview", {})
            summary["stimulus_drm_diagnostics"] = diagnostics.get("stimulus", {})
        if worker is not None and hasattr(worker, "diagnostics"):
            summary["stimulus_worker_diagnostics"] = worker.diagnostics()
        raise CameraSetupPreviewPlusGratingsMultiprocessSmokeFailure(
            f"{type(exc).__name__}: {exc}",
            summary=summary,
        ) from exc
    finally:
        if worker is not None:
            worker.close()
        if controller is not None:
            controller.close()
        if frame_source is not None:
            frame_source.close()


def main(argv: list[str] | None = None) -> int:
    """Run the multiprocess preview-plus-grating smoke as a CLI command.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Zero on success, nonzero on failure.
    """

    parser = argparse.ArgumentParser(
        description="Run a dual-output setup preview smoke with one worker-timed grating."
    )
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/behavbox_debug"))
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--stimulus-duration-s", type=float, default=None)
    parser.add_argument("--preview-connector", type=str, default="HDMI-A-1")
    parser.add_argument("--stimulus-connector", type=str, default="HDMI-A-2")
    parser.add_argument("--preview-source-mode", choices=("rgb_main", "yuv_lores"), default="rgb_main")
    parser.add_argument("--frame-rate-hz", type=float, default=30.0)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_camera_setup_preview_plus_gratings_multiprocess_smoke(
            output_root=args.output_root,
            duration_s=args.duration_s,
            stimulus_duration_s=args.stimulus_duration_s,
            preview_connector=args.preview_connector,
            stimulus_connector=args.stimulus_connector,
            preview_source_mode=args.preview_source_mode,
            frame_rate_hz=args.frame_rate_hz,
            repo_root=args.repo_root,
        )
    except HeadlessDisplayModeError as exc:
        print(exc, file=sys.stderr)
        return 2
    except CameraSetupPreviewPlusGratingsMultiprocessSmokeFailure as exc:
        print(f"Camera setup preview plus multiprocess grating smoke failed: {exc}", file=sys.stderr)
        for key, value in exc.summary.items():
            print(f"{key}: {value}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Camera setup preview plus multiprocess grating smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Camera setup preview plus multiprocess grating smoke passed.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
