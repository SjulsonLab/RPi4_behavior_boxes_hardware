# One-Pi Pi4 Refactor and Bench Bring-Up Plan

## Summary
Refactor the behavior-box hardware/runtime stack so a single Raspberry Pi 4B can drive the full box: local camera preview, low-latency visual stimulus, low-latency audio, GPIO inputs/outputs, reward delivery, and treadmill decoding. The first production milestone is not the web interface. The first production milestone is a fully usable one-Pi box that can run the lab's priority tasks and remain operable with the existing local plotting workflow.

This plan treats the work as an appliance refactor, not a sequence of isolated hardware hacks. The weak points in the current codebase are the media path, treadmill implementation, reward/calibration tooling, and the split between the old behavior-box runtime and the newer task/event model. Those need to be stabilized first so later work, including the web dashboard, sits on durable interfaces instead of more legacy glue.

The intended first supported configuration is:
- Raspberry Pi 4B
- one Pi per box
- local camera preview on `HDMI-A-1`
- low-latency visual stimulus on `HDMI-A-2`
- USB audio through the Sabrent `AU-MMSA` adapter
- GPIO treadmill decoding with `gpiozero.RotaryEncoder`
- local video recording and local preview only
- old human-readable log format preserved
- new structured event objects used internally for runtime/task messaging

## Goals and Non-Goals

### Goals
- Make the one-Pi configuration the primary supported hardware/software target.
- Preserve the ability to run experiments before the web interface is complete.
- Keep real-time plotting available through pygame in the interim, but rebuild it on top of a new plot-data stream rather than preserving the old plotting implementation.
- Add low-latency USB audio playback and loopback latency measurement.
- Replace the legacy treadmill implementation with GPIO rotary encoder support.
- Keep the old log format for operator continuity while adding structured events for new runtime integration.
- Verify task parity for:
  - go/no-go
  - Matt Chin contextual inference
  - a new video-only template for freely moving experiments, usable in head-fixed mode as well
- Add simulated-mouse testing as a blocking task-parity requirement.

### Non-Goals for This Milestone
- The operator web interface.
- Pi 5 as the primary supported target.
- Replacing old logs with a new machine-only logging format.
- Preserving the exact old pygame implementation details.
- Supporting the legacy I2C/Arduino treadmill path in the supported one-Pi box.

## Architecture

### 1. One-Pi appliance runtime
Refactor the hardware repo around explicit runtime services instead of scattered device logic:
- `display service`
  - owns local preview output and low-latency stimulus output
  - preview and stimulus use explicit connectors, not auto-picked outputs
- `audio service`
  - owns low-latency WAV playback through ALSA
  - owns loopback latency measurement through the Sabrent input
- `input service`
  - owns GPIO inputs including lick inputs, beam breaks, and treadmill decoder
- `reward service`
  - owns reward-device actuation and calibration metadata
- `plot/state publisher`
  - exposes live trial/performance information to pygame now and the web UI later

The old `BehavBox` class remains important, but it should stop being the place where every subsystem owns its own bespoke workflow. It should become the composition layer over the services above.

### 2. Display path
The supported display topology is:
- `HDMI-A-1`: local video preview / operator-facing screen
- `HDMI-A-2`: low-latency visual stimulus

Visual stimulus remains DRM/KMS-driven and precomputed. The current RPG-replacement direction stays intact:
- precompute stimuli before playback
- drive the stimulus display through DRM/KMS
- record software-side presentation timing such as enqueue time, first page-flip time, and missed-vblank counts

Preview remains best-effort and must not interfere with stimulus timing. It is acceptable for preview to lag or drop frames under load as long as the stimulus path remains stable.

The system should not depend on a desktop session, keyboard, or mouse. All display ownership must work in a headless appliance deployment.

### 3. Audio path
Audio is part of the first production milestone, not a later add-on.

