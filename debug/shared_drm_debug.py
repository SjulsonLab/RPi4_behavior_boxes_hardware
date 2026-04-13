"""Debug-only shared DRM controller for one-process dual-output experiments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import selectors
import time
from typing import Any, Callable

import numpy as np


@dataclass(frozen=True)
class SharedDrmOutputSpec:
    """Connector assignment for one shared-DRM output.

    Attributes:
        role: Human-readable output role, for example ``"preview"`` or
            ``"stimulus"``.
        connector: DRM connector name such as ``"HDMI-A-1"``.
    """

    role: str
    connector: str


class SharedDrmController:
    """Own one DRM card/resource manager for both preview and stimulus outputs.

    Args:
        preview_connector: DRM connector name for preview, typically
            ``"HDMI-A-1"``.
        stimulus_connector: DRM connector name for stimulus, typically
            ``"HDMI-A-2"``.
        pykms_module: Optional injected ``pykms``-compatible module used for
            testing.
        selector_factory: Callable returning a selector object with
            ``register``, ``select``, and ``close`` methods.

    Returns:
        SharedDrmController: Shared DRM owner exposing ``preview`` and
            ``stimulus`` outputs.
    """

    def __init__(
        self,
        *,
        preview_connector: str,
        stimulus_connector: str,
        pykms_module: Any | None = None,
        selector_factory: Callable[[], Any] = selectors.DefaultSelector,
    ) -> None:
        if preview_connector == stimulus_connector:
            raise ValueError("shared DRM controller requires duplicate connectors to be avoided")

        if pykms_module is None:
            try:
                import pykms  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "shared DRM debug controller requires python3-kms++ / pykms"
                ) from exc
            pykms_module = pykms

        self._pykms = pykms_module
        self.card = self._pykms.Card()
        self.resource_manager = self._pykms.ResourceManager(self.card)
        self._selector = selector_factory()
        self._selector.register(self.card.fd, selectors.EVENT_READ)

        self.preview = self._build_output(
            SharedDrmOutputSpec(role="preview", connector=str(preview_connector))
        )
        self.stimulus = self._build_output(
            SharedDrmOutputSpec(role="stimulus", connector=str(stimulus_connector))
        )

    def _build_output(self, spec: SharedDrmOutputSpec) -> SharedDrmOutput:
        """Reserve connector, CRTC, and plane for one output.

        Args:
            spec: Output-role and connector assignment.

        Returns:
            SharedDrmOutput: Reserved output wrapper bound to this controller.
        """

        connector = self.resource_manager.reserve_connector(spec.connector)
        crtc = self.resource_manager.reserve_crtc(connector)
        plane = self.resource_manager.reserve_primary_plane(crtc)
        mode = connector.get_default_mode()
        return SharedDrmOutput(
            controller=self,
            role=spec.role,
            connector_name=spec.connector,
            connector=connector,
            crtc=crtc,
            plane=plane,
            mode=mode,
        )

    def wait_for_flip_complete(self, timeout_s: float) -> None:
        """Wait for one DRM flip-complete event on the shared card.

        Args:
            timeout_s: Maximum time to wait in seconds.

        Returns:
            None: The helper returns after a single flip-complete event.

        Raises:
            TimeoutError: If no flip-complete event arrives before ``timeout_s``.
        """

        deadline = time.monotonic() + float(timeout_s)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for shared DRM page-flip completion")
            events = self._selector.select(remaining)
            if not events:
                continue
            for _key, _mask in events:
                for event in self.card.read_events():
                    if event.type == self._pykms.DrmEventType.FLIP_COMPLETE:
                        return

    def diagnostics(self) -> dict[str, dict[str, Any]]:
        """Return structured diagnostics for both reserved outputs.

        Returns:
            dict[str, dict[str, Any]]: Mapping with ``preview`` and ``stimulus``
            keys pointing to JSON-serializable output diagnostics.
        """

        return {
            "preview": self.preview.diagnostics(),
            "stimulus": self.stimulus.diagnostics(),
        }

    def close(self) -> None:
        """Release shared selector state and disable card planes once.

        Returns:
            None.
        """

        try:
            self._selector.close()
        finally:
            try:
                self.card.disable_planes()
            except Exception:
                return None


class SharedDrmOutput:
    """One connector/CRTC/plane reservation within a shared DRM controller.

    Args:
        controller: Shared owner for the DRM card and event selector.
        role: Output role such as ``"preview"`` or ``"stimulus"``.
        connector_name: Requested DRM connector name.
        connector: Reserved pykms connector object.
        crtc: Reserved pykms CRTC object.
        plane: Reserved pykms primary-plane object.
        mode: Active mode object exposing ``hdisplay``, ``vdisplay``, and
            ``vrefresh``.

    Returns:
        SharedDrmOutput: Output wrapper that can display RGB placeholders and
            grayscale grating framebuffers.
    """

    def __init__(
        self,
        *,
        controller: SharedDrmController,
        role: str,
        connector_name: str,
        connector: Any,
        crtc: Any,
        plane: Any,
        mode: Any,
    ) -> None:
        self.controller = controller
        self.role = str(role)
        self.connector_name = str(connector_name)
        self.connector = connector
        self.crtc = crtc
        self.plane = plane
        self.mode = mode
        self.resolution_px = (int(mode.hdisplay), int(mode.vdisplay))
        self.refresh_hz = float(getattr(mode, "vrefresh", 60.0))
        self._mode_set = False
        self._current_framebuffer_id: int | None = None
        self._front_index = 0
        self._last_commit_stage: str | None = None
        self._last_commit_error: str | None = None
        self._last_request_summary: dict[str, Any] = {}
        self._rgb_framebuffers = [
            self.controller._pykms.DumbFramebuffer(
                self.controller.card,
                self.resolution_px[0],
                self.resolution_px[1],
                "XR24",
            ),
            self.controller._pykms.DumbFramebuffer(
                self.controller.card,
                self.resolution_px[0],
                self.resolution_px[1],
                "XR24",
            ),
        ]
        self._gray_framebuffers: dict[int, Any] = {}

    def display_rgb_frame(self, frame_rgb: np.ndarray) -> None:
        """Display one RGB frame on this output.

        Args:
            frame_rgb: ``uint8`` array with shape ``(height_px, width_px, 3)``.
                Axis order is vertical pixel, horizontal pixel, color channel.

        Returns:
            None.
        """

        height_px, width_px, channels = frame_rgb.shape
        if channels != 3:
            raise ValueError("frame_rgb must have shape (height_px, width_px, 3)")
        if (width_px, height_px) != self.resolution_px:
            raise ValueError("frame_rgb resolution must match the reserved connector mode")

        framebuffer = self._rgb_framebuffers[self._front_index ^ 1]
        mapped = framebuffer.map(0)
        pixels = np.frombuffer(mapped, dtype=np.uint8).reshape(height_px, width_px, 4)
        pixels[:, :, 0] = frame_rgb[:, :, 2]
        pixels[:, :, 1] = frame_rgb[:, :, 1]
        pixels[:, :, 2] = frame_rgb[:, :, 0]
        pixels[:, :, 3] = 0
        self._flip_to_framebuffer(framebuffer, allow_modeset=not self._mode_set)
        self._mode_set = True
        self._current_framebuffer_id = int(framebuffer.id)
        self._front_index ^= 1

    def display_gray(self, gray_level_u8: int) -> None:
        """Display a solid grayscale framebuffer on this output.

        Args:
            gray_level_u8: Gray value in uint8 display units ``[0, 255]``.

        Returns:
            None.
        """

        gray_level = int(gray_level_u8)
        if gray_level not in self._gray_framebuffers:
            width_px, height_px = self.resolution_px
            framebuffer = self.controller._pykms.DumbFramebuffer(
                self.controller.card,
                width_px,
                height_px,
                "XR24",
            )
            mapped = framebuffer.map(0)
            pixels = np.frombuffer(mapped, dtype=np.uint8).reshape(height_px, width_px, 4)
            pixels[:, :, 0] = gray_level
            pixels[:, :, 1] = gray_level
            pixels[:, :, 2] = gray_level
            pixels[:, :, 3] = 0
            self._gray_framebuffers[gray_level] = framebuffer

        framebuffer = self._gray_framebuffers[gray_level]
        allow_modeset = not self._mode_set
        self._flip_to_framebuffer(framebuffer, allow_modeset=allow_modeset)
        self._mode_set = True
        self._current_framebuffer_id = int(framebuffer.id)

    def play_grating(self, stimulus_name: str, compiled_grating: Any) -> dict[str, Any]:
        """Display one compiled grating sequence on this output.

        Args:
            stimulus_name: Human-readable stimulus label.
            compiled_grating: Object with ``frames`` shaped
                ``(n_frames, height_px, width_px)`` in uint8 gray units and
                ``frame_interval_s`` in seconds.

        Returns:
            dict[str, Any]: Playback summary with the stimulus name and frame
            count.
        """

        frame_count = int(compiled_grating.frames.shape[0])
        width_px, height_px = self.resolution_px
        framebuffers: list[Any] = []
        for frame in compiled_grating.frames:
            framebuffer = self.controller._pykms.DumbFramebuffer(
                self.controller.card,
                width_px,
                height_px,
                "XR24",
            )
            mapped = framebuffer.map(0)
            pixels = np.frombuffer(mapped, dtype=np.uint8).reshape(height_px, width_px, 4)
            pixels[:, :, 0] = frame
            pixels[:, :, 1] = frame
            pixels[:, :, 2] = frame
            pixels[:, :, 3] = 0
            framebuffers.append(framebuffer)

        for index, framebuffer in enumerate(framebuffers):
            allow_modeset = not self._mode_set
            self._flip_to_framebuffer(framebuffer, allow_modeset=allow_modeset)
            self._mode_set = True
            self._current_framebuffer_id = int(framebuffer.id)
            if index > 0:
                self.controller.wait_for_flip_complete(
                    timeout_s=2.0 / max(float(self.refresh_hz), 1.0)
                )
            time.sleep(float(compiled_grating.frame_interval_s))

        return {
            "stimulus_name": str(stimulus_name),
            "frame_count": frame_count,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Return a structured snapshot of this output's DRM assignment.

        Returns:
            dict[str, Any]: JSON-serializable diagnostics including connector,
            CRTC, plane, and last-request metadata.
        """

        return {
            "role": self.role,
            "requested_connector": self.connector_name,
            "reserved_connector_id": int(getattr(self.connector, "id", -1)),
            "reserved_connector_name": str(
                getattr(self.connector, "fullname", self.connector_name)
            ),
            "reserved_crtc_id": int(getattr(self.crtc, "id", -1)),
            "reserved_plane_id": int(getattr(self.plane, "id", -1)),
            "mode_set_done": bool(self._mode_set),
            "current_framebuffer_id": self._current_framebuffer_id,
            "last_commit_stage": self._last_commit_stage,
            "last_commit_error": self._last_commit_error,
            "last_request": dict(self._last_request_summary),
        }

    def _flip_to_framebuffer(self, framebuffer: Any, *, allow_modeset: bool) -> None:
        """Submit one framebuffer to this output's primary plane.

        Args:
            framebuffer: pykms framebuffer object carrying XR24 pixels.
            allow_modeset: Whether the request may perform the initial modeset.

        Returns:
            None.
        """

        if allow_modeset:
            self._last_commit_stage = "modeset"
            mode_blob = self.mode.to_blob(self.controller.card)
            plane_properties = {
                "FB_ID": int(framebuffer.id),
                "CRTC_ID": int(self.crtc.id),
                "SRC_X": 0,
                "SRC_Y": 0,
                "SRC_W": int(framebuffer.width) << 16,
                "SRC_H": int(framebuffer.height) << 16,
                "CRTC_X": 0,
                "CRTC_Y": 0,
                "CRTC_W": int(self.mode.hdisplay),
                "CRTC_H": int(self.mode.vdisplay),
            }
            self._last_request_summary = {
                "commit_kind": "atomic",
                "allow_modeset": True,
                "framebuffer_id": int(framebuffer.id),
                "object_properties": {
                    "connector": {"CRTC_ID": int(self.crtc.id)},
                    "crtc": {"ACTIVE": 1, "MODE_ID": int(mode_blob.id)},
                    "plane": dict(plane_properties),
                },
            }
            request = self.controller._pykms.AtomicReq(self.controller.card)
            request.add(self.connector, "CRTC_ID", self.crtc.id)
            request.add(self.crtc, {"ACTIVE": 1, "MODE_ID": mode_blob.id})
            request.add(self.plane, plane_properties)
            ret = request.commit(allow_modeset=True)
            if ret < 0:
                self._last_commit_error = f"shared DRM atomic mode set failed with {ret}"
                raise RuntimeError(self._last_commit_error)
            self._last_commit_error = None
            return

        self._last_commit_stage = "page_flip"
        plane_properties = {
            "FB_ID": int(framebuffer.id),
            "CRTC_ID": int(self.crtc.id),
            "SRC_X": 0,
            "SRC_Y": 0,
            "SRC_W": int(framebuffer.width) << 16,
            "SRC_H": int(framebuffer.height) << 16,
            "CRTC_X": 0,
            "CRTC_Y": 0,
            "CRTC_W": int(self.mode.hdisplay),
            "CRTC_H": int(self.mode.vdisplay),
        }
        self._last_request_summary = {
            "commit_kind": "atomic",
            "allow_modeset": False,
            "framebuffer_id": int(framebuffer.id),
            "object_properties": {
                "plane": dict(plane_properties),
            },
        }
        request = self.controller._pykms.AtomicReq(self.controller.card)
        request.add(self.plane, plane_properties)
        ret = request.commit()
        if ret < 0:
            self._last_commit_error = f"shared DRM atomic page flip failed with {ret}"
            raise RuntimeError(self._last_commit_error)
        self._last_commit_error = None


def make_placeholder_preview_frame(resolution_px: tuple[int, int]) -> np.ndarray:
    """Build a simple static RGB placeholder frame for preview bring-up.

    Args:
        resolution_px: Output resolution as ``(width_px, height_px)``.

    Returns:
        np.ndarray: ``uint8`` RGB frame with shape
        ``(height_px, width_px, 3)``.
    """

    width_px, height_px = resolution_px
    frame = np.zeros((height_px, width_px, 3), dtype=np.uint8)
    frame[:, :, 1] = 24
    frame[:, :, 2] = 48
    frame[height_px // 4 : (3 * height_px) // 4, width_px // 4 : (3 * width_px) // 4, 0] = 220
    frame[height_px // 4 : (3 * height_px) // 4, width_px // 4 : (3 * width_px) // 4, 1] = 180
    return frame
