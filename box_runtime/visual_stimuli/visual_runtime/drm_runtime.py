"""Runtime support for precomputed visual stimuli using fake, DRM, or X-window backends."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import multiprocessing as mp
import os
from pathlib import Path
import queue
import selectors
import sys
import time
import traceback
from typing import Any

from .grating_compiler import CompiledGrating


@dataclass(frozen=True)
class DisplayConfig:
    """Display timing and geometry used for visual stimulus playback.

    Attributes:
        backend: Runtime backend name, either ``"fake"`` or ``"drm"``.
        connector: DRM connector name such as ``"HDMI-A-1"`` for real displays,
            or ``None`` for the fake backend.
        resolution_px: Display resolution as ``(width_px, height_px)``.
        refresh_hz: Display refresh rate in Hz.
    """

    backend: str
    connector: str | None
    resolution_px: tuple[int, int]
    refresh_hz: float


def query_display_config(
    backend: str,
    requested_resolution_px: tuple[int, int] | None = None,
    requested_refresh_hz: float | None = None,
    requested_connector: str | None = None,
) -> DisplayConfig:
    """Resolve the display geometry and refresh rate for a runtime backend.

    Args:
        backend: Backend name, ``"fake"``, ``"drm"``, or ``"xwindow"``.
        requested_resolution_px: Optional ``(width_px, height_px)`` override.
        requested_refresh_hz: Optional target refresh rate in Hz.
        requested_connector: Optional DRM connector name such as ``"HDMI-A-1"``.

    Returns:
        DisplayConfig: Resolved display configuration.
    """

    backend_name = backend.lower()
    if backend_name == "fake":
        resolution_px = requested_resolution_px or (640, 480)
        refresh_hz = float(requested_refresh_hz or 60.0)
        return DisplayConfig(
            backend=backend_name,
            connector=requested_connector,
            resolution_px=resolution_px,
            refresh_hz=refresh_hz,
        )

    if backend_name == "xwindow":
        resolution_px = requested_resolution_px or (640, 480)
        refresh_hz = float(requested_refresh_hz or 60.0)
        return DisplayConfig(
            backend=backend_name,
            connector=requested_connector or "HDMI-A-2",
            resolution_px=resolution_px,
            refresh_hz=refresh_hz,
        )

    if backend_name != "drm":
        raise ValueError(f"unsupported visual backend {backend!r}")

    try:
        import pykms  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "DRM backend requires the Raspberry Pi OS system package python3-kms++ / pykms"
        ) from exc

    card = pykms.Card()
    res = pykms.ResourceManager(card)
    connector_name = (requested_connector or "").strip()
    try:
        conn = res.reserve_connector(connector_name)
    except Exception as exc:
        label = connector_name or "<default>"
        raise ValueError(f"DRM connector {label} is unavailable") from exc
    mode = _select_mode(conn, requested_refresh_hz)
    resolution_px = (int(mode.hdisplay), int(mode.vdisplay))
    if requested_resolution_px is not None and tuple(requested_resolution_px) != resolution_px:
        raise ValueError(
            "DRM v1 requires the connector default mode resolution; omit visual_display_resolution_px "
            "or set it to the active mode size"
        )
    return DisplayConfig(
        backend=backend_name,
        connector=connector_name or getattr(conn, "fullname", None) or None,
        resolution_px=resolution_px,
        refresh_hz=float(getattr(mode, "vrefresh", requested_refresh_hz or 60.0)),
    )


class VisualStimRuntime:
    """Parent-process controller for the visual stimulus worker process.

    Args:
        display_config: Display geometry and timing used by the worker.
        gray_level_u8: Neutral gray value in uint8 display units [0, 255].
        stimuli: Mapping from canonical stimulus names to compiled gratings.

    Returns:
        VisualStimRuntime: Controller used by ``VisualStim`` and the test suite.
    """

    def __init__(
        self,
        display_config: DisplayConfig,
        gray_level_u8: int,
        stimuli: dict[str, CompiledGrating],
    ) -> None:
        self.display_config = display_config
        self.gray_level_u8 = int(gray_level_u8)
        self._closed = False
        self._error_message: str | None = None
        self._drm_diagnostics: dict[str, Any] = {}
        self._metrics: dict[str, Any] = {
            "play_count": 0,
            "current_label": "gray",
            "timing_log": [],
        }

        ctx = _runtime_context()
        self._command_queue: mp.Queue[dict[str, Any]] = ctx.Queue()
        self._result_queue: mp.Queue[dict[str, Any]] = ctx.Queue()
        self._idle_event = ctx.Event()
        self._ready_event = ctx.Event()
        self._process = ctx.Process(
            target=_worker_entry,
            args=(
                display_config,
                self.gray_level_u8,
                stimuli,
                self._command_queue,
                self._result_queue,
                self._idle_event,
                self._ready_event,
            ),
            daemon=True,
        )
        self._process.start()
        if not self._ready_event.wait(timeout=10.0):
            diagnostics = dict(self._drm_diagnostics)
            self.close()
            raise VisualStimRuntimeInitError(
                "visual stimulus worker failed to become ready within 10 seconds",
                diagnostics=diagnostics,
            )
        self._drain_events()
        if self._error_message is not None:
            diagnostics = dict(self._drm_diagnostics)
            self.close()
            raise VisualStimRuntimeInitError(self._error_message, diagnostics=diagnostics)

    @property
    def worker_pid(self) -> int | None:
        """Return the worker process identifier if it has started."""

        return self._process.pid

    def show_grating(self, stimulus_name: str) -> None:
        """Request playback of a preloaded stimulus by canonical name.

        Args:
            stimulus_name: Canonical stimulus identifier string.

        Returns:
            None.
        """

        self._assert_ready()
        self._drain_events()
        self._idle_event.clear()
        self._command_queue.put(
            {
                "command": "play",
                "stimulus_name": stimulus_name,
                "enqueue_ns": time.perf_counter_ns(),
            }
        )

    def display_gray(self, gray_level_u8: int, blocking: bool = True) -> None:
        """Show a solid gray frame on the display worker.

        Args:
            gray_level_u8: Grayscale value in uint8 units [0, 255].
            blocking: When ``True``, wait until the worker reports idle.

        Returns:
            None.
        """

        self._assert_ready()
        self._idle_event.clear()
        self._command_queue.put(
            {
                "command": "display_gray",
                "gray_level_u8": int(gray_level_u8),
            }
        )
        if blocking:
            self.wait_until_idle(timeout_s=2.0)

    def wait_until_idle(self, timeout_s: float) -> None:
        """Block until the worker finishes the active presentation request.

        Args:
            timeout_s: Maximum wait time in seconds.

        Returns:
            None.
        """

        if not self._idle_event.wait(timeout=timeout_s):
            raise TimeoutError(f"visual stimulus worker did not become idle within {timeout_s} s")
        self._drain_events()
        if self._error_message is not None:
            raise RuntimeError(self._error_message)

    def get_metrics(self) -> dict[str, Any]:
        """Return accumulated software timing metrics from the worker.

        Returns:
            dict[str, Any]: Dictionary containing play count, current label,
            a timing log of completed stimulus presentations, and the latest
            lightweight DRM diagnostic snapshot when available.
        """

        self._drain_events()
        return {
            "play_count": int(self._metrics["play_count"]),
            "current_label": str(self._metrics["current_label"]),
            "timing_log": list(self._metrics["timing_log"]),
            "drm_diagnostics": dict(self._drm_diagnostics),
        }

    def is_alive(self) -> bool:
        """Return whether the worker process is still alive."""

        return self._process.is_alive()

    def close(self) -> None:
        """Stop the worker process and release runtime resources.

        Returns:
            None.
        """

        if self._closed:
            return
        self._closed = True
        try:
            if self._process.is_alive():
                self._command_queue.put({"command": "close"})
                self._process.join(timeout=5.0)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=1.0)
        finally:
            self._drain_events()

    def _assert_ready(self) -> None:
        """Raise if the worker is closed or has exited unexpectedly."""

        if self._closed:
            raise RuntimeError("visual stimulus runtime is closed")
        if not self._process.is_alive():
            self._drain_events()
            if self._error_message is not None:
                raise RuntimeError(self._error_message)
            raise RuntimeError("visual stimulus worker is not alive")

    def _drain_events(self) -> None:
        """Consume all pending worker events into local metrics state."""

        while True:
            try:
                event = self._result_queue.get_nowait()
            except queue.Empty:
                break

            event_type = event.get("type")
            if event_type == "ready":
                self._metrics["current_label"] = "gray"
            elif event_type == "gray":
                self._metrics["current_label"] = "gray"
            elif event_type == "played":
                self._metrics["play_count"] += 1
                self._metrics["timing_log"].append(event["timing"])
                self._metrics["current_label"] = "gray"
            elif event_type == "diagnostic":
                self._drm_diagnostics = dict(event.get("drm_diagnostics", {}))
            elif event_type == "error":
                if "drm_diagnostics" in event:
                    self._drm_diagnostics = dict(event.get("drm_diagnostics", {}))
                self._error_message = str(event.get("message", "visual stimulus worker error"))


def _worker_entry(
    display_config: DisplayConfig,
    gray_level_u8: int,
    stimuli: dict[str, CompiledGrating],
    command_queue: mp.Queue[dict[str, Any]],
    result_queue: mp.Queue[dict[str, Any]],
    idle_event: mp.synchronize.Event,
    ready_event: mp.synchronize.Event,
) -> None:
    """Run the display worker process event loop.

    Args:
        display_config: Worker display geometry and timing.
        gray_level_u8: Neutral gray value in uint8 units [0, 255].
        stimuli: Mapping from canonical names to compiled frames.
        command_queue: Parent-to-worker command queue.
        result_queue: Worker-to-parent status queue.
        idle_event: Event set when no playback is active.
        ready_event: Event set when initialization completes.
    """

    backend: _BaseDisplayBackend | None = None
    try:
        _best_effort_realtime_config()
        backend = _build_backend(display_config, gray_level_u8, stimuli)
        result_queue.put({"type": "diagnostic", "drm_diagnostics": backend.diagnostics()})
        backend.display_gray(gray_level_u8)
        result_queue.put({"type": "diagnostic", "drm_diagnostics": backend.diagnostics()})
        result_queue.put({"type": "ready"})
        result_queue.put({"type": "gray"})
        idle_event.set()
        ready_event.set()

        while True:
            command = command_queue.get()
            command_name = command.get("command")
            if command_name == "close":
                break
            if command_name == "display_gray":
                backend.display_gray(int(command["gray_level_u8"]))
                result_queue.put({"type": "diagnostic", "drm_diagnostics": backend.diagnostics()})
                result_queue.put({"type": "gray"})
                idle_event.set()
                continue
            if command_name != "play":
                raise RuntimeError(f"unsupported worker command {command_name!r}")

            idle_event.clear()
            timing = backend.play(str(command["stimulus_name"]), int(command["enqueue_ns"]))
            backend.display_gray(gray_level_u8)
            result_queue.put({"type": "diagnostic", "drm_diagnostics": backend.diagnostics()})
            result_queue.put({"type": "played", "timing": timing})
            result_queue.put({"type": "gray"})
            idle_event.set()
    except Exception:
        error_event: dict[str, Any] = {"type": "error", "message": traceback.format_exc()}
        if backend is not None:
            diagnostics = backend.diagnostics()
            result_queue.put({"type": "diagnostic", "drm_diagnostics": diagnostics})
            error_event["drm_diagnostics"] = diagnostics
        result_queue.put(error_event)
        idle_event.set()
        ready_event.set()
    finally:
        if backend is not None:
            backend.close()


class _BaseDisplayBackend:
    """Backend interface used by the visual stimulus worker."""

    def display_gray(self, gray_level_u8: int) -> None:
        raise NotImplementedError

    def play(self, stimulus_name: str, enqueue_ns: int) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def diagnostics(self) -> dict[str, Any]:
        """Return lightweight backend diagnostics for debugging.

        Returns:
            dict[str, Any]: JSON-serializable diagnostic snapshot.
        """

        return {}


class VisualStimRuntimeInitError(RuntimeError):
    """Raised when the visual worker fails before runtime startup completes.

    Attributes:
        diagnostics: JSON-serializable DRM diagnostic snapshot captured before
            the init failure was raised to the parent process.
    """

    def __init__(self, message: str, *, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = dict(diagnostics)


class _FakeDisplayBackend(_BaseDisplayBackend):
    """Process-backed fake display used by tests and non-Pi development."""

    def __init__(
        self,
        display_config: DisplayConfig,
        gray_level_u8: int,
        stimuli: dict[str, CompiledGrating],
    ) -> None:
        self.display_config = display_config
        self.gray_level_u8 = gray_level_u8
        self.current_label = "gray"
        self._stimuli = {
            name: {
                "frame_count": stimulus.frame_count,
                "frame_interval_s": stimulus.frame_interval_s,
                "spec_name": stimulus.spec.name,
            }
            for name, stimulus in stimuli.items()
        }

    def display_gray(self, gray_level_u8: int) -> None:
        """Set the fake display state to a uniform gray frame.

        Args:
            gray_level_u8: Grayscale value in uint8 units [0, 255].
        """

        self.gray_level_u8 = gray_level_u8
        self.current_label = "gray"

    def play(self, stimulus_name: str, enqueue_ns: int) -> dict[str, Any]:
        """Simulate a precomputed playback sequence with software timing logs.

        Args:
            stimulus_name: Canonical stimulus identifier.
            enqueue_ns: Parent-process command enqueue timestamp in nanoseconds.

        Returns:
            dict[str, Any]: Timing log for one stimulus presentation.
        """

        stimulus = self._stimuli[stimulus_name]
        frame_interval_s = float(stimulus["frame_interval_s"])
        next_vblank_ns = enqueue_ns + int(round(frame_interval_s * 1_000_000_000.0))
        while time.perf_counter_ns() < next_vblank_ns:
            time.sleep(min(frame_interval_s / 4.0, 0.001))
        first_flip_ns = time.perf_counter_ns()
        self.current_label = stimulus_name

        remaining_frames = max(0, int(stimulus["frame_count"]) - 1)
        for _ in range(remaining_frames):
            time.sleep(frame_interval_s)

        return {
            "stimulus_name": str(stimulus["spec_name"]),
            "enqueue_ns": int(enqueue_ns),
            "first_flip_ns": int(first_flip_ns),
            "frame_count": int(stimulus["frame_count"]),
            "missed_next_vblank": 0,
        }

    def close(self) -> None:
        """Close the fake backend."""

        return None


class _PykmsDisplayBackend(_BaseDisplayBackend):
    """DRM/KMS display backend implemented with Raspberry Pi's pykms bindings."""

    def __init__(
        self,
        display_config: DisplayConfig,
        gray_level_u8: int,
        stimuli: dict[str, CompiledGrating],
    ) -> None:
        import numpy as np
        import pykms  # type: ignore

        self._np = np
        self._pykms = pykms
        self.display_config = display_config
        self.gray_level_u8 = gray_level_u8
        self.card = pykms.Card()
        self.res = pykms.ResourceManager(self.card)
        connector_name = (display_config.connector or "").strip()
        try:
            self.conn = self.res.reserve_connector(connector_name)
        except Exception as exc:
            label = connector_name or "<default>"
            raise RuntimeError(f"DRM connector {label} is unavailable") from exc
        self.crtc = self.res.reserve_crtc(self.conn)
        self.mode = _select_mode(self.conn, display_config.refresh_hz)
        self.frame_period_ns = int(round(1_000_000_000.0 / float(display_config.refresh_hz)))
        self._selector = selectors.DefaultSelector()
        self._selector.register(self.card.fd, selectors.EVENT_READ)
        self._plane = self.res.reserve_primary_plane(self.crtc)
        self._stimuli: dict[str, dict[str, Any]] = {}
        self._modeset_done = False
        self._current_fb_id: int | None = None
        self._gray_framebuffers: dict[int, Any] = {}
        self._last_commit_stage: str | None = None
        self._last_commit_error: str | None = None
        self._last_request_summary: dict[str, Any] = {}

        for name, stimulus in stimuli.items():
            framebuffers = [self._build_framebuffer(frame) for frame in stimulus.frames]
            self._stimuli[name] = {
                "framebuffers": framebuffers,
                "frame_count": stimulus.frame_count,
                "spec_name": stimulus.spec.name,
            }

        self._set_gray_framebuffer(gray_level_u8)

    def display_gray(self, gray_level_u8: int) -> None:
        """Display a uniform gray framebuffer.

        Args:
            gray_level_u8: Grayscale value in uint8 units [0, 255].
        """

        self._set_gray_framebuffer(gray_level_u8)
        gray_fb = self._gray_framebuffers[gray_level_u8]
        if not self._modeset_done:
            self._flip_to_framebuffer(gray_fb, allow_modeset=True)
            self._modeset_done = True
            self._current_fb_id = gray_fb.id
            return
        if self._current_fb_id == gray_fb.id:
            return
        self._flip_to_framebuffer(gray_fb, allow_modeset=False)
        self._wait_for_flip_complete(timeout_s=2.0 / float(self.display_config.refresh_hz))
        self._current_fb_id = gray_fb.id

    def play(self, stimulus_name: str, enqueue_ns: int) -> dict[str, Any]:
        """Display a preloaded stimulus sequence on the DRM primary plane.

        Args:
            stimulus_name: Canonical stimulus identifier.
            enqueue_ns: Parent-process command enqueue timestamp in nanoseconds.

        Returns:
            dict[str, Any]: Timing log for one stimulus presentation.
        """

        stimulus = self._stimuli[stimulus_name]
        framebuffers = stimulus["framebuffers"]
        if not framebuffers:
            raise RuntimeError(f"stimulus {stimulus_name!r} has no preloaded framebuffers")

        first_flip_ns: int | None = None
        for framebuffer in framebuffers:
            allow_modeset = not self._modeset_done
            self._flip_to_framebuffer(framebuffer, allow_modeset=allow_modeset)
            self._modeset_done = True
            if allow_modeset:
                self._current_fb_id = framebuffer.id
                first_flip_ns = time.perf_counter_ns()
                continue
            self._wait_for_flip_complete(timeout_s=2.0 / float(self.display_config.refresh_hz))
            self._current_fb_id = framebuffer.id
            if first_flip_ns is None:
                first_flip_ns = time.perf_counter_ns()

        if first_flip_ns is None:
            first_flip_ns = time.perf_counter_ns()

        return {
            "stimulus_name": str(stimulus["spec_name"]),
            "enqueue_ns": int(enqueue_ns),
            "first_flip_ns": int(first_flip_ns),
            "frame_count": int(stimulus["frame_count"]),
            "missed_next_vblank": int((first_flip_ns - enqueue_ns) > self.frame_period_ns),
        }

    def close(self) -> None:
        """Release DRM plane state and close the selector."""

        try:
            self._selector.close()
        finally:
            try:
                self.card.disable_planes()
            except Exception:
                return None

    def diagnostics(self) -> dict[str, Any]:
        """Return a lightweight DRM resource snapshot for visual debugging.

        Returns:
            dict[str, Any]: JSON-serializable visual DRM diagnostics containing
            reserved object identifiers and the most recent commit outcome.
        """

        requested_connector = str(getattr(self.display_config, "connector", "") or "")
        return {
            "backend": "drm_visual",
            "requested_connector": requested_connector,
            "reserved_connector_id": int(getattr(self.conn, "id", -1)),
            "reserved_connector_name": str(
                getattr(self.conn, "fullname", requested_connector)
            ),
            "reserved_crtc_id": int(getattr(self.crtc, "id", -1)),
            "reserved_plane_id": int(getattr(self._plane, "id", -1)),
            "modeset_done": bool(self._modeset_done),
            "current_framebuffer_id": self._current_fb_id,
            "last_commit_stage": self._last_commit_stage,
            "last_commit_error": self._last_commit_error,
            "last_request": dict(self._last_request_summary),
        }

    def _set_gray_framebuffer(self, gray_level_u8: int) -> None:
        """Create and cache a gray framebuffer if needed.

        Args:
            gray_level_u8: Grayscale value in uint8 units [0, 255].
        """

        if gray_level_u8 in self._gray_framebuffers:
            return
        width_px, height_px = self.display_config.resolution_px
        frame = self._np.full((height_px, width_px), fill_value=gray_level_u8, dtype=self._np.uint8)
        self._gray_framebuffers[gray_level_u8] = self._build_framebuffer(frame)

    def _build_framebuffer(self, frame: Any) -> Any:
        """Allocate a dumb framebuffer and copy one grayscale frame into it.

        Args:
            frame: ``uint8`` grayscale array with shape ``(height_px, width_px)``.

        Returns:
            Any: pykms dumb framebuffer object.
        """

        width_px, height_px = self.display_config.resolution_px
        framebuffer = self._pykms.DumbFramebuffer(self.card, width_px, height_px, "XR24")
        mapped = framebuffer.map(0)
        pixels = self._np.frombuffer(mapped, dtype=self._np.uint8).reshape(height_px, width_px, 4)
        pixels[:, :, 0] = frame
        pixels[:, :, 1] = frame
        pixels[:, :, 2] = frame
        pixels[:, :, 3] = 0
        return framebuffer

    def _flip_to_framebuffer(self, framebuffer: Any, allow_modeset: bool) -> None:
        """Submit one framebuffer to the DRM primary plane.

        Args:
            framebuffer: pykms framebuffer object.
            allow_modeset: Whether the commit may perform the initial modeset.
        """

        if allow_modeset:
            self._last_commit_stage = "modeset"
            mode_blob = self.mode.to_blob(self.card)
            plane_properties = {
                "FB_ID": framebuffer.id,
                "CRTC_ID": self.crtc.id,
                "SRC_X": 0 << 16,
                "SRC_Y": 0 << 16,
                "SRC_W": framebuffer.width << 16,
                "SRC_H": framebuffer.height << 16,
                "CRTC_X": 0,
                "CRTC_Y": 0,
                "CRTC_W": self.mode.hdisplay,
                "CRTC_H": self.mode.vdisplay,
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
            req = self._pykms.AtomicReq(self.card)
            req.add(self.conn, "CRTC_ID", self.crtc.id)
            req.add(self.crtc, {"ACTIVE": 1, "MODE_ID": mode_blob.id})
            req.add(
                self._plane,
                plane_properties,
            )
            ret = _atomic_commit_with_retry(
                commit_call=lambda: req.commit(allow_modeset=True),
                retryable_codes={-errno.EBUSY, -errno.EAGAIN},
                max_attempts=10,
                sleep_s=0.002,
            )
            if ret < 0:
                self._last_commit_error = f"atomic mode set failed with {ret}"
                raise RuntimeError(self._last_commit_error)
            self._last_commit_error = None
            return

        if self.card.has_atomic:
            self._last_commit_stage = "page_flip"
            self._last_request_summary = {
                "commit_kind": "atomic",
                "allow_modeset": False,
                "framebuffer_id": int(framebuffer.id),
                "object_properties": {
                    "crtc_primary_plane": {"FB_ID": int(framebuffer.id)},
                },
            }
            req = self._pykms.AtomicReq(self.card)
            req.add(self.crtc.primary_plane, "FB_ID", framebuffer.id)
            ret = _atomic_commit_with_retry(
                commit_call=req.commit,
                retryable_codes={-errno.EBUSY, -errno.EAGAIN},
                max_attempts=10,
                sleep_s=0.002,
            )
            if ret < 0:
                self._last_commit_error = f"atomic page flip failed with {ret}"
                raise RuntimeError(self._last_commit_error)
            self._last_commit_error = None
            return

        self._last_commit_stage = "legacy_page_flip"
        self._last_request_summary = {
            "commit_kind": "legacy_page_flip",
            "allow_modeset": False,
            "framebuffer_id": int(framebuffer.id),
            "object_properties": {},
        }
        self.crtc.page_flip(framebuffer)
        self._last_commit_error = None

    def _wait_for_flip_complete(self, timeout_s: float) -> None:
        """Block until the DRM driver reports a page-flip completion event.

        Args:
            timeout_s: Maximum time to wait for one flip completion in seconds.

        Returns:
            None: The helper returns after one ``FLIP_COMPLETE`` event.

        Raises:
            TimeoutError: If no ``FLIP_COMPLETE`` event arrives before
                ``timeout_s`` elapses.
        """

        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for DRM page-flip completion")
            events = self._selector.select(remaining)
            if not events:
                continue
            for _key, _mask in events:
                for event in self.card.read_events():
                    if event.type == self._pykms.DrmEventType.FLIP_COMPLETE:
                        return


class _XWindowDisplayBackend(_BaseDisplayBackend):
    """Desktop-window visual backend using pygame for fullscreen presentation."""

    def __init__(
        self,
        display_config: DisplayConfig,
        gray_level_u8: int,
        stimuli: dict[str, CompiledGrating],
    ) -> None:
        try:
            import pygame
        except ImportError as exc:
            raise RuntimeError("xwindow backend requires pygame to be installed") from exc

        self._pygame = pygame
        self.display_config = display_config
        self.gray_level_u8 = int(gray_level_u8)
        self._stimuli: dict[str, dict[str, Any]] = {}
        self.frame_period_ns = int(round(1_000_000_000.0 / float(display_config.refresh_hz)))
        self._display_index = _display_index_from_connector(display_config.connector)

        # Select target monitor before initializing the display subsystem.
        os.environ.setdefault("SDL_VIDEO_FULLSCREEN_DISPLAY", str(self._display_index))
        pygame.init()
        pygame.display.init()
        if hasattr(pygame.display, "set_mode"):
            pygame.display.set_mode(display_config.resolution_px, pygame.FULLSCREEN)
        self._surface = pygame.display.get_surface()
        if self._surface is None:
            raise RuntimeError("xwindow backend failed to create a fullscreen surface")

        for name, stimulus in stimuli.items():
            frame_surfaces = [self._frame_to_surface(frame) for frame in stimulus.frames]
            self._stimuli[name] = {
                "frame_surfaces": frame_surfaces,
                "frame_count": stimulus.frame_count,
                "frame_interval_s": float(stimulus.frame_interval_s),
                "spec_name": stimulus.spec.name,
            }

    def display_gray(self, gray_level_u8: int) -> None:
        """Display a uniform gray frame.

        Args:
            gray_level_u8: Grayscale value in uint8 units [0, 255].
        """

        self.gray_level_u8 = int(gray_level_u8)
        self._surface.fill((self.gray_level_u8, self.gray_level_u8, self.gray_level_u8))
        self._pump_events()
        self._pygame.display.flip()

    def play(self, stimulus_name: str, enqueue_ns: int) -> dict[str, Any]:
        """Display one compiled grating sequence using desktop fullscreen rendering.

        Args:
            stimulus_name: Canonical stimulus identifier.
            enqueue_ns: Parent-process command enqueue timestamp in nanoseconds.

        Returns:
            dict[str, Any]: Timing log for one stimulus presentation.
        """

        stimulus = self._stimuli[stimulus_name]
        frame_surfaces = stimulus["frame_surfaces"]
        first_flip_ns: int | None = None
        frame_interval_s = float(stimulus["frame_interval_s"])

        for frame_surface in frame_surfaces:
            frame_start_ns = time.perf_counter_ns()
            self._surface.blit(frame_surface, (0, 0))
            self._pump_events()
            self._pygame.display.flip()
            if first_flip_ns is None:
                first_flip_ns = time.perf_counter_ns()
            elapsed_s = (time.perf_counter_ns() - frame_start_ns) / 1_000_000_000.0
            sleep_s = max(0.0, frame_interval_s - elapsed_s)
            if sleep_s > 0.0:
                time.sleep(sleep_s)

        if first_flip_ns is None:
            first_flip_ns = time.perf_counter_ns()

        return {
            "stimulus_name": str(stimulus["spec_name"]),
            "enqueue_ns": int(enqueue_ns),
            "first_flip_ns": int(first_flip_ns),
            "frame_count": int(stimulus["frame_count"]),
            "missed_next_vblank": int((first_flip_ns - enqueue_ns) > self.frame_period_ns),
        }

    def close(self) -> None:
        """Close the pygame display backend."""

        try:
            self._pygame.display.quit()
        finally:
            self._pygame.quit()

    def _frame_to_surface(self, frame: Any) -> Any:
        """Convert one grayscale frame array into a pygame surface.

        Args:
            frame: ``uint8`` array with shape ``(height_px, width_px)``.

        Returns:
            Any: pygame surface containing the RGB-expanded frame.
        """

        np = __import__("numpy")
        rgb = np.stack([frame, frame, frame], axis=2)
        rgb_for_pygame = np.transpose(rgb, (1, 0, 2))
        surface = self._pygame.surfarray.make_surface(rgb_for_pygame)
        if surface.get_size() != self.display_config.resolution_px:
            surface = self._pygame.transform.scale(surface, self.display_config.resolution_px)
        return surface

    def _pump_events(self) -> None:
        """Drain pending window events to keep desktop compositor responsive."""

        for _event in self._pygame.event.get():
            pass


def _build_backend(
    display_config: DisplayConfig,
    gray_level_u8: int,
    stimuli: dict[str, CompiledGrating],
) -> _BaseDisplayBackend:
    """Construct the requested backend implementation."""

    if display_config.backend == "fake":
        return _FakeDisplayBackend(display_config, gray_level_u8, stimuli)
    if display_config.backend == "drm":
        return _PykmsDisplayBackend(display_config, gray_level_u8, stimuli)
    if display_config.backend == "xwindow":
        return _XWindowDisplayBackend(display_config, gray_level_u8, stimuli)
    raise ValueError(f"unsupported backend {display_config.backend!r}")


def _best_effort_realtime_config() -> None:
    """Try to reduce worker scheduling jitter without failing if the OS denies it."""

    try:
        if hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, {0})
    except Exception:
        pass
    try:
        param = os.sched_param(1)
        os.sched_setscheduler(0, os.SCHED_FIFO, param)
    except Exception:
        pass


