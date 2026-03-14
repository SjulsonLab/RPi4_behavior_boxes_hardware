"""Small task protocol used by the sample-task runner."""

from __future__ import annotations

from typing import Protocol


class TaskProtocol(Protocol):
    """Protocol for sample tasks executed by :class:`sample_tasks.common.runner.TaskRunner`.

    Data contracts:
    - ``task_config``: JSON-serializable mapping of task parameters
    - ``task_state``: mutable dictionary containing task-owned runtime state
    - ``event``: runtime event object accepted by ``BehavBox.event_name()``
    - ``now_s``: POSIX seconds as ``float``
    """

    def prepare_task(self, box, task_config: dict) -> dict: ...

    def start_task(self, box, task_state: dict) -> None: ...

    def handle_event(self, box, task_state: dict, event) -> None: ...

    def update_task(self, box, task_state: dict, now_s: float) -> None: ...

    def should_stop(self, box, task_state: dict) -> bool: ...

    def stop_task(self, box, task_state: dict, reason: str) -> None: ...

    def finalize_task(self, box, task_state: dict) -> dict: ...
