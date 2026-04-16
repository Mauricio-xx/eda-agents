"""Pydantic v2 models for analog block spec YAML.

The format is a deliberately small open-source subset of the analog-
agents spec shape (block / process / supply / specs / corners). It is
meant to travel with a ``CircuitTopology`` through the
Librarian -> Architect -> Designer -> Verifier DAG so each role can
read the same canonical object instead of rederiving spec parsing.

Example::

    block: miller_ota
    process: ihp_sg13g2
    supply:
      vdd: 1.2
      vss: 0.0
    specs:
      dc_gain: {min: 60, unit: dB}
      gbw: {min: 10e6, unit: Hz}
      phase_margin: {min: 60, unit: deg}
      power: {max: 1.0, unit: mW}
    corners: [TT_27, FF_m40, SS_125]

Loading::

    from eda_agents.specs import load_spec
    spec = load_spec("block.yaml")
    spec.targets["dc_gain"].min  # 60.0
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SpecTarget(BaseModel):
    """A single measurable spec with a minimum or maximum target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min: float | None = None
    max: float | None = None
    unit: str = ""
    description: str = ""

    @model_validator(mode="after")
    def _one_bound_required(self) -> "SpecTarget":
        if self.min is None and self.max is None:
            raise ValueError("SpecTarget needs at least one of 'min' or 'max'")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(
                f"SpecTarget has min {self.min} > max {self.max}, inconsistent"
            )
        return self

    @property
    def is_min_spec(self) -> bool:
        """True when the target is a lower bound (greater is better)."""
        return self.min is not None and self.max is None

    @property
    def is_max_spec(self) -> bool:
        """True when the target is an upper bound (smaller is better)."""
        return self.max is not None and self.min is None

    def check(self, value: float) -> tuple[bool, float]:
        """Return ``(passed, margin)`` for a measured value.

        ``margin`` is positive when the spec passes. For a min-spec it
        is ``value - min``; for a max-spec it is ``max - value``; for a
        ranged spec it is the distance to the nearest violated bound.
        """
        margin_lo = float("inf") if self.min is None else value - self.min
        margin_hi = float("inf") if self.max is None else self.max - value
        margin = min(margin_lo, margin_hi)
        return margin >= 0.0, margin


class Supply(BaseModel):
    """Supply rails for the block under test."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vdd: float
    vss: float = 0.0

    @field_validator("vdd")
    @classmethod
    def _positive_vdd(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"vdd must be positive, got {v}")
        return v


class BlockSpec(BaseModel):
    """Full block spec loaded from YAML."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    block: str
    process: str
    supply: Supply
    targets: dict[str, SpecTarget] = Field(default_factory=dict, alias="specs")
    corners: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("targets")
    @classmethod
    def _targets_nonempty(cls, v: dict[str, SpecTarget]) -> dict[str, SpecTarget]:
        if not v:
            raise ValueError("block spec must declare at least one target under 'specs'")
        return v

    def target_names(self) -> list[str]:
        return sorted(self.targets)

    def min_targets(self) -> dict[str, SpecTarget]:
        return {n: t for n, t in self.targets.items() if t.is_min_spec}

    def max_targets(self) -> dict[str, SpecTarget]:
        return {n: t for n, t in self.targets.items() if t.is_max_spec}


def _coerce(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TypeError(f"expected top-level mapping, got {type(raw).__name__}")
    # YAML often yields ints for numeric keys (e.g., 1.2 supply). Pydantic
    # handles coercion but we guard against non-string spec keys.
    if "specs" in raw and isinstance(raw["specs"], dict):
        raw["specs"] = {str(k): v for k, v in raw["specs"].items()}
    return raw


def load_spec(path: str | Path) -> BlockSpec:
    """Load a ``BlockSpec`` from a YAML file path."""
    data = yaml.safe_load(Path(path).read_text())
    return BlockSpec.model_validate(_coerce(data))


def load_spec_from_string(text: str) -> BlockSpec:
    """Load a ``BlockSpec`` from a YAML string (useful in tests)."""
    data = yaml.safe_load(text)
    return BlockSpec.model_validate(_coerce(data))
