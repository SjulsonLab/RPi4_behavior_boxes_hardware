import os
import platform


class ReservedPinError(RuntimeError):
    """Raised when BehavBox attempts to claim a reserved GPIO pin."""


RESERVED_PIN_REASONS = {
    9: "GPIO9 is reserved for the IRIG timecode sender output and must not be claimed by BehavBox.",
}


def _env_truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _validate_pin(pin: object) -> None:
    try:
        pin_number = int(pin)
    except (TypeError, ValueError):
        return
    if pin_number in RESERVED_PIN_REASONS:
        raise ReservedPinError(RESERVED_PIN_REASONS[pin_number])


def is_raspberry_pi() -> bool:
    if _env_truthy(os.environ.get("BEHAVBOX_FORCE_MOCK", "0")):
        return False

    machine = platform.machine().lower()
    if not any(token in machine for token in ("arm", "aarch")):
        return False

    try:
        with open("/proc/device-tree/model", "r", encoding="utf-8") as f:
            model = f.read().lower()
        return "raspberry pi" in model
    except Exception:
        return False


USING_MOCK_BACKEND = not is_raspberry_pi()


if not USING_MOCK_BACKEND:
    from gpiozero import (
        Button as _GPIOZeroButton,
        DigitalOutputDevice as _GPIOZeroDigitalOutputDevice,
        LED as _GPIOZeroLED,
        PWMLED as _GPIOZeroPWMLED,
        RotaryEncoder as _GPIOZeroRotaryEncoder,
    )

    def register_pin_label(pin: int, label: str, direction=None, aliases=None) -> None:
        return None

    def set_visual_stim_state(
        visual_stim_enabled=None,
        visual_stim_active=None,
        current_grating=None,
    ) -> None:
        return None

    def set_session_state(**kwargs) -> None:
        return None

    def set_task_state(**kwargs) -> None:
        return None

    def set_audio_state(**kwargs) -> None:
        return None

    def set_camera_state(**kwargs) -> None:
        return None

    def set_plot_state(**kwargs) -> None:
        return None

    def get_registry():
        return None

else:
    try:
        from box_runtime.mock_hw.devices import (
            Button as _GPIOZeroButton,
            DigitalOutputDevice as _GPIOZeroDigitalOutputDevice,
            LED as _GPIOZeroLED,
            PWMLED as _GPIOZeroPWMLED,
            RotaryEncoder as _GPIOZeroRotaryEncoder,
        )
        from box_runtime.mock_hw.registry import (
            REGISTRY,
            register_pin_label,
            set_audio_state,
            set_camera_state,
            set_plot_state,
            set_session_state,
            set_task_state,
            set_visual_stim_state,
        )
        from box_runtime.mock_hw.server import ensure_server_running
    except ImportError:
        from box_runtime.mock_hw.devices import (
            Button as _GPIOZeroButton,
            DigitalOutputDevice as _GPIOZeroDigitalOutputDevice,
            LED as _GPIOZeroLED,
            PWMLED as _GPIOZeroPWMLED,
            RotaryEncoder as _GPIOZeroRotaryEncoder,
        )
        from box_runtime.mock_hw.registry import (
            REGISTRY,
            register_pin_label,
            set_audio_state,
            set_camera_state,
            set_plot_state,
            set_session_state,
            set_task_state,
            set_visual_stim_state,
        )
        from box_runtime.mock_hw.server import ensure_server_running

    def get_registry():
        return REGISTRY

    if not _env_truthy(os.environ.get("BEHAVBOX_MOCK_UI_AUTOSTART", "1")):
        pass
    else:
        ensure_server_running()


class DigitalOutputDevice(_GPIOZeroDigitalOutputDevice):
    def __init__(self, pin, *args, **kwargs):
        _validate_pin(pin)
        super().__init__(pin, *args, **kwargs)


class LED(_GPIOZeroLED):
    def __init__(self, pin, *args, **kwargs):
        _validate_pin(pin)
        super().__init__(pin, *args, **kwargs)


class PWMLED(_GPIOZeroPWMLED):
    def __init__(self, pin, *args, **kwargs):
        _validate_pin(pin)
        super().__init__(pin, *args, **kwargs)


class Button(_GPIOZeroButton):
    def __init__(
        self,
        pin=None,
        *,
        pull_up=True,
        active_state=None,
        bounce_time=None,
        hold_time=1,
        hold_repeat=False,
        pin_factory=None,
    ):
        _validate_pin(pin)
        super().__init__(
            pin,
            pull_up=pull_up,
            active_state=active_state,
            bounce_time=bounce_time,
            hold_time=hold_time,
            hold_repeat=hold_repeat,
            pin_factory=pin_factory,
        )


class RotaryEncoder(_GPIOZeroRotaryEncoder):
    def __init__(self, a, b, *args, **kwargs):
        _validate_pin(a)
        _validate_pin(b)
        super().__init__(a, b, *args, **kwargs)
