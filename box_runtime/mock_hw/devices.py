import threading
import time
from typing import Any, Optional

from box_runtime.mock_hw.registry import REGISTRY


class _BaseDevice:
    def __init__(self, pin: int) -> None:
        self.pin = int(pin)


class _BaseOutputDevice(_BaseDevice):
    def __init__(self, pin: int, device_type: str) -> None:
        super().__init__(pin)
        self._value: Any = 0
        self._blink_thread: Optional[threading.Thread] = None
        self._blink_stop = threading.Event()
        REGISTRY.register_device(
            pin=self.pin,
            device=self,
            direction="output",
            device_type=device_type,
            initial_value=self._value,
        )

    @property
    def value(self) -> Any:
        return self._value

    @value.setter
    def value(self, value: Any) -> None:
        self._set_value(value, source="code")

    @property
    def is_active(self) -> bool:
        return bool(self._value)

    def _set_value(self, value: Any, source: str = "code") -> None:
        self._value = value
        REGISTRY.set_pin_state(pin=self.pin, value=value, source=source)

    def _write(self, value: Any) -> None:
        self._set_value(value, source="code")

    def on(self) -> None:
        self._set_value(1, source="code")

    def off(self) -> None:
        self._set_value(0, source="code")

    def toggle(self) -> None:
        self._set_value(0 if bool(self._value) else 1, source="code")

    def blink(self, on_time: float = 1.0, off_time: float = 1.0, n: Optional[int] = 1, background: bool = True) -> None:
        self._stop_blink()

        def _run_blink() -> None:
            count = 0
            while n is None or count < n:
                if self._blink_stop.is_set():
                    break
                self.on()
                if self._blink_stop.wait(max(on_time, 0)):
                    break
                self.off()
                if self._blink_stop.wait(max(off_time, 0)):
                    break
                count += 1

        if background:
            self._blink_stop.clear()
            self._blink_thread = threading.Thread(target=_run_blink, daemon=True)
            self._blink_thread.start()
        else:
            _run_blink()

    def _stop_blink(self) -> None:
        self._blink_stop.set()
        if self._blink_thread and self._blink_thread.is_alive():
            self._blink_thread.join(timeout=1)
        self._blink_thread = None
        self._blink_stop.clear()

    def close(self) -> None:
        self._stop_blink()


class DigitalOutputDevice(_BaseOutputDevice):
    def __init__(self, pin: int) -> None:
        super().__init__(pin=pin, device_type="digital_output")


class LED(_BaseOutputDevice):
    def __init__(self, pin: int) -> None:
        super().__init__(pin=pin, device_type="led")


class PWMLED(_BaseOutputDevice):
    def __init__(self, pin: int, frequency: float = 100) -> None:
        self.frequency = frequency
        super().__init__(pin=pin, device_type="pwm_led")

    @property
    def value(self) -> float:
        return float(self._value)

    @value.setter
    def value(self, value: Any) -> None:
        numeric = max(0.0, min(1.0, float(value)))
        self._set_value(numeric, source="code")

    def on(self) -> None:
        self.value = 1.0

    def off(self) -> None:
        self.value = 0.0


class Button(_BaseDevice):
    def __init__(self, pin: int, pull_up: Any = None, active_state: bool = True) -> None:
        super().__init__(pin)
        self.pull_up = pull_up
        self.active_state = active_state
        self.when_pressed = None
        self.when_released = None
        self._active = False
        self._cond = threading.Condition()
        REGISTRY.register_device(
            pin=self.pin,
            device=self,
            direction="input",
            device_type="button",
            initial_value=self._active,
        )

    @property
    def value(self) -> int:
        return 1 if self._active else 0

    @property
    def is_active(self) -> bool:
        return self._active

    def _set_active(self, active: bool, source: str = "code") -> None:
        callback = None
        with self._cond:
            if self._active == active:
                return
            self._active = active
            REGISTRY.set_pin_state(pin=self.pin, value=1 if active else 0, source=source)
            self._cond.notify_all()
            if active:
                callback = self.when_pressed
            else:
                callback = self.when_released

        if callable(callback):
            callback()

    def press(self, source: str = "code") -> None:
        self._set_active(True, source=source)

    def release(self, source: str = "code") -> None:
        self._set_active(False, source=source)

    def wait_for_press(self, timeout: Optional[float] = None) -> bool:
        with self._cond:
            if self._active:
                return True
            return self._cond.wait_for(lambda: self._active, timeout=timeout)

    def wait_for_release(self, timeout: Optional[float] = None) -> bool:
        with self._cond:
            if not self._active:
                return True
            return self._cond.wait_for(lambda: not self._active, timeout=timeout)

    def close(self) -> None:
        return None
