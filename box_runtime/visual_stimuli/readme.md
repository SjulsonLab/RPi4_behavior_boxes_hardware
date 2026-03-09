# Visual Stimulus Specs

The visual stimulus stack no longer depends on RPG, `wiringPi`, or the legacy
framebuffer/mailbox path.

Stimuli are now described by YAML files and precomputed before playback.

## Runtime target

- Raspberry Pi 4B bring-up target: 64-bit Raspberry Pi OS Bookworm
- Final validation target: fresh 64-bit Raspberry Pi OS Trixie
- Runtime mode: console DRM/KMS only
- Pi package requirement: `python3-kms++`
- Default stimulus connector: `HDMI-A-1`
- Override connector with `session_info["visual_display_connector"]` or `VISUAL_STIM_CONNECTOR`

## YAML spec format

Each file in `session_info["vis_gratings"]` must contain one YAML mapping with:

- `name`: stimulus lookup name used by `show_grating(name)`
- `duration_s`: duration in seconds
- `angle_deg`: drift angle in degrees
- `spatial_freq_cpd`: spatial frequency in cycles/degree
- `temporal_freq_hz`: temporal frequency in Hz
- `contrast`: unitless contrast in `[0, 1]`
- `background_gray_u8`: background gray in `[0, 255]`
- `waveform`: `"sine"` or `"square"`
- optional `resolution_px`: `[width_px, height_px]`
- optional `degrees_subtended`: horizontal display extent in degrees

Example files:

- `go_grating.yaml`
- `nogo_grating.yaml`

The compatibility wrapper accepts the YAML `name`, the filename stem, or the
full filename as the `show_grating(...)` argument.
