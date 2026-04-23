"""Tests for the low-latency visual stimulus runtime."""

from __future__ import annotations

from pathlib import Path
import queue
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from box_runtime.visual_stimuli.visualstim import VisualStim
from box_runtime.visual_stimuli.visual_runtime.grating_compiler import compile_grating
from box_runtime.visual_stimuli.visual_runtime.drm_runtime import (
    _PykmsDisplayBackend,
    _atomic_commit_with_retry,
    query_display_config,
    DisplayConfig,
    VisualStimRuntime,
)
from box_runtime.visual_stimuli.visual_runtime.grating_specs import load_grating_spec


def _write_spec(path: Path, **overrides: object) -> Path:
    """Write a YAML grating specification file and return its path.

    Args:
        path: Output path for the YAML file.
        **overrides: Values that replace the default specification fields.

    Returns:
        Path: Absolute path to the written YAML spec file.
    """

    payload = {
        "name": "go_grating",
        "duration_s": 0.1,
        "angle_deg": 45.0,
        "spatial_freq_cpd": 0.08,
        "temporal_freq_hz": 1.5,
        "contrast": 0.9,
        "background_gray_u8": 96,
        "waveform": "sine",
    }
    payload.update(overrides)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _session_info(spec_paths: list[Path]) -> dict[str, object]:
    """Build the minimum VisualStim session info needed by tests.

    Args:
        spec_paths: Ordered list of YAML spec paths to preload.

    Returns:
        dict[str, object]: Session configuration compatible with VisualStim.
    """

    return {
        "vis_gratings": [str(path) for path in spec_paths],
        "gray_level": 64,
        "visual_display_backend": "fake",
        "visual_backend": "fake",
        "visual_display_resolution_px": [32, 24],
        "visual_display_refresh_hz": 60.0,
        "visual_display_degrees_subtended": 80.0,
    }


def test_visualstim_public_api_compatibility(tmp_path: Path) -> None:
    """VisualStim should preserve the legacy public surface while using the new backend.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = _write_spec(tmp_path / "go_grating.yaml")
    visual = VisualStim(_session_info([spec_path]))

    try:
        assert "go_grating" in visual.gratings
        assert "go_grating.yaml" in visual.gratings
        assert hasattr(visual, "show_grating")
        assert hasattr(visual, "process_function")
        assert hasattr(visual, "load_grating_file")
        assert hasattr(visual, "load_session_gratings")
        assert hasattr(visual.myscreen, "display_greyscale")
        assert hasattr(visual.myscreen, "close")
    finally:
        visual.myscreen.close()


def test_yaml_grating_spec_validation_missing_fields_raises(tmp_path: Path) -> None:
    """Loading a spec missing required fields should fail with a clear validation error.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = _write_spec(tmp_path / "missing_waveform.yaml")
    payload = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    payload.pop("waveform")
    spec_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="waveform"):
        load_grating_spec(spec_path)


def test_loader_accepts_yaml_comments(tmp_path: Path) -> None:
    """YAML comments should be accepted without affecting parsed stimulus values.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = tmp_path / "commented_grating.yaml"
    spec_path.write_text(
        "\n".join(
            [
                "# user-facing comment",
                'name: "commented_grating"',
                "duration_s: 0.1",
                "angle_deg: 45.0  # orientation in degrees",
                "spatial_freq_cpd: 0.08",
                "temporal_freq_hz: 1.5",
                "contrast: 0.9",
                "background_gray_u8: 96",
                'waveform: "sine"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    spec = load_grating_spec(spec_path)

    assert spec.name == "commented_grating"
    assert spec.angle_deg == pytest.approx(45.0)


def test_loader_accepts_yml_extension(tmp_path: Path) -> None:
    """The spec loader should accept the .yml extension in addition to .yaml.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = _write_spec(tmp_path / "go_grating.yml")
    spec = load_grating_spec(spec_path)

    assert spec.name == "go_grating"


