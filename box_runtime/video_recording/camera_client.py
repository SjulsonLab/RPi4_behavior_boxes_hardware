"""HTTP client for controlling the camera Pi service from the BehavBox Pi."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from box_runtime.video_recording.camera_session import verify_manifest_hashes


class CameraClientError(RuntimeError):
    """Raised when camera Pi control or transfer operations fail."""


class CameraClient:
    """Control and offload client for the remote camera service.

    Data contract:
    - host: IPv4/hostname string for the camera Pi
    - port: TCP port exposing the camera HTTP service
    - remote_storage_subdir: directory under the remote home containing session dirs
    """

    def __init__(self, host: str, port: int = 8000, remote_storage_subdir: str = "behvideos"):
        self.host = host
        self.port = int(port)
        self.remote_storage_subdir = remote_storage_subdir.strip("/")

    def status(self) -> dict:
        return self._request_json("/api/status")

    def start_recording(self, **payload) -> dict:
        return self._request_json("/api/start", method="POST", payload=payload)

    def stop_recording(self, owner: str = "automated") -> dict:
        return self._request_json("/api/stop", method="POST", payload={"owner": owner})

    def list_sessions(self) -> list[dict]:
        payload = self._request_json("/api/sessions")
        return payload.get("sessions", [])

    def acknowledge_transfer(self, session_id: str) -> dict:
        return self._request_json(
            f"/api/sessions/{quote(session_id)}/ack_transfer",
            method="POST",
            payload={},
        )

    def offload_session(self, session_id: str, destination_root: str | Path) -> Path:
        """Pull a finalized session directory onto the BehavBox Pi.

        Args:
            session_id: Remote session directory name.
            destination_root: Local root directory that should receive the session.

        Returns:
            Path to the local session directory.
        """

        local_root = Path(destination_root)
        local_root.mkdir(parents=True, exist_ok=True)
        local_session_dir = local_root / session_id
        local_session_dir.mkdir(parents=True, exist_ok=True)

        remote_dir = f"pi@{self.host}:~/{self.remote_storage_subdir}/{session_id}/"
        command = ["rsync", "-az", "--partial", remote_dir, str(local_session_dir)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise CameraClientError(
                f"rsync failed for {session_id}: {result.stderr or result.stdout}"
            )

        manifest_path = local_session_dir / "session_manifest.json"
        if not manifest_path.exists():
            raise CameraClientError(f"manifest missing after transfer for {session_id}")
        verify_manifest_hashes(manifest_path, local_session_dir, raise_on_error=True)
        self.acknowledge_transfer(session_id)
        return local_session_dir

    def _request_json(self, path: str, method: str = "GET", payload=None) -> dict:
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self._url(path),
            method=method,
            data=body,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            message = exc.read().decode("utf-8")
            raise CameraClientError(f"camera service HTTP error {exc.code}: {message}") from exc
        except URLError as exc:
            raise CameraClientError(f"camera service connection error: {exc}") from exc

    def _url(self, path: str) -> str:
        return f"http://{self.host}:{self.port}{path}"
