"""Verification helpers for the Pi 5 / Trixie provisioning target.

Data contracts:
- repo_root: ``Path`` pointing at the repository checkout to probe.
- module names and command names: ordered ``list[str]`` values.
- probe inputs: ``name`` and ``code`` strings plus one expected final stdout
  line string.
- return values: ``RequirementFailure`` records describing missing runtime
  requirements or failed repository probes.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable

from environment.rpi5_trixie import (
    command_names,
    desktop_plotting_probes,
    python_module_names,
    runtime_repo_probes,
)


@dataclass(frozen=True)
class RequirementFailure:
    """One failed provisioning check.

    Args:
        kind: Failure category such as ``"python_module"``.
        name: Requirement name or probe name.
        detail: Human-readable failure detail string.
    """

    kind: str
    name: str
    detail: str


def check_python_modules(
    module_names: list[str],
    spec_finder: Callable[[str], Any] | None = None,
) -> list[RequirementFailure]:
    """Check Python import availability for the requested module names.

    Args:
        module_names: Ordered module import names.
        spec_finder: Optional ``find_spec``-style callable.

    Returns:
        list[RequirementFailure]: One failure per missing module.
    """

    finder = spec_finder or importlib.util.find_spec
    failures: list[RequirementFailure] = []
    for module_name in module_names:
        try:
            available = finder(module_name) is not None
        except Exception as exc:
            failures.append(
                RequirementFailure(
                    kind="python_module",
                    name=module_name,
                    detail=f"import lookup raised {exc}",
                )
            )
            continue
        if not available:
            failures.append(
                RequirementFailure(
                    kind="python_module",
                    name=module_name,
                    detail="module is not importable",
                )
            )
    return failures


def check_commands(
    names: list[str],
    which: Callable[[str], str | None] | None = None,
) -> list[RequirementFailure]:
    """Check shell command availability for the requested executable names.

    Args:
        names: Ordered command names looked up through ``PATH``.
        which: Optional ``shutil.which``-style callable.

    Returns:
        list[RequirementFailure]: One failure per missing command.
    """

    lookup = which or shutil.which
    failures: list[RequirementFailure] = []
    for name in names:
        if lookup(name) is not None:
            continue
        failures.append(
            RequirementFailure(
                kind="command",
                name=name,
                detail="command was not found on PATH",
            )
        )
    return failures


def run_python_probe(
    repo_root: Path,
    probe_name: str,
    code: str,
    expected_stdout: str,
    timeout_s: float = 10.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> RequirementFailure | None:
    """Run one repository probe in a subprocess.

    Args:
        repo_root: Repository checkout root added to ``sys.path``.
        probe_name: Human-readable probe name.
        code: Python source string executed after adding ``repo_root`` to
            ``sys.path``.
        expected_stdout: Expected final non-empty stdout line.
        timeout_s: Subprocess timeout in seconds.
        runner: Optional ``subprocess.run``-style callable.

    Returns:
        RequirementFailure | None: ``None`` when the probe succeeds.
    """

    repo_path = Path(repo_root).resolve()
    full_code = f"# probe_name: {probe_name}\nimport sys; sys.path.insert(0, {str(repo_path)!r}); {code}"
    command = [sys.executable, "-c", full_code]
    try:
        completed = runner(
            command,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return RequirementFailure(
            kind="repo_probe",
            name=probe_name,
            detail=f"probe timed out after {timeout_s:.1f} s",
        )

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        return RequirementFailure(
            kind="repo_probe",
            name=probe_name,
            detail=f"probe exited with code {completed.returncode}: {stderr}",
        )

    final_stdout_line = _final_nonempty_stdout_line(completed.stdout or "")
    if final_stdout_line != expected_stdout:
        return RequirementFailure(
            kind="repo_probe",
            name=probe_name,
            detail=(
                f"expected stdout final line {expected_stdout!r}, "
                f"got {final_stdout_line!r}"
            ),
        )
    return None


def verify_host(
    repo_root: Path,
    include_dev: bool,
    require_desktop_plotting: bool = False,
    timeout_s: float = 10.0,
    spec_finder: Callable[[str], Any] | None = None,
    which: Callable[[str], str | None] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[RequirementFailure]:
    """Run all manifest-driven verification checks for this target.

    Args:
        repo_root: Repository checkout root.
        include_dev: When ``True``, include dev/test-only checks.
        require_desktop_plotting: When ``True``, include desktop-session plotting checks.
        timeout_s: Timeout applied to each repository probe.
        spec_finder: Optional Python module lookup callable.
        which: Optional command lookup callable.
        runner: Optional subprocess runner for repository probes.

    Returns:
        list[RequirementFailure]: Flat ordered list of all discovered failures.
    """

    failures = []
    failures.extend(check_python_modules(python_module_names(include_dev=include_dev), spec_finder=spec_finder))
    failures.extend(check_commands(command_names(include_dev=include_dev), which=which))
    for probe in runtime_repo_probes(include_dev=include_dev):
        failure = run_python_probe(
            repo_root=repo_root,
            probe_name=probe["name"],
            code=probe["code"],
            expected_stdout=probe["expected_stdout"],
            timeout_s=timeout_s,
            runner=runner,
        )
        if failure is not None:
            failures.append(failure)
    if require_desktop_plotting:
        for probe in desktop_plotting_probes():
            failure = run_python_probe(
                repo_root=repo_root,
                probe_name=probe["name"],
                code=probe["code"],
                expected_stdout=probe["expected_stdout"],
                timeout_s=timeout_s,
                runner=runner,
            )
            if failure is not None:
                failures.append(failure)
    return failures


def _final_nonempty_stdout_line(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def main(argv: list[str] | None = None) -> int:
    """Run the verifier as a command-line tool.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: ``0`` when all checks pass, otherwise ``1``.
    """

    parser = argparse.ArgumentParser(description="Verify the Pi 5 / Trixie provisioning target.")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--include-dev", action="store_true")
    parser.add_argument("--require-desktop-plotting", action="store_true")
    parser.add_argument("--timeout-s", type=float, default=10.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    failures = verify_host(
        repo_root=args.repo_root,
        include_dev=args.include_dev,
        require_desktop_plotting=args.require_desktop_plotting,
        timeout_s=args.timeout_s,
    )

    if args.json:
        print(json.dumps([asdict(failure) for failure in failures], indent=2, sort_keys=True))
    elif failures:
        for failure in failures:
            print(f"[{failure.kind}] {failure.name}: {failure.detail}")
    else:
        print("verification passed")
    return 0 if not failures else 1


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
