"""GPIO output runtime for rewards, vacuum, punishment, cue LEDs, and triggers.

Data contracts:
- ``manifest``: ``BoxProfileManifest`` describing profile-specific output pins
- ``output_name``: canonical semantic output name from the active profile
- reward sizes: microliters as scalar ``float``
- pulse durations: seconds as scalar ``float``
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from box_runtime.behavior.gpio_backend import DigitalOutputDevice, LED, PWMLED, register_pin_label
from box_runtime.io_manifest import BoxProfileManifest, GpioPinSpec
from box_runtime.io_recording import SharedIoRecorder

if TYPE_CHECKING:
    from box_runtime.behavior.behavbox import BehavBox


class BoxLED(PWMLED):
    """PWM LED wrapper keeping a configurable default intensity."""

    set_value = 1.0

    def on(self) -> None:
        self.value = self.set_value


class OutputService:
    """Own profile-aware GPIO outputs for one BehavBox runtime."""

    def __init__(self, owner: "BehavBox", session_info: dict, manifest: BoxProfileManifest, recorder: SharedIoRecorder):
        self.owner = owner
        self.session_info = session_info
        self.manifest = manifest
        self.recorder = recorder
        self.outputs: dict[str, object] = {}
        self.history: list[dict[str, object]] = []
        self._pulse_lock = threading.RLock()

        self._setup_outputs()

    def _setup_outputs(self) -> None:
        for spec in self.manifest.outputs.values():
            device = self._build_device(spec)
            self.outputs[spec.canonical_name] = device
            register_pin_label(spec.pin, spec.canonical_name, direction="output", aliases=spec.aliases)
            self._publish_owner_alias(spec, device)

    def _build_device(self, spec: GpioPinSpec):
        if spec.canonical_name.startswith("cue_led_"):
            return BoxLED(spec.pin, frequency=200)
        return LED(spec.pin)

    def _publish_owner_alias(self, spec: GpioPinSpec, device: object) -> None:
        attr_map = {
            "cue_led_1": "cueLED1",
            "cue_led_2": "cueLED2",
            "cue_led_3": "cueLED3",
            "cue_led_4": "cueLED4",
            "cue_led_5": "cueLED5",
            "cue_led_6": "cueLED6",
            "trigger_out": "trigger_out",
        }
        attr_name = attr_map.get(spec.canonical_name)
        if attr_name is not None:
            setattr(self.owner, attr_name, device)

    def set_output(self, output_name: str, active: bool) -> None:
        """Drive one named output to a specific on/off state."""

        device = self._require_output(output_name)
        if bool(active):
            device.on()
        else:
            device.off()
        event_name = f"{self._canonical_name(output_name)}_{'on' if active else 'off'}"
        self._record_output_event(event_name, log_category="output")

    def toggle_output(self, output_name: str) -> None:
        """Toggle one named output."""

        device = self._require_output(output_name)
        if hasattr(device, "toggle"):
            device.toggle()
        else:
            if getattr(device, "is_active", False):
                device.off()
            else:
                device.on()
        self._record_output_event(f"{self._canonical_name(output_name)}_toggle", log_category="output")

    def pulse_output(self, output_name: str, duration_s: float | None = None) -> None:
        """Pulse one named output for a finite duration."""

        canonical_name = self._canonical_name(output_name)
        device = self._require_output(canonical_name)
        duration = self._default_duration(canonical_name) if duration_s is None else float(duration_s)
        duration = max(duration, 0.0)
        if hasattr(device, "blink"):
            device.blink(on_time=duration, off_time=0.1, n=1)
        else:
            with self._pulse_lock:
                device.on()
                if duration > 0:
                    time.sleep(duration)
                device.off()
        self._record_output_event(
            f"{canonical_name}_pulse",
            log_category="reward" if canonical_name.startswith("reward_") else "output",
            duration_s=round(duration, 5),
        )

    def deliver_reward(self, output_name: str = "reward_center", reward_size_ul: float | None = None) -> None:
        """Deliver liquid reward using the tracked linear calibration rule."""

        canonical_name = self._canonical_name(output_name)
        if canonical_name not in self.outputs:
            raise KeyError(f"Unknown reward output {output_name!r}.")
        reward_size = float(self.session_info.get("reward_size", 50) if reward_size_ul is None else reward_size_ul)
        calibration_key = self._reward_calibration_key(canonical_name)
        coefficient = self.session_info["calibration_coefficient"][calibration_key]
        duration_s = round((coefficient[0] * (reward_size / 1000.0) + coefficient[1]), 5)
        self.pulse_output(canonical_name, duration_s=duration_s)

    def configure_user_output(self, label: str = "ttl_output"):
        """Claim the generic user-configurable GPIO pin as an output."""

        spec = self.manifest.user_configurable["user_configurable"]
        if label in self.outputs:
            return self.outputs[label]
        device = DigitalOutputDevice(spec.pin)
        self.outputs[label] = device
        register_pin_label(spec.pin, label, direction="output", aliases=spec.aliases + ((spec.board_alias,) if spec.board_alias else ()))
        self._record_output_event("user_output_claimed", log_category="configuration", label=label, pin=spec.pin)
        self.owner.user_output = device
        return device

    def close(self) -> None:
        """Release owned output devices."""

        for device in self.outputs.values():
            if hasattr(device, "close"):
                device.close()

    def _canonical_name(self, output_name: str) -> str:
        if output_name in self.outputs:
            return output_name
        for spec in self.manifest.outputs.values():
            if output_name in spec.aliases:
                return spec.canonical_name
        return str(output_name)

    def _require_output(self, output_name: str):
        canonical_name = self._canonical_name(output_name)
        if canonical_name not in self.outputs:
            raise KeyError(f"Unknown output {output_name!r} for profile {self.manifest.profile_name!r}.")
        return self.outputs[canonical_name]

    def _reward_calibration_key(self, canonical_name: str) -> str:
        mapping = {
            "reward_left": "1",
            "reward_right": "2",
            "reward_center": "3",
            "reward_4": "4",
        }
        return mapping.get(canonical_name, "4")

    def _default_duration(self, canonical_name: str) -> float:
        if canonical_name == "vacuum":
            return float(self.session_info.get("vacuum_duration", 0.01))
        if canonical_name == "airpuff":
            return float(self.session_info.get("air_duration", 0.01))
        return 0.01

    def _record_output_event(self, name: str, *, log_category: str, **payload) -> None:
        timestamp = time.time()
        logging.info(";%s;[%s];%s", timestamp, log_category, name)
        self.recorder.record_event(name, timestamp, log_category=log_category, payload=payload)
        event_payload = {"name": name, "timestamp": timestamp}
        event_payload.update(payload)
        self.history.append(event_payload)