Implement a dedicated ALSA-based audio service with these properties:
- loads short WAV files into RAM before playback
- uses the Sabrent `AU-MMSA` device directly through ALSA
- avoids desktop audio layers and per-playback process spawn
- exposes a small cue-playback API that tasks can call
- supports audio loopback latency measurement by recording the adapter input

Loopback measurement should be performed by:
- playing a known audio waveform
- recording the hardware loopback on the input side
- recovering latency by cross-correlation against the known playback waveform

This service must be exercised by:
- go/no-go
- Matt context

The video-only template does not need to use audio.

### 4. Treadmill path
The current treadmill module is an old I2C/Arduino reader. That path is not the supported path for the new one-Pi boxes.

Replace it with a GPIO quadrature-decoder path using `gpiozero.RotaryEncoder` as the default backend. The reasons for this choice are:
- current project already uses `gpiozero`
- lower conceptual overhead than adding `pigpio` immediately
- likely simpler future compatibility story for Pi 5

Implementation rules:
- expose signed movement and direction information through the same runtime event stream used by tasks and plotting
- retain a clear treadmill interface so the backend can be swapped later if `gpiozero` proves insufficient under real wheel speeds
- do not carry the old I2C treadmill implementation into the supported one-Pi appliance path

### 5. Runtime/task messaging and logging
Use the newer structured event class from the tasks repo for message passing between the hardware runtime and task code. The current task repo already uses a structured `BehaviorEvent` object and JSONL event artifacts. That should become the runtime message contract for new work.

However, the old human-readable text log format remains in place for now. The immediate design is:
- structured `BehaviorEvent` objects for internal runtime/task communication
- old text log lines preserved for operators and continuity
- both produced in parallel during runs

Do not attempt a broad logging migration in this milestone.

### 6. Plotting path
Real-time plotting remains a hard requirement before the web interface exists, so pygame must continue to be supported. The implementation rule is:
- keep pygame as the display target for now
- do not preserve the old plotting implementation as the source of truth
- build a new plot-data publisher that emits the live information needed for plotting
- make the pygame viewer subscribe to that plot-data stream

The required compatibility target is the same information content, not pixel-exact legacy visuals.

This is important because the later web interface should consume the same state/event/plot stream instead of forcing a second full plotting rewrite.

### 7. Reward abstraction and calibration
Create one reward-delivery abstraction that supports:
- solenoids
- syringe pumps

Treat calibration differently for the two hardware types:
- **solenoids**
  - blocking calibration requirement
  - nonlinear relationship between open time and delivered volume
  - requires a dedicated calibration workflow and persisted fitted parameters or lookup values
- **syringe pumps**
  - supported through the same actuation interface
  - calibration utility may be included, but it is lower priority and does not block the nonlinear solenoid calibration redesign

Do not reuse the current calibration debug script as the production solution. It is interactive legacy code and not a reliable basis for the supported appliance workflow.

Additional hardware utility routines in the same lane:
- continuous sound playback for SPL adjustment
- persistent beam-break monitor with visible state
- direct reward-actuation test routine

### 8. Simulated mouse
Simulated-mouse support is part of the task-parity lane.

Add a test subject layer above the hardware abstraction, not inside the GPIO backend. It should include:
- observer
  - reads task/runtime state and events
- policy
  - produces nontrivial lick behavior
- controller
  - schedules press/release timing
- input injector
  - injects software-side licks through a single canonical interface

Initial simulated-mouse behavior family:
- side bias
- time-varying lick rate
- cue-dependent responding
- omission bursts or fatigue-like drift

The simulated mouse is for protocol debugging first, but it also supports a real end-to-end recovery test:
- run seeded sessions
- generate nontrivial behavioral signatures
- recover those signatures from outputs

Recovery for the first milestone must work from:
- old text logs
- structured events

It does not need to work from text logs alone.

## Work Stages

