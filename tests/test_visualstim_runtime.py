"""Tests for the low-latency visual stimulus runtime."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from essential.visualstim import VisualStim
from essential.visual_runtime.grating_compiler import compile_grating
from essential.visual_runtime.grating_specs import load_grating_spec


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
