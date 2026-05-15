"""Hardware stress task for exercising head-fixed outputs without lick input.

Data contracts:
- ``task_config``: ``dict[str, object]`` with scalar JSON-serializable task
  parameters such as durations, reward cadence, and reward outputs.
- ``task_state``: mutable ``dict[str, object]`` storing FSM phase, counters,
  pending reward outputs, and JSON-serializable event history.
- ``event``: runtime event object accepted by ``BehavBox.event_name()`` and
  ``BehavBox.event_timestamp()``. Input events are logged but do not affect
  task progression.
- ``finalize_task`` returns ``dict[str, object]`` with scalar counters and
  per-trial summaries for artifact writing.
"""

from __future__ import annotations

import time

from sample_tasks.common.fsm import append_task_event, enter_phase


PROTOCOL_NAME = "head_fixed_hardware_stress"


# User-facing task helpers.
def _merged_config(task_config: dict) -> dict:
    """Merge validated defaults with runtime overrides.

    Data contracts:
    - ``task_config``: ``dict[str, object]`` of JSON-serializable overrides.
    - Returns ``dict[str, object]`` with normalized reward output ordering and
      cadence values.
    """

    defaults = {
        "nogo_cue_name": "004-white-noise-44-1-16bit.wav",
        "nogo_cue_duration_s": 0.5,
        "nogo_grating_name": "nogo_grating",
        "cue_side": "both",
        "iti_s": 1.0,
        "response_window_s": 1.0,
        "cleanup_s": 0.25,
        "max_trials": 20,
        "max_duration_s": 600.0,
        "reward_every_n_trials": 5,
        "reward_outputs": ["reward_left", "reward_right", "reward_center", "reward_4"],
        "reward_size_ul": 50.0,
        "reward_gap_s": 0.05,
    }
    merged = dict(defaults)
    merged.update(task_config)
    merged["reward_outputs"] = [str(name) for name in list(merged["reward_outputs"])]
    merged["reward_every_n_trials"] = max(1, int(merged["reward_every_n_trials"]))
    return merged


def _publish_runtime_state(box, task_state: dict) -> None:
    """Publish the current hardware-stress task runtime state.

    Data contracts:
    - ``box``: BehavBox-like runtime exposing ``publish_runtime_state``.
    - ``task_state``: mutable task-state mapping from ``prepare_task``.
    - Returns ``None``.
    """

    box.publish_runtime_state(
        "task",
        protocol_name=PROTOCOL_NAME,
        phase=str(task_state["phase"]),
        trial_index=None if int(task_state["trial_index"]) < 0 else int(task_state["trial_index"]),
        current_trial_type="nogo",
        completed_trials=int(task_state["counters"]["completed_trials"]),
        correct_rejects=int(task_state["counters"]["correct_rejects"]),
        reward_delivery_count=int(task_state["reward_delivery_count"]),
        pending_reward_outputs=list(task_state["pending_reward_outputs"]),
        stop_reason=task_state["stop_reason"],
        stimulus_active=(task_state["phase"] == "stimulus"),
    )


def _visual_stimulus_enabled(box) -> bool:
    """Return whether visual stimulus output is enabled for this task run.

    Data contracts:
    - ``box``: BehavBox-like runtime exposing ``session_info``.
    - Returns ``bool``.
    """

    session_info = getattr(box, "session_info", {})
    if not isinstance(session_info, dict):
        return False
    return bool(session_info.get("visual_stimulus", False))


def _clear_visual_stimulus(box) -> None:
    """Return the visual display to the neutral gray background if possible.

    Data contracts:
    - ``box``: BehavBox-like runtime that may expose either a generic
      ``display_gray(gray_level_u8)`` helper or the legacy
      ``visualstim.myscreen.display_greyscale(...)`` compatibility path.
    - Returns ``None`` and silently does nothing when visual output is absent.
    """

    if not _visual_stimulus_enabled(box):
        return
    gray_level = 127
    session_info = getattr(box, "session_info", {})
    if isinstance(session_info, dict):
        gray_level = int(session_info.get("gray_level", 127))
    if hasattr(box, "display_gray"):
        box.display_gray(gray_level)
        return
    visualstim = getattr(box, "visualstim", None)
    myscreen = getattr(visualstim, "myscreen", None)
    if myscreen is not None and hasattr(myscreen, "display_greyscale"):
        myscreen.display_greyscale(gray_level, blocking=False)


