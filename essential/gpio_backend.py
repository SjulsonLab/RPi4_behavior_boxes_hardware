import os
import platform


def _env_truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
    from gpiozero import Button, DigitalOutputDevice, LED, PWMLED

    def register_pin_label(pin: int, label: str, direction=None) -> None:
        return None

    def set_visual_stim_state(
        visual_stim_enabled=None,
        visual_stim_active=None,
        current_grating=None,
    ) -> None:
        return None

    def get_registry():
        return None

else:
    try:
        from essential.mock_hw.devices import Button, DigitalOutputDevice, LED, PWMLED
        from essential.mock_hw.registry import REGISTRY, register_pin_label, set_visual_stim_state
        from essential.mock_hw.server import ensure_server_running
    except ImportError:
        from mock_hw.devices import Button, DigitalOutputDevice, LED, PWMLED
        from mock_hw.registry import REGISTRY, register_pin_label, set_visual_stim_state
        from mock_hw.server import ensure_server_running

    def get_registry():
        return REGISTRY

    if not _env_truthy(os.environ.get("BEHAVBOX_MOCK_UI_AUTOSTART", "1")):
        pass
    else:
        ensure_server_running()
