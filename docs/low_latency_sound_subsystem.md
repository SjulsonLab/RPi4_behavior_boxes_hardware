# Low-Latency BehavBox Sound Subsystem

## Summary
Replace the legacy general-purpose input/output (GPIO) sound-pin path with direct Universal Serial Bus (USB) audio playback through Advanced Linux Sound Architecture (ALSA). The first validation target is the Raspberry Pi 5 bench box with the Sabrent `AU-MMSA` adapter; software should still select the device by configurable ALSA card/device so the runtime does not depend on branding strings.

The public surface should be a `BehavBox` facade over an internal persistent audio runtime. Sounds are loaded into random-access memory (RAM) only when the user explicitly calls `load_sound()`. Playback must support `left`, `right`, or `both`, runtime gain in decibels (dB), explicit duration override, looping when the requested duration is longer than the cue, truncation when it is shorter, and immediate early stop from task logic such as lick-driven termination.

Calibration is hardware-level only. There will be no per-box or per-sound calibration files. Instead, an import pipeline converts arbitrary source waveform audio file (WAV) assets into canonical BehavBox-ready cues by resampling, removing direct current (DC) offset by mean subtraction, converting to mono, and normalizing to a white-noise reference with a root mean square (RMS) amplitude rule. Runtime gain remains fully adjustable when cues are played.

## Public Interface and File Layout
- Add a `BehavBox` audio facade with these user-facing methods:
  - `import_wav_file(source_name, cue_name=None, overwrite=False, max_duration_s=10.0, allow_longer=False)`
  - `load_sound(name)`
  - `clear_sounds()`
  - `play_sound(name, side="both", gain_db=0.0, duration_s=None)`
  - `stop_sound()`
  - `start_sound_calibration(side="both", gain_db=0.0)`
  - `stop_sound_calibration()`
  - `measure_sound_latency(name, side="both", gain_db=0.0, repeats=...)`
- `load_sound(name)` is name-based only. It resolves `name.wav` from the canonical cue directories and does not require a path.
- Keep three cue locations:
  - tracked canonical cues: `box_runtime/audio/sounds/`
  - gitignored raw source cues: `box_runtime/audio/local_source_wavs/`
  - gitignored bench-phase canonical cues: `box_runtime/audio/local_sounds/`
- Cue lookup order is `local_sounds/` first, then tracked `sounds/`.
- During the current bench phase, `import_wav_file()` writes normalized cues to `local_sounds/`. Later, selected cues can be copied into the tracked `sounds/` directory.

## Implementation Changes
- Add a new internal audio package, for example `box_runtime/audio/`, with four modules:
  - `runtime`: persistent ALSA playback worker using `pyalsaaudio`, with the device opened once and kept hot
  - `assets`: cue loading, in-memory cache management, stereo routing, looping, truncation, and click-safe ramps
  - `importer`: source-WAV import and normalization pipeline
  - `latency`: loopback capture and onset-latency estimation by cross-correlation against the known playback waveform
- Fixed version 1 runtime format:
  - playback: stereo, signed 16-bit little-endian pulse-code modulation (PCM), `48000 Hz`
  - capture: mono, signed 16-bit little-endian PCM, `48000 Hz`
- Import behavior:
  - source files come from `local_source_wavs/`
  - source `cue_name` defaults to the source stem
  - if a source file is longer than `10.0 s` and `allow_longer=False`, truncate it to the first `10.0 s` and emit a warning telling the user that the file was truncated and that `allow_longer=True` can be used if a longer cue is actually intended
  - if `allow_longer=True`, keep the full imported duration
  - mono sources pass through; stereo sources are downmixed to mono by equal-weight channel average
  - resample to `48000 Hz`
  - subtract the mean to remove DC offset
  - normalize to the built-in white-noise reference by RMS amplitude
  - save the canonical result as a mono BehavBox-ready cue
  - fail by default if the destination cue already exists unless `overwrite=True`