### Stage 0: Appliance contract freeze
Before implementation, freeze the supported hardware/software contract:
- Pi model: Pi 4B
- one Pi per box
- preview connector: `HDMI-A-1`
- stimulus connector: `HDMI-A-2`
- audio device: Sabrent `AU-MMSA`
- treadmill: GPIO quadrature via `gpiozero.RotaryEncoder`
- local preview only
- local recording only
- old text logs retained
- structured runtime/task events adopted

This stage is complete when downstream work no longer needs to debate the supported topology.

### Stage 1: Bench appliance bring-up
Use a dedicated bench Pi4 with:
- real camera
- two monitors
- Sabrent audio adapter with loopback cable
- treadmill encoder available for manual rotation

Bring up and verify:
- display connector ownership
- stimulus timing logs
- preview stability under stimulus load
- ALSA audio playback
- audio loopback latency measurement
- treadmill event generation and signed directionality

This stage requires human interaction for:
- confirming visible stimulus correctness
- confirming audible cue playback
- turning the rotary encoder

### Stage 2: Core runtime refactor
Refactor runtime internals so the appliance services become stable interfaces:
- display
- audio
- input
- reward
- plot/state publisher

At the same time:
- wire runtime/task communication through structured `BehaviorEvent` objects
- preserve the old text log format
- add the plot-data publisher
- rebuild pygame plotting on top of that publisher

This is the stage where the future web dashboard is made possible, even though the web UI itself is deferred.

### Stage 3: Hardware utility and calibration lane
Once the reward/audio/input abstractions are stable, implement:
- solenoid calibration
- optional syringe-pump calibration utility
- continuous audio playback for SPL adjustment
- beam-break monitor
- reward actuation test routine

This lane is intentionally parallel to task parity after the new abstractions stabilize. It is not the first blocking milestone, but solenoid calibration does block final experiment readiness.

### Stage 4: Task parity lane
Verify the first supported tasks on the one-Pi runtime:
- go/no-go
- Matt context
- new video-only template

Blocking requirements:
- go/no-go uses new audio service
- Matt context uses new audio service
- video-only template records video and previews locally
- rewards flow through the new reward abstraction
- logs retain old format
- structured events are emitted correctly
- pygame plotting works from the new plot-data stream
- simulated mouse runs the tasks and recovery checks pass

### Stage 5: Bench sign-off and release gate
Before declaring the platform ready for real experiments, require a short bench sign-off bundle:
- stimulus is shown on the correct monitor and looks correct
- preview is shown on the correct monitor
- audio is audible and the measured loopback latency is within the agreed threshold
- treadmill rotation produces sensible signed movement
- reward hardware physically actuates
- beam-break state matches occlusion

The intended rule is:
- automation does as much as possible
- a short bench sign-off catches the remaining physical truth that software cannot certify

Only after this stage is the platform considered ready for real experiments.

The web interface planning/implementation starts after this gate.

## Public Interfaces and Contracts

### Display contract
- preview output target: explicit connector name
- stimulus output target: explicit connector name
- stimulus playback command: name-based, precomputed, DRM-backed
- timing log fields must include:
  - enqueue timestamp
  - first flip timestamp
  - missed-vblank count

### Audio contract
- input: WAV assets loaded into RAM
- output: named cue playback on the configured ALSA device
- measurement: loopback latency in milliseconds from recorded input against known playback waveform

### Treadmill contract
- inputs: two GPIO pins carrying quadrature encoder signals
- outputs:
  - signed position/count
  - signed delta
  - direction or direction-equivalent sign
- units and scaling must be documented explicitly once encoder geometry is finalized

### Reward contract
- input:
  - reward device identifier
  - requested reward amount or actuation command
- output:
  - device actuation
  - log/event emission
  - calibration lookup or fit application where applicable

### Event/log contract
- internal runtime/task messages use structured `BehaviorEvent`
- operator-visible text logs retain the old format
- both are emitted during runs

