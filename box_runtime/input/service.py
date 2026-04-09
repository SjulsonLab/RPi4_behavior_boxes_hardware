"""GPIO input runtime and treadmill sampling service for BehavBox.

Data contracts:
``session_info`` is a mapping-like object containing profile and configuration
values. ``manifest`` is a ``BoxProfileManifest`` describing profile-specific
input pins. The treadmill speed artifact is a tab-separated value (TSV) file
with columns ``utc_posix_s`` and ``speed_cm_per_s``. Structured events are
written as minimal newline-delimited JavaScript Object Notation (JSONL) records
through the shared input/output recorder.
"""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from box_runtime.behavior.gpio_backend import Button, RotaryEncoder, register_pin_label
from box_runtime.io_manifest import BoxProfileManifest
from box_runtime.io_recording import SharedIoRecorder

if TYPE_CHECKING:
    from box_runtime.behavior.behavbox import BehavBox, BehaviorEvent


class InputService:
    """Own profile-aware GPIO input devices and treadmill sampling."""

    def __init__(
        self,
        owner: "BehavBox",
        session_info: dict,
        manifest: BoxProfileManifest,
        recorder: SharedIoRecorder,
    ):
        self.owner = owner
        self.session_info = session_info
        self.manifest = manifest
        self.recorder = recorder
        self.box_profile = manifest.profile_name
        self.treadmill_speed_hz = float(session_info.get("treadmill_speed_hz", 30.0))
        self.treadmill_wheel_diameter_cm = float(session_info.get("treadmill_wheel_diameter_cm", 2.5))
        self.treadmill_pulses_per_rotation = int(session_info.get("treadmill_pulses_per_rotation", 200))

        self._record_lock = threading.RLock()
        self._treadmill_handle = None
        self._treadmill_thread: Optional[threading.Thread] = None
        self._treadmill_stop = threading.Event()
        self._last_treadmill_steps = 0
        self._last_treadmill_sample_monotonic: Optional[float] = None

        self.user_input: Button | None = None
        self.trigger_in: Button | None = None
        self.treadmill_encoder: RotaryEncoder | None = None
        self.poke_extra1: Button | None = None
        self.poke_extra2: Button | None = None

        self._setup_inputs()

    @property
    def is_recording(self) -> bool:
        return self.recorder.is_recording

    @property
    def recording_dir(self) -> Optional[Path]:
        return self.recorder.recording_dir

    def _setup_inputs(self) -> None:
        self._setup_trigger_input()
        if self.box_profile == "freely_moving":
            self._setup_freely_moving_inputs()
        else:
            self._setup_head_fixed_inputs()

    def _create_input_button(self, pin: int) -> Button:
        """Construct one GPIO input button using the current gpiozero contract.

        Args:
            pin: Integer GPIO pin number owned by the input service.

        Returns:
            Button: Button-like input device configured with floating input
            semantics and explicit active-state handling.
        """

        return Button(pin, pull_up=None, active_state=True)

    def _setup_trigger_input(self) -> None:
        spec = self.manifest.inputs["trigger_in"]
        self.trigger_in = self._create_input_button(spec.pin)
        register_pin_label(spec.pin, spec.canonical_name, direction="input", aliases=spec.aliases)
        self.trigger_in.when_pressed = lambda: self.owner._handle_input_event("trigger_in_rising", record_interaction=False)
        self.trigger_in.when_released = lambda: self.owner._handle_input_event("trigger_in_falling", record_interaction=False)
        self.owner.trigger_in = self.trigger_in
        self.owner.ttl_trigger = None

    def _setup_head_fixed_inputs(self) -> None:
        self._setup_ir_lick_inputs()
        self._setup_contact_lick_inputs()

        encoder_a = self.manifest.inputs["treadmill_1"]
        encoder_b = self.manifest.inputs["treadmill_2"]
        self.treadmill_encoder = RotaryEncoder(encoder_a.pin, encoder_b.pin)
        register_pin_label(encoder_a.pin, encoder_a.canonical_name, direction="input", aliases=encoder_a.aliases)
        register_pin_label(encoder_b.pin, encoder_b.canonical_name, direction="input", aliases=encoder_b.aliases)
        self.owner.treadmill_encoder = self.treadmill_encoder
        self.owner.treadmill_input_1 = None
        self.owner.treadmill_input_2 = None
        self.owner.IR_rx4 = None
        self.owner.IR_rx5 = None
        self.owner.poke_left = None
        self.owner.poke_right = None
        self.owner.poke_center = None
        self.owner.poke_extra1 = None
        self.owner.poke_extra2 = None

    def _setup_freely_moving_inputs(self) -> None:
        for canonical_name in ("poke_left", "poke_right", "poke_center"):
            spec = self.manifest.inputs[canonical_name]
            button = self._create_input_button(spec.pin)
            register_pin_label(spec.pin, spec.canonical_name, direction="input", aliases=spec.aliases)
            button.when_pressed = lambda name=canonical_name: self.owner._handle_input_event(f"{name}_entry")
            button.when_released = lambda name=canonical_name: self.owner._handle_input_event(f"{name}_exit")
            setattr(self.owner, canonical_name, button)

        extra1_spec = self.manifest.inputs["poke_extra1"]
        extra2_spec = self.manifest.inputs["poke_extra2"]
        self.poke_extra1 = self._create_input_button(extra1_spec.pin)
        self.poke_extra2 = self._create_input_button(extra2_spec.pin)
        register_pin_label(extra1_spec.pin, extra1_spec.canonical_name, direction="input", aliases=extra1_spec.aliases)
        register_pin_label(extra2_spec.pin, extra2_spec.canonical_name, direction="input", aliases=extra2_spec.aliases)
        self.poke_extra1.when_pressed = lambda: self.owner._handle_input_event("poke_extra1_entry")
        self.poke_extra1.when_released = lambda: self.owner._handle_input_event("poke_extra1_exit")
        self.poke_extra2.when_pressed = lambda: self.owner._handle_input_event("poke_extra2_entry")
        self.poke_extra2.when_released = lambda: self.owner._handle_input_event("poke_extra2_exit")
        self.owner.poke_extra1 = self.poke_extra1
        self.owner.poke_extra2 = self.poke_extra2
        self.owner.IR_rx1 = self.owner.poke_left
        self.owner.IR_rx2 = self.owner.poke_right
        self.owner.IR_rx3 = self.owner.poke_center
        self.owner.IR_rx4 = self.poke_extra1
        self.owner.IR_rx5 = self.poke_extra2
        self.owner.lick_left = None
        self.owner.lick_right = None
        self.owner.lick_center = None
        self.owner.lick1 = None
        self.owner.lick2 = None
        self.owner.lick3 = None
        self.owner.treadmill_encoder = None

    def _setup_ir_lick_inputs(self) -> None:
        for canonical_name in ("ir_lick_left", "ir_lick_right", "ir_lick_center"):
            spec = self.manifest.inputs[canonical_name]
            button = self._create_input_button(spec.pin)
            register_pin_label(spec.pin, spec.canonical_name, direction="input", aliases=spec.aliases)
            button.when_pressed = lambda name=canonical_name: self.owner._handle_input_event(f"{name}_entry")
            button.when_released = lambda name=canonical_name: self.owner._handle_input_event(f"{name}_exit")
            setattr(self.owner, canonical_name, button)

        self.owner.IR_rx1 = self.owner.ir_lick_left
        self.owner.IR_rx2 = self.owner.ir_lick_right
        self.owner.IR_rx3 = self.owner.ir_lick_center

    def _setup_contact_lick_inputs(self) -> None:
        left_spec = self.manifest.inputs["lick_left"]
        right_spec = self.manifest.inputs["lick_right"]
        center_spec = self.manifest.inputs["lick_center"]
        self.owner.lick_left = self._create_input_button(left_spec.pin)
        self.owner.lick_right = self._create_input_button(right_spec.pin)
        self.owner.lick_center = self._create_input_button(center_spec.pin)
        register_pin_label(left_spec.pin, left_spec.canonical_name, direction="input", aliases=left_spec.aliases)
        register_pin_label(right_spec.pin, right_spec.canonical_name, direction="input", aliases=right_spec.aliases)
        register_pin_label(center_spec.pin, center_spec.canonical_name, direction="input", aliases=center_spec.aliases)

        self.owner.lick_left.when_pressed = self.owner.left_exit
        self.owner.lick_right.when_pressed = self.owner.right_exit
        self.owner.lick_center.when_pressed = self.owner.center_exit
        self.owner.lick_left.when_released = self.owner.left_entry
        self.owner.lick_right.when_released = self.owner.right_entry
        self.owner.lick_center.when_released = self.owner.center_entry

        self.owner.lick1 = self.owner.lick_left
        self.owner.lick2 = self.owner.lick_right
        self.owner.lick3 = self.owner.lick_center

    def configure_user_input(self, label: str = "user_input", pull_up=None, active_state: bool = True) -> Button:
        """Claim the generic user-configurable GPIO pin as an input."""

        del pull_up, active_state
        if self.user_input is not None:
            return self.user_input
        spec = self.manifest.user_configurable["user_configurable"]
        self.user_input = self._create_input_button(spec.pin)
        register_pin_label(spec.pin, label, direction="input", aliases=spec.aliases)
        self.owner.user_input = self.user_input
        return self.user_input

    def on_recording_started(self) -> None:
        with self._record_lock:
            if self.recorder.recording_dir is None:
                return
            if self.treadmill_encoder is None:
                return
            self._treadmill_handle = (self.recorder.recording_dir / "treadmill_speed.tsv").open(
                "w",
                encoding="utf-8",
                buffering=1,
            )
            self._treadmill_handle.write("utc_posix_s\tspeed_cm_per_s\n")
            self._last_treadmill_steps = int(getattr(self.treadmill_encoder, "steps", 0) or 0)
            self._last_treadmill_sample_monotonic = time.monotonic()
            self._start_treadmill_sampler()

    def on_recording_stopped(self) -> None:
        with self._record_lock:
            self._stop_treadmill_sampler()
            if self._treadmill_handle is not None:
                self._treadmill_handle.flush()
                self._treadmill_handle.close()
            self._treadmill_handle = None

    def record_event(self, event: "BehaviorEvent", log_category: str = "action") -> None:
        self.recorder.record_event(event.name, float(event.timestamp), log_category=log_category)

    def close(self) -> None:
        self.on_recording_stopped()
        for device in (
            self.user_input,
            self.trigger_in,
            getattr(self.owner, "ir_lick_left", None),
            getattr(self.owner, "ir_lick_right", None),
            getattr(self.owner, "ir_lick_center", None),
            getattr(self.owner, "lick_left", None),
            getattr(self.owner, "lick_right", None),
            getattr(self.owner, "lick_center", None),
            getattr(self.owner, "poke_left", None),
            getattr(self.owner, "poke_right", None),
            getattr(self.owner, "poke_center", None),
            self.poke_extra1,
            self.poke_extra2,
            self.treadmill_encoder,
        ):
            if device is not None and hasattr(device, "close"):
                device.close()

    def _start_treadmill_sampler(self) -> None:
        if self.treadmill_encoder is None or self._treadmill_handle is None:
            return
        if self._treadmill_thread is not None and self._treadmill_thread.is_alive():
            return
        self._treadmill_stop.clear()
        self._treadmill_thread = threading.Thread(target=self._run_treadmill_sampler, daemon=True)
        self._treadmill_thread.start()

    def _stop_treadmill_sampler(self) -> None:
        self._treadmill_stop.set()
        if self._treadmill_thread is not None and self._treadmill_thread.is_alive():
            self._treadmill_thread.join(timeout=1.0)
        self._treadmill_thread = None
        self._treadmill_stop.clear()

    def _run_treadmill_sampler(self) -> None:
        interval_s = 1.0 / max(self.treadmill_speed_hz, 1e-6)
        while not self._treadmill_stop.wait(interval_s):
            self._write_treadmill_sample()

    def _write_treadmill_sample(self) -> None:
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
