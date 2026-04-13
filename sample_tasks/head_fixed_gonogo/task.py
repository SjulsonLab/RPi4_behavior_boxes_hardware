"""Reference head-fixed go/no-go task for lifecycle and runtime validation."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from sample_tasks.common.fsm import append_task_event, enter_phase
from sample_tasks.head_fixed_gonogo.plot_state import build_plot_payload

PROTOCOL_NAME = "head_fixed_gonogo"


def _publish_runtime_state(box, task_state: dict) -> None:
    """Publish current task runtime state for live browser consumers.

    Args:
    - ``box``: BehavBox runtime exposing ``publish_runtime_state``.
    - ``task_state``: Mutable task-state dictionary.
    """

    trial_index = int(task_state["trial_index"])
    box.publish_runtime_state(
        "task",
        protocol_name=PROTOCOL_NAME,
        phase=task_state["phase"],
        trial_index=None if trial_index < 0 else trial_index,
        trial_type=task_state["current_trial_type"],
        completed_trials=int(task_state["counters"]["completed_trials"]),
        max_trials=task_state["config"].get("max_trials"),
        stimulus_active=(task_state["phase"] == "stimulus"),
    )


def _load_defaults() -> dict:
    """Load the default task configuration from the tracked JSON file.

    Returns:
    - ``defaults``: JSON configuration mapping for the task.
    """

    defaults_path = Path(__file__).with_name("defaults.json")
    return json.loads(defaults_path.read_text(encoding="utf-8"))


def _merged_config(task_config: dict) -> dict:
    """Merge tracked defaults with runtime overrides.

    Args:
    - ``task_config``: JSON-serializable runtime overrides.

    Returns:
    - ``config``: merged configuration dictionary.
    """

    merged = _load_defaults()
    merged.update(task_config)
    return merged


def _next_trial_type(task_state: dict) -> str:
    """Choose the next trial type using sequence override or RNG fallback.

    Args:
    - ``task_state``: mutable task-state dictionary.

    Returns:
    - ``trial_type``: either ``"go"`` or ``"nogo"``.
    """

    config = task_state["config"]
    sequence = list(config.get("trial_sequence", []))
    index = int(task_state["trial_index"])
    if index < len(sequence):
        return str(sequence[index]).lower()
    rng = task_state["rng"]
    return "go" if rng.random() < float(config["go_probability"]) else "nogo"


def _begin_trial(box, task_state: dict, now_s: float) -> None:
    """Start one new trial and play the corresponding audio cue.

    Args:
    - ``box``: prepared BehavBox runtime.
    - ``task_state``: mutable task-state dictionary.
    - ``now_s``: POSIX seconds as ``float``.
    """

    task_state["trial_index"] += 1
    task_state["response_recorded"] = False
    trial_type = _next_trial_type(task_state)
    task_state["current_trial_type"] = trial_type
    cue_name = task_state["config"]["go_cue_name"] if trial_type == "go" else task_state["config"]["nogo_cue_name"]
    grating_name = (
        task_state["config"]["go_grating_name"]
        if trial_type == "go"
        else task_state["config"]["nogo_grating_name"]
    )
    append_task_event(task_state, "trial_started", now_s, trial_index=int(task_state["trial_index"]), trial_type=trial_type)
    enter_phase(
        task_state,
        "stimulus",
        now_s,
        duration_s=float(task_state["config"][f"{trial_type}_cue_duration_s"]),
        trial_type=trial_type,
    )
    box.play_sound(cue_name, side=str(task_state["config"]["cue_side"]))
    if _visual_stimulus_enabled(box):
        box.show_grating(str(grating_name))
    _publish_runtime_state(box, task_state)


def _visual_stimulus_enabled(box) -> bool:
    """Return whether visual stimulus output is enabled for this task run.

    Args:
    - ``box``: BehavBox-like runtime object exposing ``session_info``.

    Returns:
    - ``enabled``: True when session configuration enables visual stimulus.
    """

    session_info = getattr(box, "session_info", {})
    if not isinstance(session_info, dict):
        return False
    return bool(session_info.get("visual_stimulus", False))


def _record_trial_outcome(task_state: dict, *, now_s: float, outcome: str, trial_type: str) -> None:
    """Append one per-trial outcome entry once."""

    trial_index = int(task_state["trial_index"])
    entries = task_state.setdefault("trial_outcomes", [])
    if entries and int(entries[-1]["trial_index"]) == trial_index:
        return
    entries.append(
        {
            "trial_index": trial_index,
            "trial_type": str(trial_type),
            "outcome": str(outcome),
            "timestamp": float(now_s),
        }
    )
    append_task_event(
        task_state,
        "trial_outcome",
        float(now_s),
        trial_index=trial_index,
        trial_type=str(trial_type),
        outcome=str(outcome),
    )


def prepare_task(box, task_config: dict) -> dict:
    """Prepare serializable mutable state for the go/no-go task.

    Args:
    - ``box``: prepared BehavBox runtime.
    - ``task_config``: JSON-serializable runtime overrides.

    Returns:
    - ``task_state``: mutable state dictionary used by task callbacks.
    """

    config = _merged_config(task_config)
    box.register_noise_cue(config["go_cue_name"], duration_s=float(config["go_cue_duration_s"]), seed=1)
    box.register_noise_cue(config["nogo_cue_name"], duration_s=float(config["nogo_cue_duration_s"]), seed=2)
    task_state = {
        "config": config,
        "phase": "idle",
        "phase_started_s": None,
        "phase_deadline_s": None,
        "started_at_s": None,
        "stopped_at_s": None,
        "trial_index": -1,
        "current_trial_type": None,
        "response_recorded": False,
        "adaptive_params": {},
        "task_events": [],
        "trial_outcomes": [],
        "stop_requested": False,
        "stop_reason": None,
        "fake_mouse": {
            "enabled": bool(config.get("fake_mouse_enabled", False)),
            "seed": config.get("fake_mouse_seed"),
        },
        "rng": random.Random(int(config["rng_seed"])),
        "counters": {
            "hits": 0,
            "misses": 0,
            "false_alarms": 0,
            "correct_rejects": 0,
            "completed_trials": 0,
        },
    }
    append_task_event(task_state, "task_prepared", time.time())
    _publish_runtime_state(box, task_state)
    box.publish_runtime_state("plot", **build_plot_payload(task_state))
    return task_state


def start_task(box, task_state: dict) -> None:
    """Start task execution by entering the initial ITI phase.

    Args:
    - ``box``: running BehavBox runtime.
    - ``task_state``: mutable task-state dictionary.
    """

    now_s = time.time()
    task_state["started_at_s"] = now_s
    append_task_event(task_state, "task_started", now_s)
    box.publish_runtime_state("session", protocol_name=PROTOCOL_NAME)
    enter_phase(task_state, "iti", now_s, duration_s=float(task_state["config"]["iti_s"]))
    _publish_runtime_state(box, task_state)


def handle_event(box, task_state: dict, event) -> None:
    """Handle one runtime event delivered by the box.

    Args:
    - ``box``: running BehavBox runtime.
    - ``task_state``: mutable task-state dictionary.
    - ``event``: runtime event object accepted by ``BehavBox.event_name()``.
    """

    name = box.event_name(event)
    timestamp = box.event_timestamp(event) or time.time()
    append_task_event(task_state, "input_event", timestamp, event_name=name, phase=task_state["phase"])
    if task_state["phase"] != "response_window":
        return
    if name != str(task_state["config"]["response_event"]):
        return
    if task_state["response_recorded"]:
        return
    task_state["response_recorded"] = True

    if task_state["current_trial_type"] == "go":
        task_state["counters"]["hits"] += 1
        _record_trial_outcome(task_state, now_s=timestamp, outcome="hit", trial_type=task_state["current_trial_type"])
        box.deliver_reward(
            output_name=str(task_state["config"]["reward_output"]),
            reward_size_ul=float(task_state["config"]["reward_size_ul"]),
        )
        enter_phase(
            task_state,
            "reward",
            timestamp,
            duration_s=float(task_state["config"]["reward_phase_s"]),
            trial_type=task_state["current_trial_type"],
        )
        _publish_runtime_state(box, task_state)
    else:
        task_state["counters"]["false_alarms"] += 1
        _record_trial_outcome(task_state, now_s=timestamp, outcome="false_alarm", trial_type=task_state["current_trial_type"])
        enter_phase(
            task_state,
            "timeout",
            timestamp,
            duration_s=float(task_state["config"]["timeout_s"]),
            trial_type=task_state["current_trial_type"],
        )
        _publish_runtime_state(box, task_state)


def update_task(box, task_state: dict, now_s: float) -> None:
    """Advance the FSM based on the current wall-clock time.

    Args:
    - ``box``: running BehavBox runtime.
    - ``task_state``: mutable task-state dictionary.
    - ``now_s``: POSIX seconds as ``float``.
    """

    phase = task_state["phase"]
    deadline_s = task_state["phase_deadline_s"]
    if phase == "idle":
        return
    if deadline_s is not None and float(now_s) < float(deadline_s):
        return

    if phase == "iti":
        _begin_trial(box, task_state, float(now_s))
        return
    if phase == "stimulus":
        enter_phase(task_state, "response_window", float(now_s), duration_s=float(task_state["config"]["response_window_s"]))
        _publish_runtime_state(box, task_state)
        return
    if phase == "response_window":
        if task_state["current_trial_type"] == "go":
            task_state["counters"]["misses"] += 1
            _record_trial_outcome(task_state, now_s=float(now_s), outcome="miss", trial_type=task_state["current_trial_type"])
        else:
            task_state["counters"]["correct_rejects"] += 1
            _record_trial_outcome(task_state, now_s=float(now_s), outcome="correct_reject", trial_type=task_state["current_trial_type"])
        task_state["counters"]["completed_trials"] += 1
        enter_phase(task_state, "inter_trial_cleanup", float(now_s), duration_s=float(task_state["config"]["cleanup_s"]))
        _publish_runtime_state(box, task_state)
        return
    if phase in {"reward", "timeout"}:
        task_state["counters"]["completed_trials"] += 1
        enter_phase(task_state, "inter_trial_cleanup", float(now_s), duration_s=float(task_state["config"]["cleanup_s"]))
        _publish_runtime_state(box, task_state)
        return
    if phase == "inter_trial_cleanup":
        enter_phase(task_state, "iti", float(now_s), duration_s=float(task_state["config"]["iti_s"]))
        _publish_runtime_state(box, task_state)


def should_stop(box, task_state: dict) -> bool:
    """Return whether the task should stop on the next runner iteration.

    Args:
    - ``box``: running BehavBox runtime.
    - ``task_state``: mutable task-state dictionary.

    Returns:
    - ``should_stop``: boolean stop decision.
    """

    if bool(task_state["stop_requested"]):
        return True
    max_trials = task_state["config"].get("max_trials")
    if max_trials is not None and int(task_state["counters"]["completed_trials"]) >= int(max_trials):
        return True
    max_duration_s = task_state["config"].get("max_duration_s")
    if max_duration_s is not None and task_state["started_at_s"] is not None:
        if float(time.time()) - float(task_state["started_at_s"]) >= float(max_duration_s):
            return True
    return False


def stop_task(box, task_state: dict, reason: str) -> None:
    """Record a stop request without mutating session configuration.

    Args:
    - ``box``: running or stopped BehavBox runtime.
    - ``task_state``: mutable task-state dictionary.
    - ``reason``: human-readable stop reason string.
    """

    now_s = time.time()
    task_state["stop_requested"] = True
    task_state["stop_reason"] = str(reason)
    task_state["stopped_at_s"] = now_s
    append_task_event(task_state, "task_stopped", now_s, reason=str(reason), phase=task_state["phase"])
    _publish_runtime_state(box, task_state)
    box.publish_runtime_state("plot", **build_plot_payload(task_state))


def finalize_task(box, task_state: dict) -> dict:
    """Build the final serializable task-state snapshot.

    Args:
    - ``box``: BehavBox runtime used by the task.
    - ``task_state``: mutable task-state dictionary.

    Returns:
    - ``final_task_state``: JSON-serializable dictionary.
    """

    now_s = time.time()
    append_task_event(task_state, "task_finalized", now_s, phase=task_state["phase"])
    box.publish_runtime_state("task", phase=task_state["phase"], stimulus_active=False)
    box.publish_runtime_state("plot", **build_plot_payload(task_state))
    return {
        "protocol_name": PROTOCOL_NAME,
        "phase": task_state["phase"],
        "current_trial_type": task_state["current_trial_type"],
        "counters": dict(task_state["counters"]),
        "adaptive_params": dict(task_state["adaptive_params"]),
        "trial_outcomes": list(task_state["trial_outcomes"]),
        "fake_mouse": dict(task_state["fake_mouse"]),
        "stop_reason": task_state["stop_reason"],
        "started_at_s": task_state["started_at_s"],
        "stopped_at_s": task_state["stopped_at_s"],
        "completed_trials": int(task_state["counters"]["completed_trials"]),
    }