def _runtime_context() -> mp.context.BaseContext:
    """Select a multiprocessing start method suitable for the current host."""

    methods = mp.get_all_start_methods()
    if sys.platform.startswith("linux") and "fork" in methods:
        return mp.get_context("fork")
    return mp.get_context("spawn")


def _select_mode(conn: Any, requested_refresh_hz: float | None) -> Any:
    """Select a connector mode matching the requested refresh rate when possible."""

    default_mode = conn.get_default_mode()
    if requested_refresh_hz is None:
        return default_mode
    for mode in conn.get_modes():
        if int(getattr(mode, "hdisplay", -1)) != int(default_mode.hdisplay):
            continue
        if int(getattr(mode, "vdisplay", -1)) != int(default_mode.vdisplay):
            continue
        if abs(float(getattr(mode, "vrefresh", 0.0)) - float(requested_refresh_hz)) < 0.5:
            return mode
    return default_mode


def _display_index_from_connector(connector: str | None) -> int:
    """Map connector names such as ``HDMI-A-2`` to SDL display indices."""

    connector_name = str(connector or "").strip().upper()
    if connector_name.endswith("-2"):
        return 1
    return 0


def _atomic_commit_with_retry(
    commit_call: Any,
    retryable_codes: set[int],
    max_attempts: int,
    sleep_s: float,
) -> int:
    """Run one DRM atomic commit with bounded retry on transient busy errors.

    Args:
        commit_call: Zero-argument callable returning DRM commit status code.
        retryable_codes: Negative errno-style return codes to retry.
        max_attempts: Maximum number of attempts before returning failure.
        sleep_s: Sleep duration between retries in seconds.

    Returns:
        int: Final commit status code returned by ``commit_call``.
    """

    attempts = max(1, int(max_attempts))
    for attempt_index in range(attempts):
        ret = int(commit_call())
        if ret >= 0:
            return ret
        if ret not in retryable_codes:
            return ret
        if attempt_index == attempts - 1:
            return ret
        time.sleep(float(sleep_s))
    return -1