def _current_trial_number(task_state: dict) -> int:
    """Return the current 1-based trial number.

    Data contracts:
    - ``task_state``: mutable task-state mapping with integer ``trial_index``.
    - Returns ``int`` trial number in 1-based indexing.
    """

    return int(task_state["trial_index"]) + 1


def _record_trial_outcome(task_state: dict, *, now_s: float, outcome: str) -> None:
    """Append one per-trial outcome entry if the trial has not been recorded.

    Data contracts:
    - ``task_state``: mutable task-state mapping with ``trial_outcomes`` list.
    - ``now_s``: POSIX seconds as ``float``.
    - ``outcome``: ``str`` outcome label for the current trial.
    - Returns ``None``.
    """

    trial_number = _current_trial_number(task_state)
    entries = task_state.setdefault("trial_outcomes", [])
    if entries and int(entries[-1]["trial_number"]) == trial_number:
        return
    entries.append(
        {
            "trial_number": trial_number,
            "trial_type": "nogo",
            "outcome": str(outcome),
            "timestamp": float(now_s),
        }
    )
    append_task_event(
        task_state,
        "trial_outcome",
        float(now_s),
        trial_number=trial_number,
        trial_type="nogo",
        outcome=str(outcome),
    )


def _begin_trial(box, task_state: dict, now_s: float) -> None:
    """Start one new no-go stress trial with cue and optional visual stimulus.

    Data contracts:
    - ``box``: BehavBox-like runtime exposing cue and visual APIs.
    - ``task_state``: mutable task-state mapping.
    - ``now_s``: POSIX seconds as ``float``.
    - Returns ``None``.
    """

    config = task_state["config"]
    task_state["trial_index"] += 1
    task_state["current_trial_type"] = "nogo"
    trial_number = _current_trial_number(task_state)
    append_task_event(task_state, "trial_started", now_s, trial_number=trial_number, trial_type="nogo")
    enter_phase(
        task_state,
        "stimulus",
        now_s,
        duration_s=float(config["nogo_cue_duration_s"]),
        trial_number=trial_number,
        trial_type="nogo",
    )
    box.play_sound(
        str(config["nogo_cue_name"]),
        side=str(config["cue_side"]),
        duration_s=float(config["nogo_cue_duration_s"]),
    )
    if _visual_stimulus_enabled(box):
        box.show_grating(str(config["nogo_grating_name"]))
    _publish_runtime_state(box, task_state)


def _should_reward_trial(task_state: dict) -> bool:
    """Return whether the current trial should actuate the reward solenoids.

    Data contracts:
    - ``task_state``: mutable task-state mapping.
    - Returns ``bool`` based on the configured reward cadence.
    """

    reward_every_n_trials = int(task_state["config"]["reward_every_n_trials"])
    return _current_trial_number(task_state) % reward_every_n_trials == 0


def _complete_trial(box, task_state: dict, now_s: float) -> None:
    """Finalize one trial and advance into inter-trial cleanup.

    Data contracts:
    - ``box``: BehavBox-like runtime exposing ``publish_runtime_state``.
    - ``task_state``: mutable task-state mapping.
    - ``now_s``: POSIX seconds as ``float``.
    - Returns ``None``.
    """

    task_state["counters"]["completed_trials"] += 1
    enter_phase(
        task_state,
        "inter_trial_cleanup",
        now_s,
        duration_s=float(task_state["config"]["cleanup_s"]),
        trial_number=_current_trial_number(task_state),
    )
    _publish_runtime_state(box, task_state)


