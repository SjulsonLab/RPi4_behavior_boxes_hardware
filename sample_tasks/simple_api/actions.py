"""Built-in action constructors for the simple task authoring API.

Data contracts:
- action objects are immutable descriptors consumed by the compiler
- scalar values must be JSON-serializable
- ``amount_ul`` may be a float or a parameter-name string reference
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ActionSpec:
    """Immutable built-in action descriptor.

    Args:
    - ``kind``: canonical built-in action name
    - ``payload``: JSON-serializable action payload mapping
    """

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


def play_cue(cue_name: str) -> ActionSpec:
    """Build one cue-playback action.

    Args:
    - ``cue_name``: name of a cue defined on the task

    Returns:
    - ``action``: immutable cue-playback descriptor
    """

    return ActionSpec("play_cue", {"cue_name": str(cue_name)})


def stop_sound() -> ActionSpec:
    """Build one sound-stop action."""

    return ActionSpec("stop_sound", {})


def deliver_reward(output_name: str = "reward_center", amount_ul: float | str | None = None) -> ActionSpec:
    """Build one reward-delivery action.

    Args:
    - ``output_name``: semantic output name
    - ``amount_ul``: reward amount in microliters or parameter-name reference

    Returns:
    - ``action``: immutable reward-delivery descriptor
    """

    return ActionSpec(
        "deliver_reward",
        {
            "output_name": str(output_name),
            "amount_ul": amount_ul,
        },
    )


def set_param(name: str, value: Any) -> ActionSpec:
    """Build one parameter-set action."""

    return ActionSpec("set_param", {"name": str(name), "value": value})


def increment_param(name: str, amount: float, max_value: float | None = None) -> ActionSpec:
    """Build one parameter-increment action."""

    return ActionSpec(
        "increment_param",
        {
            "name": str(name),
            "amount": float(amount),
            "max_value": None if max_value is None else float(max_value),
        },
    )


def decrement_param(name: str, amount: float, min_value: float | None = None) -> ActionSpec:
    """Build one parameter-decrement action."""

    return ActionSpec(
        "decrement_param",
        {
            "name": str(name),
            "amount": float(amount),
            "min_value": None if min_value is None else float(min_value),
        },
    )


def record_event(name: str, **payload: Any) -> ActionSpec:
    """Build one task-event recording action."""

    clean_payload = dict(payload)
    return ActionSpec("record_event", {"name": str(name), "payload": clean_payload})
