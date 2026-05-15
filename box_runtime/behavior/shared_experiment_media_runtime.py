"""Unified one-Pi experiment media runtime for preview, recording, and stimuli.

Data contracts:

- ``session_info`` is a mapping containing at least ``dir_name`` and optional
  media keys such as ``camera_ids``, ``vis_gratings``, ``gray_level``,
  ``camera_preview_connector``, and ``visual_display_connector``.
- Preview uses exactly one camera identifier, typically ``"camera0"``.
- Preview frames come from a frame source exposing dmabuf-backed
  ``capture_frame_for_preview()`` and ``release_frame(frame)`` methods.
- Stimuli are compiled to grayscale ``uint8`` frame stacks with shape
  ``(n_frames, height_px, width_px)`` and played on the shared DRM stimulus
  output.
- ``runtime_state()`` returns a JSON-serializable mapping keyed by camera id.
"""

from __future__ import annotations

from collections import deque
import logging
from pathlib import Path
import threading
import time
from typing import Any, Callable

try:
    from debug.shared_camera_recording_dmabuf_source import SharedCameraRecordingDmabufSource
    from debug.shared_drm_debug import SharedDrmController
except ModuleNotFoundError:
    from shared_camera_recording_dmabuf_source import SharedCameraRecordingDmabufSource
    from shared_drm_debug import SharedDrmController

from box_runtime.behavior.shared_dual_camera_recording_source import (
    SharedDualCameraRecordingSource,
)
from box_runtime.visual_stimuli.visual_runtime import compile_grating, load_grating_spec


def _normalize_camera_ids(session_info: dict[str, Any]) -> list[str]:
    """Resolve the configured ordered camera identifier list.

    Args:
        session_info: Session configuration mapping.

    Returns:
        list[str]: Ordered semantic camera ids such as ``["camera0"]``.
    """

    configured = session_info.get("camera_ids", ["camera0"])
    if isinstance(configured, str):
        return [configured]
    return [str(camera_id) for camera_id in configured]


def _normalize_gray_level(value: Any) -> int:
    """Convert one gray-level setting into uint8 display units.

    Args:
        value: Gray level as either uint8-like integer in ``[0, 255]`` or
            normalized float in ``[0.0, 1.0]``.

    Returns:
        int: Gray level in uint8 display units.
    """

    if isinstance(value, float) and 0.0 <= value <= 1.0:
        return int(round(value * 255.0))
    gray_level = int(value)
    return max(0, min(gray_level, 255))


def _normalize_positive_float(value: Any, *, default: float) -> float:
    """Resolve a positive floating-point configuration value.

    Args:
        value: Raw configuration value or ``None``.
        default: Fallback value used when ``value`` is missing or invalid.

    Returns:
        float: Positive scalar value.
    """

    if value is None:
        return float(default)
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return float(default)
    return resolved if resolved > 0.0 else float(default)


def _stimulus_aliases(grating_path: Path, stimulus_name: str) -> tuple[str, ...]:
    """Return all accepted lookup aliases for one grating spec.

    Args:
        grating_path: YAML specification path.
        stimulus_name: Canonical stimulus name from the YAML payload.

    Returns:
        tuple[str, ...]: Ordered aliases accepted by ``show_grating``.
    """

    aliases: list[str] = []
    for alias in (stimulus_name, grating_path.stem, grating_path.name):
        if alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