def _queue_serial_rewards(box, task_state: dict, now_s: float) -> None:
    """Queue serial reward delivery for the current reward trial.

    Data contracts:
    - ``box``: BehavBox-like runtime exposing ``publish_runtime_state``.
    - ``task_state``: mutable task-state mapping.
    - ``now_s``: POSIX seconds as ``float``.
    - Returns ``None``.
    """

    trial_number = _current_trial_number(task_state)
    task_state["pending_reward_outputs"] = list(task_state["config"]["reward_outputs"])
    task_state["rewarded_trial_numbers"].append(trial_number)
    append_task_event(
        task_state,
        "serial_reward_started",
        now_s,
        trial_number=trial_number,
        reward_outputs=list(task_state["pending_reward_outputs"]),
    )
    enter_phase(
        task_state,
        "reward_serial",
        now_s,
        duration_s=0.0,
        trial_number=trial_number,
        remaining_rewards=len(task_state["pending_reward_outputs"]),
    )
    _publish_runtime_state(box, task_state)


def _deliver_next_reward(box, task_state: dict, now_s: float) -> None:
    """Deliver the next queued reward output and keep the sequence serialized.

    Data contracts:
    - ``box``: BehavBox-like runtime exposing ``deliver_reward``.
    - ``task_state``: mutable task-state mapping with queued reward outputs.
    - ``now_s``: POSIX seconds as ``float``.
    - Returns ``None``.
    """

    pending = list(task_state["pending_reward_outputs"])
    if not pending:
        _complete_trial(box, task_state, now_s)
        return

    output_name = str(pending.pop(0))
    task_state["pending_reward_outputs"] = pending
    reward_size_ul = float(task_state["config"]["reward_size_ul"])
    box.deliver_reward(output_name=output_name, reward_size_ul=reward_size_ul)
    task_state["reward_delivery_count"] += 1
    task_state["delivered_reward_outputs"].append(output_name)
    append_task_event(
        task_state,
        "serial_reward_delivered",
        now_s,
        trial_number=_current_trial_number(task_state),
        output_name=output_name,
        reward_size_ul=reward_size_ul,
        remaining_rewards=len(task_state["pending_reward_outputs"]),
    )
    if task_state["pending_reward_outputs"]:
        enter_phase(
            task_state,
            "reward_serial",
            now_s,
            duration_s=float(task_state["config"]["reward_gap_s"]),
            trial_number=_current_trial_number(task_state),
            remaining_rewards=len(task_state["pending_reward_outputs"]),
        )
        _publish_runtime_state(box, task_state)
        return
    _complete_trial(box, task_state, now_s)


# Core task API.
def prepare_task(box, task_config: dict) -> dict:
    """Prepare mutable task state for the hardware stress task.

    Data contracts:
    - ``box``: prepared BehavBox runtime exposing noise registration and
      runtime-state publishing.
    - ``task_config``: ``dict[str, object]`` of JSON-serializable overrides.
    - Returns mutable ``dict[str, object]`` task state.
    """

    config = _merged_config(task_config)
    box.load_sound(str(config["nogo_cue_name"]))
    task_state = {
        "config": config,
        "phase": "idle",
        "phase_started_s": None,
        "phase_deadline_s": None,
        "started_at_s": None,
        "stopped_at_s": None,
        "trial_index": -1,
        "current_trial_type": None,
        "task_events": [],
        "trial_outcomes": [],
        "stop_requested": False,
        "stop_reason": None,
        "pending_reward_outputs": [],
        "delivered_reward_outputs": [],
        "rewarded_trial_numbers": [],
        "reward_delivery_count": 0,
        "counters": {
            "completed_trials": 0,
            "correct_rejects": 0,
        },
    }
    append_task_event(task_state, "task_prepared", time.time())
    _publish_runtime_state(box, task_state)
    return task_state


def start_task(box, task_state: dict) -> None:
    """Start task execution by entering the initial inter-trial interval.

    Data contracts:
    - ``box``: running BehavBox runtime.
    - ``task_state``: mutable task-state mapping returned by ``prepare_task``.
    - Returns ``None``.
    """

    now_s = time.time()
    task_state["started_at_s"] = now_s
    append_task_event(task_state, "task_started", now_s)
    box.publish_runtime_state("session", protocol_name=PROTOCOL_NAME)
    enter_phase(task_state, "iti", now_s, duration_s=float(task_state["config"]["iti_s"]))
    _publish_runtime_state(box, task_state)


