"""Display-mode helpers for head-fixed go/no-go task launch flows."""

from __future__ import annotations

from typing import Any


DisplayMode = str


def apply_display_mode_overrides(session_info: dict[str, Any], mode: DisplayMode) -> dict[str, Any]:
    """Return a copy of session info with display-mode-specific overrides.

    Data contracts:

    - ``session_info``:
      dict[str, Any], expected to include ``camera_ids`` as ``list[str]`` or a
      string camera id. Missing values fall back to ``["camera0"]``.
    - ``mode``:
      ``"desktop"`` keeps virtual desktop workflows via ``qt_local`` preview.
      ``"experiment"`` enables compositor-free shared DRM media ownership for
      live preview on ``HDMI-A-1`` and visual stimulus on ``HDMI-A-2``.

    Returns:
        dict[str, Any]: New session-info mapping with preview overrides applied.
    """

    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"desktop", "experiment"}:
        raise ValueError(f"Unsupported display mode {mode!r}; expected 'desktop' or 'experiment'")

    updated = dict(session_info)
    configured_ids = updated.get("camera_ids", ["camera0"])
    camera_ids = [configured_ids] if isinstance(configured_ids, str) else list(configured_ids)
    if not camera_ids:
        camera_ids = ["camera0"]
    primary_camera_id = str(camera_ids[0])

    if normalized_mode == "desktop":
        updated["camera_preview_modes"] = {primary_camera_id: "qt_local"}
        updated.setdefault("camera_preview_display", ":0")
        updated["visual_display_backend"] = "xwindow"
        updated.pop("experiment_media_backend", None)
        updated.pop("experiment_media_preview_camera_id", None)
        return updated

    updated["camera_preview_modes"] = {primary_camera_id: "off"}
    updated["camera_recording_enabled"] = True
    updated["experiment_media_backend"] = "shared_drm"
    updated["experiment_media_preview_camera_id"] = primary_camera_id
    updated["experiment_media_preview_source_mode"] = "dmabuf_main"
    updated["experiment_media_request_mode"] = "next"
    updated["visual_display_backend"] = "drm"
    return updated


def build_lightdm_action_plan(mode: DisplayMode) -> dict[str, list[str]]:
    """Return service actions for safe lightdm orchestration by display mode.

    Data contracts:

    - ``mode``: ``"desktop"`` or ``"experiment"``.
    - return dictionary keys:
      ``before`` and ``after``, each a ``list[str]`` containing ``"start"`` or
      ``"stop"`` actions for ``lightdm``.
    """

    normalized_mode = str(mode).strip().lower()
    if normalized_mode == "desktop":
        return {"before": ["start"], "after": []}
    if normalized_mode == "experiment":
        return {"before": ["stop"], "after": ["start"]}
    raise ValueError(f"Unsupported display mode {mode!r}; expected 'desktop' or 'experiment'")
