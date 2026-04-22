from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from debug.shared_drm_debug import SharedDrmController


class _FakeMode:
    """Minimal fake DRM mode carrying geometry and blob conversion."""

    def __init__(self, width_px: int, height_px: int, refresh_hz: float) -> None:
        self.hdisplay = width_px
        self.vdisplay = height_px
        self.vrefresh = refresh_hz

    def to_blob(self, _card: object) -> object:
        return SimpleNamespace(id=900)


class _FakeConnector:
    """Fake DRM connector with a default mode."""

    def __init__(self, connector_id: int, name: str, mode: _FakeMode) -> None:
        self.id = connector_id
        self.fullname = name
        self._mode = mode

    def get_default_mode(self) -> _FakeMode:
        return self._mode


class _FakePlane:
    """Fake DRM plane carrying only an id."""

    def __init__(self, plane_id: int) -> None:
        self.id = plane_id


class _FakeCrtc:
    """Fake DRM CRTC with a primary plane."""

    def __init__(self, crtc_id: int, primary_plane: _FakePlane) -> None:
        self.id = crtc_id
        self.primary_plane = primary_plane


class _FakeMap:
    """Byte buffer wrapper used by the fake framebuffer."""

    def __init__(self, size_bytes: int) -> None:
        self._buffer = bytearray(size_bytes)

    def __buffer__(self) -> memoryview:  # pragma: no cover - compatibility hook
        return memoryview(self._buffer)


class _FakeFramebuffer:
    """Fake dumb framebuffer with XR24-sized backing memory."""

    _next_id = 1000

    def __init__(self, _card: object, width_px: int, height_px: int, _fmt: str) -> None:
        self.width = width_px
        self.height = height_px
        self.id = _FakeFramebuffer._next_id
        _FakeFramebuffer._next_id += 1
        self._map = _FakeMap(width_px * height_px * 4)

    def map(self, _offset: int) -> memoryview:
        return memoryview(self._map._buffer)


class _FakeAtomicReq:
    """Fake atomic request that records requested properties."""

    last_commit_allow_modeset: bool | None = None
    last_requests: list[tuple[object, object, object | None]] = []

    def __init__(self, _card: object) -> None:
        self.requests: list[tuple[object, object, object | None]] = []

    def add(self, obj: object, props: object, value: object | None = None) -> None:
        self.requests.append((obj, props, value))

    def commit(self, allow_modeset: bool = False) -> int:
        _FakeAtomicReq.last_commit_allow_modeset = allow_modeset
        _FakeAtomicReq.last_requests = list(self.requests)
        return 0


class _FakeCard:
    """Fake DRM card used by the shared debug controller tests."""

    def __init__(self) -> None:
        self.fd = 11
        self.disable_planes_calls = 0

    def disable_planes(self) -> None:
        self.disable_planes_calls += 1

    def read_events(self) -> list[object]:
        return [SimpleNamespace(type="flip")]


class _FakeSelector:
    """Fake selector supporting register/select/close calls."""

    def __init__(self) -> None:
        self.closed = False

    def register(self, _fd: int, _event: object) -> None:
        return None

    def select(self, _timeout_s: float) -> list[tuple[object, object]]:
        return [(object(), object())]

    def close(self) -> None:
        self.closed = True


class _FakeResourceManager:
    """Fake resource manager exposing two connectors and distinct CRTCs/planes."""

    def __init__(self, _card: object) -> None:
        self._connectors = {
            "HDMI-A-1": _FakeConnector(33, "HDMI-A-1", _FakeMode(640, 480, 60.0)),
            "HDMI-A-2": _FakeConnector(42, "HDMI-A-2", _FakeMode(1024, 600, 60.0)),
        }
        self._crtcs = {
            "HDMI-A-1": _FakeCrtc(92, _FakePlane(801)),
            "HDMI-A-2": _FakeCrtc(104, _FakePlane(903)),
        }
        self._reserved_planes = {
            92: _FakePlane(81),
            104: _FakePlane(93),
        }

    def reserve_connector(self, connector_name: str) -> _FakeConnector:
        return self._connectors[connector_name]

    def reserve_crtc(self, connector: _FakeConnector) -> _FakeCrtc:
        return self._crtcs[connector.fullname]

    def reserve_primary_plane(self, crtc: _FakeCrtc) -> _FakePlane:
        return self._reserved_planes[crtc.id]


