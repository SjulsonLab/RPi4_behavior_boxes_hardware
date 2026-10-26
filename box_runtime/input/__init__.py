"""Input runtime services for BehavBox."""

__all__ = ["InputService"]


def __getattr__(name: str):
    """Return the InputService class lazily; pure input packages need no GPIO.

    Args:
        name: Requested module export name, a string.

    Returns:
        type: The existing InputService class for that export.

    Raises:
        AttributeError: For any unknown export.
    """
    if name == "InputService":
        from .service import InputService

        return InputService
    raise AttributeError(name)
