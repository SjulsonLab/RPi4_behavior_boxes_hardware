"""Loader for user-authored simple-task Python files."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def load_task_from_file(path: str | Path):
    """Load one user-authored task file and return its runnable task object.

    Args:
    - ``path``: filesystem path to a Python file containing ``TASK``

    Returns:
    - ``task``: runnable object satisfying the current task protocol
    """

    task_path = Path(path).resolve()
    if not task_path.is_file():
        raise FileNotFoundError(task_path)
    spec = importlib.util.spec_from_file_location("user_simple_task", task_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load task file {task_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "TASK"):
        raise AttributeError(f"{task_path} does not define TASK")
    task = module.TASK
    if hasattr(task, "build") and not hasattr(task, "prepare_task"):
        task = task.build()
    return task
