"""HTTP camera service with ownership modes and session recovery.

This module keeps camera control in Python while isolating session-state and
browser-control behavior so the BehavBox Pi can stop shelling out over SSH.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request

from box_runtime.video_recording.camera_session import (
    CameraSessionError,
    estimate_required_bytes,
)
from box_runtime.video_recording.picamera2_recorder import Picamera2Recorder


HTML_MANUAL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Camera Manual Control</title>
<style>
body { font-family: sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
.controls { display: flex; gap: 0.75rem; margin: 1rem 0; }
button, input { font-size: 1rem; padding: 0.5rem 0.75rem; }
img { max-width: 100%; border: 1px solid #ccc; background: #111; }
pre { background: #f5f5f5; padding: 0.75rem; overflow-x: auto; }
</style>
</head>
<body>
<h1>Camera Manual Control</h1>
<p>Manual control is available only when the camera service is not owned by an automated BehavBox session.</p>
<label for="session-id">Session ID</label>
<input id="session-id" value="manual_session" />
<div class="controls">
<button id="manual-start">Manual Start</button>
<button id="manual-stop">Manual Stop</button>
<button id="refresh-status">Refresh Status</button>
</div>
<img src="/stream.mjpg" alt="camera preview" />
<pre id="status"></pre>
<script>
async function api(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  return response.json();
}
async function refreshStatus() {
  const response = await fetch("/api/status");
  const payload = await response.json();
  document.getElementById("status").textContent = JSON.stringify(payload, null, 2);
}
document.getElementById("manual-start").onclick = async () => {
  const sessionId = document.getElementById("session-id").value;
  const payload = await api("/api/start", {session_id: sessionId, owner: "manual", duration_s: 0});
  document.getElementById("status").textContent = JSON.stringify(payload, null, 2);
};
document.getElementById("manual-stop").onclick = async () => {
  const payload = await api("/api/stop", {owner: "manual"});
  document.getElementById("status").textContent = JSON.stringify(payload, null, 2);
};
document.getElementById("refresh-status").onclick = refreshStatus;
refreshStatus();
</script>
</body>
</html>
"""

