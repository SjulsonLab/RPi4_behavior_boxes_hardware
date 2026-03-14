Audio Subsystem
===============

Overview
--------

The BehavBox audio subsystem replaces the old GPIO-triggered sound-board path
with direct Universal Serial Bus (USB) audio playback through Advanced Linux
Sound Architecture (ALSA). Users interact with audio through the
``BehavBox`` methods documented below; tasks do not need to talk to ALSA
directly.

Version 1 is built around these decisions:

- cues are stored as mono waveform audio file (WAV) files
- cues are loaded into random-access memory (RAM) only when
  :meth:`box_runtime.behavior.behavbox.BehavBox.load_sound` is called
- playback supports ``left``, ``right``, or ``both`` routing
- playback gain remains adjustable at runtime in decibels (dB)
- cues can be looped, truncated, interrupted, or stopped early
- import-time normalization uses a white-noise reference and a root mean square
  (RMS) amplitude rule

Directory Layout
----------------

The audio runtime uses three directories:

- ``box_runtime/audio/sounds/``
  Tracked canonical cues that are safe to commit.
- ``box_runtime/audio/local_source_wavs/``
  Gitignored raw source files under local development or bench testing.
- ``box_runtime/audio/local_sounds/``
  Gitignored canonical cues created locally during import and validation.

Cue lookup order is:

1. ``local_sounds/``
2. ``sounds/``

This allows local bench cues to override tracked cues without modifying the
tracked repository state.

Import Workflow
---------------

Raw source cues are imported through
:meth:`box_runtime.behavior.behavbox.BehavBox.import_wav_file`.

Import does the following:

1. reads the source WAV from ``local_source_wavs/``
2. truncates the source to ``10.0 s`` by default unless
   ``allow_longer=True`` is passed
3. downmixes stereo input to mono by equal-weight averaging
4. resamples to ``48000 Hz``
5. subtracts the mean to remove direct current offset (DC offset)
6. normalizes the cue to the white-noise reference using RMS amplitude
7. writes the canonical cue to ``local_sounds/``

Examples
~~~~~~~~

Import a raw source file with the same output name:

.. code-block:: python

   box.import_wav_file("buzzer")

Import a raw source file under a new cue name:

.. code-block:: python

   box.import_wav_file("session_buzzer_take2", cue_name="buzzer")

Overwrite an existing local canonical cue:

.. code-block:: python

   box.import_wav_file("buzzer", overwrite=True)

Allow a source file longer than the default truncation limit:

.. code-block:: python

   box.import_wav_file("long_noise", allow_longer=True)

Loading and Clearing Cues
-------------------------

Canonical cues are not loaded automatically at startup. This is deliberate so
that only task-relevant cues occupy memory.

Load a cue by name:

.. code-block:: python

   box.load_sound("buzzer")

Clear all loaded cues:

.. code-block:: python

   box.clear_sounds()

Playback
--------

Playback is managed by
:meth:`box_runtime.behavior.behavbox.BehavBox.play_sound`.

Supported runtime controls:

- ``side="left"``
- ``side="right"``
- ``side="both"``
- ``gain_db=...``
- ``duration_s=...``

Playback examples
~~~~~~~~~~~~~~~~~

Play a cue on both speakers using its natural duration:

.. code-block:: python

   box.play_sound("buzzer")

Play only on the left channel:

.. code-block:: python

   box.play_sound("buzzer", side="left")

Play louder by six decibels:

.. code-block:: python

   box.play_sound("buzzer", gain_db=6.0)

Play for five seconds, looping if necessary:

.. code-block:: python

   box.play_sound("buzzer", duration_s=5.0)

Stop a cue early:

.. code-block:: python

   box.stop_sound()

Playback Rules
--------------

The runtime guarantees these behaviors:

- only one cue is active at a time
- a new cue interrupts the current cue immediately
- if ``duration_s`` is shorter than the cue, playback truncates early
- if ``duration_s`` is longer than the cue, playback loops until the exact
  requested duration is reached
- short ramps are applied at cue onset, stop, and loop seams to reduce clicks

Runtime Gain and Clipping
-------------------------

Runtime gain is applied by simple sample multiplication in floating point
before conversion to signed 16-bit pulse-code modulation (PCM).

Positive gain is allowed freely. If a request is likely to clip significantly,
the runtime logs a warning *after* playback launch so that warning generation
does not add latency to the hot path.

The warning includes the estimated percentage of output samples that clipped.

Calibration
-----------

Calibration in version 1 is hardware-level only.

The supported routine is continuous white-noise playback:

.. code-block:: python

   box.start_sound_calibration(side="both", gain_db=0.0)
   # adjust the physical speaker gain while watching a sound pressure level (SPL) meter
   box.stop_sound_calibration()

No per-box or per-sound calibration files are created.

Latency Measurement
-------------------

Loopback latency measurement is available through
:meth:`box_runtime.behavior.behavbox.BehavBox.measure_sound_latency`.

Example:

.. code-block:: python

   latencies_ms = box.measure_sound_latency("buzzer", side="both", repeats=5)

This routine is diagnostic mode only. It is not part of the normal cue
delivery path.

Current Bench Notes
-------------------

On the Raspberry Pi 5 bench box with the Sabrent adapter and loopback cable:

- direct ALSA playback is working
- the installed ``python3-alsaaudio`` capture path is broken under Python 3.11
  on Debian Bookworm
- the current measurement code falls back to a streaming ``arecord`` capture
  path for loopback analysis
- recent bench measurements produced a stable loopback estimate around
  ``121.94 ms`` for the tested white-noise cue

That number is a software-plus-device-loopback measurement, not a claim about
animal-perceived loudness timing at the speaker.

API Reference
-------------

.. autoclass:: box_runtime.behavior.behavbox.BehavBox
   :members: import_wav_file, load_sound, clear_sounds, play_sound, stop_sound, start_sound_calibration, stop_sound_calibration, measure_sound_latency
   :member-order: bysource

.. automodule:: box_runtime.audio.importer
   :members: AudioPaths, CueImporter

.. automodule:: box_runtime.audio.runtime
   :members: SoundRuntime