- Playback behavior:
  - only one active cue at a time
  - `play_sound()` interrupts any currently playing cue immediately
  - `stop_sound()` stops the current cue immediately
  - if `duration_s` is longer than the cue, the cue loops to the exact requested sample count
  - if `duration_s` is shorter, the cue is truncated to the exact requested sample count
  - short attack and release ramps are always applied, including loop seams, to reduce clicks
  - runtime gain is applied by sample multiplication in floating point, then clipped safely to the 16-bit output range
- Clipping policy:
  - positive gain is allowed freely
  - playback does not fail on clipping; it clips safely at output
  - warn only when clipping is likely to be significant
  - to avoid adding latency, do not emit the warning before playback starts
  - after playback launch, compute or report the predicted clipped-sample fraction for the exact output buffer that was played
  - default significance rule: emit the warning only if either predicted peak exceeds full scale by at least `1.0 dB` or at least `0.1%` of output samples would clip
  - the warning message must include the estimated percentage of output samples that clipped
- Break cleanly from the legacy sound-pin design on the supported path:
  - remove default ownership of `sound_1` to `sound_4` from `BehavBox`
  - update mock hardware expectations, documentation, and tests to reflect direct audio playback instead of GPIO sound pins

## Dependencies and Performance
- Add `pyalsaaudio` as the new dependency for direct ALSA access.
- Keep the hot path minimal:
  - no `aplay` subprocesses
  - no device reopen per cue
  - no synchronous warning logs before playback
  - precompute routed stereo forms when a cue is loaded into RAM
- The runtime should optimize for low latency by keeping the ALSA stream open and only doing duration slicing, gain multiplication, clipping, and write scheduling at play time.
- Loopback latency measurement is diagnostic mode only, not part of normal playback.

## Tests and Bench Validation
Write these tests first, confirm failure, then implement.

- Import and canonicalization:
  - valid source WAV imports to the expected sample rate, shape, dtype, and units
  - stereo source downmix is correct
  - mean subtraction removes DC offset
  - files longer than `10.0 s` are truncated by default and warn clearly about `allow_longer=True`
  - files longer than `10.0 s` are preserved when `allow_longer=True`
  - overwrite is refused by default
  - RMS normalization produces the expected relative amplitude change
- Cache and lookup:
  - `load_sound(name)` loads only the requested cue into RAM
  - `clear_sounds()` releases all loaded cues
  - lookup prefers `local_sounds/` over tracked `sounds/`
- Playback semantics:
  - long duration loops to the exact requested sample count
  - short duration truncates to the exact requested sample count
  - `stop_sound()` terminates playback early
  - a second `play_sound()` interrupts the first cue
  - `left`, `right`, and `both` routing produce the correct stereo layout
  - ramps are present at onset, stop, and loop seams
  - significant-clipping warnings are deferred until after playback launch
  - clipping warnings include the estimated percent of output samples that would clip
- Loopback analysis:
  - synthetic captured traces recover known onset latency accurately
  - repeated synthetic measurements are stable
- Integration:
  - `BehavBox` delegates correctly to the audio runtime
  - legacy GPIO sound ownership is removed from the supported runtime path
  - mock hardware and documentation match the new design

Bench validation on the Raspberry Pi 5 with the Sabrent adapter and loopback cable:
- confirm ALSA playback and capture on the USB device
- confirm loopback latency can be measured repeatedly and remains stable
- confirm `left`, `right`, and `both` routing by manual listening, since the mono loopback cannot verify stereo separation directly
- confirm long-duration looping works
- confirm early stop works from a live task event
- confirm continuous white-noise calibration mode runs without glitches

## Assumptions and Defaults
- The Sabrent `AU-MMSA` is the intended production adapter, even if the operating system reports it as a generic C-Media device.
- Canonical BehavBox cues are mono in version 1.
- Runtime volume remains adjustable on every playback call.
- White noise is the only built-in calibration signal in version 1.
- Calibration is hardware-level only; cue import performs normalization, but no persistent calibration profiles are created.
- RMS normalization is accepted as an approximate loudness match, not a perceptual loudness model.
