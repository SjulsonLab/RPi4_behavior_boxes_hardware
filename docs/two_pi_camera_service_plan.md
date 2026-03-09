# Two-Pi Camera Service With Drift-Aware UTC and Crash-Visible Outputs

## Summary
Replace the current SSH-based camera control with an always-on HTTP camera service running on the camera Pi. Keep MJPEG browser preview for now, but treat it as best-effort and non-authoritative. Record one raw `H.264` stream plus one raw timing `TSV` per uninterrupted recording attempt. Derive final frame UTC offline from per-frame `sensor_timestamp_ns` and `arrival_utc_ns`; do not use a single start-time anchor. Make crashes visible in final artifacts: a clean session produces one `MP4` plus one `TSV`, while a crash/restart session produces separate `MP4` and `TSV` outputs per attempt, plus a manifest describing attempt boundaries and gaps.

## Key Changes
### Camera Pi service
- Run a Python HTTP service that owns recording, camera configuration, session state, recovery, preview, and offload readiness.
- Expose endpoints for:
  - `start`, `stop`, `status`
  - current config and config changes
  - preview stream
  - session listing and recovery status
  - transfer acknowledgement and cleanup
- Add ownership modes:
  - `manual` for browser-controlled operation
  - `automated` for BehavBox-controlled operation
- Provide two browser interfaces:
  - **Manual UI**: include the same controls Mikhail currently exposes
  - **Monitor UI**: preview/status only during automated runs, plus emergency stop
- During automated ownership, manual start/config changes are blocked.

### Recording and timestamps
- For each uninterrupted recording attempt, write:
  - `attempt_NNN.h264`
  - `attempt_NNN_raw_frames.tsv`
  - append-only session log/state metadata
- Raw per-frame timing rows contain:
  - `frame_index`
  - `sensor_timestamp_ns`
  - `arrival_utc_ns`
- Final UTC is computed offline from the whole attempt using a drift-aware fit from sensor time to UTC.
- Do not compute final UTC as `recording_start_posix + relative_frame_time`.
- Final outputs:
  - clean single-attempt session: one final `MP4` and one final `TSV`
  - crashed multi-attempt session: one final `MP4` and one final `TSV` per attempt
- For crashed sessions, do not merge attempts into one viewing file.
- Write a session manifest capturing:
  - attempt list and order
  - crash/restart boundaries
  - gap intervals
  - camera settings
  - checksums
  - timestamp-fit diagnostics

### Crash recovery and session state
- Each session lives in one directory on the camera Pi and moves through explicit states:
  - `LIVE`
  - `RECOVERING`
  - `READY`
  - `TRANSFERRED`
  - `ERROR`
- On startup, any abandoned `LIVE` session is auto-recovered and exposed for offload/review.
- Recovery salvages as much of each raw `H.264` attempt as possible, finalizes attempt-level outputs, and marks the session `READY` or `ERROR`.
- Camera settings are fixed for the whole session. If attempts differ incompatibly, finalization marks the session for manual review instead of forcing merge/remux behavior.

### Offload and storage
- Keep all artifacts for a session in one directory on the camera Pi.
- BehavBox Pi pulls only `READY` session directories.
- Use `rsync` for directory transfer and delete camera-side session data only after manifest/hash verification succeeds.
- After verified transfer:
  - camera Pi deletes transferred session artifacts and keeps only transfer-history logging
  - BehavBox Pi keeps final user-facing outputs and manifest
- Before recording start, estimate required bytes from bitrate and requested duration plus safety margin.
- Reject start if projected free space is insufficient, and surface this as a blocking error to the BehavBox Pi so the task cannot start.

## Public Interfaces
- BehavBox gets a `CameraClient` that replaces shell-based start/stop/status with HTTP calls.
- Camera service becomes the source of truth for:
  - current recording owner
  - low-space blocking state
  - session readiness for transfer
  - recovery status
  - preview URL
- Preview remains MJPEG for v1 and must never backpressure recording or timestamp capture.

## Tests To Write First
- Timestamp-fit tests with synthetic drift and jitter over `>3 hours`; verify derived UTC beats single-anchor reconstruction.
- Clean single-attempt test: one raw attempt finalizes to one `MP4` plus one `TSV`.
- Crash/restart test: multiple attempts finalize to separate `MP4`/`TSV` pairs with correct manifest boundaries and gap recording.
- Truncated-stream recovery test: salvage recoverable `H.264` tail after simulated interruption.
- Ownership/locking tests:
  - manual controls blocked during automated runs
  - monitor UI remains view-only plus emergency stop
- Preview isolation test: slow MJPEG consumers cannot block recording or timestamp writes.
- Space-guard tests: insufficient projected space blocks recording and propagates a task-blocking error.
- Offload tests:
  - only `READY` sessions transfer
  - failed verification leaves source intact
  - successful verified transfer removes source files and cleans empty directories
- Real hardware validation:
  - long-duration Pi run with an external timing reference before claiming the timing target is met

## Assumptions and Defaults
- Trusted local network; no additional auth layer in v1 unless later required.
- Preview latency is less important than timestamp correctness.
- `MP4Box` is the default remux tool if compatible with the chosen encoded stream; if not, implementation must use a documented fallback.
- Clean sessions stay simple; crashed sessions stay visibly segmented so users can process attempts separately.
