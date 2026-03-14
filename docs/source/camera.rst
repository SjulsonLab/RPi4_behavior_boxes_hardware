Camera Subsystem
================

Overview
--------

The BehavBox camera subsystem now supports a direct one-Pi automated runtime in
addition to the older HTTP-oriented camera service path that remains in the
repository for reference and future work.

The active one-Pi path is coordinated directly by ``BehavBox`` and is designed
to run on both Raspberry Pi 4 and Raspberry Pi 5. The public camera-facing
runtime is shaped for more than one camera even though the current production
path normally uses a single active camera.

The active runtime covers:

- direct local recording through ``Picamera2Recorder``
- manifest-driven session finalization
- per-camera runtime state publication for the mock UI and future web UI
- optional direct local preview through the display stack
- explicit one-Pi connector ownership rules

The current canonical camera identifiers are ``camera0`` and ``camera1``.

One-Pi Topology
---------------

The current supported one-Pi topology is intentionally narrow:

- visual stimulus owns ``HDMI-A-2``
- local camera preview, when enabled, owns ``HDMI-A-1``
- visual stimulus takes priority over camera preview

``BehavBox.validate_media_config()`` enforces this topology before session
startup. The runtime does not silently fall back to different connectors.

If visual stimulus is requested and ``HDMI-A-2`` is unavailable, session
startup should fail rather than quietly using the wrong display.

Architecture
------------

The active one-Pi camera stack is split into three layers:

- ``BehavBox``
  owns session lifecycle and validates display/camera configuration
- ``CameraManager``
  owns one or more local camera runtimes keyed by camera identifier
- ``LocalCameraRuntime``
  owns one camera recorder instance plus optional preview sink

This keeps visual stimulus and camera as separate subsystems while still giving
``BehavBox`` explicit control over lifecycle and display-resource conflicts.

Multi-Camera Shape
------------------

Pi 4 and Pi 5 are both targets, but the camera API is intentionally written in
a multi-camera-capable shape.

The current configuration surface allows:

- ``camera_enabled``
- ``camera_ids``
- ``camera_preview_modes``
- ``camera_preview_connector``
- ``camera_preview_max_hz``

This means the code can already be configured with ``["camera0", "camera1"]``.
If requested hardware is not physically available, startup should fail cleanly
with a camera-specific error rather than silently dropping a camera.

Version 1 browser preview plumbing is not yet exposed as an operator-facing
interface, but the runtime state is already structured so that future per-camera
preview can be added without redesigning the local recording path.

Recording Path
--------------

The direct recorder path uses ``Picamera2Recorder``. Recording is authoritative;
preview is always best-effort.

The recorder writes:

- one raw ``.h264`` elementary stream per uninterrupted attempt
- one raw per-frame timestamp ``.tsv`` per uninterrupted attempt

On clean stop or later recovery/finalization, the session finalizer produces:

- one user-facing ``.mp4`` for a clean single-attempt session, or one per
  attempt for crash-visible multi-attempt sessions
- one user-facing finalized ``.tsv`` for the clean session, or one per attempt
  for crash-visible sessions
- one ``session_manifest.json`` summarizing attempts, files, and timing
  diagnostics

Raw Timestamp File
------------------

The raw per-attempt timestamp log is append-only and crash-tolerant. It is
written during acquisition and is intended for audit, fitting, and recovery.

The current raw TSV columns are:

- ``frame_index``
- ``sensor_timestamp_ns``
- ``arrival_utc_ns``
- ``boottime_to_realtime_offset_ns``

Interpretation:

- ``frame_index``
  zero-based frame counter
- ``sensor_timestamp_ns``
  camera metadata timestamp in nanoseconds since boot
- ``arrival_utc_ns``
  software-side wall-clock timestamp in nanoseconds when the callback ran
- ``boottime_to_realtime_offset_ns``
  sampled ``CLOCK_REALTIME - CLOCK_BOOTTIME`` offset in nanoseconds

The raw file is intentionally detailed and remains the authoritative record used
for offline UTC estimation and debugging.

Finalized Timestamp File
------------------------

The finalized user-facing TSV is intentionally simpler than the raw file.

The current finalized TSV columns are:

- ``frame_index``
- ``utc_s``

``utc_s`` is written as Unix time in seconds with three decimal places. This is
the user-facing drift-corrected timestamp stream intended to stay simple during
normal analysis use.

UTC Derivation
--------------

The current UTC derivation model does **not** anchor every frame to a single
recording-start timestamp.

Instead, the finalizer:

1. reads the raw ``sensor_timestamp_ns`` values
2. reads the raw sampled ``boottime_to_realtime_offset_ns`` values
3. smooths the offset over time in sensor-timestamp space
4. computes

   ``derived_utc_ns = sensor_timestamp_ns + smoothed_offset_ns``

The raw ``arrival_utc_ns`` values are still preserved because they are useful
for diagnostics, validation, and debugging. They are no longer the primary
fitting target for the current UTC derivation path.

This split keeps the raw logs transparent while letting the user-facing file
stay minimal.

Clock Model
-----------

The runtime treats ``CLOCK_REALTIME`` as the system estimate of UTC, not as a
perfect ground truth.

Chrony or other system time synchronization may adjust that estimate over time.
Because of that, the raw offset measurements are preserved so that offline
processing can estimate a smooth mapping from boot-time-domain camera timestamps
to wall-clock time.

This is also why the finalized user-facing file is generated offline instead of
being written directly during acquisition.

Preview
-------

Local preview is optional and secondary to recording.

The active one-Pi preview path currently uses a direct local JPEG-frame viewer
for DRM/KMS output rather than requiring the HTTP camera service for automated
runs. Preview is capped and may drop frames. It must not block recording or
visual stimulus startup.

Current preview rules:

- only one ``drm_local`` camera preview is supported in the active topology
- local preview uses ``HDMI-A-1``
- preview can be off even when recording is enabled

Browser preview remains planned work above the same per-camera runtime/state
model rather than a separate recording stack.

Legacy HTTP Path
----------------

The repository still contains the older HTTP camera service modules and related
tests. They remain useful for historical reference and for future browser-based
camera workflows.

However, the active one-Pi ``BehavBox`` runtime no longer depends on the HTTP
camera client path for automated task execution.

API Reference
-------------

.. automodule:: box_runtime.video_recording.local_camera_runtime
   :members: CameraManager, LocalCameraRuntime, CameraHardwareUnavailable