def handle_event(box, task_state: dict, event) -> None:
    """Record input events without using them to change task progression.

    Data contracts:
    - ``box``: BehavBox runtime exposing ``event_name`` and ``event_timestamp``.
    - ``task_state``: mutable task-state mapping.
    - ``event``: runtime event object.
    - Returns ``None``.
    """

    name = box.event_name(event)
    timestamp = box.event_timestamp(event) or time.time()
    append_task_event(task_state, "input_event", timestamp, event_name=name, phase=task_state["phase"])


def update_task(box, task_state: dict, now_s: float) -> None:
    """Advance the stress-task finite-state machine based on elapsed time.

    Data contracts:
    - ``box``: running BehavBox runtime.
    - ``task_state``: mutable task-state mapping.
    - ``now_s``: POSIX seconds as ``float``.
    - Returns ``None``.
    """

    phase = str(task_state["phase"])
    deadline_s = task_state["phase_deadline_s"]
    if phase == "idle":
        return
    if deadline_s is not None and float(now_s) < float(deadline_s):
        return

    if phase == "iti":
        _begin_trial(box, task_state, float(now_s))
        return
    if phase == "stimulus":
        _clear_visual_stimulus(box)
        enter_phase(
            task_state,
            "response_window",
            float(now_s),
            duration_s=float(task_state["config"]["response_window_s"]),
            trial_number=_current_trial_number(task_state),
        )
        _publish_runtime_state(box, task_state)
        return
    if phase == "response_window":
        task_state["counters"]["correct_rejects"] += 1
        _record_trial_outcome(task_state, now_s=float(now_s), outcome="correct_reject")
        if _should_reward_trial(task_state):
            _queue_serial_rewards(box, task_state, float(now_s))
            return
        _complete_trial(box, task_state, float(now_s))
        return
    if phase == "reward_serial":
        _deliver_next_reward(box, task_state, float(now_s))
        return
    if phase == "inter_trial_cleanup":
        enter_phase(task_state, "iti", float(now_s), duration_s=float(task_state["config"]["iti_s"]))
        _publish_runtime_state(box, task_state)


def should_stop(box, task_state: dict) -> bool:
    """Return whether the task should stop on the next runner iteration.

    Data contracts:
    - ``box``: running BehavBox runtime, unused beyond the task API contract.
    - ``task_state``: mutable task-state mapping.
    - Returns ``bool``.
    """

    del box
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
    """Stop the task and record the stop reason without mutating config.

    Data contracts:
    - ``box``: running or stopped BehavBox runtime.
    - ``task_state``: mutable task-state mapping.
    - ``reason``: human-readable ``str``.
    - Returns ``None``.
    """

    now_s = time.time()
    task_state["stop_requested"] = True
    task_state["stop_reason"] = str(reason)
    task_state["stopped_at_s"] = now_s
    _clear_visual_stimulus(box)
    append_task_event(task_state, "task_stopped", now_s, reason=str(reason), phase=task_state["phase"])
    _publish_runtime_state(box, task_state)


def finalize_task(box, task_state: dict) -> dict:
    """Build the final JSON-serializable task summary.

    Data contracts:
    - ``box``: BehavBox runtime exposing ``publish_runtime_state``.
    - ``task_state``: mutable task-state mapping.
    - Returns ``dict[str, object]`` with scalar counters and per-trial summaries.
    """

    now_s = time.time()
    append_task_event(task_state, "task_finalized", now_s, phase=task_state["phase"])
    box.publish_runtime_state("task", phase=task_state["phase"], stimulus_active=False)
    return {
        "protocol_name": PROTOCOL_NAME,
        "phase": task_state["phase"],
        "current_trial_type": task_state["current_trial_type"],
        "counters": dict(task_state["counters"]),
        "trial_outcomes": list(task_state["trial_outcomes"]),
        "rewarded_trial_numbers": list(task_state["rewarded_trial_numbers"]),
        "delivered_reward_outputs": list(task_state["delivered_reward_outputs"]),
        "reward_delivery_count": int(task_state["reward_delivery_count"]),
        "stop_reason": task_state["stop_reason"],
        "started_at_s": task_state["started_at_s"],
        "stopped_at_s": task_state["stopped_at_s"],
        "completed_trials": int(task_state["counters"]["completed_trials"]),
        "config": dict(task_state["config"]),
    }
