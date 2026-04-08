"""Helpers for Pi 5 / Trixie provisioning metadata.

Data contracts:
- manifest path input: ``Path | None``; when ``None``, the module-local JSON
  manifest is loaded.
- manifest return value: ``dict[str, object]`` mirroring the JSON structure in
  ``rpi5_trixie_manifest.json``.
- package/module/command return values: ordered ``list[str]`` values with
  duplicates removed while preserving first occurrence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


MANIFEST_PATH = Path(__file__).with_name("rpi5_trixie_manifest.json")


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """Load the Pi 5 / Trixie provisioning manifest.

    Args:
        path: Optional JSON manifest path.

    Returns:
        dict[str, Any]: Manifest dictionary with ``apt``, ``python_modules``,
        ``commands``, ``repo_probes``, and ``uv`` sections.
    """

    manifest_path = Path(path) if path is not None else MANIFEST_PATH
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def runtime_apt_packages(manifest: dict[str, Any] | None = None) -> list[str]:
    """Return the ordered runtime apt package list.

    Args:
        manifest: Optional manifest dictionary.

    Returns:
        list[str]: Apt package names required for the runtime target.
    """

    data = manifest or load_manifest()
    return _unique_preserving_order(data["apt"]["runtime"])


def dev_test_apt_packages(manifest: dict[str, Any] | None = None) -> list[str]:
    """Return the ordered development/testing apt package list.

    Args:
        manifest: Optional manifest dictionary.

    Returns:
        list[str]: Apt package names required only for development/testing.
    """

    data = manifest or load_manifest()
    return _unique_preserving_order(data["apt"]["dev_test"])


def combined_apt_packages(
    include_dev: bool,
    manifest: dict[str, Any] | None = None,
) -> list[str]:
    """Return runtime packages plus optional development/testing packages.

    Args:
        include_dev: When ``True``, append dev/test packages.
        manifest: Optional manifest dictionary.

    Returns:
        list[str]: Ordered apt package names with duplicates removed.
    """

    data = manifest or load_manifest()
    packages = list(runtime_apt_packages(data))
    if include_dev:
        packages.extend(dev_test_apt_packages(data))
    return _unique_preserving_order(packages)


def python_module_names(
    include_dev: bool,
    manifest: dict[str, Any] | None = None,
) -> list[str]:
    """Return required Python import names for verification.

    Args:
        include_dev: When ``True``, include dev/test-only modules.
        manifest: Optional manifest dictionary.

    Returns:
        list[str]: Ordered Python module import names.
    """

    data = manifest or load_manifest()
    names = list(data["python_modules"]["runtime"])
    if include_dev:
        names.extend(data["python_modules"]["dev_test"])
    return _unique_preserving_order(names)


def command_names(
    include_dev: bool,
    manifest: dict[str, Any] | None = None,
) -> list[str]:
    """Return required executable command names for verification.

    Args:
        include_dev: When ``True``, include dev/test-only commands.
        manifest: Optional manifest dictionary.

    Returns:
        list[str]: Ordered command names checked via ``PATH`` lookup.
    """

    data = manifest or load_manifest()
    names = list(data["commands"]["runtime"])
    if include_dev:
        names.extend(data["commands"]["dev_test"])
    return _unique_preserving_order(names)


def runtime_repo_probes(
    include_dev: bool,
    manifest: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return repository-specific runtime verification probe definitions.

    Args:
        include_dev: When ``True``, include dev/test-only probes.
        manifest: Optional manifest dictionary.

    Returns:
        list[dict[str, str]]: Ordered runtime probe dictionaries with
        ``name``, ``code``, and ``expected_stdout`` fields.
    """

    data = manifest or load_manifest()
    probes = list(data["repo_probes"]["runtime"])
    if include_dev:
        probes.extend(data["repo_probes"]["dev_test"])
    return probes


def desktop_plotting_probes(manifest: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Return repository-specific desktop plotting probe definitions.

    Args:
        manifest: Optional manifest dictionary.

    Returns:
        list[dict[str, str]]: Ordered desktop plotting probe definitions.
    """

    data = manifest or load_manifest()
    return list(data["repo_probes"].get("desktop_plotting", []))


def repo_probes(
    include_dev: bool,
    include_desktop_plotting: bool = False,
    manifest: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return the selected repository probe definitions.

    Args:
        include_dev: When ``True``, include dev/test runtime probes.
        include_desktop_plotting: When ``True``, append desktop plotting probes.
        manifest: Optional manifest dictionary.

    Returns:
        list[dict[str, str]]: Ordered probe dictionaries.
    """

    probes = list(runtime_repo_probes(include_dev=include_dev, manifest=manifest))
    if include_desktop_plotting:
        probes.extend(desktop_plotting_probes(manifest=manifest))
    return probes


def uv_metadata(manifest: dict[str, Any] | None = None) -> dict[str, str]:
    """Return ``uv`` installation metadata.

    Args:
        manifest: Optional manifest dictionary.

    Returns:
        dict[str, str]: ``uv`` installer metadata including the Astral script
        URL and expected binary name.
    """

    data = manifest or load_manifest()
    return dict(data["uv"])


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def main(argv: list[str] | None = None) -> int:
    """Provide a tiny CLI for shell/bootstrap consumers.

    Args:
        argv: Optional argument vector excluding the executable name.

    Returns:
        int: Process exit code.
    """

    parser = argparse.ArgumentParser(description="Inspect the Pi 5 / Trixie provisioning manifest.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command_name in ("apt-packages", "python-modules", "commands", "repo-probes"):
        subparser = subparsers.add_parser(command_name)
        subparser.add_argument("--include-dev", action="store_true")
        if command_name == "repo-probes":
            subparser.add_argument("--include-desktop-plotting", action="store_true")

    subparsers.add_parser("uv-install-script-url")

    args = parser.parse_args(argv)

    if args.command == "apt-packages":
        print("\n".join(combined_apt_packages(include_dev=args.include_dev)))
        return 0
    if args.command == "python-modules":
        print("\n".join(python_module_names(include_dev=args.include_dev)))
        return 0
    if args.command == "commands":
        print("\n".join(command_names(include_dev=args.include_dev)))
        return 0
    if args.command == "repo-probes":
        print(
            json.dumps(
                repo_probes(
                    include_dev=args.include_dev,
                    include_desktop_plotting=args.include_desktop_plotting,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "uv-install-script-url":
        print(uv_metadata()["install_script_url"])
        return 0
    raise ValueError(f"unsupported command {args.command!r}")


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