def test_loader_rejects_json_specs_with_migration_error(tmp_path: Path) -> None:
    """JSON-authored spec files should fail with a clear migration message.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = tmp_path / "legacy_grating.json"
    spec_path.write_text(
        "\n".join(
            [
                "{",
                '  "name": "legacy_grating",',
                '  "duration_s": 0.1,',
                '  "angle_deg": 45.0,',
                '  "spatial_freq_cpd": 0.08,',
                '  "temporal_freq_hz": 1.5,',
                '  "contrast": 0.9,',
                '  "background_gray_u8": 96,',
                '  "waveform": "sine"',
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="YAML"):
        load_grating_spec(spec_path)


def test_grating_compiler_output_contract(tmp_path: Path) -> None:
    """Compiled gratings should expose the documented frame dtype, shape, and gray range.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = _write_spec(
        tmp_path / "static_gray.yaml",
        name="static_gray",
        duration_s=0.1,
        contrast=0.0,
        background_gray_u8=111,
    )
    spec = load_grating_spec(spec_path)
    compiled = compile_grating(
        spec=spec,
        resolution_px=(32, 24),
        refresh_hz=60.0,
        degrees_subtended=80.0,
    )

    assert compiled.frames.dtype == np.uint8
    assert compiled.frames.shape == (6, 24, 32)
    assert int(compiled.frames.min()) == 111
    assert int(compiled.frames.max()) == 111


def test_visualstim_passes_configured_display_connector(monkeypatch, tmp_path: Path) -> None:
    """VisualStim should pass the configured DRM connector into display discovery.

    Inputs:
        monkeypatch: pytest fixture used to patch display-config discovery.
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    observed: dict[str, object] = {}

    def fake_query_display_config(
        backend: str,
        requested_resolution_px: tuple[int, int] | None = None,
        requested_refresh_hz: float | None = None,
        requested_connector: str | None = None,
    ):
        observed["backend"] = backend
        observed["requested_connector"] = requested_connector
        observed["requested_resolution_px"] = requested_resolution_px
        observed["requested_refresh_hz"] = requested_refresh_hz
        return SimpleNamespace(backend="fake", resolution_px=(32, 24), refresh_hz=60.0)

    monkeypatch.setattr(
        "box_runtime.visual_stimuli.visualstim.query_display_config",
        fake_query_display_config,
    )

    spec_path = _write_spec(tmp_path / "go_grating.yaml")
    session_info = _session_info([spec_path])
    session_info["visual_display_connector"] = "HDMI-A-9"
    visual = VisualStim(session_info)

    try:
        assert observed["backend"] == "fake"
        assert observed["requested_connector"] == "HDMI-A-9"
    finally:
        visual.myscreen.close()


def test_visualstim_prefers_visual_display_backend_over_legacy_key(monkeypatch, tmp_path: Path) -> None:
    """The standardized backend key should override the legacy visual_backend field.

    Inputs:
        monkeypatch: pytest fixture used to patch display-config discovery.
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    observed: dict[str, object] = {}

    def fake_query_display_config(
        backend: str,
        requested_resolution_px: tuple[int, int] | None = None,
        requested_refresh_hz: float | None = None,
        requested_connector: str | None = None,
    ):
        observed["backend"] = backend
        observed["requested_connector"] = requested_connector
        return SimpleNamespace(backend="fake", resolution_px=(32, 24), refresh_hz=60.0)

    monkeypatch.setattr(
        "box_runtime.visual_stimuli.visualstim.query_display_config",
        fake_query_display_config,
    )

    spec_path = _write_spec(tmp_path / "go_grating.yaml")
    session_info = _session_info([spec_path])
    session_info["visual_display_backend"] = "fake"
    session_info["visual_backend"] = "drm"
    visual = VisualStim(session_info)

    try:
        assert observed["backend"] == "fake"
    finally:
        visual.myscreen.close()


