"""Picamera2-backed recording runtime for the camera HTTP service."""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder, MJPEGEncoder
    from picamera2.outputs import FileOutput
except Exception:  # pragma: no cover - exercised on Pi hardware
    Picamera2 = None
    H264Encoder = None
    MJPEGEncoder = None
    FileOutput = None

from box_runtime.video_recording.camera_session import (
    CameraSessionError,
    finalize_session_directory,
)


@dataclass
class _StreamingFrame:
    data: bytes | None = None


class _StreamingOutput:
    def __init__(self) -> None:
        self.frame = _StreamingFrame()
        self.condition = threading.Condition()

    def write(self, buf: bytes) -> int:
        with self.condition:
            self.frame.data = bytes(buf)
            self.condition.notify_all()
        return len(buf)


class _FrameWriter:
    """Append-only frame metadata sink that survives process interruption."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = path.open("w", encoding="utf-8", buffering=1)
        self.handle.write("frame_index\tsensor_timestamp_ns\tarrival_utc_ns\n")
        self.frame_index = 0
        self.lock = threading.Lock()

    def append(self, sensor_timestamp_ns: int, arrival_utc_ns: int) -> None:
        with self.lock:
            self.handle.write(
                f"{self.frame_index}\t{int(sensor_timestamp_ns)}\t{int(arrival_utc_ns)}\n"
            )
            self.frame_index += 1

    def close(self) -> None:
        with self.lock:
            self.handle.flush()
            self.handle.close()


class Picamera2Recorder:
    """Real camera recorder used by the local and HTTP camera runtimes.

    Data contracts:
    - storage_root: directory containing session subdirectories
    - camera_num: zero-based camera index accepted by Picamera2
    - session_state.json: records state, owner, fps, bitrate_bps, and attempt count
    - raw attempt files: H.264 elementary stream + append-only frame TSV
    """

    DEFAULT_BITRATE_BPS = 8_000_000
    DEFAULT_FPS = 30.0

    def __init__(self, storage_root: Path, *, camera_num: int = 0, camera_id: str | None = None):
        if Picamera2 is None:
            raise RuntimeError("Picamera2 runtime is unavailable on this host")
        self.storage_root = Path(storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.camera_num = int(camera_num)
        self.camera_id = camera_id or f"camera{self.camera_num}"
        self.lock = threading.RLock()
        self.current_session_dir: Path | None = None
        self.current_owner: str | None = None
        self.current_session_id: str | None = None
        self.current_fps = self.DEFAULT_FPS
        self.current_bitrate_bps = self.DEFAULT_BITRATE_BPS
        self.recording = False
        self._frame_writer: _FrameWriter | None = None
        self._recording_encoder = None
        self._stream_output = _StreamingOutput()

        self.picam2 = Picamera2(camera_num=self.camera_num)
        self._configure_preview_pipeline()

    def _configure_preview_pipeline(self) -> None:
        config = self.picam2.create_video_configuration(
            main={"size": (1920, 1080), "format": "YUV420"},
            lores={"size": (640, 480), "format": "YUV420"},
            controls={"FrameRate": self.current_fps},
        )
        self.picam2.configure(config)
        self._preview_encoder = MJPEGEncoder(bitrate=2_000_000)
        self.picam2.start_encoder(
            self._preview_encoder,
            FileOutput(self._stream_output),
            name="lores",
        )
        self.picam2.start()

    def start(self, session_id: str, owner: str, payload: dict[str, Any]) -> None:
        with self.lock:
            if self.recording:
                raise CameraSessionError("recorder is already active")
            self.current_session_id = session_id
            self.current_owner = owner
            self.current_bitrate_bps = int(payload.get("bitrate_bps", self.DEFAULT_BITRATE_BPS))
            self.current_fps = float(payload.get("fps", self.DEFAULT_FPS))

            session_dir = self.storage_root / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            state = self._load_state(session_dir)
            attempt_index = int(state.get("attempt_count", 0)) + 1
            attempt_name = f"attempt_{attempt_index:03d}"
            raw_h264_path = session_dir / f"{attempt_name}.h264"
            raw_tsv_path = session_dir / f"{attempt_name}_raw_frames.tsv"
            self._frame_writer = _FrameWriter(raw_tsv_path)

            def append_frame(request):
                meta = request.get_metadata()
                sensor_ts = int(meta.get("SensorTimestamp", 0))
                self._frame_writer.append(sensor_ts, time.time_ns())

            self.picam2.pre_callback = append_frame
            self._recording_encoder = H264Encoder(bitrate=self.current_bitrate_bps)
            self.picam2.start_encoder(
                self._recording_encoder,
                str(raw_h264_path),
                name="main",
            )
            self.recording = True
            self.current_session_dir = session_dir
            self._write_state(
                session_dir,
                {
                    "state": "LIVE",
                    "owner": owner,
                    "session_id": session_id,
                    "attempt_count": attempt_index,
                    "fps": self.current_fps,
                    "bitrate_bps": self.current_bitrate_bps,
                },
            )

    def stop(self) -> None:
        with self.lock:
            if not self.recording:
                return
            if self._recording_encoder is not None:
                self.picam2.stop_encoder(self._recording_encoder)
            if self._frame_writer is not None:
                self._frame_writer.close()
            self.recording = False
            self._recording_encoder = None
            self._frame_writer = None
            self._finalize_current_session("READY")
            self.current_session_dir = None
            self.current_owner = None
            self.current_session_id = None

    def recover_live_sessions(self) -> list[str]:
        recovered = []
        for session_dir in sorted(self.storage_root.iterdir()):
            if not session_dir.is_dir():
                continue
            state = self._load_state(session_dir)
            if state.get("state") != "LIVE":
                continue
            self._write_state(session_dir, {**state, "state": "RECOVERING"})
            try:
                fps = float(state.get("fps", self.DEFAULT_FPS))
                finalize_session_directory(session_dir, fps=fps)
                self._write_state(session_dir, {**state, "state": "READY"})
            except Exception as exc:  # pragma: no cover - recovery path is hardware-biased
                self._write_state(
                    session_dir,
                    {
                        **state,
                        "state": "ERROR",
                        "error": str(exc),
                    },
                )
            recovered.append(session_dir.name)
        return recovered

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = []
        for session_dir in sorted(self.storage_root.iterdir()):
            if not session_dir.is_dir():
                continue
            state = self._load_state(session_dir)
            if not state:
                continue
            sessions.append(
                {
                    "session_id": session_dir.name,
                    "state": state.get("state", "UNKNOWN"),
                    "attempt_count": int(state.get("attempt_count", 0)),
                    "owner": state.get("owner"),
                }
            )
        return sessions

    def acknowledge_transfer(self, session_id: str) -> None:
        session_dir = self.storage_root / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)
        transfer_log = self.storage_root / "transfer_history.log"
        with transfer_log.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.time_ns()}\t{session_id}\n")

    def preview_frame(self) -> bytes | None:
        return self._stream_output.frame.data

    def available_modes(self) -> list[dict[str, Any]]:
        modes = []
        for mode in getattr(self.picam2, "sensor_modes", []):
            modes.append(
                {
                    "size": tuple(mode.get("size", (0, 0))),
                    "fps": float(mode.get("fps", self.current_fps)),
                }
            )
        return modes

    def current_config(self) -> dict[str, Any]:
        config = self.picam2.camera_configuration()
        return {
            "camera_id": self.camera_id,
            "camera_num": self.camera_num,
            "main_size": tuple(config["main"]["size"]),
            "frame_rate": float(config["controls"]["FrameRate"]),
        }

    def close(self) -> None:
        """Close the underlying Picamera2 device and preview encoder."""

        with self.lock:
            try:
                self.stop()
            except Exception:
                pass
            try:
                self.picam2.stop_encoder(self._preview_encoder)
            except Exception:
                pass
            try:
                self.picam2.stop()
            except Exception:
                pass

    def configure(self, mode: dict[str, Any]) -> None:
        with self.lock:
            if self.recording:
                raise CameraSessionError("cannot reconfigure while recording")
            config = self.picam2.create_video_configuration(
                main={"size": tuple(mode["size"]), "format": "YUV420"},
                lores={"size": (640, 480), "format": "YUV420"},
                controls={"FrameRate": float(mode["fps"])},
            )
            self.picam2.stop()
            self.picam2.configure(config)
            self.picam2.start_encoder(
                self._preview_encoder,
                FileOutput(self._stream_output),
                name="lores",
            )
            self.picam2.start()
            self.current_fps = float(mode["fps"])

    def _finalize_current_session(self, state_name: str) -> None:
        if self.current_session_dir is None:
            return
        state = self._load_state(self.current_session_dir)
        finalize_session_directory(self.current_session_dir, fps=float(state.get("fps", self.current_fps)))
        self._write_state(self.current_session_dir, {**state, "state": state_name})

    def _state_path(self, session_dir: Path) -> Path:
        return session_dir / "session_state.json"

    def _load_state(self, session_dir: Path) -> dict[str, Any]:
        state_path = self._state_path(session_dir)
        if not state_path.exists():
            return {}
        return json.loads(state_path.read_text(encoding="utf-8"))

    def _write_state(self, session_dir: Path, payload: dict[str, Any]) -> None:
        state_path = self._state_path(session_dir)
        state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
