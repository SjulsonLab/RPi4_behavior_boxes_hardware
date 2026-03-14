"""GPIO input runtime and recording service for BehavBox.

Data contracts:
- session_info: mapping-like object containing profile/configuration values
- recording directories: pathlib.Path locations for input artifacts
- treadmill speed file: TSV with columns ``utc_posix_s`` and ``speed_cm_per_s``
- structured event file: JSONL records with ``name`` and ``timestamp`` fields
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from box_runtime.behavior.gpio_backend import Button, DigitalOutputDevice, RotaryEncoder, register_pin_label

if TYPE_CHECKING:
    from box_runtime.behavior.behavbox import BehavBox, BehaviorEvent


HEAD_FIXED_INPUT_PINS = {
    "lick_1": 26,
    "lick_2": 27,
    "lick_3": 15,
    "treadmill_encoder_a": 13,
    "treadmill_encoder_b": 16,
}

FREELY_MOVING_INPUT_PINS = {
    "lick_1": 26,
    "lick_2": 27,
    "lick_3": 15,
    "poke_extra1": 13,
    "poke_extra2": 16,
}


class InputService:
    """Own GPIO input devices and input-recording artifacts for one BehavBox.

    Args:
        owner: ``BehavBox`` instance receiving task-facing input events.
        session_info: Mapping with profile and recording configuration.

    Returns:
        ``InputService`` configured for the requested profile.
    """

    def __init__(self, owner: "BehavBox", session_info: dict):
        self.owner = owner
        self.session_info = session_info
        self.input_profile = str(session_info.get("input_profile", "head_fixed")).lower()
        self.ttl_trigger_pin = int(session_info.get("ttl_trigger_pin", 4))
        self.treadmill_speed_hz = float(session_info.get("treadmill_speed_hz", 30.0))
        self.treadmill_wheel_diameter_cm = float(session_info.get("treadmill_wheel_diameter_cm", 2.5))
        self.treadmill_pulses_per_rotation = int(session_info.get("treadmill_pulses_per_rotation", 200))

        self.user_wants_recording = False
        self.task_wants_recording = False
        self.is_recording = False
        self.recording_dir: Optional[Path] = None

        self._record_lock = threading.RLock()
        self._log_handle = None
        self._jsonl_handle = None
        self._treadmill_handle = None
        self._treadmill_thread: Optional[threading.Thread] = None
        self._treadmill_stop = threading.Event()
        self._last_treadmill_steps = 0
        self._last_treadmill_sample_monotonic: Optional[float] = None

        self.ttl_trigger: Button | None = None
        self.ttl_output: DigitalOutputDevice | None = None
        self.treadmill_encoder: RotaryEncoder | None = None
        self.poke_extra1: Button | None = None
        self.poke_extra2: Button | None = None

        self._setup_inputs()

    def _setup_inputs(self) -> None:
        """Create input devices for the selected profile and publish owner aliases."""

        self._setup_lick_inputs()
        self._setup_ttl_trigger()

        if self.input_profile == "freely_moving":
            self._setup_freely_moving_inputs()
        else:
            self._setup_head_fixed_inputs()

    def _setup_lick_inputs(self) -> None:
        """Create lick inputs and bind them to legacy BehavBox callbacks."""

        lick_pins = (
            FREELY_MOVING_INPUT_PINS
            if self.input_profile == "freely_moving"
            else HEAD_FIXED_INPUT_PINS
        )
        self.owner.lick1 = Button(lick_pins["lick_1"], None, True)
        self.owner.lick2 = Button(lick_pins["lick_2"], None, True)
        self.owner.lick3 = Button(lick_pins["lick_3"], None, True)
        register_pin_label(lick_pins["lick_1"], "lick_1", direction="input")
        register_pin_label(lick_pins["lick_2"], "lick_2", direction="input")
        register_pin_label(lick_pins["lick_3"], "lick_3", direction="input")
        self.owner.lick1.when_pressed = self.owner.left_exit
        self.owner.lick2.when_pressed = self.owner.right_exit
        self.owner.lick3.when_pressed = self.owner.center_exit
        self.owner.lick1.when_released = self.owner.left_entry
        self.owner.lick2.when_released = self.owner.right_entry
        self.owner.lick3.when_released = self.owner.center_entry

    def _setup_ttl_trigger(self) -> None:
        """Create the default TTL trigger input on the configured pin."""

        self.ttl_trigger = Button(self.ttl_trigger_pin, None, True)
        register_pin_label(self.ttl_trigger_pin, "ttl_trigger", direction="input")
        self.ttl_trigger.when_pressed = self._ttl_trigger_rising
        self.ttl_trigger.when_released = self._ttl_trigger_falling
        self.owner.ttl_trigger = self.ttl_trigger

    def _setup_head_fixed_inputs(self) -> None:
        """Create head-fixed treadmill encoder inputs."""

        pins = HEAD_FIXED_INPUT_PINS
        self.treadmill_encoder = RotaryEncoder(
            pins["treadmill_encoder_a"],
            pins["treadmill_encoder_b"],
        )
        register_pin_label(pins["treadmill_encoder_a"], "treadmill_encoder_a", direction="input")
        register_pin_label(pins["treadmill_encoder_b"], "treadmill_encoder_b", direction="input")
        self.owner.treadmill_encoder = self.treadmill_encoder
        self.owner.treadmill_input_1 = None
        self.owner.treadmill_input_2 = None
        self.owner.IR_rx4 = None
        self.owner.IR_rx5 = None
        self.owner.poke_extra1 = None
        self.owner.poke_extra2 = None

    def _setup_freely_moving_inputs(self) -> None:
        """Create freely moving beam-break inputs on GPIO13/16."""

        pins = FREELY_MOVING_INPUT_PINS
        self.poke_extra1 = Button(pins["poke_extra1"], None, True)
        self.poke_extra2 = Button(pins["poke_extra2"], None, True)
        register_pin_label(pins["poke_extra1"], "poke_extra1", direction="input")
        register_pin_label(pins["poke_extra2"], "poke_extra2", direction="input")
        self.poke_extra1.when_pressed = lambda: self.owner._handle_input_event("poke_extra1_entry")
        self.poke_extra1.when_released = lambda: self.owner._handle_input_event("poke_extra1_exit")
        self.poke_extra2.when_pressed = lambda: self.owner._handle_input_event("poke_extra2_entry")
        self.poke_extra2.when_released = lambda: self.owner._handle_input_event("poke_extra2_exit")
        self.owner.poke_extra1 = self.poke_extra1
        self.owner.poke_extra2 = self.poke_extra2
        self.owner.treadmill_encoder = None
        self.owner.treadmill_input_1 = None
        self.owner.treadmill_input_2 = None
        self.owner.IR_rx4 = self.poke_extra1
        self.owner.IR_rx5 = self.poke_extra2

    def _ttl_trigger_rising(self) -> None:
        self.owner._handle_input_event("ttl_trigger_rising", record_interaction=False)

    def _ttl_trigger_falling(self) -> None:
        self.owner._handle_input_event("ttl_trigger_falling", record_interaction=False)

    def record_event(self, event: "BehaviorEvent", log_category: str = "action") -> None:
        """Write one minimal event to the active input-recording artifacts."""

        with self._record_lock:
            if not self.is_recording or self._log_handle is None or self._jsonl_handle is None:
                return
            self._log_handle.write(f";{event.timestamp};[{log_category}];{event.name}\n")
            self._jsonl_handle.write(
                json.dumps({"name": event.name, "timestamp": float(event.timestamp)}, sort_keys=True) + "\n"
            )

    def start_recording(self, owner: str = "user", task_dir: str | os.PathLike | None = None) -> str:
        """Assert recording demand for a user or task and open artifacts if needed.

        Args:
            owner: ``"user"`` or ``"task"``.
            task_dir: Optional task/session directory used for task-owned recordings.

        Returns:
            Absolute path to the active recording directory.
        """

        with self._record_lock:
            if owner == "task":
                self.task_wants_recording = True
            else:
                self.user_wants_recording = True

            if self.is_recording and self.recording_dir is not None:
                return str(self.recording_dir)

            self.recording_dir = self._select_recording_dir(owner=owner, task_dir=task_dir)
            self.recording_dir.mkdir(parents=True, exist_ok=True)
            self._open_recording_artifacts()
            self.is_recording = True
            self._last_treadmill_steps = int(getattr(self.treadmill_encoder, "steps", 0) or 0)
            self._last_treadmill_sample_monotonic = time.monotonic()
            self._start_treadmill_sampler()

        self.owner._handle_input_event("input_recording_started", record_interaction=False, log_category="configuration")
        return str(self.recording_dir)

    def stop_recording(self, owner: str = "user") -> dict[str, object]:
        """Clear recording demand for a user or task and stop if no demand remains.

        Args:
            owner: ``"user"`` or ``"task"``.

        Returns:
            Status dictionary describing whether recording stopped or was deferred.
        """

        with self._record_lock:
            if owner == "task":
                self.task_wants_recording = False
            else:
                self.user_wants_recording = False

            if not self.is_recording:
                return {"status": "idle", "recording_dir": None}

            if owner == "user" and self.task_wants_recording:
                self.owner._handle_input_event(
                    "input_recording_stop_deferred",
                    record_interaction=False,
                    log_category="warning",
                )
                return {"status": "deferred", "recording_dir": str(self.recording_dir)}

            if self.user_wants_recording or self.task_wants_recording:
                return {"status": "running", "recording_dir": str(self.recording_dir)}

        self.owner._handle_input_event("input_recording_stopped", record_interaction=False, log_category="configuration")
        with self._record_lock:
            recording_dir = str(self.recording_dir) if self.recording_dir is not None else None
            self._close_recording_artifacts()
            self.is_recording = False
        return {"status": "stopped", "recording_dir": recording_dir}

    def handoff_ttl_to_output(self, label: str = "ttl_output") -> DigitalOutputDevice:
        """Relinquish the TTL input pin and return it as a digital output device.

        Args:
            label: Registry label for the output-side ownership.

        Returns:
            ``DigitalOutputDevice`` bound to the former TTL pin.
        """

        if self.ttl_output is not None:
            return self.ttl_output
        if self.ttl_trigger is not None:
            self.ttl_trigger.when_pressed = None
            self.ttl_trigger.when_released = None
            self.ttl_trigger.close()
            self.ttl_trigger = None
            self.owner.ttl_trigger = None
        self.ttl_output = DigitalOutputDevice(self.ttl_trigger_pin)
        register_pin_label(self.ttl_trigger_pin, label, direction="output")
        self.owner._handle_input_event("ttl_trigger_handoff", record_interaction=False, log_category="configuration")
        return self.ttl_output

    def close(self) -> None:
        """Release owned devices and recording resources."""

        with self._record_lock:
            self.user_wants_recording = False
            self.task_wants_recording = False
            if self.is_recording:
                self._close_recording_artifacts()
                self.is_recording = False
        for device in (
            self.ttl_trigger,
            self.ttl_output,
            self.owner.lick1,
            self.owner.lick2,
            self.owner.lick3,
            self.poke_extra1,
            self.poke_extra2,
            self.treadmill_encoder,
        ):
            if device is not None and hasattr(device, "close"):
                device.close()

    def _select_recording_dir(self, owner: str, task_dir: str | os.PathLike | None) -> Path:
        """Resolve the directory used for one new recording session."""

        if owner == "task" and task_dir is not None:
            return Path(task_dir).expanduser().resolve()
        external_storage = self.session_info.get("external_storage")
        if external_storage:
            root = Path(str(external_storage)).expanduser()
        else:
            env_root = os.environ.get("INPUT_RECORDING_ROOT")
            if env_root:
                root = Path(env_root).expanduser()
            else:
                root = Path.home() / "behavbox_recordings"
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        return (root / f"{timestamp}_input_recording").resolve()

    def _open_recording_artifacts(self) -> None:
        """Open text, JSONL, and treadmill artifact files in line-buffered mode."""

        assert self.recording_dir is not None
        self._log_handle = (self.recording_dir / "input_events.log").open("w", encoding="utf-8", buffering=1)
        self._jsonl_handle = (self.recording_dir / "events.jsonl").open("w", encoding="utf-8", buffering=1)
        if self.treadmill_encoder is not None:
            self._treadmill_handle = (self.recording_dir / "treadmill_speed.tsv").open(
                "w",
                encoding="utf-8",
                buffering=1,
            )
            self._treadmill_handle.write("utc_posix_s\tspeed_cm_per_s\n")
        else:
            self._treadmill_handle = None

    def _close_recording_artifacts(self) -> None:
        """Stop the treadmill sampler and close open artifact files."""

        self._stop_treadmill_sampler()
        for handle in (self._treadmill_handle, self._jsonl_handle, self._log_handle):
            if handle is not None:
                handle.flush()
                handle.close()
        self._treadmill_handle = None
        self._jsonl_handle = None
        self._log_handle = None

    def _start_treadmill_sampler(self) -> None:
        """Start the fixed-rate treadmill speed sampler when head-fixed is active."""

        if self.treadmill_encoder is None or self._treadmill_handle is None:
            return
        if self._treadmill_thread is not None and self._treadmill_thread.is_alive():
            return
        self._treadmill_stop.clear()
        self._treadmill_thread = threading.Thread(target=self._run_treadmill_sampler, daemon=True)
        self._treadmill_thread.start()

    def _stop_treadmill_sampler(self) -> None:
        """Stop the background treadmill sampling thread."""

        self._treadmill_stop.set()
        if self._treadmill_thread is not None and self._treadmill_thread.is_alive():
            self._treadmill_thread.join(timeout=1.0)
        self._treadmill_thread = None
        self._treadmill_stop.clear()

    def _run_treadmill_sampler(self) -> None:
        """Write fixed-bin treadmill speed samples while recording remains active."""

        interval_s = 1.0 / max(self.treadmill_speed_hz, 1e-6)
        while not self._treadmill_stop.wait(interval_s):
            self._write_treadmill_sample()

    def _write_treadmill_sample(self) -> None:
        """Append one treadmill speed sample in cm/s for the preceding interval."""

        with self._record_lock:
            if self.treadmill_encoder is None or self._treadmill_handle is None:
                return
            now_monotonic = time.monotonic()
            now_utc = time.time()
            prev_monotonic = self._last_treadmill_sample_monotonic
            current_steps = int(getattr(self.treadmill_encoder, "steps", 0) or 0)
            prev_steps = self._last_treadmill_steps
            self._last_treadmill_sample_monotonic = now_monotonic
            self._last_treadmill_steps = current_steps

            if prev_monotonic is None:
                speed_cm_per_s = 0.0
            else:
                delta_t = max(now_monotonic - prev_monotonic, 1e-9)
                delta_steps = current_steps - prev_steps
                circumference_cm = math.pi * self.treadmill_wheel_diameter_cm
                distance_cm = delta_steps * circumference_cm / float(self.treadmill_pulses_per_rotation)
                speed_cm_per_s = distance_cm / delta_t

            self._treadmill_handle.write(f"{now_utc:.6f}\t{speed_cm_per_s:.6f}\n")
