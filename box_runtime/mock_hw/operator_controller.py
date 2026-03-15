"""Operator-facing task launch controller for the mock hardware web server.

Data contracts:
- ``session_tag``: filesystem-safe string used as the run directory basename
- ``max_trials``: positive integer trial cap for ``head_fixed_gonogo``
- ``max_duration_s``: positive float session duration cap in seconds
- ``state()``: JSON-serializable controller state dictionary
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from sample_tasks.head_fixed_gonogo.session_config import build_session_info


_SAFE_SESSION_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class OperatorRunController:
    """Own one operator-launched go/no-go run at a time in a background thread.

    Args:
        output_root: Directory root where operator-launched runs are written.
        box_factory: Callable receiving one session-info mapping and returning a
            BehavBox-compatible runtime object.
        runner_factory: Callable receiving ``box``, ``task``, and
            ``task_config`` and returning a TaskRunner-compatible object.
        clock: Optional zero-argument callable returning POSIX seconds.
    """

    def __init__(
        self,
        *,
        output_root: Path | None = None,
        box_factory: Callable[[dict[str, Any]], Any] | None = None,
        runner_factory: Callable[..., Any] | None = None,
        task_module: Any | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.output_root = Path(output_root or (Path.cwd() / "tmp_operator_runs")).resolve()
        self._box_factory = box_factory
        self._runner_factory = runner_factory
        self._task_module = task_module
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._active_runner: Any | None = None
        self._active_thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "status": "idle",
            "run_active": False,
            "session_tag": None,
            "protocol_name": "head_fixed_gonogo",
            "max_trials": None,
            "max_duration_s": None,
            "output_root": str(self.output_root),
            "active_run_dir": None,
            "started_at_s": None,
            "stopped_at_s": None,
            "stop_reason": None,
            "error_message": None,
            "final_task_state": None,
        }

    def state(self) -> dict[str, Any]:
        """Return a shallow JSON-serializable snapshot of controller state."""

        with self._lock:
            return dict(self._state)

    def start_run(self, *, session_tag: str, max_trials: int, max_duration_s: float) -> dict[str, Any]:
        """Launch one operator-controlled task run.

        Args:
            session_tag: Filesystem-safe run basename string.
            max_trials: Positive integer trial cap.
            max_duration_s: Positive float duration cap in seconds.

        Returns:
            dict[str, Any]: Serializable controller state after launch request.
        """

        clean_tag = str(session_tag).strip()
        if not clean_tag or not _SAFE_SESSION_TAG.match(clean_tag):
            raise ValueError("session_tag must contain only letters, numbers, '.', '-', or '_'")
        if int(max_trials) <= 0:
            raise ValueError("max_trials must be positive")
        if float(max_duration_s) <= 0:
            raise ValueError("max_duration_s must be positive")

        with self._lock:
            if self._state["status"] in {"starting", "running", "stopping"}:
                raise RuntimeError("operator run is already active")

            self.output_root.mkdir(parents=True, exist_ok=True)
            session_info = build_session_info(self.output_root, clean_tag)
            task_config = {
                "max_trials": int(max_trials),
                "max_duration_s": float(max_duration_s),
            }
            box_factory = self._resolve_box_factory()
            runner_factory = self._resolve_runner_factory()
            task_module = self._resolve_task_module()
            self._state["protocol_name"] = str(getattr(task_module, "PROTOCOL_NAME", "head_fixed_gonogo"))
            box = box_factory(session_info)
            runner = runner_factory(
                box=box,
                task=task_module,
                task_config=task_config,
            )
            self._active_runner = runner
            self._active_thread = threading.Thread(
                target=self._run_runner,
                args=(runner,),
                daemon=True,
            )
            self._state.update(
                {
                    "status": "starting",
                    "run_active": True,
                    "session_tag": clean_tag,
                    "max_trials": int(max_trials),
                    "max_duration_s": float(max_duration_s),
                    "active_run_dir": str(session_info["dir_name"]),
                    "started_at_s": float(self._clock()),
                    "stopped_at_s": None,
                    "stop_reason": None,
                    "error_message": None,
                    "final_task_state": None,
                }
            )
            self._active_thread.start()
            return dict(self._state)

    def stop_run(self) -> dict[str, Any]:
        """Request graceful stop/finalization of the active operator run."""

        with self._lock:
            if self._active_runner is None or self._state["status"] not in {"starting", "running"}:
                raise RuntimeError("no active operator run")
            self._state["status"] = "stopping"
            self._state["stop_reason"] = "operator_stop"
            runner = self._active_runner
        runner.stop(reason="operator_stop")
        return self.state()

    def shutdown(self, timeout_s: float = 2.0) -> None:
        """Best-effort cleanup for process shutdown or test teardown."""

        try:
            current = self.state()
            if current["status"] in {"starting", "running"}:
                self.stop_run()
        except RuntimeError:
            pass
        thread = None
        with self._lock:
            thread = self._active_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(float(timeout_s), 0.0))

    def _run_runner(self, runner: Any) -> None:
        """Execute one TaskRunner-compatible object and capture terminal state."""

        with self._lock:
            if self._state["status"] == "starting":
                self._state["status"] = "running"
        try:
            final_task_state = runner.run()
        except Exception as exc:
            with self._lock:
                self._state.update(
                    {
                        "status": "error",
                        "run_active": False,
                        "stopped_at_s": float(self._clock()),
                        "error_message": str(exc),
                        "final_task_state": None,
                    }
                )
                self._active_runner = None
                self._active_thread = None
            return

        with self._lock:
            final_status = "completed"
            if self._state["status"] not in {"stopping", "running", "starting"}:
                final_status = str(self._state["status"])
            self._state.update(
                {
                    "status": final_status,
                    "run_active": False,
                    "stopped_at_s": float(self._clock()),
                    "final_task_state": final_task_state,
                    "error_message": None,
                }
            )
            self._active_runner = None
            self._active_thread = None

    def _resolve_box_factory(self) -> Callable[[dict[str, Any]], Any]:
        if self._box_factory is None:
            from box_runtime.behavior.behavbox import BehavBox

            self._box_factory = BehavBox
        return self._box_factory

    def _resolve_runner_factory(self) -> Callable[..., Any]:
        if self._runner_factory is None:
            from sample_tasks.common.runner import TaskRunner

            self._runner_factory = TaskRunner
        return self._runner_factory

    def _resolve_task_module(self) -> Any:
        if self._task_module is None:
            from sample_tasks.head_fixed_gonogo import task as gonogo_task

            self._task_module = gonogo_task
        return self._task_module
