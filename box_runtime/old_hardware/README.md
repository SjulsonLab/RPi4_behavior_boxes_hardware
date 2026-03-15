# Archived Hardware Support Code

This directory holds legacy hardware-interface code that is no longer part of
the supported runtime surface on `main`.

The intent is separation, not deletion:

- active runtime code stays under the usual `box_runtime/` service packages
- old camera-acquisition scripts, old treadmill code, and old hardware-support
  helpers live here so they do not clutter the active tree

Nothing in this directory should be treated as part of the current supported
BehavBox API without an explicit migration back into the active runtime.

Current archive groups:

- `video_recording/old/`
  superseded camera-acquisition and preview scripts
- `treadmill/`
  superseded Arduino and inter-integrated circuit (I2C) treadmill code
- `support/`
  superseded hardware-support helpers such as old camera, analog-to-digital
  converter (ADC), real-time clock (RTC), crontab, and syringe-pump support
