"""Session finalization and timestamp utilities for the camera service.

Data contracts:
- sensor timestamps: int64 nanoseconds since boot, shape (n_frames,)
- arrival UTC timestamps: int64 nanoseconds since UNIX epoch, shape (n_frames,)
- derived UTC timestamps: int64 nanoseconds since UNIX epoch, shape (n_frames,)
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np


class CameraSessionError(RuntimeError):
    """Raised when session data are invalid or cannot be finalized safely."""


def estimate_required_bytes(
    duration_s: float,
    bitrate_bps: float,
    safety_margin: float = 1.25,
) -> int:
    """Estimate bytes required for a recording.

    Args:
        duration_s: Recording duration in seconds.
        bitrate_bps: Target encoded bitrate in bits/second.
        safety_margin: Multiplicative slack factor, unitless.

    Returns:
        Estimated required bytes as a positive integer.
    """

    required = duration_s * bitrate_bps / 8.0 * safety_margin
    return int(math.ceil(required))


def derive_frame_utc_ns(
    sensor_timestamps_ns: np.ndarray,
    arrival_utc_ns: np.ndarray,
    bin_size_ns: int = 60 * 1_000_000_000,
) -> tuple[np.ndarray, dict]:
    """Estimate capture UTC from sensor and arrival timestamps.

    Args:
        sensor_timestamps_ns: int64 array of sensor timestamps in ns since boot.
        arrival_utc_ns: int64 array of callback arrival timestamps in UTC ns.
        bin_size_ns: Width of offset-smoothing bins in ns.

    Returns:
        Tuple of:
        - derived UTC int64 array with shape (n_frames,)
        - diagnostics dict with residual summaries in ns
    """

    sensor = np.asarray(sensor_timestamps_ns, dtype=np.int64)
    arrival = np.asarray(arrival_utc_ns, dtype=np.int64)
    if sensor.ndim != 1 or arrival.ndim != 1 or len(sensor) != len(arrival):
        raise CameraSessionError("sensor and arrival timestamps must be 1D arrays of equal length")
    if len(sensor) == 0:
        raise CameraSessionError("cannot derive UTC for an empty frame series")

    offsets = arrival - sensor
    start = int(sensor[0])
    stop = int(sensor[-1])
    if stop == start:
        derived = sensor + np.median(offsets).astype(np.int64)
        residual = arrival - derived
        return derived, _residual_diagnostics(residual)

    bin_edges = np.arange(start, stop + bin_size_ns, bin_size_ns, dtype=np.int64)
    if len(bin_edges) < 2:
        bin_edges = np.array([start, stop + 1], dtype=np.int64)

    centers = []
    median_offsets = []
    for left, right in zip(bin_edges[:-1], bin_edges[1:]):
        mask = (sensor >= left) & (sensor < right)
        if not np.any(mask):
            continue
        centers.append((left + right) / 2.0)
        median_offsets.append(float(np.median(offsets[mask])))

    if not centers:
        smoothed_offsets = np.full(sensor.shape, float(np.median(offsets)))
    elif len(centers) == 1:
        smoothed_offsets = np.full(sensor.shape, median_offsets[0])
    else:
        smoothed_offsets = np.interp(
            sensor.astype(np.float64),
            np.asarray(centers, dtype=np.float64),
            np.asarray(median_offsets, dtype=np.float64),
            left=median_offsets[0],
            right=median_offsets[-1],
        )

    derived = sensor + np.rint(smoothed_offsets).astype(np.int64)
    residual = arrival - derived
    return derived, _residual_diagnostics(residual)


def _residual_diagnostics(residual_ns: np.ndarray) -> dict:
    absolute = np.abs(residual_ns.astype(np.int64))
    return {
        "p50_abs_residual_ns": int(np.percentile(absolute, 50)),
        "p95_abs_residual_ns": int(np.percentile(absolute, 95)),
        "max_abs_residual_ns": int(np.max(absolute)),
    }


def load_raw_frames_tsv(path: Path) -> dict[str, np.ndarray]:
    """Load raw per-frame timestamps from TSV.

    Args:
        path: TSV path with frame_index, sensor_timestamp_ns, arrival_utc_ns.

    Returns:
        Dict of int64 arrays keyed by column name.
    """

    frame_index = []
    sensor = []
    arrival = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            frame_index.append(int(row["frame_index"]))
            sensor.append(int(row["sensor_timestamp_ns"]))
            arrival.append(int(row["arrival_utc_ns"]))
    return {
        "frame_index": np.asarray(frame_index, dtype=np.int64),
        "sensor_timestamp_ns": np.asarray(sensor, dtype=np.int64),
        "arrival_utc_ns": np.asarray(arrival, dtype=np.int64),
    }


def write_manifest(path: Path, manifest: dict, base_dir: Path | None = None) -> dict:
    """Write a session manifest, filling missing SHA256 hashes when possible."""

    manifest_data = json.loads(json.dumps(manifest))
    files = manifest_data.get("files", [])
    if base_dir is not None:
        for entry in files:
            name = entry.get("name")
            if not name:
                continue
            if not entry.get("sha256"):
                entry["sha256"] = _sha256(base_dir / name)
            if "size_bytes" not in entry:
                entry["size_bytes"] = (base_dir / name).stat().st_size
    path.write_text(json.dumps(manifest_data, indent=2, sort_keys=True), encoding="utf-8")
    return manifest_data


def load_manifest(path: Path) -> dict:
    """Load a JSON manifest from disk."""

    return json.loads(path.read_text(encoding="utf-8"))


def verify_manifest_hashes(
    manifest_path: Path,
    base_dir: Path,
    raise_on_error: bool = False,
) -> bool:
    """Verify manifest hashes against files under base_dir."""

    manifest = load_manifest(manifest_path)
    for entry in manifest.get("files", []):
        file_path = base_dir / entry["name"]
        if not file_path.exists():
            if raise_on_error:
                raise CameraSessionError(f"missing file during verification: {entry['name']}")
            return False
        if _sha256(file_path) != entry["sha256"]:
            if raise_on_error:
                raise CameraSessionError(f"hash mismatch for {entry['name']}")
            return False
    return True


def finalize_session_directory(
    session_dir: Path,
    fps: float,
    remuxer: Callable[[Path, Path, float], None] | None = None,
) -> dict:
    """Finalize one session directory into experimenter-facing outputs.

    Args:
        session_dir: Directory containing one or more raw attempts.
        fps: Frame rate used for raw H.264 remuxing.
        remuxer: Callable mapping (src_h264, dst_mp4, fps). Defaults to MP4Box/ffmpeg.

    Returns:
        Manifest dictionary written to session_manifest.json.
    """

    session_path = Path(session_dir)
    remux = remuxer or default_remuxer
    raw_paths = sorted(session_path.glob("attempt_*_raw_frames.tsv"))
    if not raw_paths:
        raise CameraSessionError(f"no raw frame TSV files found in {session_path}")

    clean_session = len(raw_paths) == 1
    manifest_attempts = []
    manifest_files = []
    gaps = []
    last_end_utc_ns = None

    for raw_tsv_path in raw_paths:
        attempt_name = raw_tsv_path.stem.replace("_raw_frames", "")
        raw_h264_path = session_path / f"{attempt_name}.h264"
        if not raw_h264_path.exists():
            raise CameraSessionError(f"missing raw H.264 for {attempt_name}")

        rows = load_raw_frames_tsv(raw_tsv_path)
        derived_utc_ns, diagnostics = derive_frame_utc_ns(
            rows["sensor_timestamp_ns"],
            rows["arrival_utc_ns"],
        )

        if clean_session:
            final_tsv = session_path / "session.tsv"
            final_mp4 = session_path / "session.mp4"
        else:
            final_tsv = session_path / f"{attempt_name}.tsv"
            final_mp4 = session_path / f"{attempt_name}.mp4"

        _write_final_attempt_tsv(final_tsv, rows, derived_utc_ns)
        remux(raw_h264_path, final_mp4, fps)

        start_utc_ns = int(derived_utc_ns[0])
        end_utc_ns = int(derived_utc_ns[-1])
        if last_end_utc_ns is not None and start_utc_ns > last_end_utc_ns:
            gaps.append(
                {
                    "start_utc_ns": int(last_end_utc_ns),
                    "end_utc_ns": start_utc_ns,
                    "duration_ns": int(start_utc_ns - last_end_utc_ns),
                }
            )
        last_end_utc_ns = end_utc_ns

        manifest_attempts.append(
            {
                "attempt": attempt_name,
                "raw_h264": raw_h264_path.name,
                "raw_tsv": raw_tsv_path.name,
                "final_mp4": final_mp4.name,
                "final_tsv": final_tsv.name,
                "frame_count": int(len(derived_utc_ns)),
                "start_utc_ns": start_utc_ns,
                "end_utc_ns": end_utc_ns,
                "diagnostics": diagnostics,
            }
        )
        manifest_files.extend(
            [
                {"name": final_mp4.name, "sha256": ""},
                {"name": final_tsv.name, "sha256": ""},
            ]
        )

    manifest = {
        "clean_session": clean_session,
        "attempt_count": len(manifest_attempts),
        "attempts": manifest_attempts,
        "gaps": gaps,
        "files": manifest_files,
    }
    manifest_path = session_path / "session_manifest.json"
    return write_manifest(manifest_path, manifest, base_dir=session_path)


def default_remuxer(src_h264: Path, dst_mp4: Path, fps: float) -> None:
    """Remux raw H.264 into MP4 without re-encoding."""

    commands = [
        ["MP4Box", "-add", str(src_h264), str(dst_mp4), "-fps", str(fps)],
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(src_h264),
            "-c",
            "copy",
            str(dst_mp4),
        ],
    ]
    last_error = None
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0 and dst_mp4.exists():
            return
        last_error = result.stderr or result.stdout
    raise CameraSessionError(
        f"could not remux {src_h264.name} to MP4; last error: {last_error or 'no remux tool found'}"
    )


def _write_final_attempt_tsv(
    path: Path,
    rows: dict[str, np.ndarray],
    derived_utc_ns: np.ndarray,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            [
                "frame_index",
                "sensor_timestamp_ns",
                "arrival_utc_ns",
                "derived_utc_ns",
            ]
        )
        for values in zip(
            rows["frame_index"],
            rows["sensor_timestamp_ns"],
            rows["arrival_utc_ns"],
            derived_utc_ns,
        ):
            writer.writerow([int(value) for value in values])


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8192)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()
