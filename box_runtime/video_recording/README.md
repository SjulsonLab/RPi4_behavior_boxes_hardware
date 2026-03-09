# Video Recording

This directory contains the active one-Pi camera service, recording, and local
preview code:

- `camera_client.py`
- `camera_session.py`
- `drm_preview_viewer.py`
- `http_camera_service.py`
- `picamera2_recorder.py`
- `VideoCapture.py`

Legacy acquisition and preview scripts were moved into `old/` to keep the active path separate from the archived one.

## Default topology

- `BehavBox` and `VideoCapture` now default `camera_host` to `127.0.0.1`
- the HTTP service remains the source of truth for preview and remote control
- optional remote camera hosts are still supported by explicit override

## Headless dual-monitor defaults

- stimulus monitor: `HDMI-A-1`
- preview monitor: `HDMI-A-2`
- no X11/Wayland session required

Relevant environment variables:

- `CAMERA_SERVICE_PORT`: HTTP service port, default `8000`
- `CAMERA_PREVIEW_DRM_ENABLE`: set to `0`/`false` to disable the local preview viewer
- `CAMERA_PREVIEW_DRM_CONNECTOR`: DRM connector for the fullscreen preview, default `HDMI-A-2`
- `CAMERA_PREVIEW_DRM_MAX_HZ`: preview refresh cap, default `15`
- `CAMERA_PREVIEW_STALL_TIMEOUT_S`: seconds before the preview blacks out after a stall, default `0.5`

For the older two-Pi design notes, see:

- `../docs/two_pi_camera_service_plan.md`