def _fake_pykms() -> object:
    return SimpleNamespace(
        Card=_FakeCard,
        ResourceManager=_FakeResourceManager,
        DumbFramebuffer=_FakeFramebuffer,
        AtomicReq=_FakeAtomicReq,
        DrmEventType=SimpleNamespace(FLIP_COMPLETE="flip"),
    )


def test_shared_drm_controller_rejects_duplicate_connectors() -> None:
    """The shared controller should fail fast on duplicate connector requests."""

    with pytest.raises(ValueError, match="duplicate connectors"):
        SharedDrmController(
            preview_connector="HDMI-A-1",
            stimulus_connector="HDMI-A-1",
            pykms_module=_fake_pykms(),
            selector_factory=_FakeSelector,
        )


def test_shared_drm_controller_reports_both_output_diagnostics() -> None:
    """Diagnostics should expose both outputs' DRM reservations.

    Returns:
        None: Assertions validate connector, CRTC, and plane identifiers.
    """

    controller = SharedDrmController(
        preview_connector="HDMI-A-1",
        stimulus_connector="HDMI-A-2",
        pykms_module=_fake_pykms(),
        selector_factory=_FakeSelector,
    )

    diagnostics = controller.diagnostics()

    assert diagnostics["preview"]["reserved_connector_id"] == 33
    assert diagnostics["preview"]["reserved_crtc_id"] == 92
    assert diagnostics["stimulus"]["reserved_connector_id"] == 42
    assert diagnostics["stimulus"]["reserved_plane_id"] == 93


def test_shared_drm_controller_records_first_request_summary() -> None:
    """Displaying on one output should record the first atomic request summary."""

    controller = SharedDrmController(
        preview_connector="HDMI-A-1",
        stimulus_connector="HDMI-A-2",
        pykms_module=_fake_pykms(),
        selector_factory=_FakeSelector,
    )
    frame_rgb = np.zeros((480, 640, 3), dtype=np.uint8)

    controller.preview.display_rgb_frame(frame_rgb)

    diagnostics = controller.preview.diagnostics()
    assert diagnostics["last_request"]["allow_modeset"] is True
    assert diagnostics["last_request"]["object_properties"]["connector"]["CRTC_ID"] == 92
    assert diagnostics["last_request"]["object_properties"]["plane"]["CRTC_W"] == 640


def test_shared_drm_preview_page_flip_targets_reserved_preview_plane() -> None:
    """Preview page flips should target the reserved preview plane, not CRTC primary_plane."""

    controller = SharedDrmController(
        preview_connector="HDMI-A-1",
        stimulus_connector="HDMI-A-2",
        pykms_module=_fake_pykms(),
        selector_factory=_FakeSelector,
    )
    frame_rgb = np.zeros((480, 640, 3), dtype=np.uint8)

    controller.preview.display_rgb_frame(frame_rgb)
    controller.preview.display_rgb_frame(frame_rgb)

    page_flip_target, props, value = _FakeAtomicReq.last_requests[-1]
    assert page_flip_target is controller.preview.plane
    assert page_flip_target is not controller.preview.crtc.primary_plane
    assert isinstance(props, dict)
    assert value is None
    diagnostics = controller.preview.diagnostics()
    plane_props = diagnostics["last_request"]["object_properties"]["plane"]
    assert props["FB_ID"] == plane_props["FB_ID"]
    assert plane_props["CRTC_ID"] == 92
    assert plane_props["CRTC_W"] == 640
    assert plane_props["CRTC_H"] == 480
    assert plane_props["SRC_W"] == 640 << 16
    assert plane_props["SRC_H"] == 480 << 16


