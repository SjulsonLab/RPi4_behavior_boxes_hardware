"""Lifecycle runner for reference sample tasks."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from sample_tasks.common.fsm import append_task_event
from sample_tasks.common.session_artifacts import write_final_task_state, write_task_events


class TaskRunner:
    """Run one task against an explicit BehavBox lifecycle.

    Args:
        box: Prepared BehavBox appliance wrapper.
        task: Task module or object exposing the task API methods.
        task_config: JSON-serializable task configuration mapping.
        clock: Optional zero-argument callable returning POSIX seconds.
    """

    def __init__(self, box, task, task_config: Optional[dict] = None, clock=None, step_hooks: Optional[list] = None):
        self.box = box
        self.task = task
        self.task_config = {} if task_config is None else dict(task_config)
        self.clock = clock or time.time
        self.step_hooks = [] if step_hooks is None else list(step_hooks)
        self.task_state: Optional[dict] = None
        self.runner_events: list[dict] = []
        self.is_prepared = False
        self.is_started = False
        self.is_stopped = False
        self.is_finalized = False
        self.is_closed = False
        self.stop_reason: Optional[str] = None

    def _protocol_name(self) -> str:
        """Return the task protocol name used in runtime state and artifacts.

        Returns:
        - ``protocol_name``: Stable task protocol name string.
        """

        return str(getattr(self.task, "PROTOCOL_NAME", getattr(self.task, "__name__", "task")))

    def prepare(self) -> dict:
        """Prepare the box and task state.

        Returns:
        - ``task_state``: mutable task-state dictionary.
        """

        if self.is_prepared:
            return self.task_state
        self.box.prepare_session()
        self.box.publish_runtime_state("session", protocol_name=self._protocol_name())
        self.task_state = self.task.prepare_task(self.box, self.task_config)
        append_task_event(self.task_state, "runner_prepared", self.clock())
        self.is_prepared = True
        return self.task_state

    def start(self) -> None:
        """Start the prepared session and task."""

        if self.is_started:
            return
        if not self.is_prepared:
            self.prepare()
        self.box.start_session()
        self.box.publish_runtime_state("session", protocol_name=self._protocol_name())
        self.task.start_task(self.box, self.task_state)
        self.runner_events.append({"name": "session_started", "timestamp": float(self.clock())})
        self.is_started = True

    def step(self) -> bool:
        """Run one non-blocking lifecycle iteration.

        Returns:
        - ``continue_running``: ``True`` while the task should continue.
        """

        if self.is_stopped:
            return False
        if not self.is_started:
            self.start()
        try:
            for event in self.box.poll_runtime():
                self.task.handle_event(self.box, self.task_state, event)
            self.task.update_task(self.box, self.task_state, now_s=float(self.clock()))
            for hook in self.step_hooks:
                hook(self)
            if self.is_stopped:
                return False
            if self.task.should_stop(self.box, self.task_state):
                self.stop(reason="completed")
                return False
            return True
        except Exception:
            self.stop(reason="error")
            raise

    def stop(self, reason: str = "manual") -> None:
        """Stop the task and session if still active.

        Args:
            reason: Human-readable stop reason string.
        """

        if self.is_stopped:
            return
        self.stop_reason = str(reason)
        if self.is_started:
            self.task.stop_task(self.box, self.task_state, self.stop_reason)
            self.box.stop_session()
            self.box.publish_runtime_state("session", protocol_name=self._protocol_name())
            self.runner_events.append({"name": "session_stopped", "timestamp": float(self.clock()), "reason": self.stop_reason})
        self.is_stopped = True

    def finalize(self) -> dict:
        """Write standardized artifacts and close runtime resources.

        Returns:
        - ``final_task_state``: JSON-serializable final task-state dictionary.
        """

        if self.is_finalized:
            return self._final_task_state
        if not self.is_stopped and self.is_started:
            self.stop(reason="completed")
        if not self.is_prepared:
            self.prepare()
        final_task_state = self.task.finalize_task(self.box, self.task_state)
        session_dir = Path(self.box.session_info["dir_name"])
        write_task_events(session_dir, list(self.task_state.get("task_events", [])) + self.runner_events)
        if getattr(self.box, "_lifecycle_state", None) == "stopped":
            self.box.finalize_session()
        write_final_task_state(session_dir, final_task_state)
        self.box.close()
        self.is_finalized = True
        self.is_closed = True
        self._final_task_state = final_task_state
        return final_task_state

    def run(self, poll_interval_s: float = 0.01) -> dict:
        """Run the task until its stop criterion fires.

        Args:
            poll_interval_s: Sleep interval between lifecycle steps in seconds.

        Returns:
        - ``final_task_state``: JSON-serializable final task-state dictionary.
        """

        self.prepare()
        self.start()
        while self.step():
            time.sleep(float(poll_interval_s))
        return self.finalize()
