"""Helper that combines a preview+recording camera with a recording-only camera."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

try:
    from debug.shared_camera_recording_dmabuf_source import SharedCameraRecordingDmabufSource
except ModuleNotFoundError:
    from shared_camera_recording_dmabuf_source import SharedCameraRecordingDmabufSource


class SharedDualCameraRecordingSource:
    """Own two recording sources while exposing preview frames from camera0.

    Data contracts:

    - ``output_root`` is the directory under which both cameras' ``.h264`` and
      ``.csv`` artifacts are created.
    - ``preview_camera_id`` is the semantic camera identifier for the previewed
      camera, typically ``"camera0"``.
    - ``recording_camera_id`` is the semantic camera identifier for the
      recording-only camera, typically ``"camera1"``.
    - ``resolution_px`` is ``(width_px, height_px)`` in display pixels and is
      applied to both camera video configurations in this smoke.
    - ``capture_frame_for_preview()`` and ``release_frame(frame)`` operate only
      on the preview camera source.
    - ``diagnostics()`` returns a flat JSON-serializable dictionary where each
      camera's recording paths, overlay settings, and timestamp counts are
      namespaced as ``camera0_*`` or ``camera1_*``.
    """

    def __init__(
        self,
        *,
        output_root: Path,
        preview_camera_id: str = "camera0",
        recording_camera_id: str = "camera1",
        resolution_px: tuple[int, int],
        frame_rate_hz: float = 30.0,
        sensor_mode: int = 0,
        request_mode: str = "next",
        preview_overlay_enabled: bool = True,
        recording_overlay_enabled: bool = True,
        recording_source_factory: Callable[..., Any] = SharedCameraRecordingDmabufSource,
    ) -> None:
        self.output_root = Path(output_root)
        self.preview_camera_id = str(preview_camera_id)
        self.recording_camera_id = str(recording_camera_id)
        self.resolution_px = (int(resolution_px[0]), int(resolution_px[1]))
        self.frame_rate_hz = float(frame_rate_hz)
        self.sensor_mode = int(sensor_mode)
        self.request_mode = str(request_mode)
        self.preview_overlay_enabled = bool(preview_overlay_enabled)
        self.recording_overlay_enabled = bool(recording_overlay_enabled)
        self._recording_source_factory = recording_source_factory
        self._preview_source = None
        self._recording_source = None
        self._closed = False

        self.output_root.mkdir(parents=True, exist_ok=True)

        try:
            preview_video_path, preview_timestamp_path = self._build_output_paths(
                camera_id=self.preview_camera_id,
                preview_enabled=True,
            )
            self._preview_source = self._recording_source_factory(
                camera_id=self.preview_camera_id,
                resolution_px=self.resolution_px,
                video_path=preview_video_path,
                timestamp_csv_path=preview_timestamp_path,
                frame_rate_hz=self.frame_rate_hz,
                sensor_mode=self.sensor_mode,
                request_mode=self.request_mode,
                overlay_enabled=self.preview_overlay_enabled,
            )
            recording_video_path, recording_timestamp_path = self._build_output_paths(
                camera_id=self.recording_camera_id,
                preview_enabled=False,
            )
            self._recording_source = self._recording_source_factory(
                camera_id=self.recording_camera_id,
                resolution_px=self.resolution_px,
                video_path=recording_video_path,
                timestamp_csv_path=recording_timestamp_path,
                frame_rate_hz=self.frame_rate_hz,
                sensor_mode=self.sensor_mode,
                request_mode=self.request_mode,
                overlay_enabled=self.recording_overlay_enabled,
            )
        except Exception:
            self.close()
            raise

    def _build_output_paths(self, *, camera_id: str, preview_enabled: bool) -> tuple[Path, Path]:
        """Build video and CSV output paths for one camera.

        Args:
            camera_id: Semantic camera identifier such as ``"camera0"``.
            preview_enabled: Whether the camera is the previewed camera.

        Returns:
            tuple[Path, Path]: ``(video_path, timestamp_csv_path)``.
        """

        camera_label = str(camera_id)
        if preview_enabled:
            return (
                self.output_root / f"{camera_label}_preview_recording_output.h264",
                self.output_root / f"{camera_label}_preview_recording_timestamp.csv",
            )
        return (
            self.output_root / f"{camera_label}_recording_output.h264",
            self.output_root / f"{camera_label}_recording_timestamp.csv",
        )

    def _namespace_camera_diagnostics(
        self,
        *,
        camera_label: str,
        diagnostics: dict[str, object],
    ) -> dict[str, object]:
        """Namespace one camera's diagnostics for inclusion in a shared summary.

        Args:
            camera_label: Stable output label such as ``"camera0"``.
            diagnostics: Per-camera JSON-serializable diagnostics dictionary.

        Returns:
            dict[str, object]: Flat namespaced diagnostics.
        """

        namespaced: dict[str, object] = {}
        for key, value in diagnostics.items():
            if key == "camera_id":
                continue
            namespaced[f"{camera_label}_{key}"] = value
        return namespaced

    def capture_frame_for_preview(self) -> Any:
        """Capture one preview frame from the preview camera.

        Returns:
            Any: Display-ready dmabuf frame object from the preview camera.
        """

        return self._preview_source.capture_frame_for_preview()

    def release_frame(self, frame: Any) -> None:
        """Release one preview frame captured from the preview camera.

        Args:
            frame: Frame object previously returned by
                ``capture_frame_for_preview()``.

        Returns:
            None.
        """

        self._preview_source.release_frame(frame)

    def diagnostics(self) -> dict[str, object]:
        """Return a combined JSON-serializable summary for both cameras.

        Returns:
            dict[str, object]: Flat diagnostics for preview camera and
            recording-only camera, including output paths and timestamp counts.
        """

        diagnostics: dict[str, object] = {
            "preview_camera_id": self.preview_camera_id,
            "recording_camera_id": self.recording_camera_id,
        }
        if self._preview_source is not None and hasattr(self._preview_source, "diagnostics"):
            diagnostics.update(
                self._namespace_camera_diagnostics(
                    camera_label=self.preview_camera_id,
                    diagnostics=self._preview_source.diagnostics(),
                )
            )
        if self._recording_source is not None and hasattr(self._recording_source, "diagnostics"):
            diagnostics.update(
                self._namespace_camera_diagnostics(
                    camera_label=self.recording_camera_id,
                    diagnostics=self._recording_source.diagnostics(),
                )
            )
        return diagnostics

    def close(self) -> None:
        """Close both camera sources, tolerating partial startup.

        Returns:
            None.
        """

        if self._closed:
            return
        self._closed = True
        if self._recording_source is not None:
            self._recording_source.close()
            self._recording_source = None
        if self._preview_source is not None:
            self._preview_source.close()
            self._preview_source = None
