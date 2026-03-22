"""Experiment scenarios: base protocol, reactive exploration, and SPICE exploration.

Combines base scenario, reactive exploration (analytical), and SPICE exploration
into a single module for the eda-agents package.
"""

from __future__ import annotations

import json
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from eda_agents.topologies.miller_ota import MillerOTADesigner
from eda_agents.core.topology import CircuitTopology

_now = lambda: datetime.now(timezone.utc).isoformat()
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
# Reactive exploration scenario (analytical)
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

DIM_NAMES = list(BOUNDS.keys())


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DesignPointRecord:
    """A single evaluated design point."""

    agent: str
    params: dict[str, float]  # gmid_input, L_input_um, etc.
    fom: float
    valid: bool
    round_idx: int
    results: dict[str, object] | None = None  # full analytical results for simulation


@dataclass
class StoreSnapshot:
    """State of all design points in the store at a given moment."""

    design_points: list[DesignPointRecord] = field(default_factory=list)
    best_fom: float = 0.0
    best_params: dict[str, float] | None = None
    best_agent: str | None = None


@dataclass
class CoordinationSnapshot:
    """Active coordination state at a given moment."""

    active_intents: list[dict[str, Any]] = field(default_factory=list)
    active_reservations: list[dict[str, Any]] = field(default_factory=list)


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
# Partitioning helpers
# ---------------------------------------------------------------------------

def _partition_range(
    lo: float, hi: float, n_parts: int, idx: int
) -> tuple[float, float]:
    """Partition [lo, hi] into n_parts and return the idx-th sub-range."""
    step = (hi - lo) / n_parts
    return lo + idx * step, lo + (idx + 1) * step


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Reactive Exploration Scenario
# ---------------------------------------------------------------------------

