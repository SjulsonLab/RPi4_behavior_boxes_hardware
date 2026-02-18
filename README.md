# RPi4 Behavior Boxes Hardware

This repository contains only the hardware and low-level support components
from the original `RPi4_behavior_boxes` codebase.

Included directories:
- `essential/` (device interfaces, camera, treadmill, pump, acquisition)
- `debug/` (hardware test/debug scripts)
- `environment/` (environment specification files)
- `mock-gpiozero/` (off-Pi GPIO mock for development/testing)
- `irig_decoding/` (IRIG decode tooling from `charlie-irig`)
- `video_acquisition/` (newer camera acquisition scripts from `matt-behavior`)
- `HQ_camera/` (HQ camera support scripts from `matt-behavior`)

Excluded from this split:
- `task_protocol/` (task-specific experiment logic)
- `obsolete/` (legacy task code)

## Origin

This repository was split from:
`original/RPi4_behavior_boxes`

to support independent versioning of hardware code and task code.

## Integration Notes

- `hardware_refactor_plan.md` was imported from `luke_agent_test` as branch-analysis context for unification work.
- Camera stack additions were imported from `matt-behavior` in Phase 2 (`video_acquisition/`, `HQ_camera/`, and related `essential/video_acquisition/` updates).
