Overview
========

What This Documentation Covers
------------------------------

These Sphinx pages describe the currently supported user-facing surfaces on the
``main`` branch.

At the time of writing, the main areas that users can exercise are:

- the ``BehavBox`` audio methods for importing, loading, playing, and stopping
  named cues
- the one-Pi camera runtime, including local recording and local preview
- the input and output services that own profile-specific general-purpose
  input/output (GPIO) mappings, dedicated trigger lines, and generic user GPIO4
- standalone and task-owned recording artifacts
- the explicit task lifecycle and the current ``head_fixed_gonogo`` sample task

What This Documentation Does Not Treat As Implemented
-----------------------------------------------------

The repository also contains planning documents for:

- additional reference tasks in the hardware repo
- further lifecycle refactors beyond the currently implemented runner and task
  hooks
- future browser-facing workflows

Those plans are important, but they are not yet the supported runtime contract
for ``main``. These Sphinx pages deliberately distinguish implemented behavior
from proposed reorganization work so users are not misled during testing.

Current Main-Branch Runtime Highlights
--------------------------------------

Audio
~~~~~

- direct Universal Serial Bus (USB) audio playback through Advanced Linux Sound
  Architecture (ALSA)
- import-time cue normalization
- runtime channel routing and gain
- diagnostic loopback latency measurement

Camera
~~~~~~

- one-Pi local recording through ``Picamera2Recorder``
- manifest-driven session finalization
- optional direct preview on ``HDMI-A-1``
- explicit display ownership that keeps visual stimulus on ``HDMI-A-2``

Inputs
~~~~~~

- head-fixed and freely moving input profiles
- head-fixed treadmill decoding through ``gpiozero.RotaryEncoder``
- dedicated transistor-transistor logic (TTL) trigger input on GPIO23
- generic user-configurable GPIO4 that can be claimed explicitly as an input or
  output
- parallel artifact writing for text logs, newline-delimited JavaScript Object
  Notation (JSONL) events, and treadmill speed traces

Outputs
~~~~~~~

- profile-aware semantic outputs driven by ``OutputService``
- dedicated ``trigger_out`` on GPIO24
- stable reward and generic output methods exposed through ``BehavBox``

Sample task
~~~~~~~~~~~

- the current supported reference task is ``head_fixed_gonogo``
- it is intended for box bring-up, lifecycle testing, and mock-hardware
  validation rather than as the lab's long-term protocol repository

Documentation scope
~~~~~~~~~~~~~~~~~~~

- these pages focus on the supported surfaces users can try on ``main``
- planning documents remain in Markdown outside the Sphinx reference