class ReactiveExplorationScenario(Scenario):
    """Multi-agent reactive design exploration for Miller OTA.

    Agents explore the 5D design space in synchronized rounds. Between rounds,
    the coordination strategy determines how agents react to shared state:

    - none: fixed random search within assigned partition
    - intents_only: rejection sampling to avoid other agents' declared regions
    - reservations: intents + advisory locks on best-design writes
    - full_rep: attraction toward global best + repulsion from density
    """

    name = "reactive_exploration"
    description = (
        "Reactive round-based Miller OTA design exploration on IHP SG13G2 "
        "with inter-round coordination (attraction/repulsion policy)"
    )

    def __init__(
        self,
        total_budget: int = 60,
        batch_size: int = 5,
        partition_dim: str = "gmid_input",
        attraction_strength: float = 0.3,
        repulsion_strength: float = 0.5,
    ):
        self.total_budget = total_budget
        self.batch_size = batch_size
        self.partition_dim = partition_dim
        self.attraction_strength = attraction_strength
        self.repulsion_strength = repulsion_strength
        self.n_rounds = total_budget // batch_size
        self.designer = MillerOTADesigner()

    def generate_tasks(
        self, n_agents: int, seed: int = 42
    ) -> list[dict[str, Any]]:
        """Generate lightweight agent configs (satisfies ABC).

        The actual operations are generated round-by-round via
        generate_round_operations().
        """
        configs = self.make_agent_configs(n_agents, seed)
        return [
            {
                "agent_id": cfg.agent_id,
                "operations": [],  # filled per-round by harness
                "config": {
                    "center": cfg.center,
                    "partition_lo": cfg.partition_lo,
                    "partition_hi": cfg.partition_hi,
                    "seed": cfg.seed,
                },
            }
            for cfg in configs
        ]

    def make_agent_configs(
        self, n_agents: int, seed: int = 42
    ) -> list[AgentConfig]:
        """Create initial agent configurations with partitioned regions."""
        configs = []
        for idx in range(n_agents):
            partition_lo = {}
            partition_hi = {}
            center = {}

            for dim_name in DIM_NAMES:
                lo, hi = BOUNDS[dim_name]
                if dim_name == self.partition_dim:
                    plo, phi = _partition_range(lo, hi, n_agents, idx)
                    partition_lo[dim_name] = plo
                    partition_hi[dim_name] = phi
                else:
                    partition_lo[dim_name] = lo
                    partition_hi[dim_name] = hi

                center[dim_name] = (
                    partition_lo[dim_name] + partition_hi[dim_name]
                ) / 2

            configs.append(AgentConfig(
                agent_id=f"agent-{idx}",
                center=center,
                partition_lo=partition_lo,
                partition_hi=partition_hi,
                seed=seed + idx * 1000,
            ))
        return configs

    def generate_round_operations(
        self,
        agent_id: str,
        round_idx: int,
        config: dict[str, Any],
        store_snapshot: StoreSnapshot,
        coord_snapshot: CoordinationSnapshot,
        strategy: str,
        rng: random.Random,
    ) -> tuple[list[tuple[str, ...]], dict[str, float]]:
        """Generate operations for one agent in one round.

        Returns:
            (operations, updated_center) -- operations to execute and new center
        """
        center = dict(config["center"])
        partition_lo = config["partition_lo"]
        partition_hi = config["partition_hi"]

        # Compute sampling radius that shrinks with round
        base_sigma = 0.3
        decay = 1.0 / (1.0 + round_idx * 0.3)
        sigma = base_sigma * decay

        # Apply policy based on strategy
        shifted = False
        if strategy == "full_rep" and round_idx > 0:
            center, shifted = self._apply_full_rep_policy(
                center, agent_id, store_snapshot
            )
        elif strategy in ("intents_only", "reservations") and round_idx > 0:
            # No center shift, but we'll do rejection sampling below
            pass

        # Sample batch_size points around center
        operations: list[tuple[str, ...]] = []

        # Signal intent at start of each round (intents_only, reservations, full_rep)
        if strategy in ("intents_only", "reservations", "full_rep"):
            region_desc = (
                f"round {round_idx}: exploring near "
                + ", ".join(f"{k}={center[k]:.2f}" for k in DIM_NAMES[:2])
            )
            operations.append((
                "intent", "knowledge", f"region-{agent_id}", region_desc
            ))

        best_fom_this_round = 0.0
        best_result_this_round = None

        for _ in range(self.batch_size):
            point = self._sample_point(
                center, sigma, partition_lo, partition_hi,
                strategy, coord_snapshot, rng,
            )

            result = self.designer.analytical_design(
                gmid_input=point["gmid_input"],
                gmid_load=point["gmid_load"],
                L_input=point["L_input_um"] * 1e-6,
                L_load=point["L_load_um"] * 1e-6,
                Cc=point["Cc_pF"] * 1e-12,
                Ibias=point["Ibias_uA"] * 1e-6,
            )

            point_key = (
                f"design-point-{agent_id}-r{round_idx}"
                f"-gm{point['gmid_input']:.1f}-L{point['L_input_um']:.2f}"
            )
            point_content = json.dumps({
                "agent": agent_id,
                "round": round_idx,
                "gmid_input": round(point["gmid_input"], 2),
                "L_input_um": round(point["L_input_um"], 3),
                "gmid_load": round(point["gmid_load"], 2),
                "L_load_um": round(point["L_load_um"], 3),
                "Cc_pF": round(point["Cc_pF"], 2),
                "Ibias_uA": round(point["Ibias_uA"], 2),
                "Adc_dB": round(result.Adc_dB, 1),
                "GBW_MHz": round(result.GBW / 1e6, 3),
                "PM_deg": round(result.PM, 1),
                "power_uW": round(result.power_uW, 2),
                "area_um2": round(result.area_um2, 2),
                "FoM": result.FoM,
                "valid": result.valid,
                "violations": result.violations,
            })

            operations.append(("write", "knowledge", point_key, point_content))

            if result.FoM > best_fom_this_round:
                best_fom_this_round = result.FoM
                best_result_this_round = result

        # Write best-design if this round found something better than store best
        if best_result_this_round and best_fom_this_round > store_snapshot.best_fom:
            operations.append((
                "write", "knowledge", "best-design",
                f"{agent_id}: {best_result_this_round.summary()}"
            ))

        # Sensitivity signal on last round
        if round_idx == self.n_rounds - 1 and strategy == "full_rep":
            operations.append((
                "sensitivity", "knowledge", "best-design",
                f"{agent_id} should refine search near new best"
            ))

        return operations, center

    def _apply_full_rep_policy(
        self,
        center: dict[str, float],
        agent_id: str,
        snapshot: StoreSnapshot,
    ) -> tuple[dict[str, float], bool]:
        """Shift center toward global best (attraction) and away from density (repulsion).

        Returns:
            (new_center, shifted) -- whether the center moved significantly
        """
        new_center = dict(center)
        shifted = False

        if not snapshot.design_points:
            return new_center, shifted

        # Attraction: shift toward global best if it's from another agent
        if (
            snapshot.best_params
            and snapshot.best_agent
            and snapshot.best_agent != agent_id
        ):
            for dim in DIM_NAMES:
                if dim in snapshot.best_params:
                    delta = snapshot.best_params[dim] - new_center[dim]
                    new_center[dim] += self.attraction_strength * delta

        # Repulsion: compute centroid of other agents' points, push away
        others = [
            dp for dp in snapshot.design_points if dp.agent != agent_id
        ]
        if others:
            centroid = {dim: 0.0 for dim in DIM_NAMES}
            for dp in others:
                for dim in DIM_NAMES:
                    if dim in dp.params:
                        centroid[dim] += dp.params[dim]
            for dim in DIM_NAMES:
                centroid[dim] /= len(others)
                push = (new_center[dim] - centroid[dim]) * 0.1
                new_center[dim] += self.repulsion_strength * push

        # Clamp to valid bounds
        for dim in DIM_NAMES:
            lo, hi = BOUNDS[dim]
            new_center[dim] = _clamp(new_center[dim], lo, hi)

        # Check if center moved significantly (>5% of range in any dim)
        for dim in DIM_NAMES:
            lo, hi = BOUNDS[dim]
            dim_range = hi - lo
            if dim_range > 0 and abs(new_center[dim] - center[dim]) / dim_range > 0.05:
                shifted = True
                break

        return new_center, shifted

    def _sample_point(
        self,
        center: dict[str, float],
        sigma: float,
        partition_lo: dict[str, float],
        partition_hi: dict[str, float],
        strategy: str,
        coord_snapshot: CoordinationSnapshot,
        rng: random.Random,
    ) -> dict[str, float]:
        """Sample a single point near center, respecting bounds.

        For intents_only/reservations: rejection-sample to avoid other agents' regions.
        """
        max_attempts = 20
        for _ in range(max_attempts):
            point = {}
            for dim in DIM_NAMES:
                lo, hi = BOUNDS[dim]
                dim_range = hi - lo
                val = rng.gauss(center[dim], dim_range * sigma)
                point[dim] = _clamp(val, lo, hi)

            # For intents_only or reservations: try to avoid other agents' declared regions
            if strategy in ("intents_only", "reservations"):
                if not self._point_in_other_region(
                    point, coord_snapshot.active_intents
                ):
                    return point
            else:
                return point

        # Fallback: return last sample
        return point

    def _point_in_other_region(
        self,
        point: dict[str, float],
        intents: list[dict[str, Any]],
    ) -> bool:
        """Check if point falls inside any other agent's declared region.

        Intent descriptions are free-text but we extract approximate regions
        from the structured intent data if available.
        """
        # Intents are advisory -- this is a soft check based on partition info
        # embedded in intent descriptions. In practice, intents contain "near"
        # coordinates, so we check if the point is within 10% of the intent center.
        for intent in intents:
            desc = intent.get("description", "")
            # Parse "gmid_input=X.XX" from description
            try:
                parts = desc.split("near ")
                if len(parts) < 2:
                    continue
                coord_str = parts[1]
                for pair in coord_str.split(", "):
                    key, val_str = pair.split("=")
                    key = key.strip()
                    val = float(val_str)
                    if key in point:
                        lo, hi = BOUNDS[key]
                        threshold = (hi - lo) * 0.1
                        if abs(point[key] - val) < threshold:
                            return True
            except (ValueError, IndexError):
                continue
        return False

    def expected_conflict_level(self) -> str:
        return "medium"


# ---------------------------------------------------------------------------
# SPICE Exploration Scenario
# ---------------------------------------------------------------------------

@dataclass
class SpiceExplorationScenario(Scenario):
    """SPICE-validated design exploration scenario.

    Like ReactiveExplorationScenario but with reduced budget
    to account for ~10s/sim SPICE latency, and topology-agnostic.

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