def test_visualstim_defaults_to_hdmi_a_2_connector(monkeypatch, tmp_path: Path) -> None:
    """VisualStim should default to HDMI-A-2 for one-Pi visual stimulus output.

    Inputs:
        monkeypatch: pytest fixture used to patch display-config discovery.
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    observed: dict[str, object] = {}

    def fake_query_display_config(
        backend: str,
        requested_resolution_px: tuple[int, int] | None = None,
        requested_refresh_hz: float | None = None,
        requested_connector: str | None = None,
    ):
        observed["requested_connector"] = requested_connector
        return SimpleNamespace(backend="fake", resolution_px=(32, 24), refresh_hz=60.0)

    monkeypatch.setattr(
        "box_runtime.visual_stimuli.visualstim.query_display_config",
        fake_query_display_config,
    )

    spec_path = _write_spec(tmp_path / "go_grating.yaml")
    session_info = _session_info([spec_path])
    session_info.pop("visual_display_connector", None)
    visual = VisualStim(session_info)

    try:
        assert observed["requested_connector"] == "HDMI-A-2"
    finally:
        visual.myscreen.close()


def test_query_display_config_missing_connector_raises_clear_error(monkeypatch) -> None:
    """DRM connector lookup should fail clearly when the requested output is absent.

    Inputs:
        monkeypatch: pytest fixture used to install a fake pykms module.

    Returns:
        None.
    """

    class FakeResourceManager:
        def __init__(self, card) -> None:
            self.card = card

        def reserve_connector(self, name: str):
            raise RuntimeError(f"connector {name} is unavailable")

    fake_pykms = SimpleNamespace(
        Card=lambda: object(),
        ResourceManager=FakeResourceManager,
    )
    monkeypatch.setitem(sys.modules, "pykms", fake_pykms)

    with pytest.raises(ValueError, match="HDMI-A-9"):
        query_display_config(
            backend="drm",
            requested_resolution_px=None,
            requested_refresh_hz=None,
            requested_connector="HDMI-A-9",
        )


def test_query_display_config_xwindow_uses_requested_resolution_and_refresh() -> None:
    """xwindow backend should accept requested geometry without DRM discovery.

    Returns:
        None.
    """

    config = query_display_config(
        backend="xwindow",
        requested_resolution_px=(1280, 720),
        requested_refresh_hz=75.0,
        requested_connector="HDMI-A-2",
    )

    assert config.backend == "xwindow"
    assert config.resolution_px == (1280, 720)
    assert config.refresh_hz == pytest.approx(75.0)
    assert config.connector == "HDMI-A-2"


def test_show_grating_uses_persistent_worker(tmp_path: Path) -> None:
    """Repeated play requests should reuse a single worker process.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = _write_spec(tmp_path / "go_grating.yaml")
    visual = VisualStim(_session_info([spec_path]))

    try:
        worker_pid_before = visual._runtime.worker_pid
        visual.show_grating("go_grating")
        visual._runtime.wait_until_idle(timeout_s=2.0)
        visual.show_grating("go_grating")
        visual._runtime.wait_until_idle(timeout_s=2.0)

        assert visual._runtime.worker_pid == worker_pid_before
        assert visual._runtime.get_metrics()["play_count"] == 2
    finally:
        visual.myscreen.close()


def test_load_grating_file_after_init_updates_runtime(tmp_path: Path) -> None:
    """Loading a new spec after init should rebuild the worker and make it playable.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    first_spec = _write_spec(tmp_path / "go_grating.yaml")
    second_spec = _write_spec(
        tmp_path / "nogo_grating.yml",
        name="nogo_grating",
        angle_deg=135.0,
        waveform="square",
    )
    visual = VisualStim(_session_info([first_spec]))

    try:
        original_pid = visual._runtime.worker_pid
        visual.load_grating_file(second_spec)
        visual.show_grating("nogo_grating")
        visual._runtime.wait_until_idle(timeout_s=2.0)

        assert visual._runtime.worker_pid != original_pid
        assert visual._runtime.get_metrics()["play_count"] == 1
    finally:
        visual.myscreen.close()


def test_load_grating_dir_finds_yaml_and_yml(tmp_path: Path) -> None:
    """Directory loading should scan both .yaml and .yml stimulus spec files.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    yaml_path = _write_spec(tmp_path / "go_grating.yaml")
    yml_path = _write_spec(
        tmp_path / "nogo_grating.yml",
        name="nogo_grating",
        angle_deg=135.0,
        waveform="square",
    )
    visual = VisualStim(_session_info([]))

    try:
        visual.load_grating_dir(tmp_path)

        assert yaml_path.name in visual.gratings
        assert yml_path.name in visual.gratings
        assert "go_grating" in visual.gratings
        assert "nogo_grating" in visual.gratings
    finally:
        visual.myscreen.close()