### Plot contract
- plot-data publisher must provide the live information needed for:
  - current trial/state
  - recent licks/choices
  - reward outcomes
  - task performance summaries
- pygame viewer consumes this stream
- future web UI is expected to consume the same stream

### Simulated-mouse contract
- inputs:
  - task/runtime state/events
  - seeded policy parameters
- outputs:
  - software-injected lick actions
  - reproducible sessions whose signatures can be recovered from logs/events

## Tests To Write First

### Appliance/media tests
- display runtime uses explicit preview/stimulus connectors and does not auto-mirror
- stimulus timing logs record enqueue/flip/missed-vblank information
- preview running does not prevent stimulus startup
- ALSA service preloads WAVs and exposes deterministic playback commands
- loopback analyzer recovers known latency from synthetic traces
- treadmill adapter converts synthetic encoder steps into signed movement correctly

### Messaging/logging/plot tests
- runtime/task communication uses structured `BehaviorEvent` objects
- old text log format remains unchanged for the covered message types
- plot-data publisher exposes required live plot information independently of pygame
- pygame viewer renders from plot-data events without owning task logic

### Reward/calibration tests
- reward abstraction supports both solenoid and syringe-pump devices
- solenoid calibration model supports nonlinear mapping and persisted parameters
- beam-break monitor reports state changes correctly
- continuous audio calibration mode starts/stops correctly

### Task-parity tests
- go/no-go uses audio service and emits expected artifacts/events/logs
- context uses audio service and emits expected artifacts/events/logs
- video-only template records video and emits expected minimal outputs
- reward commands route through the unified reward abstraction
- simulated mouse can inject licks through the canonical input injector
- recovery analysis reconstructs seeded side bias and rate drift from text logs plus structured events

### Bench acceptance tests
- stimulus and preview run together on the Pi4 without connector conflicts
- measured audio loopback latency remains stable across repeated trials
- treadmill manual rotation produces correct directionality
- physical reward and beam-break behavior match operator observation

## Performance Considerations
- Stimulus remains DRM/KMS-driven and precomputed; software timing estimates are for regression detection, not absolute photon-on-screen claims.
- Preview must remain best-effort and must never compromise stimulus timing.
- Audio playback must avoid desktop audio stacks and per-cue process spawn.
- Treadmill decoding should be event/callback based rather than polling-based.
- Plot publishing must be lightweight enough that pygame rendering cannot perturb task timing.
- Simulated-mouse injection must use the software input path, not direct mutation of task state.

## Risks and Mitigations
- `gpiozero.RotaryEncoder` may prove insufficient at real wheel speeds.
  - Mitigation: keep a backend interface so the decoder can be replaced without changing task-facing behavior.
- Audio latency may depend strongly on ALSA buffer choices and device configuration.
  - Mitigation: make loopback measurement part of bring-up and keep tuning parameters explicit.
- Legacy pygame expectations may drag old behavior into new code.
  - Mitigation: treat pygame as a renderer only, not the source of plotting logic.
- Parallel calibration/tooling work may drift from the runtime interfaces.
  - Mitigation: do not start that lane until the reward/audio/input abstractions are stable.
- Old text logs may not fully express the structured state needed for recovery.
  - Mitigation: recovery is allowed to use both text logs and structured events in the first milestone.

## Acceptance Criteria
The one-Pi refactor is considered complete for the first milestone when:
- the bench Pi4 can run preview, stimulus, audio, and treadmill together in the supported topology
- go/no-go and Matt context both run on the one-Pi runtime and use the new audio path
- the video-only template runs and records locally
- rewards are routed through the new reward abstraction
- pygame real-time plotting works from the new plot-data stream
- simulated-mouse sessions show recoverable nontrivial behavior
- old text logs remain available
- structured runtime/task events are in use
- bench sign-off passes for stimulus, preview, audio, treadmill, reward, and beam-break behavior

At that point, the system is ready for real experiments, and the next major track is the browser operator interface.
