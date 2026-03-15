"""Minimal experiment task used by the user tutorial.

Data contracts:
- ``task_config``: JSON-serializable mapping with scalar task parameters
- ``task_state``: mutable dictionary storing finite-state-machine (FSM) state
- ``event``: runtime event object accepted by ``BehavBox.event_name()``
- return value from ``finalize_task``: JSON-serializable summary dictionary
"""

from __future__ import annotations

import time

from sample_tasks.common.fsm import append_task_event, enter_phase


PROTOCOL_NAME = "minimal_experiment"


def _merged_config(task_config: dict) -> dict:
    """Merge default tutorial settings with runtime overrides.

    Args:
    - ``task_config``: JSON-serializable runtime overrides

    Returns:
    - ``config``: merged configuration dictionary
    """

    defaults = {
        "cue_name": "minimal_tutorial_cue",
        "cue_duration_s": 0.25,
        "cue_side": "both",
        "response_event": "center_entry",
        "response_window_s": 5.0,
        "max_duration_s": 30.0,
        "reward_on_response": False,
        "reward_output": "reward_center",
        "reward_size_ul": 50.0,
    }
    defaults.update(task_config)
    return defaults


def _publish_runtime_state(box, task_state: dict) -> None:
    """Publish minimal task status for mock and future web consumers.

    Args:
    - ``box``: BehavBox runtime exposing ``publish_runtime_state``
    - ``task_state``: mutable task-state dictionary
    """

    box.publish_runtime_state(
        "task",
        protocol_name=PROTOCOL_NAME,
        phase=task_state["phase"],
        response_detected=bool(task_state["response_detected"]),
        response_event=str(task_state["config"]["response_event"]),
        stop_reason=task_state["stop_reason"],
        stimulus_active=(task_state["phase"] == "cue"),
    )


def prepare_task(box, task_config: dict) -> dict:
    """Prepare the minimal tutorial task.

    Args:
    - ``box``: prepared BehavBox runtime
    - ``task_config``: JSON-serializable runtime overrides

    Returns:
    - ``task_state``: mutable task-state dictionary
    """

    config = _merged_config(task_config)
    box.register_noise_cue(
        str(config["cue_name"]),
        duration_s=float(config["cue_duration_s"]),
        seed=7,
    )
    task_state = {
        "config": config,
        "phase": "idle",
        "phase_started_s": None,
        "phase_deadline_s": None,
        "started_at_s": None,
        "stopped_at_s": None,
        "stop_requested": False,
        "stop_reason": None,
        "response_detected": False,
        "response_timestamp_s": None,
        "response_latency_s": None,
        "task_events": [],
    }
    append_task_event(task_state, "task_prepared", time.time())
    _publish_runtime_state(box, task_state)
    return task_state


def start_task(box, task_state: dict) -> None:
    """Start the cue phase of the minimal tutorial task.

    Args:
    - ``box``: running BehavBox runtime
    - ``task_state``: mutable task-state dictionary
    """

    now_s = time.time()
    task_state["started_at_s"] = now_s
    append_task_event(task_state, "task_started", now_s)
    enter_phase(
        task_state,
        "cue",
        now_s,
        duration_s=float(task_state["config"]["cue_duration_s"]),
    )
    box.play_sound(
        str(task_state["config"]["cue_name"]),
        side=str(task_state["config"]["cue_side"]),
    )
    _publish_runtime_state(box, task_state)


