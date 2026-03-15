"""Regression checks for archiving legacy hardware-support code.

Data contracts:
- repository root: ``Path`` pointing to the hardware repo root
- archived paths: relative directory paths expected to exist after archival
- active imports: importable module names for the supported runtime surface
"""

from __future__ import annotations

import importlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_hardware_directories_move_under_archive_root() -> None:
    """Legacy hardware-only trees should live under ``box_runtime/old_hardware``."""
    archived_paths = [
        REPO_ROOT / "box_runtime" / "old_hardware" / "video_recording" / "old",
        REPO_ROOT / "box_runtime" / "old_hardware" / "treadmill",
        REPO_ROOT / "box_runtime" / "old_hardware" / "support" / "camera",
        REPO_ROOT / "box_runtime" / "old_hardware" / "support" / "ADC",
        REPO_ROOT / "box_runtime" / "old_hardware" / "support" / "RTC",
        REPO_ROOT / "box_runtime" / "old_hardware" / "support" / "scripts",
        REPO_ROOT / "box_runtime" / "old_hardware" / "support" / "syringe_pump_c_code",
    ]
    old_paths = [
        REPO_ROOT / "box_runtime" / "video_recording" / "old",
        REPO_ROOT / "box_runtime" / "treadmill",
        REPO_ROOT / "box_runtime" / "support" / "camera",
        REPO_ROOT / "box_runtime" / "support" / "ADC",
        REPO_ROOT / "box_runtime" / "support" / "RTC",
        REPO_ROOT / "box_runtime" / "support" / "scripts",
        REPO_ROOT / "box_runtime" / "support" / "syringe_pump_c_code",
    ]

    for archived_path in archived_paths:
        assert archived_path.is_dir(), archived_path
    for old_path in old_paths:
        assert not old_path.exists(), old_path


def test_active_runtime_modules_still_import_after_archival() -> None:
    """Archiving legacy trees must not break active runtime module imports."""
    module_names = [
        "box_runtime.behavior.behavbox",
        "box_runtime.input.service",
        "box_runtime.output.service",
        "box_runtime.video_recording.VideoCapture",
        "sample_tasks.head_fixed_gonogo.task",
    ]

    for module_name in module_names:
        assert importlib.import_module(module_name) is not None