HTML_MONITOR = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Camera Monitor</title>
<style>
body { font-family: sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
.controls { display: flex; gap: 0.75rem; margin: 1rem 0; }
button { font-size: 1rem; padding: 0.5rem 0.75rem; }
img { max-width: 100%; border: 1px solid #ccc; background: #111; }
pre { background: #f5f5f5; padding: 0.75rem; overflow-x: auto; }
</style>
</head>
<body>
<h1>Camera Monitor</h1>
<p>Monitor mode is read-only during automated runs except for emergency stop.</p>
<div class="controls">
<button id="refresh-status">Refresh Status</button>
<button id="emergency-stop">Emergency Stop</button>
</div>
<img src="/stream.mjpg" alt="camera preview" />
<pre id="status"></pre>
<script>
async function refreshStatus() {
  const response = await fetch("/api/status");
  const payload = await response.json();
  document.getElementById("status").textContent = JSON.stringify(payload, null, 2);
}
document.getElementById("refresh-status").onclick = refreshStatus;
document.getElementById("emergency-stop").onclick = async () => {
  const response = await fetch("/api/stop", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({owner: "manual"}),
  });
  const payload = await response.json();
  document.getElementById("status").textContent = JSON.stringify(payload, null, 2);
};
refreshStatus();
</script>
</body>
</html>
"""


@dataclass
class _ServiceState:
    recording: bool = False
    owner: str | None = None
    session_id: str | None = None


class _DummyRecorder:
    """In-memory recorder used for tests or non-Pi environments."""

    def __init__(self, storage_root: Path):
        self.storage_root = Path(storage_root)
        self.current_session_id: str | None = None
        self.current_owner: str | None = None
        self.recording = False

    def start(self, session_id: str, owner: str, payload: dict[str, Any]) -> None:
        self.current_session_id = session_id
        self.current_owner = owner
        self.recording = True

    def stop(self) -> None:
        self.recording = False

    def recover_live_sessions(self) -> list[str]:
        return []

    def list_sessions(self) -> list[dict[str, Any]]:
        return []

    def acknowledge_transfer(self, session_id: str) -> None:
        return None

    def preview_frame(self) -> bytes | None:
        return None


def create_app(
    storage_root: Path,
    recorder_factory=None,
    free_space_bytes: int | None = None,
) -> Flask:
    """Create the Flask service app.

    Args:
        storage_root: Path where camera session directories live.
        recorder_factory: Callable taking storage_root and returning a recorder.
        free_space_bytes: Optional test override for free-space reporting.
    """

    app = Flask(__name__)
    state = _ServiceState()
    root = Path(storage_root)
    root.mkdir(parents=True, exist_ok=True)
    recorder = recorder_factory(root) if recorder_factory is not None else _DummyRecorder(root)
    recorder.recover_live_sessions()

    def available_bytes() -> int:
        if free_space_bytes is not None:
            return int(free_space_bytes)
        return int(shutil.disk_usage(root).free)

    @app.get("/api/status")
    def api_status():
        return jsonify(
            {
                "status": "ok",
                "recording": state.recording,
                "owner": state.owner,
                "session_id": state.session_id,
                "free_space_bytes": available_bytes(),
                "preview_url": "/stream.mjpg",
            }
        )

    @app.get("/api/config")
    def api_config():
        current = recorder.current_config() if hasattr(recorder, "current_config") else {}
        modes = recorder.available_modes() if hasattr(recorder, "available_modes") else []
        return jsonify({"status": "ok", "current": current, "modes": modes})

    @app.post("/api/configure")
    def api_configure():
        payload = request.get_json(silent=True) or {}
        mode = payload.get("mode")
        if mode is None:
            return jsonify({"status": "error", "message": "mode is required"}), 400
        if not hasattr(recorder, "configure"):
            return jsonify({"status": "error", "message": "recorder does not support configure"}), 400
        try:
            recorder.configure(mode)
        except CameraSessionError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
        return jsonify({"status": "ok"})

    @app.post("/api/start")
    def api_start():
        payload = request.get_json(silent=True) or {}
        owner = payload.get("owner", "manual")
        session_id = payload.get("session_id")
        duration_s = float(payload.get("duration_s", 0))
        bitrate_bps = float(payload.get("bitrate_bps", 8_000_000))
        if not session_id:
            return jsonify({"status": "error", "message": "session_id is required"}), 400
        if state.recording and state.owner == "automated" and owner != "automated":
            return jsonify(
                {
                    "status": "error",
                    "message": "manual control is blocked during automated recording",
                }
            ), 409
        if state.recording:
            return jsonify({"status": "error", "message": "already recording"}), 409
        required_bytes = estimate_required_bytes(duration_s, bitrate_bps)
        if available_bytes() < required_bytes:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "insufficient free space for requested session",
                        "block_behavbox": True,
                        "required_bytes": required_bytes,
                        "free_space_bytes": available_bytes(),
                    }
                ),
                507,
            )
        recorder.start(session_id, owner, payload)
        state.recording = True
        state.owner = owner
        state.session_id = session_id
        return jsonify({"status": "ok", "session_id": session_id})

    @app.post("/api/stop")
    def api_stop():
        if not state.recording:
            return jsonify({"status": "ok", "message": "already stopped"})
        owner = (request.get_json(silent=True) or {}).get("owner")
        if owner and owner != state.owner and owner != "manual":
            return jsonify({"status": "error", "message": "owner mismatch"}), 409
        recorder.stop()
        state.recording = False
        state.owner = None
        state.session_id = None
        return jsonify({"status": "ok"})

    @app.get("/api/sessions")
    def api_sessions():
        return jsonify({"status": "ok", "sessions": recorder.list_sessions()})

    @app.post("/api/sessions/<session_id>/ack_transfer")
    def api_ack_transfer(session_id: str):
        recorder.acknowledge_transfer(session_id)
        return jsonify({"status": "ok", "session_id": session_id})

    @app.get("/manual")
    def manual_ui():
        return Response(HTML_MANUAL, mimetype="text/html")

    @app.get("/monitor")
    def monitor_ui():
        return Response(HTML_MONITOR, mimetype="text/html")

    @app.get("/stream.mjpg")
    def stream_mjpg():
        def generate():
            # Best-effort preview: repeat the latest available frame and never block recording.
            while True:
                frame = recorder.preview_frame()
                if frame is None:
                    time.sleep(0.1)
                    continue
                yield (
                    b"--FRAME\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )

        return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=FRAME")

    return app


def main() -> None:  # pragma: no cover - exercised on the Pi
    storage_root = Path(os.environ.get("CAMERA_STORAGE_ROOT", str(Path.home() / "behvideos")))
    host = os.environ.get("CAMERA_SERVICE_HOST", "0.0.0.0")
    port = int(os.environ.get("CAMERA_SERVICE_PORT", "8000"))
    app = create_app(storage_root=storage_root, recorder_factory=Picamera2Recorder)
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":  # pragma: no cover - exercised on the Pi
    main()
