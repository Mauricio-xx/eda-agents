"""System-level SPICE evaluation handler for multi-block topologies.

Extends the single-block SpiceEvaluationHandler pattern to SystemTopology.
Handles: netlist generation, SPICE execution, ENOB extraction, FoM computation,
budget management, and caching -- same interface as SpiceEvaluationHandler but
for system-level evaluation.

Key differences from single-block handler:
  - Uses SystemTopology.generate_system_netlist() instead of CircuitTopology
  - Extracts ENOB from bit_data.txt (FFT post-processing)
  - No analytical pre-filter (no simple analytical model for SAR ADC ENOB)
  - Longer simulation timeout (24s vs 5s typical)
  - Supports per_block mode: agents modify only their block's params,
    other blocks held at current best values
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from eda_agents.core.spice_runner import SpiceRunner
from eda_agents.core.system_topology import SystemTopology

logger = logging.getLogger(__name__)


@dataclass
class SystemEvalResult:
    """Result from a system-level SPICE evaluation."""

    params: dict[str, float]
    eval_mode: str  # "spice", "budget_exhausted"
    spice: dict[str, float | None] = field(default_factory=dict)
    enob_data: dict[str, float] = field(default_factory=dict)
    netlist_hash: str = ""
    fom: float = 0.0
    valid: bool = False
    violations: list[str] = field(default_factory=list)
    cached: bool = False
    sim_time_s: float = 0.0
    sim_dir: str = ""
    agent_id: str | None = None
    block_name: str | None = None  # which block was modified (per_block mode)


class SystemSpiceHandler:
    """Agent-callable system-level SPICE evaluation.

    In per_block mode, agents only modify their assigned block's parameters.
    Other blocks use the current_best params (updated after each evaluation).

    Parameters
    ----------
    topology : SystemTopology
        System topology (e.g., SARADCTopology).
    runner : SpiceRunner
        Configured ngspice runner.
    work_dir : Path
        Base directory for simulation files.
    max_evals : int
        Maximum SPICE evaluations total (shared across all agents).
    """

    def __init__(
        self,
        topology: SystemTopology,
        runner: SpiceRunner,
        work_dir: Path,
        max_evals: int = 30,
    ):
        self.topology = topology
        self.runner = runner
        self.work_dir = work_dir
        self.max_evals = max_evals

        self._eval_count = 0
        self._cache: dict[str, SystemEvalResult] = {}
        self._lock = asyncio.Lock()
        self._results: list[SystemEvalResult] = []
        self._current_best_params = dict(topology.default_params())
        self._best_fom = 0.0

        work_dir.mkdir(parents=True, exist_ok=True)

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_evals - self._eval_count)

    @property
    def eval_count(self) -> int:
        return self._eval_count

    @property
    def results(self) -> list[SystemEvalResult]:
        return list(self._results)

    @property
    def current_best_params(self) -> dict[str, float]:
        return dict(self._current_best_params)

    def _cache_key(self, params: dict[str, float]) -> str:
        rounded = {k: round(v, 3) for k, v in sorted(params.items())}
        return hashlib.md5(json.dumps(rounded).encode()).hexdigest()[:12]

    def _merge_block_params(
        self, block_name: str, block_params: dict[str, float]
    ) -> dict[str, float]:
        """Merge block-level params into full system params.

        For per_block mode: agent provides params for their block only,
        other blocks use current_best.
        """
        full_params = dict(self._current_best_params)
        block_space = self.topology.block_design_space(block_name)
        for k in block_space:
            if k in block_params:
                full_params[k] = block_params[k]
        return full_params

    async def evaluate(
        self,
        params: dict[str, float],
        agent_id: str | None = None,
        block_name: str | None = None,
    ) -> SystemEvalResult:
        """Evaluate a system design point.

        Parameters
        ----------
        params : dict
            System params (co_tuning) or block params (per_block).
        agent_id : str, optional
            Agent identifier for tracking.
        block_name : str, optional
            If provided, params are block-level and merged with current_best.
        """
        # Merge block params if per_block mode
        if block_name is not None:
            system_params = self._merge_block_params(block_name, params)
        else:
            system_params = dict(params)

        cache_key = self._cache_key(system_params)

        # Check cache
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            result = SystemEvalResult(
                params=system_params,
                eval_mode=cached.eval_mode,
                spice=cached.spice,
                enob_data=cached.enob_data,
                netlist_hash=cached.netlist_hash,
                fom=cached.fom,
                valid=cached.valid,
                violations=cached.violations,
                cached=True,
                sim_time_s=0.0,
                agent_id=agent_id,
                block_name=block_name,
            )
            self._results.append(result)
            return result

        # Budget check
        async with self._lock:
            if self._eval_count >= self.max_evals:
                result = SystemEvalResult(
                    params=system_params,
                    eval_mode="budget_exhausted",
                    violations=[
                        f"SPICE budget exhausted ({self.max_evals} evals)."
                    ],
                    agent_id=agent_id,
                    block_name=block_name,
                )
                self._results.append(result)
                return result
            self._eval_count += 1
            eval_idx = self._eval_count

        # Generate netlist and run SPICE
        sim_dir = self.work_dir / f"sim_{eval_idx:04d}"
        sim_dir.mkdir(parents=True, exist_ok=True)

        try:
            cir_path = self.topology.generate_system_netlist(
                system_params, sim_dir
            )
        except Exception as e:
            logger.error("Netlist generation failed: %s", e)
            result = SystemEvalResult(
                params=system_params,
                eval_mode="spice",
                fom=0.0,
                valid=False,
                violations=[f"Netlist generation failed: {e}"],
                agent_id=agent_id,
                block_name=block_name,
            )
            self._results.append(result)
            return result

        netlist_hash = ""
        try:
            netlist_hash = hashlib.sha256(cir_path.read_bytes()).hexdigest()[:16]
        except Exception:
            pass

        t0 = time.monotonic()
        spice_result = await self.runner.run_async(cir_path, sim_dir)
        sim_time = time.monotonic() - t0

        # Extract ENOB from bit_data.txt
        enob_data: dict[str, float] = {}
        if spice_result.success:
            try:
                # Import here to avoid circular deps and numpy requirement
                # at module level. Cover both the canonical SAR7BitTopology
                # and the legacy SARADCTopology alias so pre-rename callers
                # keep working with zero code change.
                from eda_agents.topologies.sar_adc_7bit import SAR7BitTopology
                from eda_agents.topologies.sar_adc_8bit import SARADCTopology
                if isinstance(self.topology, (SAR7BitTopology, SARADCTopology)):
                    enob_data = self.topology.extract_enob(sim_dir)
                    # Store ENOB in spice_result measurements for FoM
                    spice_result.measurements.update(enob_data)
            except Exception as e:
                logger.warning("ENOB extraction failed: %s", e)
                enob_data = {"enob": 0.0, "error": str(e)}

        # Build SPICE result dict
        spice_dict: dict[str, float | None] = {}
        if spice_result.success:
            spice_dict = {
                "sim_time_s": sim_time,
                **{k: v for k, v in spice_result.measurements.items()},
            }
        else:
            spice_dict = {"error": spice_result.error, "sim_time_s": sim_time}

        # Compute FoM and validity
        fom = self.topology.compute_system_fom(spice_result, system_params)
        valid, violations = self.topology.check_system_validity(
            spice_result, system_params
        )

        result = SystemEvalResult(
            params=system_params,
            eval_mode="spice",
            spice=spice_dict,
            enob_data=enob_data,
            netlist_hash=f"sha256:{netlist_hash}",
            fom=fom,
            valid=valid,
            violations=violations,
            sim_time_s=sim_time,
            sim_dir=str(sim_dir),
            agent_id=agent_id,
            block_name=block_name,
        )

        # Cache and store
        self._cache[cache_key] = result
        self._results.append(result)

        # Update current best
        if fom > self._best_fom:
            self._best_fom = fom
            self._current_best_params = dict(system_params)

        enob = enob_data.get("enob", 0)
        logger.info(
            "System eval #%d: ENOB=%.2f, FoM=%.2e, valid=%s [%.1fs]",
            eval_idx, enob, fom, valid, sim_time,
        )

        return result

    def to_json(self, result: SystemEvalResult) -> str:
        """Serialize result to JSON for agent consumption."""
        d = {
            "params": result.params,
            "eval_mode": result.eval_mode,
            "fom": result.fom,
            "valid": result.valid,
            "violations": result.violations,
        }

        if result.enob_data:
            d["enob_data"] = {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in result.enob_data.items()
            }

        if result.eval_mode == "spice" and result.spice:
            d["spice"] = {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in result.spice.items()
                if v is not None
            }
            d["sim_time_s"] = round(result.sim_time_s, 1)

        if result.cached:
            d["cached"] = True

        d["budget_remaining"] = self.budget_remaining
        d["current_best_fom"] = round(self._best_fom, 2)

        return json.dumps(d, default=str)

    def export_results(self, path: Path) -> None:
        """Export all results for traceability."""
        data = []
        for r in self._results:
            entry = {
                "params": r.params,
                "eval_mode": r.eval_mode,
                "agent_id": r.agent_id,
                "block_name": r.block_name,
                "spice": r.spice,
                "enob_data": r.enob_data,
                "netlist_hash": r.netlist_hash,
                "fom": r.fom,
                "valid": r.valid,
                "violations": r.violations,
                "cached": r.cached,
                "sim_time_s": r.sim_time_s,
                "sim_dir": r.sim_dir,
            }
            data.append(entry)
        path.write_text(json.dumps(data, indent=2, default=str))
