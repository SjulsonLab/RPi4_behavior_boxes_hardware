"""Local one-Pi camera orchestration for automated BehavBox sessions.

Data contracts:

- ``camera_id``: semantic camera identifier string such as ``"camera0"`` or
  ``"camera1"``
- ``session_info``: mapping containing at least ``dir_name`` and optional
  camera configuration fields such as ``camera_ids`` and
  ``camera_preview_modes``
- ``runtime_state``: mapping keyed by camera identifier with JSON-serializable
  state dictionaries
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Callable

from box_runtime.video_recording.drm_preview_viewer import (
    DirectJpegPreviewViewer,
    PreviewDisplayConfig,
)
from box_runtime.video_recording.picamera2_recorder import Picamera2Recorder


class CameraHardwareUnavailable(RuntimeError):
    """Raised when one requested camera cannot be opened on the local host."""


def _camera_num_from_id(camera_id: str) -> int:
    """Convert a semantic camera identifier into a zero-based camera index.

    Args:
        camera_id: Camera identifier string such as ``"camera0"``.

    Returns:
        int: Zero-based camera index used by Picamera2.
    """

    text = str(camera_id).strip().lower()
    if not text.startswith("camera"):
        raise ValueError(f"camera_id must look like 'camera0', got {camera_id!r}")
    return int(text.removeprefix("camera"))


def _normalize_camera_ids(session_info: dict[str, Any]) -> list[str]:
    """Resolve the configured ordered camera identifier list.

    Args:
        session_info: Session configuration mapping.

    Returns:
        list[str]: Ordered camera identifiers.
    """

    configured = session_info.get("camera_ids", ["camera0"])
    if isinstance(configured, str):
        return [configured]
    return [str(camera_id) for camera_id in configured]


def _resolve_preview_mode(session_info: dict[str, Any], camera_id: str) -> str:
    """Resolve one per-camera preview mode string.

    Args:
        session_info: Session configuration mapping.
        camera_id: Camera identifier string.

    Returns:
        str: Preview mode string such as ``"off"`` or ``"drm_local"``.
    """

    configured = session_info.get("camera_preview_modes", session_info.get("camera_preview_mode", "off"))
    if isinstance(configured, dict):
        return str(configured.get(camera_id, "off")).strip().lower()
    return str(configured).strip().lower()


def _resolve_preview_connector(session_info: dict[str, Any], camera_id: str) -> str:
    """Resolve one per-camera preview connector name.

    Args:
        session_info: Session configuration mapping.
        camera_id: Camera identifier string.

    Returns:
        str: DRM connector name such as ``"HDMI-A-1"``.
    """

    configured = session_info.get("camera_preview_connectors")
    if isinstance(configured, dict):
        return str(configured.get(camera_id, session_info.get("camera_preview_connector", "HDMI-A-1")))
    return str(session_info.get("camera_preview_connector", "HDMI-A-1"))


class LocalCameraRuntime:
    """One local automated camera runtime owned by BehavBox.

    Args:
        camera_id: Semantic camera identifier string.
        session_info: Session configuration mapping.
        recorder_factory: Optional factory returning a Picamera2-compatible
            recorder. The callable must accept ``storage_root`` plus keyword-only
            ``camera_num`` and ``camera_id``.
        preview_sink_factory: Optional factory returning a preview sink. The
            callable must accept ``camera_id``, ``connector``, ``frame_provider``,
            and ``max_preview_hz``.
        clock: Optional monotonic or wall-clock callable returning seconds.
    """

    def __init__(
        self,
        camera_id: str,
        session_info: dict[str, Any],
        *,
        recorder_factory: Callable[..., Any] | None = None,
        preview_sink_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.camera_id = str(camera_id)
        self.camera_num = _camera_num_from_id(self.camera_id)
        self.session_info = session_info
        self._clock = clock or time.time
        self._recorder_factory = recorder_factory or Picamera2Recorder
        self._preview_sink_factory = preview_sink_factory or self._default_preview_sink
        self.storage_root = Path(self.session_info["dir_name"]) / "camera_recordings"
        self.preview_mode = _resolve_preview_mode(session_info, self.camera_id)
        self.preview_connector = _resolve_preview_connector(session_info, self.camera_id)
        self.preview_max_hz = float(self.session_info.get("camera_preview_max_hz", 15.0))
        self.recorder: Any | None = None
        self.preview_sink: Any | None = None
        self.is_prepared = False
        self.is_recording = False
        self.is_preview_active = False

    # User-facing methods
    def prepare(self) -> None:
        """Instantiate the recorder and validate local camera availability."""

        if self.is_prepared:
            return
        self.storage_root.mkdir(parents=True, exist_ok=True)
        try:
            self.recorder = self._recorder_factory(
                self.storage_root,
                camera_num=self.camera_num,
                camera_id=self.camera_id,
            )
        except CameraHardwareUnavailable:
            raise
        except Exception as exc:
            raise CameraHardwareUnavailable(f"{self.camera_id} is unavailable: {exc}") from exc
        self.is_prepared = True

    def start_recording(self, owner: str = "automated") -> None:
        """Start one camera recording under the local session directory.

        Args:
            owner: Recording owner string.
        """

        self.prepare()
        if self.is_recording:
            return
        assert self.recorder is not None
        self.recorder.start(
            session_id=self.camera_id,
            owner=owner,
            payload={
                "fps": float(self.session_info.get("camera_fps", self.session_info.get("frame_rate", 30.0))),
                "bitrate_bps": int(self.session_info.get("camera_bitrate_bps", Picamera2Recorder.DEFAULT_BITRATE_BPS)),
            },
        )
        self.is_recording = True

    def stop_recording(self) -> None:
        """Stop one active camera recording if needed."""

        if not self.is_recording or self.recorder is None:
            return
        self.recorder.stop()
        self.is_recording = False

    def start_preview(self) -> None:
        """Start the configured local preview sink when enabled."""

        if self.preview_mode != "drm_local":
            return
        self.prepare()
        if self.is_preview_active:
            return
        assert self.recorder is not None
        self.preview_sink = self._preview_sink_factory(
            camera_id=self.camera_id,
            connector=self.preview_connector,
            frame_provider=self.recorder.preview_frame,
            max_preview_hz=self.preview_max_hz,
        )
        if hasattr(self.preview_sink, "start"):
            self.preview_sink.start()
        self.is_preview_active = True

    def stop_preview(self) -> None:
        """Stop the local preview sink if it is running."""

        if not self.is_preview_active:
            return
        if self.preview_sink is not None and hasattr(self.preview_sink, "close"):
            self.preview_sink.close()
        self.preview_sink = None
        self.is_preview_active = False

    def close(self) -> None:
        """Close preview and recorder resources for this runtime."""

        self.stop_preview()
        self.stop_recording()
        if self.recorder is not None and hasattr(self.recorder, "close"):
            self.recorder.close()
        self.recorder = None
        self.is_prepared = False

    def state_dict(self) -> dict[str, Any]:
        """Return one JSON-serializable camera runtime state dictionary."""

        return {
            "camera_id": self.camera_id,
            "camera_num": self.camera_num,
            "prepared": self.is_prepared,
            "recording": self.is_recording,
            "preview_active": self.is_preview_active,
            "preview_mode": self.preview_mode,
            "preview_connector": self.preview_connector if self.preview_mode == "drm_local" else None,
            "storage_root": str(self.storage_root),
        }

    # Helper methods
    def _default_preview_sink(
        self,
        *,
        camera_id: str,
        connector: str,
        frame_provider: Callable[[], bytes | None],
        max_preview_hz: float,
    ) -> DirectJpegPreviewViewer:
        """Create the default direct-frame DRM preview sink.

        Args:
            camera_id: Semantic camera identifier string.
            connector: DRM connector name.
            frame_provider: Zero-argument callback returning latest JPEG bytes.
            max_preview_hz: Maximum display update rate in Hz.

        Returns:
            DirectJpegPreviewViewer: Started-on-demand preview viewer.
        """

        del camera_id
        config = PreviewDisplayConfig(
            connector=connector,
            resolution_px=(640, 480),
            stream_url="",
            max_preview_hz=max_preview_hz,
            stall_timeout_s=0.5,
        )
        return DirectJpegPreviewViewer(config=config, frame_provider=frame_provider)


class CameraManager:
    """Small multi-camera-capable manager for the one-Pi BehavBox runtime.

    Args:
        session_info: Session configuration mapping.
        state_callback: Optional callback receiving the full runtime-state
            mapping after every state change.
        recorder_factory: Optional recorder factory passed through to each
            ``LocalCameraRuntime``.
        preview_sink_factory: Optional preview sink factory passed through to
            each ``LocalCameraRuntime``.
    """

    def __init__(
        self,
        session_info: dict[str, Any],
        *,
        state_callback: Callable[[dict[str, dict[str, Any]]], None] | None = None,
        recorder_factory: Callable[..., Any] | None = None,
        preview_sink_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.session_info = session_info
        self._state_callback = state_callback
        self._recorder_factory = recorder_factory
        self._preview_sink_factory = preview_sink_factory
        self.camera_ids = _normalize_camera_ids(session_info)
        self.runtimes: dict[str, LocalCameraRuntime] = {}

    # User-facing methods
    def prepare(self) -> None:
        """Prepare all configured cameras and publish initial runtime state."""

        if not bool(self.session_info.get("camera_enabled", False)):
            self._publish_state()
            return
        if self.runtimes:
            self._publish_state()
            return
        for camera_id in self.camera_ids:
            runtime = LocalCameraRuntime(
                camera_id=camera_id,
                session_info=self.session_info,
                recorder_factory=self._recorder_factory,
                preview_sink_factory=self._preview_sink_factory,
            )
            runtime.prepare()
            self.runtimes[camera_id] = runtime
        self._publish_state()

    def start_session(self, owner: str = "automated") -> None:
        """Start recordings and enabled previews for all configured cameras."""

        self.prepare()
        for camera_id in self.camera_ids:
            runtime = self.runtimes[camera_id]
            runtime.start_recording(owner=owner)
            runtime.start_preview()
        self._publish_state()

    def stop_session(self) -> None:
        """Stop previews then recordings for all prepared cameras."""

        for camera_id in reversed(self.camera_ids):
            runtime = self.runtimes.get(camera_id)
            if runtime is None:
                continue
            runtime.stop_preview()
            runtime.stop_recording()
        self._publish_state()

    def start_recording(self, camera_id: str = "camera0", owner: str = "automated") -> None:
        """Start one named camera recording.

        Args:
            camera_id: Camera identifier string.
            owner: Recording owner string.
        """

        self.prepare()
        self.runtimes[camera_id].start_recording(owner=owner)
        self._publish_state()

    def stop_recording(self, camera_id: str = "camera0") -> None:
        """Stop one named camera recording."""

        runtime = self.runtimes.get(camera_id)
        if runtime is None:
            return
        runtime.stop_recording()
        self._publish_state()

    def start_preview(self, camera_id: str = "camera0") -> None:
        """Start one named local preview sink if configured."""

        self.prepare()
        self.runtimes[camera_id].start_preview()
        self._publish_state()

    def stop_preview(self, camera_id: str = "camera0") -> None:
        """Stop one named local preview sink."""

        runtime = self.runtimes.get(camera_id)
        if runtime is None:
            return
        runtime.stop_preview()
        self._publish_state()

    def close(self) -> None:
        """Close all local camera runtimes."""

        for runtime in self.runtimes.values():
            runtime.close()
        self.runtimes.clear()
        self._publish_state()

    def runtime_state(self) -> dict[str, dict[str, Any]]:
        """Return the current per-camera runtime-state mapping."""

        return {
            camera_id: runtime.state_dict()
            for camera_id, runtime in self.runtimes.items()
        }

    # Helper methods
    def _publish_state(self) -> None:
        if self._state_callback is not None:
            self._state_callback(self.runtime_state())
