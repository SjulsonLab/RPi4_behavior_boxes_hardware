"""Compatibility wrapper for low-latency visual stimulus presentation."""

from __future__ import annotations

from collections import OrderedDict
import logging
import os
from pathlib import Path
import time
from typing import Any

from box_runtime.visual_stimuli.visual_runtime import (
    VisualStimRuntime,
    compile_grating,
    load_grating_spec,
    query_display_config,
)


class _ScreenCompat:
    """Compatibility object exposing the subset of the old RPG Screen API used here.

    Args:
        runtime: Worker-backed visual stimulus runtime.
        gray_level_u8: Default neutral gray level in uint8 units [0, 255].
    """

    def __init__(self, runtime: VisualStimRuntime, gray_level_u8: int) -> None:
        self._runtime = runtime
        self._gray_level_u8 = gray_level_u8

    def display_greyscale(self, color: int | float, blocking: bool = True) -> None:
        """Display a solid grayscale frame.

        Args:
            color: Grayscale color as either uint8 [0, 255] or normalized float [0, 1].
            blocking: When ``True``, wait for the worker to become idle.

        Returns:
            None.
        """

        gray_level_u8 = _normalize_gray_level(color)
        self._gray_level_u8 = gray_level_u8
        self._runtime.display_gray(gray_level_u8, blocking=blocking)

    def close(self) -> None:
        """Close the underlying runtime worker."""

        self._runtime.close()


class VisualStim:
    """Low-latency visual stimulus controller compatible with legacy task code.

    Args:
        session_info: Mapping containing at least ``vis_gratings`` and
            ``gray_level``. ``vis_gratings`` is a list of YAML spec file paths.
            Optional keys are ``visual_backend``, ``visual_display_resolution_px``,
            ``visual_display_refresh_hz``, and ``visual_display_degrees_subtended``.

    Returns:
        VisualStim: Compatibility facade backed by the new runtime worker.
    """

    def __init__(self, session_info: dict[str, Any]) -> None:
        self.session_info = session_info
        self.gray_level_u8 = _normalize_gray_level(session_info.get("gray_level", 127))
        self.gratings: OrderedDict[str, Any] = OrderedDict()
        self._compiled_stimuli: dict[str, Any] = {}
        self._alias_map: dict[str, str] = {}

        backend_name = str(session_info.get("visual_backend", "drm")).lower()
        requested_resolution = _normalize_resolution(session_info.get("visual_display_resolution_px"))
        requested_refresh_hz = _normalize_positive_float(
            session_info.get("visual_display_refresh_hz"),
            default=None,
        )
        self._default_degrees_subtended = _normalize_positive_float(
            session_info.get("visual_display_degrees_subtended"),
            default=80.0,
        )
        self._display_config = query_display_config(
            backend=backend_name,
            requested_resolution_px=requested_resolution,
            requested_refresh_hz=requested_refresh_hz,
        )

        self.load_session_gratings()
        self._runtime = VisualStimRuntime(
            display_config=self._display_config,
            gray_level_u8=self.gray_level_u8,
            stimuli=self._compiled_stimuli,
        )
        self.myscreen = _ScreenCompat(self._runtime, self.gray_level_u8)
        self.myscreen.display_greyscale(self.gray_level_u8, blocking=True)
        logging.info(";%s;[initialization];screen_opened", time.time())

    def load_grating_file(self, grating_file: str | os.PathLike[str]) -> None:
        """Load, validate, and compile one YAML grating specification file.

        Args:
            grating_file: Path to a YAML specification file.

        Returns:
            None.
        """

        grating_path = Path(grating_file).expanduser().resolve()
        logging.info(";%s;[initialization];loading grating file", time.time())
        spec = load_grating_spec(grating_path)
        compiled = compile_grating(
            spec=spec,
            resolution_px=spec.resolution_px or self._display_config.resolution_px,
            refresh_hz=self._display_config.refresh_hz,
            degrees_subtended=spec.degrees_subtended or self._default_degrees_subtended,
        )
        self._compiled_stimuli[spec.name] = compiled
        for alias in _stimulus_aliases(grating_path, spec.name):
            existing = self._alias_map.get(alias)
            if existing is not None and existing != spec.name:
                raise ValueError(f"duplicate visual stimulus alias {alias!r}")
            self._alias_map[alias] = spec.name
            self.gratings[alias] = compiled
        logging.info(";%s;[initialization];loaded", time.time())
        if hasattr(self, "_runtime"):
            self._restart_runtime()

    def load_grating_dir(self, grating_directory: str | os.PathLike[str]) -> None:
        """Load all YAML grating specs from one directory.

        Args:
            grating_directory: Directory containing ``*.yaml`` or ``*.yml`` spec files.

        Returns:
            None.
        """

        logging.info(";%s;[initialization];loading all gratings in directory", time.time())
        grating_dir = Path(grating_directory).expanduser().resolve()
        spec_paths = sorted(
            list(grating_dir.glob("*.yaml")) + list(grating_dir.glob("*.yml"))
        )
        for spec_path in spec_paths:
            self.load_grating_file(spec_path)

    def load_session_gratings(self) -> None:
        """Load all visual stimulus specs referenced by ``session_info``."""

        for filepath in self.session_info.get("vis_gratings", []):
            self.load_grating_file(filepath)

    def list_gratings(self) -> list[str]:
        """Return the currently registered stimulus aliases.

        Returns:
            list[str]: Alias keys accepted by ``show_grating``.
        """

        return list(self.gratings.keys())

    def clear_gratings(self) -> None:
        """Clear compiled stimuli and alias mappings.

        Returns:
            None.
        """

        self.gratings = OrderedDict()
        self._compiled_stimuli = {}
        self._alias_map = {}
        if hasattr(self, "_runtime"):
            self._runtime.close()

    def show_grating(self, grating_name: str) -> None:
        """Queue a precomputed stimulus for playback on the persistent worker.

        Args:
            grating_name: Stimulus alias, filename, filename stem, or
                ``name`` field from the YAML spec.

        Returns:
            None.

        Raises:
            KeyError: If the stimulus name is unknown.
        """

        canonical_name = self._alias_map.get(grating_name)
        if canonical_name is None:
            raise KeyError(f"unknown visual stimulus {grating_name!r}")
        logging.info(";%s;[configuration];queueing stimulus %s", time.time(), grating_name)
        self._runtime.show_grating(canonical_name)

    def process_function(self, grating_name: str) -> None:
        """Compatibility shim matching the old process target method signature.

        Args:
            grating_name: Stimulus alias accepted by ``show_grating``.

        Returns:
            None.
        """

        self.show_grating(grating_name)

    def _restart_runtime(self) -> None:
        """Rebuild the worker so newly loaded stimuli become immediately playable."""

        self._runtime.close()
        self._runtime = VisualStimRuntime(
            display_config=self._display_config,
            gray_level_u8=self.gray_level_u8,
            stimuli=self._compiled_stimuli,
        )
        self.myscreen = _ScreenCompat(self._runtime, self.gray_level_u8)
        self.myscreen.display_greyscale(self.gray_level_u8, blocking=True)

    def __del__(self) -> None:
        """Close the runtime during garbage collection."""

        runtime = getattr(self, "_runtime", None)
        if runtime is not None:
            runtime.close()


