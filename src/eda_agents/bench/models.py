"""Pydantic v2 models for the eda-agents benchmark suite.

These mirror ``bench/schemas/task.json`` and ``bench/schemas/result.json``
so callers can validate at runtime without a JSON-schema engine. The
schema files remain the source of truth for external consumers; tests
in ``tests/test_bench_schemas.py`` keep enums and required keys in sync.

Inspired by ``behavioral-veriloga-eval/schemas`` (no LICENSE upstream;
re-authored from scratch to stay open-source — see
``docs/license_status.md``).
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums (string-valued so they JSON-serialise cleanly)
# ---------------------------------------------------------------------------


class TaskFamily(str, Enum):
    SPEC_TO_TOPOLOGY = "spec-to-topology"
    BUGFIX = "bugfix"
    TB_GENERATION = "tb-generation"
    END_TO_END = "end-to-end"


class TaskDomain(str, Enum):
    CURRENT = "current"
    VOLTAGE = "voltage"
    MIXED = "mixed"
    DIGITAL = "digital"


class TaskHarness(str, Enum):
    ANALOG_ROLES = "analog_roles"
    AUTORESEARCH = "autoresearch"
    DIGITAL_AUTORESEARCH = "digital_autoresearch"
    CALLABLE = "callable"
    DRY_RUN = "dry_run"


class Backend(str, Enum):
    NGSPICE = "ngspice"
    NGSPICE_OSDI = "ngspice-osdi"
    NGSPICE_XSPICE = "ngspice-xspice"
    VERILATOR = "verilator"
    LIBRELANE = "librelane"
    DRY_RUN = "dry-run"


class TaskScoring(str, Enum):
    COMPILE = "compile"
    SIM_RUN = "sim_run"
    AUDIT_PASSED = "audit_passed"
    REGEX_MATCH = "regex_match"
    METRICS_IN_RANGE = "metrics_in_range"


class BenchStatus(str, Enum):
    PASS = "PASS"
    FAIL_COMPILE = "FAIL_COMPILE"
    FAIL_SIM = "FAIL_SIM"
    FAIL_AUDIT = "FAIL_AUDIT"
    FAIL_INFRA = "FAIL_INFRA"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


# Allowed PDK identifiers — keep aligned with ``core/pdk.py`` registry.
_ALLOWED_PDKS: tuple[str, ...] = ("ihp_sg13g2", "gf180mcu")


# ---------------------------------------------------------------------------
# Task / metric models
# ---------------------------------------------------------------------------


class MetricBound(BaseModel):
    """One ``min`` / ``max`` numeric expectation for an audited metric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min: float | None = None
    max: float | None = None
    unit: str = ""

    @model_validator(mode="after")
    def _bound_required(self) -> "MetricBound":
        if self.min is None and self.max is None:
            raise ValueError("MetricBound needs at least one of 'min' / 'max'")
        if (
            self.min is not None
            and self.max is not None
            and self.min > self.max
        ):
            raise ValueError(
                f"MetricBound min {self.min} > max {self.max}, inconsistent"
            )
        return self

    def check(self, value: float) -> tuple[bool, float]:
        """Return ``(passed, margin)`` for a measured value.

        Margin is positive when the metric is in range. Mirrors the
        ``SpecTarget.check`` semantics in :mod:`eda_agents.specs`.
        """
        margin_lo = float("inf") if self.min is None else value - self.min
        margin_hi = float("inf") if self.max is None else self.max - value
        margin = min(margin_lo, margin_hi)
        return margin >= 0.0, margin


