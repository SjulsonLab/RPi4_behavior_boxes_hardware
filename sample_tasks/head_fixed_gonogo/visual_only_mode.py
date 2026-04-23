"""Helpers for a visual-only camera preview plus drifting-grating workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence
from urllib import request

def build_visual_only_session_info(
    output_root: Path,
    session_tag: str,
    *,
    visual_connector: str = "HDMI-A-1",
    grating_names: Sequence[str] | None = None,
) -> dict[str, object]:
    """Build one visual-only session-info mapping for ``VisualStim``.

    Data contracts:

    - ``output_root``:
      Filesystem path where any visual-only run artifacts may be stored.
    - ``session_tag``:
      Human-readable session name used for the output directory basename.
    - ``visual_connector``:
      DRM connector string such as ``"HDMI-A-1"`` or ``"HDMI-A-2"``.
    - ``grating_names``:
      Ordered sequence of grating names without file suffixes. Supported names
      map to YAML files inside ``box_runtime/visual_stimuli``.

    Returns:
        dict[str, object]: Session-info mapping accepted by ``VisualStim``.
    """

    normalized_connector = str(visual_connector).strip() or "HDMI-A-1"
    normalized_grating_names = list(grating_names or ["go_grating"])
    repo_root = Path(__file__).resolve().parents[2]
    visual_root = repo_root / "box_runtime" / "visual_stimuli"
    visual_backend = "drm" if _host_looks_like_raspberry_pi() else "fake"
    session_dir = Path(output_root).resolve() / session_tag
    return {
        "external_storage": str(Path(output_root).resolve()),
        "basename": session_tag,
        "dir_name": str(session_dir),
        "gray_level": 127,
        "visual_stimulus": True,
        "vis_gratings": [str(visual_root / f"{name}.yaml") for name in normalized_grating_names],
        "visual_display_backend": visual_backend,
        "visual_display_connector": normalized_connector,
        "visual_display_refresh_hz": 60.0,
        "visual_display_degrees_subtended": 80.0,
    }


def build_visual_only_lightdm_plan(*, stop_for_grating: bool, restore_after: bool) -> dict[str, list[str]]:
    """Return the lightdm actions needed for a visual-only grating run.

    Data contracts:

    - ``stop_for_grating``:
      ``True`` to stop ``lightdm`` before attempting DRM stimulus output.
    - ``restore_after``:
      ``True`` to restart ``lightdm`` after the launcher exits.

    Returns:
        dict[str, list[str]]: Mapping with ``before`` and ``after`` action lists.
    """

    before: list[str] = ["stop"] if bool(stop_for_grating) else []
    after: list[str] = ["start"] if bool(stop_for_grating) and bool(restore_after) else []
    return {"before": before, "after": after}


def build_camera_service_start_command() -> list[str]:
    """Return the module-entrypoint command used for the HTTP camera service.

    Returns:
        list[str]: Command vector suitable for ``subprocess.Popen``.
    """

    return ["python3", "-m", "box_runtime.video_recording.http_camera_service"]


def build_camera_service_environment(
    base_env: Mapping[str, str],
    *,
    port: int,
    storage_root: Path | None = None,
) -> dict[str, str]:
    """Build the environment for one camera service process.

    Data contracts:

    - ``base_env``:
      Mapping of environment variables to inherit.
    - ``port``:
      Positive integer TCP port for the HTTP service.
    - ``storage_root``:
      Optional path where camera session directories should be stored.

    Returns:
        dict[str, str]: Process environment mapping.
    """

    env = dict(base_env)
    env["CAMERA_SERVICE_PORT"] = str(int(port))
    if storage_root is not None:
        env["CAMERA_STORAGE_ROOT"] = str(Path(storage_root))
    return env


def camera_service_status_url(*, port: int) -> str:
    """Return the loopback status URL for the HTTP camera service.

    Args:
        port: Positive integer TCP port.

    Returns:
        str: Status endpoint URL.
    """

    return f"http://127.0.0.1:{int(port)}/api/status"


def camera_monitor_url(*, host: str, port: int) -> str:
    """Return the browser-facing monitor URL for the HTTP camera service.

    Args:
        host: Hostname or IP visible to the operator browser.
        port: Positive integer TCP port.

    Returns:
        str: Monitor endpoint URL.
    """

    return f"http://{str(host).strip()}:{int(port)}/monitor"


def ensure_camera_service_running(
    *,
    port: int,
    monitor_host: str,
    storage_root: Path | None = None,
    startup_attempts: int = 10,
    startup_delay_s: float = 0.5,
    status_loader: Callable[[str], dict[str, Any]] | None = None,
    process_launcher: Callable[[list[str], dict[str, str]], Any] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[str, bool]:
    """Ensure the HTTP camera service is healthy, starting it if needed.

    Data contracts:

    - ``port``:
      Positive integer TCP port for the local service.
    - ``monitor_host``:
      Operator-visible hostname or IP used in the returned browser URL.
    - ``storage_root``:
      Optional path passed to the camera service as ``CAMERA_STORAGE_ROOT``.
    - ``startup_attempts``:
      Positive integer number of health-check polls after process launch.
    - ``startup_delay_s``:
      Delay between launch-time health polls in seconds.
    - ``status_loader``:
      Callable accepting the status URL and returning the decoded JSON payload.
    - ``process_launcher``:
      Callable accepting ``command`` and ``env`` and starting the background service.
    - ``sleep``:
      Delay function used between health polls.

    Returns:
        tuple[str, bool]: Browser monitor URL and whether this helper started the service.
    """

    normalized_port = int(port)
    normalized_host = str(monitor_host).strip()
    monitor_url = camera_monitor_url(host=normalized_host, port=normalized_port)
    status_url = camera_service_status_url(port=normalized_port)
    effective_status_loader = status_loader or _load_camera_service_status
    effective_process_launcher = process_launcher or _launch_camera_service_process
    effective_sleep = sleep or time.sleep

    if _camera_service_is_healthy(status_url, effective_status_loader):
        return monitor_url, False

    command = build_camera_service_start_command()
    env = build_camera_service_environment(os.environ, port=normalized_port, storage_root=storage_root)
    effective_process_launcher(command, env)
    for _attempt_index in range(max(1, int(startup_attempts))):
        effective_sleep(float(startup_delay_s))
        if _camera_service_is_healthy(status_url, effective_status_loader):
            return monitor_url, True
    raise RuntimeError("camera service did not become healthy after startup")


def _camera_service_is_healthy(
    status_url: str,
    status_loader: Callable[[str], dict[str, Any]],
) -> bool:
    """Return whether the camera service health payload reports ``status=ok``."""

    try:
        payload = status_loader(status_url)
    except Exception:
        return False
    return str(payload.get("status", "")).strip().lower() == "ok"


def _load_camera_service_status(status_url: str) -> dict[str, Any]:
    """Load and decode one camera service health payload."""

    with request.urlopen(status_url, timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _launch_camera_service_process(command: list[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
    """Launch the HTTP camera service in the background."""

    return subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _host_looks_like_raspberry_pi() -> bool:
    """Return whether the current host appears to be a Raspberry Pi."""

    model_path = Path("/sys/firmware/devicetree/base/model")
    try:
        model_text = model_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return "raspberry pi" in model_text.lower()
