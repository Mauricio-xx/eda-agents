"""Pydantic v2 result models for the EDA bridge.

Mirrors the structure of ``virtuoso-bridge-lite``'s ``models.py`` (status
enum + result with ``.ok`` / ``.save_json``) but reimplemented from scratch
under Apache-2.0 since the upstream repo has no LICENSE file (see
``docs/license_status.md``).

Differences vs. the upstream inspiration:

  - No SKILL-specific ``is_nil`` property — the open stack has no
    ``nil``/``t`` SKILL convention to encode.
  - ``BridgeResult`` covers the generic "did this tool invocation succeed"
    case (DRC, LVS, netlist export, ssh command). ``SimulationResult`` is
    the SPICE-specific subclass (extra ``measurements`` payload).
  - Both models are frozen so Future-completion stamps cannot mutate the
    record after a job finishes — the registry rewrites the JSON instead.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionStatus(str, Enum):
    """Outcome of a single bridge invocation."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    ERROR = "error"
    CANCELLED = "cancelled"


class BridgeResult(BaseModel):
    """Generic result for any bridge-orchestrated tool invocation.

    Use this for DRC / LVS / xschem netlist export / ssh command / etc.
    For SPICE simulations use ``SimulationResult`` which adds a
    ``measurements`` payload.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ExecutionStatus
    tool: str = Field(
        description="Short identifier of the underlying tool, "
        "e.g. 'xschem', 'klayout-drc', 'klayout-lvs', 'ssh'."
    )
    output: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_s: float | None = None
    artifacts: list[str] = Field(
        default_factory=list,
        description="Filesystem paths produced by the invocation (netlist, "
        ".lyrdb, GDS, etc.). Stored as strings for JSON-roundtrip stability.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when the tool reported a clean success."""
        return self.status == ExecutionStatus.SUCCESS

    def save_json(self, path: str | Path, *, indent: int = 2) -> Path:
        """Persist the result as a JSON file. Creates parent dirs."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=indent), encoding="utf-8")
        return p

    @classmethod
    def load_json(cls, path: str | Path) -> "BridgeResult":
        """Inverse of ``save_json``."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)


class SimulationResult(BaseModel):
    """Result of a SPICE simulation orchestrated through the bridge.

    The bridge wraps ``eda_agents.core.spice_runner.SpiceRunner`` and
    summarises ``SpiceResult`` into this Pydantic-friendly shape so the
    JobRegistry can persist and resume runs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ExecutionStatus
    tool: str = "ngspice"
    netlist: str | None = Field(
        default=None,
        description="Path to the .cir / .sp deck that was simulated.",
    )
    measurements: dict[str, float] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_s: float | None = None
    artifacts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS

    def save_json(self, path: str | Path, *, indent: int = 2) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=indent), encoding="utf-8")
        return p

    @classmethod
    def load_json(cls, path: str | Path) -> "SimulationResult":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)


__all__ = [
    "BridgeResult",
    "ExecutionStatus",
    "SimulationResult",
]
