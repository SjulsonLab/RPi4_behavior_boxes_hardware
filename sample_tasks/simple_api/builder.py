"""Builder objects for the simplified task authoring API.

Data contracts:
- cue durations and timer durations use seconds as floats
- parameters must be JSON-serializable scalar values
- built tasks compile into objects satisfying ``TaskProtocol``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sample_tasks.simple_api.actions import (
    ActionSpec,
    decrement_param,
    deliver_reward,
    increment_param,
    play_cue,
    record_event,
    set_param,
    stop_sound,
)


@dataclass(frozen=True)
class CueSpec:
    """Named cue definition.

    Args:
    - ``name``: cue identifier string
    - ``duration_s``: cue duration in seconds
    - ``side``: playback side string, ``left``, ``right``, or ``both``
    """

    name: str
    duration_s: float
    side: str = "both"


@dataclass(frozen=True)
class TimerTransitionSpec:
    """Timer-based transition between task states."""

    seconds: float
    goto: str


@dataclass(frozen=True)
class EventTransitionSpec:
    """Event-driven transition between task states."""

    event_name: str
    actions: tuple[ActionSpec, ...]
    goto: str


@dataclass
class StateSpec:
    """One finite-state-machine state definition."""

    name: str
    on_enter_actions: list[ActionSpec] = field(default_factory=list)
    timer_transition: TimerTransitionSpec | None = None
    event_transitions: dict[str, EventTransitionSpec] = field(default_factory=dict)
    terminal_reason: str | None = None


class StateBuilder:
    """Mutable state-scoped builder that delegates back to the parent task."""

    def __init__(self, task: "SimpleTask", state_name: str) -> None:
        self._task = task
        self._state_name = state_name

    @property
    def _spec(self) -> StateSpec:
        return self._task._states[self._state_name]

    def on_enter(self, *actions: ActionSpec) -> "StateBuilder":
        """Append actions that execute on state entry."""

        self._spec.on_enter_actions.extend(self._task._validate_actions(actions))
        return self

    def after(self, seconds: float, goto: str) -> "StateBuilder":
        """Define one timer-based transition from this state."""

        if self._spec.timer_transition is not None:
            raise ValueError(f"state {self._state_name!r} already has a timer transition")
        self._spec.timer_transition = TimerTransitionSpec(seconds=float(seconds), goto=str(goto))
        return self

    def on_event(self, event_name: str, *actions: ActionSpec, goto: str) -> "StateBuilder":
        """Define one event-driven transition from this state."""

        clean_event_name = str(event_name)
        if clean_event_name in self._spec.event_transitions:
            raise ValueError(f"state {self._state_name!r} already has a transition for {clean_event_name!r}")
        self._spec.event_transitions[clean_event_name] = EventTransitionSpec(
            event_name=clean_event_name,
            actions=tuple(self._task._validate_actions(actions)),
            goto=str(goto),
        )
        return self

    def finish(self, reason: str) -> "StateBuilder":
        """Mark this state as terminal."""

        if self._spec.terminal_reason is not None:
            raise ValueError(f"state {self._state_name!r} is already terminal")
        self._spec.terminal_reason = str(reason)
        return self

    def state(self, name: str) -> "StateBuilder":
        """Delegate to the parent task to define another state."""

        state_name = str(name)
        if state_name in self._task._states:
            return StateBuilder(self._task, state_name)
        return self._task.state(state_name)

    def cue(self, name: str, duration_s: float, side: str = "both") -> "SimpleTask":
        """Delegate cue creation to the parent task."""

        return self._task.cue(name, duration_s=duration_s, side=side)

    def param(self, name: str, value: Any) -> "SimpleTask":
        """Delegate parameter creation to the parent task."""

        return self._task.param(name, value)

    def build(self):
        """Delegate compilation to the parent task."""

        return self._task.build()


class SimpleTask:
    """High-level builder for common FSM tasks.

    Args:
    - ``name``: protocol name string used in runtime state and artifacts
    - ``box_profile``: semantic box profile string, defaults to ``head_fixed``
    """

    def __init__(self, name: str, box_profile: str = "head_fixed") -> None:
        self.name = str(name)
        self.box_profile = str(box_profile)
        self._params: dict[str, Any] = {}
        self._cues: dict[str, CueSpec] = {}
        self._states: dict[str, StateSpec] = {}

    def param(self, name: str, value: Any) -> "SimpleTask":
        """Define one initial task parameter."""

        self._params[str(name)] = value
        return self

    def cue(self, name: str, duration_s: float, side: str = "both") -> "SimpleTask":
        """Define one named cue."""

        cue_name = str(name)
        if cue_name in self._cues:
            raise ValueError(f"duplicate cue name: {cue_name!r}")
        self._cues[cue_name] = CueSpec(name=cue_name, duration_s=float(duration_s), side=str(side))
        return self

    def state(self, name: str) -> StateBuilder:
        """Define one named task state."""

        state_name = str(name)
        if state_name in self._states:
            raise ValueError(f"duplicate state name: {state_name!r}")
        self._states[state_name] = StateSpec(name=state_name)
        return StateBuilder(self, state_name)

    def build(self):
        """Compile the builder into a runnable task object."""

        from sample_tasks.simple_api.compiler import compile_simple_task

        return compile_simple_task(self)

    def _validate_actions(self, actions: tuple[ActionSpec, ...]) -> list[ActionSpec]:
        """Normalize and validate built-in action descriptors."""

        clean_actions: list[ActionSpec] = []
        for action in actions:
            if not isinstance(action, ActionSpec):
                raise TypeError(f"expected ActionSpec, got {type(action)!r}")
            clean_actions.append(action)
        return clean_actions

    def play_cue(self, cue_name: str) -> ActionSpec:
        return play_cue(cue_name)

    def stop_sound(self) -> ActionSpec:
        return stop_sound()

    def deliver_reward(self, output_name: str = "reward_center", amount_ul: float | str | None = None) -> ActionSpec:
        return deliver_reward(output_name=output_name, amount_ul=amount_ul)

    def set_param(self, name: str, value: Any) -> ActionSpec:
        return set_param(name, value)

    def increment_param(self, name: str, amount: float, max_value: float | None = None) -> ActionSpec:
        return increment_param(name, amount, max_value=max_value)

    def decrement_param(self, name: str, amount: float, min_value: float | None = None) -> ActionSpec:
        return decrement_param(name, amount, min_value=min_value)

    def record_event(self, name: str, **payload: Any) -> ActionSpec:
        return record_event(name, **payload)
