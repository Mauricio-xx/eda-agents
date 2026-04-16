"""Experiment scenarios: base protocol and SPICE exploration.

The analytical ``ReactiveExplorationScenario`` was removed in the S10a
cohesion cleanup alongside the ``reactive_harness`` runner. SPICE-driven
exploration is provided by ``SpiceExplorationScenario`` below.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from eda_agents.core.topology import CircuitTopology

_uuid = lambda: str(uuid4())


# ---------------------------------------------------------------------------
# Base scenario protocol and result model
# ---------------------------------------------------------------------------


class ScenarioResult(BaseModel):
    """Results from a single scenario run."""

    experiment_id: str = Field(default_factory=_uuid)
    scenario: str
    strategy: str
    agent_count: int
    duration_seconds: float = 0.0
    total_writes: int = 0
    conflicts: int = 0
    conflict_rate: float = 0.0
    contention_events: int = 0
    sensitivity_triggers: int = 0
    coordination_overhead_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Scenario(ABC):
    """Base class for experiment scenarios."""

    name: str = "base"
    description: str = ""

    @abstractmethod
    def generate_tasks(
        self, n_agents: int, seed: int = 42
    ) -> list[dict[str, Any]]:
        """Generate task assignments for each agent.

        Returns a list of task dicts, one per agent. Each dict contains:
        - agent_id: str
        - operations: list of (action, target_type, target_key, content) tuples
        """
        ...

    @abstractmethod
    def expected_conflict_level(self) -> str:
        """Return expected conflict level: 'none', 'low', 'medium', 'high'."""
        ...


# ---------------------------------------------------------------------------
# Miller-OTA design space bounds (used by SpiceExplorationScenario fallback)
# ---------------------------------------------------------------------------

# Design space bounds (user-facing units: S/A, um, pF, uA)
GMID_INPUT_MIN, GMID_INPUT_MAX = 5.0, 25.0
GMID_LOAD_MIN, GMID_LOAD_MAX = 5.0, 20.0
L_INPUT_MIN, L_INPUT_MAX = 0.13, 2.0  # um
L_LOAD_MIN, L_LOAD_MAX = 0.13, 2.0    # um
CC_MIN, CC_MAX = 0.1, 5.0             # pF
IBIAS_MIN, IBIAS_MAX = 0.5, 50.0      # uA (first-stage per branch)

BOUNDS = {
    "gmid_input": (GMID_INPUT_MIN, GMID_INPUT_MAX),
    "gmid_load": (GMID_LOAD_MIN, GMID_LOAD_MAX),
    "L_input_um": (L_INPUT_MIN, L_INPUT_MAX),
    "L_load_um": (L_LOAD_MIN, L_LOAD_MAX),
    "Cc_pF": (CC_MIN, CC_MAX),
    "Ibias_uA": (IBIAS_MIN, IBIAS_MAX),
}

# ---------------------------------------------------------------------------
# Agent configuration
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    """Per-agent exploration configuration."""

    agent_id: str
    center: dict[str, float]       # current sampling center in 5D
    partition_lo: dict[str, float]  # assigned region lower bounds
    partition_hi: dict[str, float]  # assigned region upper bounds
    seed: int


# ---------------------------------------------------------------------------
# SPICE Exploration Scenario
# ---------------------------------------------------------------------------

@dataclass
class SpiceExplorationScenario(Scenario):
    """SPICE-validated design exploration scenario.

    Topology-agnostic SPICE-in-the-loop exploration with reduced budget
    (~10s/sim).

    Parameters
    ----------
    topology : CircuitTopology
        Circuit topology to explore.
    total_budget : int
        Total SPICE evaluations per agent (default 30).
    batch_size : int
        Evaluations per agent per round (default 5).
    analytical_prefilter : bool
        Skip SPICE for analytically invalid designs (default True).
    partition_dim : str
        Dimension to partition across agents (default "gmid_input").
    """

    name: str = "spice_exploration"
    description: str = "SPICE-in-the-loop multi-agent OTA design exploration"

    topology: CircuitTopology = field(default=None)  # type: ignore[assignment]
    total_budget: int = 30
    batch_size: int = 5
    analytical_prefilter: bool = True
    partition_dim: str = "gmid_input"

    def __post_init__(self):
        if self.topology is None:
            from eda_agents.topologies.ota_miller import MillerOTATopology
            self.topology = MillerOTATopology()
        self.n_rounds = self.total_budget // self.batch_size

    @property
    def topology_name(self) -> str:
        return self.topology.topology_name()

    @property
    def design_bounds(self) -> dict[str, tuple[float, float]]:
        """Use topology's design space if available, otherwise default BOUNDS."""
        space = self.topology.design_space()
        # Map topology design space keys to our BOUNDS format
        if space:
            return space
        return dict(BOUNDS)

    def generate_tasks(
        self, n_agents: int, seed: int = 42
    ) -> list[dict[str, Any]]:
        """Generate task assignments (compatibility with base Scenario)."""
        configs = self.make_agent_configs(n_agents, seed)
        tasks = []
        for config in configs:
            tasks.append({
                "agent_id": config.agent_id,
                "operations": [
                    ("evaluate", "spice", f"round-0-{i}", "")
                    for i in range(self.batch_size)
                ],
            })
        return tasks

    def expected_conflict_level(self) -> str:
        return "low"

    def make_agent_configs(
        self, n_agents: int, seed: int = 42
    ) -> list[AgentConfig]:
        """Create per-agent configs with partitioned design space."""
        rng = random.Random(seed)
        bounds = self.design_bounds
        configs = []

        # Partition the partition_dim equally
        if self.partition_dim in bounds:
            lo, hi = bounds[self.partition_dim]
            step = (hi - lo) / n_agents
        else:
            lo, hi = BOUNDS.get(self.partition_dim, (5.0, 25.0))
            step = (hi - lo) / n_agents

        for i in range(n_agents):
            agent_id = f"agent_{i}"
            p_lo = lo + i * step
            p_hi = lo + (i + 1) * step

            partition_lo = {k: v[0] for k, v in bounds.items()}
            partition_hi = {k: v[1] for k, v in bounds.items()}
            partition_lo[self.partition_dim] = p_lo
            partition_hi[self.partition_dim] = p_hi

            center = {
                k: (partition_lo[k] + partition_hi[k]) / 2.0
                for k in bounds
            }

            configs.append(AgentConfig(
                agent_id=agent_id,
                partition_lo=partition_lo,
                partition_hi=partition_hi,
                center=center,
                seed=rng.randint(0, 2**31),
            ))

        return configs
