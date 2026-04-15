"""Benchmark suite package for eda-agents (Sesión 9).

Public surface:

* :class:`BenchTask` / :class:`BenchResult` — Pydantic v2 models that
  mirror ``bench/schemas/{task,result}.json``.
* :func:`load_task` / :func:`load_tasks_from_dir` — disk loaders.
* :class:`TaskFamily`, :class:`TaskDomain`, :class:`TaskHarness`,
  :class:`Backend`, :class:`BenchStatus` — string enums for IDE help and
  static checks.

Schema files live under ``bench/schemas/`` at the repo root; the
Pydantic models duplicate the constraints so callers can validate
without dragging in a JSON-schema runtime dependency. Tests verify the
two stay in sync.
"""

from eda_agents.bench.models import (
    Backend,
    BenchResult,
    BenchStatus,
    BenchTask,
    TaskDomain,
    TaskFamily,
    TaskHarness,
    load_task,
    load_tasks_from_dir,
)

__all__ = [
    "Backend",
    "BenchResult",
    "BenchStatus",
    "BenchTask",
    "TaskDomain",
    "TaskFamily",
    "TaskHarness",
    "load_task",
    "load_tasks_from_dir",
]