def _stimulus_aliases(grating_path: Path, stimulus_name: str) -> tuple[str, ...]:
    """Return all accepted lookup aliases for one stimulus file.

    Args:
        grating_path: YAML spec file path.
        stimulus_name: Explicit stimulus name from the spec.

    Returns:
        tuple[str, ...]: Ordered aliases accepted by ``show_grating``.
    """

    aliases = []
    for alias in (stimulus_name, grating_path.stem, grating_path.name):
        if alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


def _normalize_gray_level(value: Any) -> int:
    """Normalize a gray level to uint8 display units.

    Args:
        value: Either uint8-like integer [0, 255] or normalized float [0, 1].

    Returns:
        int: Grayscale value in uint8 units [0, 255].
    """

    if isinstance(value, float) and 0.0 <= value <= 1.0:
        return int(round(value * 255.0))
    gray_level_u8 = int(value)
    if gray_level_u8 < 0 or gray_level_u8 > 255:
        raise ValueError("gray_level must be in [0, 255] or normalized float [0, 1]")
    return gray_level_u8


def _normalize_resolution(value: Any) -> tuple[int, int] | None:
    """Normalize an optional ``[width_px, height_px]`` session override."""

    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("visual_display_resolution_px must be [width_px, height_px]")
    width_px, height_px = int(value[0]), int(value[1])
    if width_px <= 0 or height_px <= 0:
        raise ValueError("visual_display_resolution_px values must be > 0")
    return (width_px, height_px)


def _normalize_positive_float(value: Any, default: float | None) -> float | None:
    """Normalize an optional positive floating-point session parameter."""

    if value is None:
        return default
    value_f = float(value)
    if value_f <= 0.0:
        raise ValueError("session float overrides must be > 0")
    return value_f
