# Raspberry Pi OS Deviations

This document records ways in which the BehavBox setup currently deviates from
an otherwise standard Raspberry Pi OS installation.

The goal is to make these deviations explicit so that future box setup is
repeatable and does not silently depend on one bench machine's history.

## Audio Python binding

### Standard Raspberry Pi OS behavior

On Raspberry Pi OS Bookworm, installing `python3-alsaaudio` with `apt` provides
the system `alsaaudio` extension module from the Debian package set.

### BehavBox deviation

For the BehavBox audio subsystem, the Debian-packaged module is currently not
good enough on the bench Raspberry Pi 5. The native loopback capture path failed
under Python 3.11 with:

```text
SystemError: PY_SSIZE_T_CLEAN macro must be defined for '#' formats
```

Because of that, the bench box now uses an isolated virtual environment with an
upstream `pyalsaaudio` build instead of the Debian package for the active audio
measurement workflow.

Current bench path:

- Virtual environment: `/home/pi/behavbox-audio-venv`
- Package source: upstream `pyalsaaudio` from Python Package Index (PyPI)

### Why this matters

The fallback `arecord`-based loopback capture path worked, but it produced a
wildly inflated latency estimate of about `121.94 ms`. After switching to the
upstream `pyalsaaudio` build in the virtual environment, the same loopback test
on the Sabrent `AU-MMSA` device produced a steady-state latency estimate of
about `4.48 ms`, with one first-run outlier at about `9.81 ms`.

That means the packaging difference is not cosmetic; it changes whether the
measurement path is trustworthy.

### Required setup on current boxes

Until this is automated or documented elsewhere, the working audio-measurement
setup is:

1. Install system build prerequisites such as `libasound2-dev`.
2. Create a virtual environment for the box, for example
   `/home/pi/behavbox-audio-venv`.
3. Install upstream `pyalsaaudio` into that environment.
4. Run bench latency measurements with that environment's Python interpreter,
   not the Raspberry Pi OS system interpreter.

### Open question

This deviation should eventually move from "bench note" to a supported setup
path. The remaining project decision is whether to handle it by:

- documenting a required virtual environment for audio tools
- adding a setup script that creates it automatically
- or updating the project's environment management to install the working ALSA
  Python binding directly