def handle_event(box, task_state: dict, event) -> None:
    """Handle one runtime event during the response window.

    Args:
    - ``box``: running BehavBox runtime
    - ``task_state``: mutable task-state dictionary
    - ``event``: runtime event accepted by ``BehavBox.event_name()``
    """

    name = box.event_name(event)
    timestamp = box.event_timestamp(event) or time.time()
    append_task_event(task_state, "input_event", timestamp, event_name=name, phase=task_state["phase"])
    if task_state["phase"] != "response_window":
        return
    if name != str(task_state["config"]["response_event"]):
        return
    if task_state["response_detected"]:
        return

    task_state["response_detected"] = True
    task_state["response_timestamp_s"] = float(timestamp)
    task_state["response_latency_s"] = float(timestamp) - float(task_state["phase_started_s"])
    task_state["stop_requested"] = True
    task_state["stop_reason"] = "response_received"
    append_task_event(
        task_state,
        "response_detected",
        float(timestamp),
        event_name=name,
        response_latency_s=float(task_state["response_latency_s"]),
    )
    if bool(task_state["config"]["reward_on_response"]):
        box.deliver_reward(
            output_name=str(task_state["config"]["reward_output"]),
            reward_size_ul=float(task_state["config"]["reward_size_ul"]),
        )
    enter_phase(task_state, "finished", float(timestamp))
    _publish_runtime_state(box, task_state)


def update_task(box, task_state: dict, now_s: float) -> None:
    """Advance the minimal finite state machine based on elapsed time.

    Args:
    - ``box``: running BehavBox runtime
    - ``task_state``: mutable task-state dictionary
    - ``now_s``: POSIX seconds as ``float``
    """

    if task_state["stop_requested"]:
        return

    if task_state["started_at_s"] is not None:
        elapsed_s = float(now_s) - float(task_state["started_at_s"])
        if elapsed_s >= float(task_state["config"]["max_duration_s"]):
            task_state["stop_requested"] = True
            task_state["stop_reason"] = "max_duration_elapsed"
            enter_phase(task_state, "finished", float(now_s))
            _publish_runtime_state(box, task_state)
            return

    deadline_s = task_state["phase_deadline_s"]
    if deadline_s is not None and float(now_s) < float(deadline_s):
        return

    if task_state["phase"] == "cue":
        enter_phase(
            task_state,
            "response_window",
            float(now_s),
            duration_s=float(task_state["config"]["response_window_s"]),
        )
        _publish_runtime_state(box, task_state)
    elif task_state["phase"] == "response_window":
        task_state["stop_requested"] = True
        task_state["stop_reason"] = "response_window_elapsed"
        append_task_event(task_state, "response_window_elapsed", float(now_s))
        enter_phase(task_state, "finished", float(now_s))
        _publish_runtime_state(box, task_state)


def should_stop(box, task_state: dict) -> bool:
    """Return whether the task should stop.

    Args:
    - ``box``: BehavBox runtime, unused but part of the task contract
    - ``task_state``: mutable task-state dictionary

    Returns:
    - ``should_stop``: ``True`` when the task has reached a stop condition
    """

    del box
    return bool(task_state["stop_requested"])


def stop_task(box, task_state: dict, reason: str) -> None:
    """Stop the tutorial task and record the stop reason.

    Args:
    - ``box``: running BehavBox runtime
    - ``task_state``: mutable task-state dictionary
    - ``reason``: human-readable stop reason string
    """

    now_s = time.time()
    task_state["stopped_at_s"] = now_s
    if task_state["stop_reason"] is None:
        task_state["stop_reason"] = str(reason)
    task_state["stop_requested"] = True
    box.stop_sound()
    append_task_event(task_state, "task_stopped", now_s, reason=str(task_state["stop_reason"]))
    _publish_runtime_state(box, task_state)


def finalize_task(box, task_state: dict) -> dict:
    """Build the final JSON-serializable summary for the tutorial task.

    Args:
    - ``box``: BehavBox runtime, unused but part of the task contract
    - ``task_state``: mutable task-state dictionary

    Returns:
    - ``final_task_state``: JSON-serializable task summary
    """

    del box
    return {
        "protocol_name": PROTOCOL_NAME,
        "phase": task_state["phase"],
        "started_at_s": task_state["started_at_s"],
        "stopped_at_s": task_state["stopped_at_s"],
        "stop_reason": task_state["stop_reason"],
        "response_detected": bool(task_state["response_detected"]),
        "response_timestamp_s": task_state["response_timestamp_s"],
        "response_latency_s": task_state["response_latency_s"],
        "config": dict(task_state["config"]),
    }