class BenchTask(BaseModel):
    """A single benchmark task definition.

    Identical wire format to ``bench/schemas/task.json``. Constructing a
    :class:`BenchTask` validates the document; consumers should treat it
    as immutable (``frozen=True``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)

    id: str
    family: TaskFamily
    category: str
    domain: TaskDomain
    difficulty: str = Field(pattern=r"^(easy|medium|hard)$")
    expected_backend: Backend
    harness: TaskHarness
    pdk: str | None = None
    topology: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    expected_metrics: dict[str, MetricBound] = Field(default_factory=dict)
    scoring: list[TaskScoring]
    weight: float = 1.0
    timeout_s: int = 600
    notes: str = ""

    @field_validator("scoring")
    @classmethod
    def _scoring_nonempty(cls, v: list[TaskScoring]) -> list[TaskScoring]:
        if not v:
            raise ValueError("BenchTask.scoring must list at least one criterion")
        return v

    @field_validator("pdk")
    @classmethod
    def _pdk_known(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in _ALLOWED_PDKS:
            raise ValueError(
                f"unknown pdk {v!r}; allowed: {_ALLOWED_PDKS} or null"
            )
        return v

    @field_validator("weight")
    @classmethod
    def _weight_nonneg(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError(f"weight must be >=0, got {v}")
        return v

    @field_validator("timeout_s")
    @classmethod
    def _timeout_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"timeout_s must be > 0, got {v}")
        return v

    @model_validator(mode="after")
    def _digital_pdk_optional(self) -> "BenchTask":
        # Digital tasks may have pdk=None when the harness picks it up
        # from a LibreLane config; analog tasks must declare a PDK.
        if self.domain is not TaskDomain.DIGITAL and self.pdk is None:
            raise ValueError(
                f"task {self.id!r}: non-digital tasks must declare a pdk"
            )
        return self


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class BenchScores(BaseModel):
    """Per-criterion scores; missing criteria are unscored (omitted)."""

    model_config = ConfigDict(extra="forbid")

    compile: float | None = None
    sim_run: float | None = None
    audit_passed: float | None = None
    regex_match: float | None = None
    metrics_in_range: float | None = None
    weighted_total: float

    @model_validator(mode="after")
    def _bounds(self) -> "BenchScores":
        for name in (
            "compile",
            "sim_run",
            "audit_passed",
            "regex_match",
            "metrics_in_range",
        ):
            v = getattr(self, name)
            if v is not None and not 0.0 <= v <= 1.0:
                raise ValueError(f"score {name}={v} outside [0,1]")
        if not 0.0 <= self.weighted_total <= 1.0:
            raise ValueError(
                f"weighted_total {self.weighted_total} outside [0,1]"
            )
        return self


class BenchResult(BaseModel):
    """One run record. JSON wire format mirrors ``bench/schemas/result.json``."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    task_id: str
    status: BenchStatus
    scores: BenchScores
    harness_used: str
    duration_s: float
    backend_used: str | None = None
    pdk_used: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    metrics: dict[str, float | str | bool | None] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    started: str | None = None
    finished: str | None = None

    @field_validator("duration_s")
    @classmethod
    def _duration_nonneg(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError(f"duration_s must be >=0, got {v}")
        return v

    @property
    def passed(self) -> bool:
        return self.status is BenchStatus.PASS

    def save_json(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return out

    @classmethod
    def load_json(cls, path: str | Path) -> "BenchResult":
        return cls.model_validate(
            json.loads(Path(path).read_text(encoding="utf-8"))
        )


# ---------------------------------------------------------------------------
# Disk loaders
# ---------------------------------------------------------------------------


def _load_doc(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif path.suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(
            f"unsupported task file extension: {path.suffix} (use .yaml or .json)"
        )
    if not isinstance(data, dict):
        raise TypeError(
            f"{path}: expected top-level mapping, got {type(data).__name__}"
        )
    return data


def load_task(path: str | Path) -> BenchTask:
    """Load a single task from YAML or JSON on disk."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"task file not found: {p}")
    return BenchTask.model_validate(_load_doc(p))


def load_tasks_from_dir(
    root: str | Path,
    *,
    family: TaskFamily | str | None = None,
    recursive: bool = True,
) -> list[BenchTask]:
    """Load every ``*.yaml`` / ``*.yml`` / ``*.json`` task under ``root``.

    Parameters
    ----------
    root
        Directory to scan. If a subdirectory matches a family name
        (``spec-to-topology`` etc.), tasks under it are loaded with the
        full path retained for ordering.
    family
        Optional filter that drops any task whose ``family`` field does
        not match. Accepts the enum or its string value.
    recursive
        Set False to scan only the top-level directory.
    """
    root_p = Path(root)
    if not root_p.is_dir():
        raise FileNotFoundError(f"task directory not found: {root_p}")
    pattern_iter = (
        root_p.rglob("*") if recursive else root_p.glob("*")
    )
    fam_str = family.value if isinstance(family, TaskFamily) else family
    out: list[BenchTask] = []
    for p in sorted(pattern_iter):
        if not p.is_file():
            continue
        if p.suffix not in {".yaml", ".yml", ".json"}:
            continue
        task = load_task(p)
        if fam_str is not None and task.family.value != fam_str:
            continue
        out.append(task)
    return out


__all__ = [
    "Backend",
    "BenchResult",
    "BenchScores",
    "BenchStatus",
    "BenchTask",
    "MetricBound",
    "TaskDomain",
    "TaskFamily",
    "TaskHarness",
    "TaskScoring",
    "load_task",
    "load_tasks_from_dir",
]