def test_myscreen_close_shuts_worker_cleanly(tmp_path: Path) -> None:
    """The compatibility myscreen.close shim should stop the display worker.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = _write_spec(tmp_path / "go_grating.yaml")
    visual = VisualStim(_session_info([spec_path]))

    visual.myscreen.close()

    assert not visual._runtime.is_alive()


def test_visualstim_close_is_idempotent_and_stops_worker(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path / "go_grating.yaml")
    visual = VisualStim(_session_info([spec_path]))

    visual.close()
    visual.close()

    assert not visual._runtime.is_alive()


def test_atomic_commit_with_retry_succeeds_after_transient_busy() -> None:
    statuses = iter([-16, -16, 0])

    def fake_commit() -> int:
        return next(statuses)

    result = _atomic_commit_with_retry(
        commit_call=fake_commit,
        retryable_codes={-16},
        max_attempts=5,
        sleep_s=0.0,
    )

    assert result == 0


def test_atomic_commit_with_retry_returns_last_retryable_failure() -> None:
    statuses = iter([-16, -16, -16])

    def fake_commit() -> int:
        return next(statuses)

    result = _atomic_commit_with_retry(
        commit_call=fake_commit,
        retryable_codes={-16},
        max_attempts=3,
        sleep_s=0.0,
    )

    assert result == -16


def test_pykms_backend_exposes_flip_wait_helper() -> None:
    """The DRM backend should define the flip-completion helper it calls.

    Returns:
        None.
    """

    assert hasattr(_PykmsDisplayBackend, "_wait_for_flip_complete")


def test_pykms_wait_for_flip_complete_returns_on_flip_event(monkeypatch) -> None:
    """The DRM backend should stop waiting when a flip-complete event arrives.

    Args:
        monkeypatch: Pytest fixture used to control the monotonic clock.

    Returns:
        None.
    """

    backend = object.__new__(_PykmsDisplayBackend)
    flip_complete_type = object()
    backend._pykms = SimpleNamespace(DrmEventType=SimpleNamespace(FLIP_COMPLETE=flip_complete_type))
    backend.card = SimpleNamespace(read_events=lambda: [SimpleNamespace(type=flip_complete_type)])
    backend._selector = SimpleNamespace(select=lambda _timeout: [(object(), object())])
    monotonic_values = iter([0.0, 0.0, 0.0])
    monkeypatch.setattr(
        "box_runtime.visual_stimuli.visual_runtime.drm_runtime.time.monotonic",
        lambda: next(monotonic_values),
    )

    backend._wait_for_flip_complete(timeout_s=0.1)


def test_pykms_wait_for_flip_complete_times_out_without_flip_event(monkeypatch) -> None:
    """The DRM backend should raise TimeoutError when no flip event appears.

    Args:
        monkeypatch: Pytest fixture used to control the monotonic clock.

    Returns:
        None.
    """

    backend = object.__new__(_PykmsDisplayBackend)
    flip_complete_type = object()
    backend._pykms = SimpleNamespace(DrmEventType=SimpleNamespace(FLIP_COMPLETE=flip_complete_type))
    backend.card = SimpleNamespace(read_events=lambda: [])
    backend._selector = SimpleNamespace(select=lambda _timeout: [])
    monotonic_values = iter([0.0, 0.05, 0.11])
    monkeypatch.setattr(
        "box_runtime.visual_stimuli.visual_runtime.drm_runtime.time.monotonic",
        lambda: next(monotonic_values),
    )

    with pytest.raises(TimeoutError, match="timed out"):
        backend._wait_for_flip_complete(timeout_s=0.1)


def test_pykms_backend_reports_connector_crtc_and_plane_diagnostics() -> None:
    """The DRM visual backend should expose lightweight resource diagnostics.

    Returns:
        None.
    """

    backend = object.__new__(_PykmsDisplayBackend)
    backend.display_config = SimpleNamespace(connector="HDMI-A-2")
    backend.conn = SimpleNamespace(id=41, fullname="HDMI-A-2")
    backend.crtc = SimpleNamespace(id=52)
    backend._plane = SimpleNamespace(id=63)
    backend._modeset_done = True
    backend._current_fb_id = 77
    backend._last_commit_stage = "modeset"
    backend._last_commit_error = "atomic mode set failed with -13"
    backend._last_request_summary = {
        "commit_kind": "atomic",
        "allow_modeset": True,
        "framebuffer_id": 77,
        "object_properties": {
            "connector": {"CRTC_ID": 52},
            "crtc": {"ACTIVE": 1, "MODE_ID": 99},
            "plane": {"FB_ID": 77},
        },
    }

    diagnostics = backend.diagnostics()

    assert diagnostics["backend"] == "drm_visual"
    assert diagnostics["requested_connector"] == "HDMI-A-2"
    assert diagnostics["reserved_connector_id"] == 41
    assert diagnostics["reserved_connector_name"] == "HDMI-A-2"
    assert diagnostics["reserved_crtc_id"] == 52
    assert diagnostics["reserved_plane_id"] == 63
    assert diagnostics["modeset_done"] is True
    assert diagnostics["current_framebuffer_id"] == 77
    assert diagnostics["last_commit_stage"] == "modeset"
    assert diagnostics["last_commit_error"] == "atomic mode set failed with -13"
    assert diagnostics["last_request"]["allow_modeset"] is True
    assert diagnostics["last_request"]["object_properties"]["plane"]["FB_ID"] == 77


def test_pykms_backend_records_page_flip_request_summary(monkeypatch) -> None:
    """Visual page flips should preserve the submitted atomic request summary.

    Args:
        monkeypatch: Pytest fixture used to patch atomic request creation.
    """

    class FakeAtomicReq:
        def __init__(self, card: object) -> None:
            self.card = card
            self.calls: list[tuple[object, object, object]] = []

        def add(self, obj: object, prop: object, value: object | None = None) -> None:
            self.calls.append((obj, prop, value))

        def commit(self, allow_modeset: bool = False) -> int:
            return 0

    req_instances: list[FakeAtomicReq] = []

    def fake_atomic_req(card: object) -> FakeAtomicReq:
        req = FakeAtomicReq(card)
        req_instances.append(req)
        return req

    backend = object.__new__(_PykmsDisplayBackend)
    backend._pykms = SimpleNamespace(AtomicReq=fake_atomic_req)
    backend.card = SimpleNamespace(has_atomic=True)
    backend.display_config = SimpleNamespace(connector="HDMI-A-2")
    backend.conn = SimpleNamespace(id=41, fullname="HDMI-A-2")
    backend.crtc = SimpleNamespace(id=52, primary_plane="crtc-primary-plane")
    backend._plane = SimpleNamespace(id=63)
    backend._modeset_done = True
    backend._current_fb_id = None
    backend._last_commit_stage = None
    backend._last_commit_error = None
    backend._last_request_summary = {}

    framebuffer = SimpleNamespace(id=77, width=800, height=600)

    backend._flip_to_framebuffer(framebuffer, allow_modeset=False)

    diagnostics = backend.diagnostics()
    assert diagnostics["last_request"]["allow_modeset"] is False
    assert diagnostics["last_request"]["framebuffer_id"] == 77
    assert diagnostics["last_request"]["object_properties"]["crtc_primary_plane"]["FB_ID"] == 77


def test_visual_runtime_get_metrics_includes_drm_diagnostics() -> None:
    """Parent metrics snapshots should include the latest worker DRM diagnostics.

    Returns:
        None.
    """

    runtime = object.__new__(VisualStimRuntime)
    runtime._metrics = {"play_count": 0, "current_label": "gray", "timing_log": []}
    runtime._error_message = None
    runtime._drain_events = lambda: None
    runtime._drm_diagnostics = {
        "backend": "drm_visual",
        "requested_connector": "HDMI-A-2",
        "reserved_crtc_id": 52,
    }

    metrics = runtime.get_metrics()

    assert metrics["drm_diagnostics"]["backend"] == "drm_visual"
    assert metrics["drm_diagnostics"]["reserved_crtc_id"] == 52


def test_visual_runtime_error_event_updates_drm_diagnostics() -> None:
    """Error events should be allowed to carry the latest DRM diagnostics.

    Returns:
        None.
    """

    runtime = object.__new__(VisualStimRuntime)
    runtime._metrics = {"play_count": 0, "current_label": "gray", "timing_log": []}
    runtime._error_message = None
    runtime._drm_diagnostics = {}

    class FakeQueue:
        def __init__(self) -> None:
            self._items = [
                {
                    "type": "error",
                    "message": "RuntimeError: atomic mode set failed with -13",
                    "drm_diagnostics": {
                        "backend": "drm_visual",
                        "requested_connector": "HDMI-A-2",
                        "reserved_plane_id": 63,
                    },
                }
            ]

        def get_nowait(self) -> dict[str, object]:
            if not self._items:
                raise queue.Empty
            return self._items.pop(0)

    runtime._result_queue = FakeQueue()

    runtime._drain_events()

    assert runtime._error_message == "RuntimeError: atomic mode set failed with -13"
    assert runtime._drm_diagnostics["requested_connector"] == "HDMI-A-2"
    assert runtime._drm_diagnostics["reserved_plane_id"] == 63


def test_visual_runtime_init_failure_preserves_latest_drm_diagnostics(monkeypatch) -> None:
    """Runtime init errors should retain the latest worker DRM diagnostics.

    Args:
        monkeypatch: Pytest fixture used to replace multiprocessing context.

    Returns:
        None.
    """

    class FakeQueue:
        def __init__(self, items: list[dict[str, object]]) -> None:
            self._items = list(items)

        def put(self, item: dict[str, object]) -> None:
            self._items.append(item)

        def get_nowait(self) -> dict[str, object]:
            if not self._items:
                raise queue.Empty
            return self._items.pop(0)

    class FakeEvent:
        def wait(self, timeout: float | None = None) -> bool:
            return True

        def set(self) -> None:
            return None

        def clear(self) -> None:
            return None

    class FakeProcess:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.pid = 4321

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            return None

        def terminate(self) -> None:
            return None

    class FakeContext:
        def __init__(self) -> None:
            self._queue_calls = 0

        def Queue(self) -> FakeQueue:
            self._queue_calls += 1
            if self._queue_calls == 1:
                return FakeQueue([])
            return FakeQueue(
                [
                    {
                        "type": "diagnostic",
                        "drm_diagnostics": {
                            "backend": "drm_visual",
                            "requested_connector": "HDMI-A-2",
                            "reserved_crtc_id": 52,
                            "last_commit_stage": "modeset",
                            "last_commit_error": "atomic mode set failed with -13",
                        },
                    },
                    {
                        "type": "error",
                        "message": "RuntimeError: atomic mode set failed with -13",
                    },
                ]
            )

        def Event(self) -> FakeEvent:
            return FakeEvent()

        def Process(self, *args: object, **kwargs: object) -> FakeProcess:
            return FakeProcess(*args, **kwargs)

    monkeypatch.setattr(
        "box_runtime.visual_stimuli.visual_runtime.drm_runtime._runtime_context",
        lambda: FakeContext(),
    )

    with pytest.raises(RuntimeError, match="atomic mode set failed with -13") as exc_info:
        VisualStimRuntime(
            display_config=DisplayConfig(
                backend="fake",
                connector="HDMI-A-2",
                resolution_px=(32, 24),
                refresh_hz=60.0,
            ),
            gray_level_u8=64,
            stimuli={},
        )

    assert getattr(exc_info.value, "diagnostics", {})["requested_connector"] == "HDMI-A-2"
    assert getattr(exc_info.value, "diagnostics", {})["reserved_crtc_id"] == 52


def test_unknown_grating_name_raises_clear_error(tmp_path: Path) -> None:
    """Unknown grating names should fail with a clear lookup error.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = _write_spec(tmp_path / "go_grating.yaml")
    visual = VisualStim(_session_info([spec_path]))

    try:
        with pytest.raises(KeyError, match="missing_grating"):
            visual.show_grating("missing_grating")
    finally:
        visual.myscreen.close()


