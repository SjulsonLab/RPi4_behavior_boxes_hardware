"""Compiler from ``SimpleTask`` builder objects to runnable ``TaskProtocol`` tasks.

Data contracts:
- compiled tasks expose the current ``TaskProtocol`` methods
- task parameters remain JSON-serializable scalar values in ``final_task_state``
- runtime phase names are the user-defined state names
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sample_tasks.common.fsm import append_task_event
from sample_tasks.simple_api.actions import ActionSpec
from sample_tasks.simple_api.builder import CueSpec, EventTransitionSpec, SimpleTask, StateSpec, TimerTransitionSpec


def _validate_task(task: SimpleTask) -> None:
    """Validate a built task before compilation."""

    if not task._states:
        raise ValueError("simple task must define at least one state")
    for state_name, state_spec in task._states.items():
        if state_spec.timer_transition is not None:
            _validate_transition_target(state_name, state_spec.timer_transition.goto, task)
        for event_transition in state_spec.event_transitions.values():
            _validate_transition_target(state_name, event_transition.goto, task)
            _validate_actions(task, event_transition.actions)
        _validate_actions(task, tuple(state_spec.on_enter_actions))
    if not any(spec.terminal_reason is not None for spec in task._states.values()):
        raise ValueError("simple task must define at least one terminal state")


def _validate_transition_target(state_name: str, goto: str, task: SimpleTask) -> None:
    if goto not in task._states:
        raise ValueError(f"state {state_name!r} points to missing target state {goto!r}")


def _validate_actions(task: SimpleTask, actions: tuple[ActionSpec, ...]) -> None:
    for action in actions:
        if action.kind == "play_cue":
            cue_name = str(action.payload["cue_name"])
            if cue_name not in task._cues:
                raise ValueError(f"unknown cue name in action: {cue_name!r}")
        elif action.kind in {"increment_param", "decrement_param"}:
            param_name = str(action.payload["name"])
            if param_name not in task._params:
                raise ValueError(f"unknown parameter name in action: {param_name!r}")
        elif action.kind == "deliver_reward":
            amount_ul = action.payload.get("amount_ul")
            if isinstance(amount_ul, str) and amount_ul not in task._params:
                raise ValueError(f"unknown parameter name in action: {amount_ul!r}")


def _resolve_amount(task_state: dict, amount_ul: float | str | None) -> float | None:
    if isinstance(amount_ul, str):
        return float(task_state["params"][amount_ul])
    if amount_ul is None:
        return None
    return float(amount_ul)


def _execute_action(box, task_state: dict, action: ActionSpec, now_s: float) -> None:
    """Execute one compiled action against the box and mutable task state.

    Data contracts:
    - box: BehavBox-like runtime object exposing the action methods used here
    - task_state: dict with JSON-serializable scalar params in task_state["params"]
    - action: ActionSpec naming one built-in action and scalar payload values
    - now_s: float wall-clock timestamp in seconds
    - returns: None, mutates task_state in place and may emit runtime side effects
    """

    if action.kind == "play_cue":
        cue_name = str(action.payload["cue_name"])
        cue_spec: CueSpec = task_state["cues"][cue_name]
        box.play_sound(cue_name, side=cue_spec.side, duration_s=cue_spec.duration_s)
        append_task_event(task_state, "action_play_cue", now_s, cue_name=cue_name, side=cue_spec.side)
        return
    if action.kind == "stop_sound":
        box.stop_sound()
        append_task_event(task_state, "action_stop_sound", now_s)
        return
    if action.kind == "deliver_reward":
        amount_ul = _resolve_amount(task_state, action.payload.get("amount_ul"))
        output_name = str(action.payload.get("output_name", "reward_center"))
        box.deliver_reward(output_name=output_name, reward_size_ul=amount_ul)
        append_task_event(task_state, "action_deliver_reward", now_s, output_name=output_name, amount_ul=amount_ul)
        return
    if action.kind == "set_param":
        task_state["params"][str(action.payload["name"])] = action.payload["value"]
        append_task_event(
            task_state,
            "action_set_param",
            now_s,
            param_name=str(action.payload["name"]),
            value=action.payload["value"],
        )
        return
    if action.kind == "increment_param":
        name = str(action.payload["name"])
        current_value = float(task_state["params"][name])
        new_value = current_value + float(action.payload["amount"])
        max_value = action.payload.get("max_value")
        if max_value is not None:
            new_value = min(new_value, float(max_value))
        task_state["params"][name] = new_value
        append_task_event(task_state, "action_increment_param", now_s, param_name=name, value=new_value)
        return
    if action.kind == "decrement_param":
        name = str(action.payload["name"])
        current_value = float(task_state["params"][name])
        new_value = current_value - float(action.payload["amount"])
        min_value = action.payload.get("min_value")
        if min_value is not None:
            new_value = max(new_value, float(min_value))
        task_state["params"][name] = new_value
        append_task_event(task_state, "action_decrement_param", now_s, param_name=name, value=new_value)
        return
    if action.kind == "record_event":
        payload = dict(action.payload.get("payload", {}))
        append_task_event(task_state, str(action.payload["name"]), now_s, **payload)
        return
    raise RuntimeError(f"unsupported action kind: {action.kind!r}")


def _publish_runtime_state(box, task_state: dict, protocol_name: str) -> None:
    box.publish_runtime_state(
        "task",
        protocol_name=protocol_name,
        phase=task_state["phase"],
        params=dict(task_state["params"]),
        stop_reason=task_state["stop_reason"],
        response_detected=bool(task_state["response_detected"]),
        stimulus_active=bool(task_state["current_state_has_cue"]),
    )


@dataclass
class CompiledSimpleTask:
    """Runnable task object implementing the current task protocol."""

    PROTOCOL_NAME: str
    BOX_PROFILE: str
    initial_state_name: str
    cues: dict[str, CueSpec]
    params: dict[str, Any]
    states: dict[str, StateSpec]

    def _enter_state(self, box, task_state: dict, state_name: str, now_s: float) -> None:
        state_spec = self.states[state_name]
        task_state["phase"] = state_name
        task_state["phase_started_s"] = float(now_s)
        task_state["phase_deadline_s"] = None
        task_state["current_state_has_cue"] = any(action.kind == "play_cue" for action in state_spec.on_enter_actions)
        append_task_event(task_state, "phase_changed", float(now_s), phase=state_name)
        if state_spec.timer_transition is not None:
            task_state["phase_deadline_s"] = float(now_s) + float(state_spec.timer_transition.seconds)
        for action in state_spec.on_enter_actions:
            _execute_action(box, task_state, action, float(now_s))
        if state_spec.terminal_reason is not None:
            task_state["stop_requested"] = True
            task_state["stop_reason"] = state_spec.terminal_reason
        _publish_runtime_state(box, task_state, self.PROTOCOL_NAME)

    def prepare_task(self, box, task_config: dict) -> dict:
        """Prepare mutable task state for the compiled task."""

        for cue_index, cue_spec in enumerate(self.cues.values(), start=1):
            box.register_noise_cue(
                cue_spec.name,
                duration_s=float(cue_spec.duration_s),
                seed=int(cue_index),
            )
        task_state = {
            "config": dict(task_config),
            "cues": dict(self.cues),
            "params": dict(self.params),
            "phase": "idle",
            "phase_started_s": None,
            "phase_deadline_s": None,
            "started_at_s": None,
            "stopped_at_s": None,
            "stop_requested": False,
            "stop_reason": None,
            "response_detected": False,
            "response_timestamp_s": None,
            "task_events": [],
            "current_state_has_cue": False,
        }
        for name, value in dict(task_config).items():
            if name in task_state["params"]:
                task_state["params"][name] = value
        append_task_event(task_state, "task_prepared", time.time())
        return task_state

    def start_task(self, box, task_state: dict) -> None:
        """Start the compiled task in its initial state."""

        now_s = time.time()
        task_state["started_at_s"] = now_s
        append_task_event(task_state, "task_started", now_s)
        self._enter_state(box, task_state, self.initial_state_name, now_s)

    def handle_event(self, box, task_state: dict, event) -> None:
        """Handle one runtime event during the current state."""

        state_spec = self.states[task_state["phase"]]
        event_name = box.event_name(event)
        timestamp = box.event_timestamp(event) or time.time()
        append_task_event(task_state, "input_event", timestamp, event_name=event_name, phase=task_state["phase"])
        transition = state_spec.event_transitions.get(str(event_name))
        if transition is None:
            return
        task_state["response_detected"] = True
        task_state["response_timestamp_s"] = float(timestamp)
        for action in transition.actions:
            _execute_action(box, task_state, action, float(timestamp))
        self._enter_state(box, task_state, transition.goto, float(timestamp))

    def update_task(self, box, task_state: dict, now_s: float) -> None:
        """Advance timer-based transitions or stop on max duration."""

        if task_state["stop_requested"]:
            return
        max_duration_s = task_state["config"].get("max_duration_s")
        if max_duration_s is not None and task_state["started_at_s"] is not None:
            if float(now_s) - float(task_state["started_at_s"]) >= float(max_duration_s):
                task_state["stop_requested"] = True
                task_state["stop_reason"] = "max_duration_elapsed"
                _publish_runtime_state(box, task_state, self.PROTOCOL_NAME)
                return

        deadline_s = task_state["phase_deadline_s"]
        if deadline_s is None or float(now_s) < float(deadline_s):
            return

        state_spec = self.states[task_state["phase"]]
        transition = state_spec.timer_transition
        if transition is None:
            return
        self._enter_state(box, task_state, transition.goto, float(now_s))

    def should_stop(self, box, task_state: dict) -> bool:
        """Return whether the runner should stop."""

        del box
        return bool(task_state["stop_requested"])

    def stop_task(self, box, task_state: dict, reason: str) -> None:
        """Record the stop reason and stop any active cue."""

        now_s = time.time()
        task_state["stopped_at_s"] = now_s
        if task_state["stop_reason"] is None:
            task_state["stop_reason"] = str(reason)
        task_state["stop_requested"] = True
        box.stop_sound()
        append_task_event(task_state, "task_stopped", now_s, reason=str(task_state["stop_reason"]))
        _publish_runtime_state(box, task_state, self.PROTOCOL_NAME)

    def finalize_task(self, box, task_state: dict) -> dict:
        """Build the final task-state summary."""

        del box
        return {
            "protocol_name": self.PROTOCOL_NAME,
            "phase": task_state["phase"],
            "started_at_s": task_state["started_at_s"],
            "stopped_at_s": task_state["stopped_at_s"],
            "stop_reason": task_state["stop_reason"],
            "response_detected": bool(task_state["response_detected"]),
            "response_timestamp_s": task_state["response_timestamp_s"],
            "params": dict(task_state["params"]),
        }


def compile_simple_task(task: SimpleTask) -> CompiledSimpleTask:
    """Compile one builder object into a runnable task object."""

    _validate_task(task)
    initial_state_name = next(iter(task._states))
    return CompiledSimpleTask(
        PROTOCOL_NAME=task.name,
        BOX_PROFILE=task.box_profile,
        initial_state_name=initial_state_name,
        cues=dict(task._cues),
        params=dict(task._params),
        states=dict(task._states),
    )
