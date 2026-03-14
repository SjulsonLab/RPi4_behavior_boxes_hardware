import threading
import time
from collections import deque
from typing import Any, Dict, Optional


class PinRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pins: Dict[int, Dict[str, Any]] = {}
        self._devices: Dict[int, Any] = {}
        self._labels: Dict[str, int] = {}
        self._events: deque = deque(maxlen=5000)
        self._visual_state: Dict[str, Any] = {
            "visual_stim_enabled": False,
            "visual_stim_active": False,
            "current_grating": None,
            "last_visual_stim_on_ts": None,
            "last_visual_stim_off_ts": None,
        }
        self._runtime_state: Dict[str, Dict[str, Any]] = {
            "session": {
                "active": False,
                "lifecycle_state": "idle",
                "protocol_name": None,
                "box_name": None,
            },
            "task": {
                "protocol_name": None,
                "phase": None,
                "trial_index": None,
                "trial_type": None,
                "completed_trials": 0,
                "max_trials": None,
                "stimulus_active": False,
            },
            "audio": {
                "active": False,
                "current_cue_name": None,
                "last_cue_name": None,
            },
        }

    def reset(self) -> None:
        with self._lock:
            self._pins.clear()
            self._devices.clear()
            self._labels.clear()
            self._events.clear()
            self._visual_state = {
                "visual_stim_enabled": False,
                "visual_stim_active": False,
                "current_grating": None,
                "last_visual_stim_on_ts": None,
                "last_visual_stim_off_ts": None,
            }
            self._runtime_state = {
                "session": {
                    "active": False,
                    "lifecycle_state": "idle",
                    "protocol_name": None,
                    "box_name": None,
                },
                "task": {
                    "protocol_name": None,
                    "phase": None,
                    "trial_index": None,
                    "trial_type": None,
                    "completed_trials": 0,
                    "max_trials": None,
                    "stimulus_active": False,
                },
                "audio": {
                    "active": False,
                    "current_cue_name": None,
                    "last_cue_name": None,
                },
            }

    def register_device(
        self,
        pin: int,
        device: Any,
        direction: str,
        device_type: str,
        initial_value: Any = 0,
    ) -> None:
        with self._lock:
            self._devices[pin] = device
            pin_entry = self._pins.get(pin, {})
            pin_entry.update(
                {
                    "pin": pin,
                    "direction": direction,
                    "device_type": device_type,
                    "value": initial_value,
                    "active": bool(initial_value),
                    "label": pin_entry.get("label"),
                    "aliases": list(pin_entry.get("aliases", [])),
                }
            )
            self._pins[pin] = pin_entry

    def register_label(
        self,
        pin: int,
        label: str,
        direction: Optional[str] = None,
        aliases: Optional[list[str] | tuple[str, ...]] = None,
    ) -> None:
        with self._lock:
            if not label:
                return
            alias_values = [str(alias).strip() for alias in (aliases or []) if str(alias).strip()]
            names_for_pin = [label, *alias_values]
            stale_labels = [name for name, mapped_pin in self._labels.items() if mapped_pin == pin and name not in names_for_pin]
            for stale_label in stale_labels:
                del self._labels[stale_label]
            for name in names_for_pin:
                self._labels[name] = pin
            pin_entry = self._pins.setdefault(pin, {"pin": pin, "value": 0, "active": False})
            pin_entry["label"] = label
            pin_entry["aliases"] = alias_values
            if direction:
                pin_entry["direction"] = direction

    def _record_pin_event(self, pin: int, value: Any, source: str) -> None:
        pin_entry = self._pins.get(pin, {})
        event = {
            "ts": time.time(),
            "kind": "pin",
            "pin": pin,
            "label": pin_entry.get("label"),
            "value": value,
            "active": bool(value),
            "source": source,
        }
        self._events.append(event)

    def set_pin_state(self, pin: int, value: Any, source: str = "code") -> None:
        with self._lock:
            pin_entry = self._pins.setdefault(pin, {"pin": pin})
            pin_entry["value"] = value
            pin_entry["active"] = bool(value)
            self._record_pin_event(pin, value, source)

    def set_visual_state(
        self,
        visual_stim_enabled: Optional[bool] = None,
        visual_stim_active: Optional[bool] = None,
        current_grating: Optional[str] = None,
    ) -> None:
        with self._lock:
            ts = time.time()
            if visual_stim_enabled is not None:
                self._visual_state["visual_stim_enabled"] = visual_stim_enabled
            if visual_stim_active is not None:
                self._visual_state["visual_stim_active"] = visual_stim_active
                if visual_stim_active:
                    self._visual_state["last_visual_stim_on_ts"] = ts
                else:
                    self._visual_state["last_visual_stim_off_ts"] = ts
            if current_grating is not None:
                self._visual_state["current_grating"] = current_grating

            self._events.append(
                {
                    "ts": ts,
                    "kind": "visual_stim",
                    "visual_stim_enabled": self._visual_state["visual_stim_enabled"],
                    "visual_stim_active": self._visual_state["visual_stim_active"],
                    "current_grating": self._visual_state["current_grating"],
                    "source": "code",
                }
            )

    def set_runtime_state(self, section: str, source: str = "code", **values) -> None:
        """Update one runtime-state section and record a state-change event.

        Args:
            section: Runtime section name such as ``session``, ``task``, or ``audio``.
            source: State-update source string.
            values: JSON-serializable key/value updates for the section.
        """

        with self._lock:
            if section not in self._runtime_state:
                self._runtime_state[section] = {}
            self._runtime_state[section].update(values)
            event = {
                "ts": time.time(),
                "kind": f"runtime_{section}",
                "section": section,
                "source": source,
            }
            event.update(values)
            self._events.append(event)

    def _get_pin_by_label(self, label: str) -> int:
        with self._lock:
            if label not in self._labels:
                raise KeyError(f"Unknown label: {label}")
            return self._labels[label]

    def press_input(self, label: str, source: str = "ui") -> None:
        pin = self._get_pin_by_label(label)
        device = self._devices.get(pin)
        if device is None or not hasattr(device, "press"):
            raise ValueError(f"Label {label} does not map to a button input")
        device.press(source=source)

    def release_input(self, label: str, source: str = "ui") -> None:
        pin = self._get_pin_by_label(label)
        device = self._devices.get(pin)
        if device is None or not hasattr(device, "release"):
            raise ValueError(f"Label {label} does not map to a button input")
        device.release(source=source)

    def pulse_input(self, label: str, duration_ms: int, source: str = "pulse") -> None:
        duration_s = max(duration_ms, 0) / 1000.0

        def _pulse() -> None:
            self.press_input(label, source=source)
            if duration_s > 0:
                time.sleep(duration_s)
            self.release_input(label, source=source)

        threading.Thread(target=_pulse, daemon=True).start()

    def set_output_state(self, label: str, active: bool, source: str = "ui") -> None:
        pin = self._get_pin_by_label(label)
        device = self._devices.get(pin)
        if device is None or not hasattr(device, "on") or not hasattr(device, "off"):
            raise ValueError(f"Label {label} does not map to an output device")
        if bool(active):
            if hasattr(device, "_set_value"):
                device._set_value(1 if not isinstance(getattr(device, "value", 0), float) else 1.0, source=source)
            else:
                device.on()
        else:
            if hasattr(device, "_set_value"):
                device._set_value(0 if not isinstance(getattr(device, "value", 0), float) else 0.0, source=source)
            else:
                device.off()

    def toggle_output(self, label: str, source: str = "ui") -> None:
        pin = self._get_pin_by_label(label)
        device = self._devices.get(pin)
        if device is None or not hasattr(device, "toggle"):
            raise ValueError(f"Label {label} does not map to a toggleable output device")
        if hasattr(device, "_set_value"):
            current_value = getattr(device, "value", 0)
            next_value = 0 if bool(current_value) else 1
            if isinstance(current_value, float):
                next_value = float(next_value)
            device._set_value(next_value, source=source)
        else:
            device.toggle()

    def pulse_output(self, label: str, duration_ms: int, source: str = "pulse") -> None:
        duration_s = max(duration_ms, 0) / 1000.0
        pin = self._get_pin_by_label(label)
        device = self._devices.get(pin)
        if device is None or not hasattr(device, "on") or not hasattr(device, "off"):
            raise ValueError(f"Label {label} does not map to a pulseable output device")

        def _pulse() -> None:
            self.set_output_state(label, True, source=source)
            if duration_s > 0:
                time.sleep(duration_s)
            self.set_output_state(label, False, source=source)

        threading.Thread(target=_pulse, daemon=True).start()

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            pins = sorted(self._pins.values(), key=lambda item: item.get("pin", -1))
            return {
                "pins": pins,
                "labels": dict(self._labels),
                "visual": dict(self._visual_state),
                "runtime": {
                    section: dict(values)
                    for section, values in self._runtime_state.items()
                },
            }

    def get_events(self, limit: int = 200) -> Dict[str, Any]:
        with self._lock:
            events = list(self._events)[-max(limit, 0):]
            return {"events": list(reversed(events))}


REGISTRY = PinRegistry()


def register_pin_label(
    pin: int,
    label: str,
    direction: Optional[str] = None,
    aliases: Optional[list[str] | tuple[str, ...]] = None,
) -> None:
    REGISTRY.register_label(pin=pin, label=label, direction=direction, aliases=aliases)


def set_visual_stim_state(
    visual_stim_enabled: Optional[bool] = None,
    visual_stim_active: Optional[bool] = None,
    current_grating: Optional[str] = None,
) -> None:
    REGISTRY.set_visual_state(
        visual_stim_enabled=visual_stim_enabled,
        visual_stim_active=visual_stim_active,
        current_grating=current_grating,
    )


def set_session_state(source: str = "code", **values) -> None:
    REGISTRY.set_runtime_state("session", source=source, **values)


def set_task_state(source: str = "code", **values) -> None:
    REGISTRY.set_runtime_state("task", source=source, **values)


def set_audio_state(source: str = "code", **values) -> None:
    REGISTRY.set_runtime_state("audio", source=source, **values)