def test_shared_drm_preview_page_flip_waits_for_flip_completion() -> None:
    """Preview page flips should wait for flip completion after a non-modeset commit."""

    wait_calls: list[float] = []

    class TrackingController(SharedDrmController):
        def wait_for_flip_complete(self, timeout_s: float) -> None:
            wait_calls.append(float(timeout_s))

    controller = TrackingController(
        preview_connector="HDMI-A-1",
        stimulus_connector="HDMI-A-2",
        pykms_module=_fake_pykms(),
        selector_factory=_FakeSelector,
    )
    frame_rgb = np.zeros((480, 640, 3), dtype=np.uint8)

    controller.preview.display_rgb_frame(frame_rgb)
    controller.preview.display_rgb_frame(frame_rgb)

    assert len(wait_calls) == 1
    assert wait_calls[0] > 0.0


def test_shared_drm_stimulus_page_flip_targets_reserved_stimulus_plane() -> None:
    """Stimulus page flips should target the reserved stimulus plane, not CRTC primary_plane."""

    controller = SharedDrmController(
        preview_connector="HDMI-A-1",
        stimulus_connector="HDMI-A-2",
        pykms_module=_fake_pykms(),
        selector_factory=_FakeSelector,
    )

    controller.stimulus.display_gray(127)
    controller.stimulus.display_gray(126)

    page_flip_target, props, value = _FakeAtomicReq.last_requests[-1]
    assert page_flip_target is controller.stimulus.plane
    assert page_flip_target is not controller.stimulus.crtc.primary_plane
    assert isinstance(props, dict)
    assert value is None
    diagnostics = controller.stimulus.diagnostics()
    plane_props = diagnostics["last_request"]["object_properties"]["plane"]
    assert props["FB_ID"] == plane_props["FB_ID"]
    assert plane_props["CRTC_ID"] == 104
    assert plane_props["CRTC_W"] == 1024
    assert plane_props["CRTC_H"] == 600
    assert plane_props["SRC_W"] == 1024 << 16
    assert plane_props["SRC_H"] == 600 << 16


def test_shared_drm_stimulus_initial_gray_waits_for_flip_completion() -> None:
    """Initial stimulus gray display should wait for modeset completion before later page flips."""

    wait_calls: list[float] = []

    class TrackingController(SharedDrmController):
        def wait_for_flip_complete(self, timeout_s: float) -> None:
            wait_calls.append(float(timeout_s))

    controller = TrackingController(
        preview_connector="HDMI-A-1",
        stimulus_connector="HDMI-A-2",
        pykms_module=_fake_pykms(),
        selector_factory=_FakeSelector,
    )

    controller.stimulus.display_gray(127)

    assert len(wait_calls) == 1
    assert wait_calls[0] > 0.0


def test_shared_drm_stimulus_gray_frame_page_flip_targets_reserved_plane() -> None:
    """Stimulus grayscale-frame page flips should use the reserved stimulus plane."""

    controller = SharedDrmController(
        preview_connector="HDMI-A-1",
        stimulus_connector="HDMI-A-2",
        pykms_module=_fake_pykms(),
        selector_factory=_FakeSelector,
    )
    gray_frame = np.zeros((600, 1024), dtype=np.uint8)

    controller.stimulus.display_gray_frame(gray_frame)
    controller.stimulus.display_gray_frame(gray_frame)

    page_flip_target, props, value = _FakeAtomicReq.last_requests[-1]
    assert page_flip_target is controller.stimulus.plane
    assert isinstance(props, dict)
    assert value is None
    diagnostics = controller.stimulus.diagnostics()
    plane_props = diagnostics["last_request"]["object_properties"]["plane"]
    assert props["FB_ID"] == plane_props["FB_ID"]
    assert plane_props["CRTC_ID"] == 104