def test_fake_backend_records_timing_and_restores_gray(tmp_path: Path) -> None:
    """The fake backend should log timing metadata and restore gray after playback.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = _write_spec(tmp_path / "go_grating.yaml")
    visual = VisualStim(_session_info([spec_path]))

    try:
        visual.show_grating("go_grating")
        visual._runtime.wait_until_idle(timeout_s=2.0)
        metrics = visual._runtime.get_metrics()

        assert metrics["play_count"] == 1
        assert metrics["current_label"] == "gray"
        assert len(metrics["timing_log"]) == 1
        timing_log = metrics["timing_log"][0]
        assert timing_log["stimulus_name"] == "go_grating"
        assert timing_log["enqueue_ns"] > 0
        assert timing_log["first_flip_ns"] >= timing_log["enqueue_ns"]
        assert timing_log["missed_next_vblank"] == 0
    finally:
        visual.myscreen.close()


@pytest.mark.skipif(
    "VISUALSTIM_ENABLE_HARDWARE_SMOKE" not in __import__("os").environ,
    reason="hardware smoke test requires explicit opt-in on a Raspberry Pi",
)
def test_hardware_smoke_preloads_and_logs_timings(tmp_path: Path) -> None:
    """Hardware smoke test for an explicitly enabled Raspberry Pi DRM environment.

    Inputs:
        tmp_path: pytest temporary directory used for YAML spec storage.

    Returns:
        None.
    """

    spec_path = _write_spec(tmp_path / "go_grating.yaml", duration_s=0.05)
    session_info = _session_info([spec_path])
    session_info["visual_backend"] = "drm"
    visual = VisualStim(session_info)

    try:
        for _ in range(3):
            visual.show_grating("go_grating")
            visual._runtime.wait_until_idle(timeout_s=2.0)

        metrics = visual._runtime.get_metrics()
        assert metrics["play_count"] == 3
        assert len(metrics["timing_log"]) == 3
        assert sum(entry["missed_next_vblank"] for entry in metrics["timing_log"]) == 0
    finally:
        visual.myscreen.close()