class SharedDrmExperimentMediaRuntime:
    """Run one shared-DRM experiment session with preview, recording, and stimuli.

    Args:
        session_info: Session configuration mapping.
        state_callback: Optional callback receiving the full runtime-state
            mapping after every state transition.
        frame_source_factory: Factory returning a recording-capable dmabuf
            frame source.
        controller_factory: Factory returning a shared DRM controller exposing
            ``preview`` and ``stimulus`` outputs.
        monotonic_fn: Monotonic clock returning seconds.
        sleep_fn: Sleep callable taking seconds.
    """

    def __init__(
        self,
        session_info: dict[str, Any],
        *,
        state_callback: Callable[[dict[str, dict[str, Any]]], None] | None = None,
        frame_source_factory: Callable[..., Any] = SharedCameraRecordingDmabufSource,
        dual_camera_source_factory: Callable[..., Any] = SharedDualCameraRecordingSource,
        controller_factory: Callable[..., Any] = SharedDrmController,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session_info = session_info
        self._state_callback = state_callback
        self._frame_source_factory = frame_source_factory
        self._dual_camera_source_factory = dual_camera_source_factory
        self._controller_factory = controller_factory
        self._monotonic_fn = monotonic_fn
        self._sleep_fn = sleep_fn

        camera_ids = _normalize_camera_ids(session_info)
        self.camera_ids = list(camera_ids)
        self.preview_camera_id = str(
            session_info.get("experiment_media_preview_camera_id", camera_ids[0] if camera_ids else "camera0")
        )
        self.recording_camera_ids = [
            camera_id for camera_id in self.camera_ids if str(camera_id) != self.preview_camera_id
        ]
        if len(self.recording_camera_ids) > 1:
            raise ValueError("shared experiment media currently supports at most one recording-only camera")
        self.preview_connector = str(session_info.get("camera_preview_connector", "HDMI-A-1"))
        self.stimulus_connector = str(session_info.get("visual_display_connector", "HDMI-A-2"))
        self.gray_level_u8 = _normalize_gray_level(session_info.get("gray_level", 127))
        self.preview_source_mode = str(
            session_info.get("experiment_media_preview_source_mode", "dmabuf_main")
        ).strip().lower()
        self.request_mode = str(session_info.get("experiment_media_request_mode", "next")).strip().lower()
        self.frame_rate_hz = _normalize_positive_float(
            session_info.get("camera_preview_max_hz", session_info.get("camera_fps", 30.0)),
            default=30.0,
        )
        self.sensor_mode = int(session_info.get("experiment_media_sensor_mode", session_info.get("camera_sensor_mode", 1)))
        self.overlay_enabled = bool(
            session_info.get("experiment_media_overlay_enabled", session_info.get("camera_timestamp_overlay_enabled", True))
        )
        self._default_degrees_subtended = _normalize_positive_float(
            session_info.get("visual_display_degrees_subtended"),
            default=80.0,
        )
        self.storage_root = Path(self.session_info["dir_name"]) / "camera_recordings"
        self.video_path = self.storage_root / f"{self.preview_camera_id}_preview_recording_output.h264"
        self.timestamp_csv_path = self.storage_root / f"{self.preview_camera_id}_preview_recording_timestamp.csv"

        self.controller: Any | None = None
        self.frame_source: Any | None = None
        self._compiled_stimuli: dict[str, Any] = {}
        self._alias_map: dict[str, str] = {}
        self._queued_gratings: deque[str] = deque()
        self._queue_lock = threading.Lock()
        self._stimulus_output_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._worker_error: BaseException | None = None
        self._worker_error_phase: str | None = None
        self._current_preview_frame: Any | None = None
        self._is_prepared = False
        self._session_active = False
        self._stimulus_generation = 0

    # User-facing methods
    def prepare(self) -> None:
        """Reserve DRM outputs and compile session gratings.

        Returns:
            None.
        """

        if self.preview_source_mode != "dmabuf_main":
            raise ValueError(
                "shared experiment media runtime currently supports only experiment_media_preview_source_mode='dmabuf_main'"
            )
        if self.request_mode not in {"next", "latest"}:
            raise ValueError("experiment_media_request_mode must be 'next' or 'latest'")
        if self._is_prepared:
            self._publish_state()
            return

        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.controller = self._controller_factory(
            preview_connector=self.preview_connector,
            stimulus_connector=self.stimulus_connector,
        )
        self._compile_session_gratings()
        if self.controller.stimulus is not None:
            self.controller.stimulus.display_gray(self.gray_level_u8)
        logging.info(";%s;[initialization];screen_opened", time.time())
        self._is_prepared = True
        self._publish_state()

    def start_session(self, owner: str = "automated") -> None:
        """Start live preview, recording, and worker-driven stimulus playback.

        Args:
            owner: Informational owner label for the session start.

        Returns:
            None.
        """

        del owner
        self.prepare()
        self._raise_if_worker_failed()
        if self._session_active:
            self._publish_state()
            return
        assert self.controller is not None

        if self.recording_camera_ids:
            self.frame_source = self._dual_camera_source_factory(
                output_root=self.storage_root,
                preview_camera_id=self.preview_camera_id,
                recording_camera_id=str(self.recording_camera_ids[0]),
                resolution_px=tuple(self.controller.preview.resolution_px),
                frame_rate_hz=float(self.frame_rate_hz),
                sensor_mode=int(self.sensor_mode),
                request_mode=self.request_mode,
                preview_overlay_enabled=self.overlay_enabled,
                recording_overlay_enabled=self.overlay_enabled,
            )
        else:
            self.frame_source = self._frame_source_factory(
                camera_id=self.preview_camera_id,
                resolution_px=tuple(self.controller.preview.resolution_px),
                video_path=self.video_path,
                timestamp_csv_path=self.timestamp_csv_path,
                frame_rate_hz=float(self.frame_rate_hz),
                sensor_mode=int(self.sensor_mode),
                request_mode=self.request_mode,
                overlay_enabled=self.overlay_enabled,
            )
        self._queued_gratings.clear()
        self._stop_event.clear()
        self._worker_error = None
        self._worker_error_phase = None
        self._worker_thread = threading.Thread(
            target=self._worker_main,
            name=f"shared-experiment-media-{self.preview_camera_id}",
            daemon=True,
        )
        self._worker_thread.start()
        self._session_active = True
        self._publish_state()

    def stop_session(self) -> None:
        """Stop live preview/recording worker while keeping prepared DRM state.

        Returns:
            None.
        """

        worker_error: BaseException | None = None
        if self._session_active:
            self._stop_event.set()
            if self._worker_thread is not None:
                self._worker_thread.join(timeout=5.0)
            worker_error = self._worker_error
        self._worker_thread = None
        self._session_active = False

        self._release_current_preview_frame()
        frame_source = self.frame_source
        self.frame_source = None
        if frame_source is not None:
            frame_source.close()
        self._safe_restore_gray()
        self._publish_state()
        if worker_error is not None:
            raise RuntimeError(
                f"shared experiment media worker failed during {self._worker_error_phase or 'unknown_phase'}: {worker_error}"
            ) from worker_error

    def close(self) -> None:
        """Close session worker and release DRM/controller state.

        Returns:
            None.
        """

        try:
            if self._session_active:
                self.stop_session()
        finally:
            controller = self.controller
            self.controller = None
            if controller is not None:
                controller.close()
            self._is_prepared = False
            self._publish_state()

    def show_grating(self, grating_name: str) -> None:
        """Queue one compiled grating for playback on the stimulus output.

        Args:
            grating_name: Stimulus alias, filename stem, filename, or canonical
                spec ``name``.

        Returns:
            None.

        Raises:
            KeyError: If the stimulus name is unknown.
        """

        canonical_name = self._alias_map.get(str(grating_name))
        if canonical_name is None:
            raise KeyError(f"unknown visual stimulus {grating_name!r}")
        with self._queue_lock:
            self._queued_gratings.append(canonical_name)

    def display_gray(self, gray_level_u8: int | None = None) -> None:
        """Display one neutral gray frame on the shared stimulus output.

        Args:
            gray_level_u8: Optional uint8 gray level in ``[0, 255]``. When
                ``None``, use the session default neutral gray level.

        Returns:
            None.
        """

        controller = self.controller
        if controller is None or controller.stimulus is None:
            raise RuntimeError("visual stimulus runtime is unavailable before prepare().")
        resolved_gray_level = self.gray_level_u8 if gray_level_u8 is None else _normalize_gray_level(gray_level_u8)
        with self._queue_lock:
            self._queued_gratings.clear()
            self._stimulus_generation += 1
        self._display_gray_now(resolved_gray_level)

    def runtime_state(self) -> dict[str, dict[str, Any]]:
        """Return the current JSON-serializable per-camera runtime state.

        Returns:
            dict[str, dict[str, Any]]: Runtime-state mapping keyed by camera id.
        """

        preview_diagnostics = {}
        stimulus_diagnostics = {}
        if self.controller is not None:
            preview_diagnostics = dict(self.controller.preview.diagnostics())
            if self.controller.stimulus is not None:
                stimulus_diagnostics = dict(self.controller.stimulus.diagnostics())

        frame_source_diagnostics = {}
        if self.frame_source is not None and hasattr(self.frame_source, "diagnostics"):
            frame_source_diagnostics = dict(self.frame_source.diagnostics())

        runtime_state = {
            self.preview_camera_id: {
                "camera_id": self.preview_camera_id,
                "prepared": self._is_prepared,
                "recording": self._session_active,
                "preview_active": self._session_active,
                "preview_mode": "shared_drm_dmabuf",
                "preview_connector": self.preview_connector,
                "preview_available": False,
                "preview_url": None,
                "storage_root": str(self.storage_root),
                "video_path": str(self.video_path),
                "timestamp_csv_path": str(self.timestamp_csv_path),
                "camera_preview_source_mode": self.preview_source_mode,
                "request_mode": self.request_mode,
                "overlay_enabled": self.overlay_enabled,
                "drm_diagnostics": preview_diagnostics,
                "stimulus_drm_diagnostics": stimulus_diagnostics,
                "frame_source_diagnostics": frame_source_diagnostics,
                "worker_error_phase": self._worker_error_phase,
                "worker_error_message": None if self._worker_error is None else str(self._worker_error),
            }
        }
        if self.recording_camera_ids:
            recording_camera_id = str(self.recording_camera_ids[0])
            runtime_state[recording_camera_id] = {
                "camera_id": recording_camera_id,
                "prepared": self._is_prepared,
                "recording": self._session_active,
                "preview_active": False,
                "preview_mode": "off",
                "preview_connector": None,
                "preview_available": False,
                "preview_url": None,
                "storage_root": str(self.storage_root),
                "video_path": str(self.storage_root / f"{recording_camera_id}_recording_output.h264"),
                "timestamp_csv_path": str(self.storage_root / f"{recording_camera_id}_recording_timestamp.csv"),
                "camera_preview_source_mode": None,
                "request_mode": self.request_mode,
                "overlay_enabled": self.overlay_enabled,
                "drm_diagnostics": {},
                "stimulus_drm_diagnostics": {},
                "frame_source_diagnostics": frame_source_diagnostics,
                "worker_error_phase": self._worker_error_phase,
                "worker_error_message": None if self._worker_error is None else str(self._worker_error),
            }

        self._apply_frame_source_diagnostics(runtime_state, frame_source_diagnostics)
        return runtime_state

    def _apply_frame_source_diagnostics(
        self,
        runtime_state: dict[str, dict[str, Any]],
        frame_source_diagnostics: dict[str, object],
    ) -> None:
        """Overlay per-camera recording diagnostics onto the runtime state.

        Args:
            runtime_state: Mutable per-camera runtime state dictionary.
            frame_source_diagnostics: Flat diagnostics returned by the current
                frame-source helper.

        Returns:
            None.
        """

        if not frame_source_diagnostics:
            return

        preview_state = runtime_state.get(self.preview_camera_id)
        if preview_state is not None:
            preview_state["video_path"] = str(
                frame_source_diagnostics.get("camera0_video_path", frame_source_diagnostics.get("video_path", preview_state["video_path"]))
            )
            preview_state["timestamp_csv_path"] = str(
                frame_source_diagnostics.get(
                    "camera0_timestamp_csv_path",
                    frame_source_diagnostics.get("timestamp_csv_path", preview_state["timestamp_csv_path"]),
                )
            )
            preview_state["overlay_enabled"] = bool(
                frame_source_diagnostics.get("camera0_overlay_enabled", frame_source_diagnostics.get("overlay_enabled", preview_state["overlay_enabled"]))
            )

        for recording_camera_id in self.recording_camera_ids:
            recording_state = runtime_state.get(str(recording_camera_id))
            if recording_state is None:
                continue
            camera_key = str(recording_camera_id)
            recording_state["video_path"] = str(
                frame_source_diagnostics.get(f"{camera_key}_video_path", recording_state["video_path"])
            )
            recording_state["timestamp_csv_path"] = str(
                frame_source_diagnostics.get(
                    f"{camera_key}_timestamp_csv_path",
                    recording_state["timestamp_csv_path"],
                )
            )
            recording_state["overlay_enabled"] = bool(
                frame_source_diagnostics.get(f"{camera_key}_overlay_enabled", recording_state["overlay_enabled"])
            )

    # Helper methods
    def _compile_session_gratings(self) -> None:
        """Load and compile all grating specs referenced by ``session_info``.

        Returns:
            None.
        """

        assert self.controller is not None
        compiled: dict[str, Any] = {}
        alias_map: dict[str, str] = {}
        for grating_file in self.session_info.get("vis_gratings", []):
            grating_path = Path(grating_file).expanduser().resolve()
            spec = load_grating_spec(grating_path)
            compiled_spec = compile_grating(
                spec=spec,
                resolution_px=tuple(self.controller.stimulus.resolution_px),
                refresh_hz=float(self.controller.stimulus.refresh_hz),
                degrees_subtended=spec.degrees_subtended or self._default_degrees_subtended,
            )
            compiled[spec.name] = compiled_spec
            for alias in _stimulus_aliases(grating_path, spec.name):
                existing = alias_map.get(alias)
                if existing is not None and existing != spec.name:
                    raise ValueError(f"duplicate visual stimulus alias {alias!r}")
                alias_map[alias] = spec.name
        self._compiled_stimuli = compiled
        self._alias_map = alias_map

    def _publish_state(self) -> None:
        if self._state_callback is not None:
            self._state_callback(self.runtime_state())

    def _raise_if_worker_failed(self) -> None:
        if self._worker_error is None:
            return
        raise RuntimeError(
            f"shared experiment media worker failed during {self._worker_error_phase or 'unknown_phase'}: {self._worker_error}"
        ) from self._worker_error

    def _worker_main(self) -> None:
        """Run the preview and queued-stimulus loop until stopped.

        Returns:
            None.
        """

        try:
            while not self._stop_event.is_set():
                queued_name = self._pop_next_grating()
                if queued_name is None:
                    self._display_preview_once()
                    continue
                self._play_compiled_grating(queued_name)
        except BaseException as exc:
            self._worker_error = exc
            self._worker_error_phase = self._worker_error_phase or "worker_loop"
            logging.exception("shared experiment media worker failed")
        finally:
            self._release_current_preview_frame()

    def _display_preview_once(self) -> None:
        """Capture and display one preview frame while preserving request lifetime.

        Returns:
            None.
        """

        assert self.frame_source is not None
        assert self.controller is not None
        self._worker_error_phase = "preview_capture"
        frame = self.frame_source.capture_frame_for_preview()
        self._worker_error_phase = "preview_display"
        try:
            self.controller.preview.display_dmabuf_frame(frame)
        except Exception:
            self.frame_source.release_frame(frame)
            raise
        previous_frame = self._current_preview_frame
        self._current_preview_frame = frame
        if previous_frame is not None:
            self.frame_source.release_frame(previous_frame)

    def _play_compiled_grating(self, canonical_name: str) -> None:
        """Display one precompiled grating while keeping preview updates alive.

        Args:
            canonical_name: Canonical stimulus name present in
                ``self._compiled_stimuli``.

        Returns:
            None.
        """

        assert self.controller is not None
        compiled = self._compiled_stimuli[canonical_name]
        frame_interval_s = float(compiled.frame_interval_s)
        playback_generation = self._stimulus_generation
        for frame_gray_u8 in compiled.frames:
            if self._stop_event.is_set() or self._stimulus_generation != playback_generation:
                break
            self._worker_error_phase = f"stimulus_display:{canonical_name}"
            with self._stimulus_output_lock:
                self.controller.stimulus.display_gray_frame(frame_gray_u8)
            frame_deadline_s = self._monotonic_fn() + max(frame_interval_s, 0.0)
            while (
                not self._stop_event.is_set()
                and self._stimulus_generation == playback_generation
                and self._monotonic_fn() < frame_deadline_s
            ):
                self._display_preview_once()
            if self._stimulus_generation != playback_generation:
                break
            remaining_s = frame_deadline_s - self._monotonic_fn()
            if remaining_s > 0.0:
                self._sleep_fn(remaining_s)
        self._safe_restore_gray()

    def _safe_restore_gray(self) -> None:
        """Best-effort restore of the neutral stimulus gray framebuffer.

        Returns:
            None.
        """

        controller = self.controller
        if controller is None or controller.stimulus is None:
            return
        try:
            self._display_gray_now(self.gray_level_u8)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "shared experiment media gray restore failed; leaving current stimulus framebuffer in place: %s",
                exc,
            )

    def _display_gray_now(self, gray_level_u8: int) -> None:
        """Display one gray frame while serializing stimulus output access.

        Args:
            gray_level_u8: Integer grayscale level in uint8 display units.

        Returns:
            None.
        """

        controller = self.controller
        if controller is None or controller.stimulus is None:
            raise RuntimeError("visual stimulus runtime is unavailable before prepare().")
        with self._stimulus_output_lock:
            controller.stimulus.display_gray(int(gray_level_u8))

    def _release_current_preview_frame(self) -> None:
        """Release the currently displayed preview frame if one is outstanding.

        Returns:
            None.
        """

        if self._current_preview_frame is None or self.frame_source is None:
            self._current_preview_frame = None
            return
        self.frame_source.release_frame(self._current_preview_frame)
        self._current_preview_frame = None

    def _pop_next_grating(self) -> str | None:
        """Pop the next queued grating name in first-in-first-out order.

        Returns:
            str | None: Next canonical stimulus name, or ``None`` when no
            stimulus is queued.
        """

        with self._queue_lock:
            if not self._queued_gratings:
                return None
            return self._queued_gratings.popleft()
