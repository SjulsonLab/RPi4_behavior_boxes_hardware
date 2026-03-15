"""Seeded fake-mouse support for head-fixed go/no-go runs.

Data contracts:
- ``seed``: integer random seed controlling deterministic behavior
- ``runner``: TaskRunner-compatible object exposing ``box`` and ``task_state``
- input injection always goes through ``MockInputInjector``
"""

from __future__ import annotations

import random
from typing import Callable

from sample_tasks.common.mock_inputs import MockInputInjector


class GoNoGoFakeMouse:
    """Seeded fake mouse that injects center licks during response windows."""

    def __init__(self, seed: int, injector: MockInputInjector | None = None) -> None:
        self.seed = int(seed)
        self._rng = random.Random(self.seed)
        self._injector = injector or MockInputInjector()
        self._current_trial_key: tuple[int, str] | None = None
        self._should_respond = False
        self._response_deadline_s: float | None = None
        self._fired = False

    def step(self, runner) -> None:
        """Advance fake-mouse state against one active task loop iteration."""

        task_state = runner.task_state or {}
        if not bool(task_state.get("config", {}).get("fake_mouse_enabled", False)):
            return

        phase = str(task_state.get("phase", "idle"))
        if phase != "response_window":
            self._current_trial_key = None
            self._response_deadline_s = None
            self._fired = False
            return

        trial_index = int(task_state.get("trial_index", -1))
        trial_type = str(task_state.get("current_trial_type") or "unknown")
        trial_key = (trial_index, trial_type)
        now_s = float(runner.clock())
        if trial_key != self._current_trial_key:
            self._current_trial_key = trial_key
            self._fired = False
            self._should_respond = self._choose_response(trial_index=trial_index, trial_type=trial_type)
            self._response_deadline_s = now_s + self._rng.uniform(0.01, 0.04)

        if self._fired or not self._should_respond:
            return
        if self._response_deadline_s is not None and now_s < self._response_deadline_s:
            return

        self._injector.pulse("lick_3", duration_ms=10)
        self._fired = True

    def _choose_response(self, *, trial_index: int, trial_type: str) -> bool:
        del trial_index
        if trial_type == "go":
            return self._rng.random() < 0.9
        if trial_type == "nogo":
            return self._rng.random() < 0.2
        return False


def build_fake_mouse_step_hook(seed: int) -> Callable[[object], None]:
    """Build one TaskRunner step hook wrapping a seeded fake mouse."""

    fake_mouse = GoNoGoFakeMouse(seed=int(seed))
    return fake_mouse.step
