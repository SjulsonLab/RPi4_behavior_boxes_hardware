# RPi4 Behavior Boxes Hardware

This repository contains only the hardware and low-level support components
from the original `RPi4_behavior_boxes` codebase.

Included directories:
- `essential/` (device interfaces, camera, treadmill, pump, acquisition)
- `debug/` (hardware test/debug scripts)
- `environment/` (environment specification files)

Excluded from this split:
- `task_protocol/` (task-specific experiment logic)
- `obsolete/` (legacy task code)

## Origin

This repository was split from:
`original/RPi4_behavior_boxes`

to support independent versioning of hardware code and task code.
