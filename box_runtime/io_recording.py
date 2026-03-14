"""Shared IO recording artifacts for input and output services.

Data contracts:
- recording directories: ``pathlib.Path`` roots containing a human-readable log
  file and a minimal ``events.jsonl`` file
- event records: minimal JSON objects with ``name`` and ``timestamp`` plus any
  additional scalar metadata fields
- recording ownership flags: ``owner`` is ``"user"`` or ``"task"``
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Optional


class SharedIoRecorder:
    """Manage shared input/output recording files and ownership flags."""

    def __init__(self, session_info: dict):
        self.session_info = session_info
        self._record_lock = threading.RLock()
        self.user_wants_recording = False
        self.task_wants_recording = False
        self.is_recording = False
        self.recording_dir: Optional[Path] = None
        self._log_handle = None
        self._jsonl_handle = None

    def start_recording(self, owner: str = "user", task_dir: str | os.PathLike | None = None) -> tuple[str, bool]:
        """Assert recording demand and open shared files if needed.

        Args:
        - ``owner``: recording owner string, ``"user"`` or ``"task"``
        - ``task_dir``: task/session directory used for task-owned recordings

        Returns:
        - ``(recording_dir, started_now)``: absolute directory path and whether
          this call opened a new recording session
        """

        with self._record_lock:
            if owner == "task":
                self.task_wants_recording = True
            else:
                self.user_wants_recording = True

            if self.is_recording and self.recording_dir is not None:
                return str(self.recording_dir), False

            self.recording_dir = self._select_recording_dir(owner=owner, task_dir=task_dir)
            self.recording_dir.mkdir(parents=True, exist_ok=True)
            self._open_recording_artifacts()
            self.is_recording = True
            return str(self.recording_dir), True

    def stop_recording(self, owner: str = "user") -> dict[str, object]:
        """Clear recording demand and report whether recording should stop.

        Args:
        - ``owner``: recording owner string, ``"user"`` or ``"task"``

        Returns:
        - ``status``: dictionary with ``status`` and ``recording_dir`` fields
        """

        with self._record_lock:
            if owner == "task":
                self.task_wants_recording = False
            else:
                self.user_wants_recording = False

            if not self.is_recording:
                return {"status": "idle", "recording_dir": None}
            if owner == "user" and self.task_wants_recording:
                return {"status": "deferred", "recording_dir": str(self.recording_dir)}
            if self.user_wants_recording or self.task_wants_recording:
                return {"status": "running", "recording_dir": str(self.recording_dir)}
            return {"status": "stop_pending", "recording_dir": str(self.recording_dir)}

    def finalize_stop(self) -> dict[str, object]:
        """Close shared recording files after stop events have been emitted."""

        with self._record_lock:
            recording_dir = str(self.recording_dir) if self.recording_dir is not None else None
            self._close_recording_artifacts()
            self.is_recording = False
            return {"status": "stopped", "recording_dir": recording_dir}

    def record_event(
        self,
        name: str,
        timestamp: float,
        *,
        log_category: str = "action",
        payload: dict[str, object] | None = None,
    ) -> None:
        """Append one minimal shared IO event to the active recording files."""

        with self._record_lock:
            if not self.is_recording or self._log_handle is None or self._jsonl_handle is None:
                return
            payload = dict(payload or {})
            payload_text = ""
            if payload:
                payload_text = " " + ", ".join(f"{key}={value}" for key, value in sorted(payload.items()))
            self._log_handle.write(f";{timestamp};[{log_category}];{name}{payload_text}\n")
            json_payload = {"name": str(name), "timestamp": float(timestamp)}
            json_payload.update(payload)
            self._jsonl_handle.write(json.dumps(json_payload, sort_keys=True) + "\n")

    def close(self) -> None:
        """Release open shared recording resources without emitting events."""

        with self._record_lock:
            self.user_wants_recording = False
            self.task_wants_recording = False
            self._close_recording_artifacts()
            self.is_recording = False

    def _select_recording_dir(self, owner: str, task_dir: str | os.PathLike | None) -> Path:
        if owner == "task" and task_dir is not None:
            return Path(task_dir).expanduser().resolve()
        external_storage = self.session_info.get("external_storage")
        if external_storage:
            root = Path(str(external_storage)).expanduser()
        else:
            env_root = os.environ.get("INPUT_RECORDING_ROOT")
            if env_root:
                root = Path(env_root).expanduser()
            else:
                root = Path.home() / "behavbox_recordings"
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        return (root / f"{timestamp}_input_recording").resolve()

    def _open_recording_artifacts(self) -> None:
        assert self.recording_dir is not None
        self._log_handle = (self.recording_dir / "input_events.log").open("w", encoding="utf-8", buffering=1)
        self._jsonl_handle = (self.recording_dir / "events.jsonl").open("w", encoding="utf-8", buffering=1)

    def _close_recording_artifacts(self) -> None:
        for handle in (self._jsonl_handle, self._log_handle):
            if handle is not None:
                handle.flush()
                handle.close()
        self._jsonl_handle = None
        self._log_handle = None
