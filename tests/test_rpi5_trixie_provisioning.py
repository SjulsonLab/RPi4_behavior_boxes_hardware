from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from environment.rpi5_trixie import (
    combined_apt_packages,
    desktop_plotting_probes,
    load_manifest,
    runtime_repo_probes,
    runtime_apt_packages,
    uv_metadata,
)
from environment.rpi5_trixie_verifier import (
    check_commands,
    check_python_modules,
    run_python_probe,
    verify_host,
)


def test_runtime_package_group_contains_required_media_and_plotting_packages() -> None:
    packages = runtime_apt_packages()

    for package_name in (
        "python3-picamera2",
        "python3-kms++",
        "python3-pygame",
        "python3-matplotlib",
        "python3-alsaaudio",
        "ffmpeg",
        "alsa-utils",
        "rsync",
    ):
        assert package_name in packages


def test_combined_package_group_adds_dev_packages_without_duplicates() -> None:
    runtime_only = runtime_apt_packages()
    combined = combined_apt_packages(include_dev=True)

    assert "python3-pytest" not in runtime_only
    assert "python3-pytest" in combined
    assert len(combined) == len(set(combined))


def test_manifest_declares_uv_install_via_astral_script() -> None:
    metadata = uv_metadata()
    manifest = load_manifest()

    assert metadata["install_method"] == "astral_install_script"
    assert metadata["install_script_url"] == "https://astral.sh/uv/install.sh"
    assert metadata["binary_name"] == "uv"
    assert "uv" in manifest["commands"]["dev_test"]


def test_manifest_separates_runtime_probes_from_desktop_plotting_probe() -> None:
    manifest = load_manifest()
    runtime_probe_names = {probe["name"] for probe in runtime_repo_probes(manifest)}
    plotting_probe_names = {probe["name"] for probe in desktop_plotting_probes(manifest)}

    assert "behavbox_import" in runtime_probe_names
    assert "desktop_plotting_probe" in plotting_probe_names
    assert "desktop_plotting_probe" not in runtime_probe_names


def test_check_python_modules_reports_missing_modules_cleanly() -> None:
    def fake_find_spec(name: str):
        return object() if name == "numpy" else None

    failures = check_python_modules(["numpy", "scipy"], spec_finder=fake_find_spec)

    assert len(failures) == 1
    assert failures[0].kind == "python_module"
    assert failures[0].name == "scipy"


def test_check_commands_reports_missing_commands_cleanly() -> None:
    def fake_which(name: str):
        return f"/usr/bin/{name}" if name == "ffmpeg" else None

    failures = check_commands(["ffmpeg", "rsync"], which=fake_which)

    assert len(failures) == 1
    assert failures[0].kind == "command"
    assert failures[0].name == "rsync"


def test_run_python_probe_treats_false_output_as_failure(tmp_path: Path) -> None:
    def fake_runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="False\n",
            stderr="",
        )

    failure = run_python_probe(
        repo_root=tmp_path,
        probe_name="desktop_plotting_probe",
        code="print(False)",
        expected_stdout="True",
        runner=fake_runner,
    )

    assert failure is not None
    assert failure.kind == "repo_probe"
    assert failure.name == "desktop_plotting_probe"
    assert "expected stdout" in failure.detail


def test_run_python_probe_treats_timeout_as_failure(tmp_path: Path) -> None:
    def fake_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    failure = run_python_probe(
        repo_root=tmp_path,
        probe_name="desktop_plotting_probe",
        code="print(True)",
        expected_stdout="True",
        runner=fake_runner,
    )

    assert failure is not None
    assert failure.kind == "repo_probe"
    assert failure.name == "desktop_plotting_probe"
    assert "timed out" in failure.detail


def test_verify_host_distinguishes_runtime_failures_from_desktop_plotting_failures(tmp_path: Path) -> None:
    def fake_find_spec(name: str):
        return object()

    def fake_which(name: str):
        return f"/usr/bin/{name}"

    def fake_runner(command, **kwargs):
        probe_name = command[-1]
        if "desktop_plotting_probe" in probe_name:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="False\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

    runtime_only_failures = verify_host(
        repo_root=tmp_path,
        include_dev=False,
        require_desktop_plotting=False,
        spec_finder=fake_find_spec,
        which=fake_which,
        runner=fake_runner,
    )
    plotting_failures = verify_host(
        repo_root=tmp_path,
        include_dev=False,
        require_desktop_plotting=True,
        spec_finder=fake_find_spec,
        which=fake_which,
        runner=fake_runner,
    )

    assert runtime_only_failures == []
    assert len(plotting_failures) == 1
    assert plotting_failures[0].name == "desktop_plotting_probe"
