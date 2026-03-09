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
                }
            )
            self._pins[pin] = pin_entry

    def register_label(self, pin: int, label: str, direction: Optional[str] = None) -> None:
        with self._lock:
            if not label:
                return
            self._labels[label] = pin
            pin_entry = self._pins.setdefault(pin, {"pin": pin, "value": 0, "active": False})
            pin_entry["label"] = label
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

    def _get_pin_by_label(self, label: str) -> int:
        with self._lock:
            if label not in self._labels:
                raise KeyError(f"Unknown input label: {label}")
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

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            pins = sorted(self._pins.values(), key=lambda item: item.get("pin", -1))
            return {
                "pins": pins,
                "labels": dict(self._labels),
                "visual": dict(self._visual_state),
            }

    def get_events(self, limit: int = 200) -> Dict[str, Any]:
        with self._lock:
            events = list(self._events)[-max(limit, 0):]
            return {"events": list(reversed(events))}


REGISTRY = PinRegistry()


def register_pin_label(pin: int, label: str, direction: Optional[str] = None) -> None:
    REGISTRY.register_label(pin=pin, label=label, direction=direction)


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
