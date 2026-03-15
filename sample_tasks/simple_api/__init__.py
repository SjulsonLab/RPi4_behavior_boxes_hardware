"""Simple task authoring API for common end-user tasks."""

from sample_tasks.simple_api.actions import (
    decrement_param,
    deliver_reward,
    increment_param,
    play_cue,
    record_event,
    set_param,
    stop_sound,
)
from sample_tasks.simple_api.builder import SimpleTask

__all__ = [
    "SimpleTask",
    "play_cue",
    "stop_sound",
    "deliver_reward",
    "set_param",
    "increment_param",
    "decrement_param",
    "record_event",
]
