"""SPICE evaluation handler for agent experiments.

Wraps CircuitTopology + SpiceRunner with budget management, caching,
and optional analytical pre-filtering. Used by experiment harnesses
to provide SPICE-in-the-loop evaluation to LLM/ADK agents.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from eda_agents.core.topology import CircuitTopology
from eda_agents.core.spice_runner import SpiceRunner

logger = logging.getLogger(__name__)


@dataclass
class SpiceEvalResult:
    """Combined analytical + SPICE evaluation result."""

    params: dict[str, float]
    eval_mode: str  # "spice", "analytical_prefilter", "analytical_budget"
    analytical: dict[str, float | bool | list] = field(default_factory=dict)
    spice: dict[str, float | None] = field(default_factory=dict)
    transistor_sizing: dict[str, dict] = field(default_factory=dict)
    netlist_hash: str = ""
    fom: float = 0.0
    valid: bool = False
    violations: list[str] = field(default_factory=list)
    cached: bool = False
    sim_time_s: float = 0.0
    sim_dir: str = ""
    agent_id: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""


class SpiceEvaluationHandler:
    """Agent-callable SPICE evaluation with budget and caching.

    Features:
        - Analytical pre-filter: skip SPICE for clearly invalid designs
        - Result caching: identical params (rounded) return cached result
        - Budget management: limited SPICE evals per agent, falls back to
          analytical estimate when exhausted
        - Concurrent-safe: each evaluation gets unique temp subdir

    Parameters
    ----------
    topology : CircuitTopology
        Circuit topology to evaluate (MillerOTA, AnalogAcademyOTA, etc.).
    runner : SpiceRunner
        Configured ngspice runner.
    work_dir : Path
        Base directory for simulation files.
    max_evals : int
        Maximum SPICE evaluations. After exhaustion, returns analytical
        estimate with a warning.
    analytical_prefilter : bool
        If True, skip SPICE for designs with analytical PM < 45 deg
        or Adc < 35 dB (likely invalid in SPICE too).
    """

    def __init__(
        self,
        topology: CircuitTopology,
        runner: SpiceRunner,
        work_dir: Path,
        max_evals: int = 30,
        analytical_prefilter: bool = True,
    ):
        self.topology = topology
        self.runner = runner
        self.work_dir = work_dir
        self.max_evals = max_evals
        self.analytical_prefilter = analytical_prefilter

        self._eval_count = 0
        self._cache: dict[str, SpiceEvalResult] = {}
        self._lock = asyncio.Lock()
        self._results: list[SpiceEvalResult] = []

        work_dir.mkdir(parents=True, exist_ok=True)

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_evals - self._eval_count)

    @property
    def eval_count(self) -> int:
        return self._eval_count

    @property
    def results(self) -> list[SpiceEvalResult]:
        return list(self._results)

    def _cache_key(self, params: dict[str, float]) -> str:
        """Hash params rounded to 3 decimal places."""
        rounded = {k: round(v, 3) for k, v in sorted(params.items())}
        return hashlib.md5(json.dumps(rounded).encode()).hexdigest()[:12]

    def _get_analytical(
        self, params: dict[str, float], sizing: dict[str, dict]
    ) -> dict:
        """Extract analytical estimates from sizing metadata."""
        ana = sizing.get("_analytical", {})
        if ana:
            return dict(ana)
        # Fallback: no analytical data available (e.g., AnalogAcademy topology)
        return {"note": "no analytical model available for this topology"}

    async def evaluate(
        self, params: dict[str, float], agent_id: str | None = None
    ) -> SpiceEvalResult:
        """Evaluate a design point, using SPICE if budget allows.

        Pipeline:
            1. Check cache
            2. Run topology.params_to_sizing() for analytical estimate
            3. If pre-filter enabled, skip SPICE for clearly invalid designs
            4. If budget exhausted, return analytical estimate
            5. Run topology.generate_netlist() + runner.run_async()
            6. Compute FoM from SPICE results
            7. Cache and store result
        """
        cache_key = self._cache_key(params)

        # 1. Check cache
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            result = SpiceEvalResult(
                params=cached.params,
                eval_mode=cached.eval_mode,
                analytical=cached.analytical,
                spice=cached.spice,
                transistor_sizing=cached.transistor_sizing,
                netlist_hash=cached.netlist_hash,
                fom=cached.fom,
                valid=cached.valid,
                violations=cached.violations,
                cached=True,
                sim_time_s=0.0,
                agent_id=agent_id,
            )
            self._results.append(result)
            return result

        # 2. Analytical sizing
        # Use a fresh topology instance to avoid state conflicts in concurrent use
        sizing = self.topology.params_to_sizing(params)
        analytical = self._get_analytical(params, sizing)

        # 3. Analytical pre-filter
        if self.analytical_prefilter and analytical.get("Adc_dB") is not None:
            adc = analytical.get("Adc_dB", 0)
            pm = analytical.get("PM_deg", 0)
            if adc < 35.0 or pm < 45.0:
                result = SpiceEvalResult(
                    params=params,
                    eval_mode="analytical_prefilter",
                    analytical=analytical,
                    fom=analytical.get("FoM", 0.0),
                    valid=False,
                    violations=[
                        f"Pre-filtered: analytical Adc={adc:.1f}dB, PM={pm:.1f}deg"
                    ],
                    agent_id=agent_id,
                )
                self._cache[cache_key] = result
                self._results.append(result)
                return result

        # 4. Budget check
        async with self._lock:
            if self._eval_count >= self.max_evals:
                result = SpiceEvalResult(
                    params=params,
                    eval_mode="analytical_budget",
                    analytical=analytical,
                    fom=analytical.get("FoM", 0.0),
                    valid=analytical.get("valid", False),
                    violations=[
                        f"SPICE budget exhausted ({self.max_evals} evals). "
                        "Returning analytical estimate."
                    ],
                    agent_id=agent_id,
                )
                self._results.append(result)
                return result
            self._eval_count += 1
            eval_idx = self._eval_count

        # 5. Generate netlist and run SPICE
        sim_dir = self.work_dir / f"sim_{eval_idx:04d}"
        sim_dir.mkdir(parents=True, exist_ok=True)

        try:
            cir_path = self.topology.generate_netlist(sizing, sim_dir)
        except Exception as e:
            result = SpiceEvalResult(
                params=params,
                eval_mode="spice",
                analytical=analytical,
                fom=0.0,
                valid=False,
                violations=[f"Netlist generation failed: {e}"],
                agent_id=agent_id,
            )
            self._results.append(result)
            return result

        # Hash the netlist for reproducibility
        netlist_hash = ""
        try:
            netlist_hash = hashlib.sha256(cir_path.read_bytes()).hexdigest()[:16]
        except Exception:
            pass

        spice_result = await self.runner.run_async(cir_path, sim_dir)

        if not spice_result.success:
            logger.warning(
                "SPICE #%d failed: error=%s | returncode hint in stderr: %s",
                eval_idx,
                spice_result.error,
                spice_result.stderr_tail[:500] if spice_result.stderr_tail else "(empty)",
            )

        # 6. Build result
        spice_dict: dict[str, float | None] = {}
        if spice_result.success:
            spice_dict = {
                "Adc_dB": spice_result.Adc_dB,
                "Adc_peak_dB": spice_result.Adc_peak_dB,
                "GBW_Hz": spice_result.GBW_Hz,
                "GBW_MHz": spice_result.GBW_MHz,
                "PM_deg": spice_result.PM_deg,
                "sim_time_s": spice_result.sim_time_s,
            }
            # Add any extra measurements
            for k, v in spice_result.measurements.items():
                if k not in spice_dict:
                    spice_dict[k] = v
        else:
            spice_dict = {"error": spice_result.error}

        # Compute FoM and validity from SPICE results
        fom = self.topology.compute_fom(spice_result, sizing)
        valid, violations = self.topology.check_validity(spice_result, sizing)

        # Build transistor sizing dict (exclude metadata keys)
        transistors = {
            k: v for k, v in sizing.items()
            if not k.startswith("_") and isinstance(v, dict)
        }

        result = SpiceEvalResult(
            params=params,
            eval_mode="spice",
            analytical=analytical,
            spice=spice_dict,
            transistor_sizing=transistors,
            netlist_hash=f"sha256:{netlist_hash}",
            fom=fom,
            valid=valid,
            violations=violations,
            sim_time_s=spice_result.sim_time_s,
            sim_dir=str(sim_dir),
            agent_id=agent_id,
            stdout_tail=spice_result.stdout_tail[-2000:] if spice_result.stdout_tail else "",
            stderr_tail=spice_result.stderr_tail[-1000:] if spice_result.stderr_tail else "",
        )

        # 7. Cache and store
        self._cache[cache_key] = result
        self._results.append(result)

        logger.info(
            "SPICE eval #%d: Adc=%.1f dB, GBW=%.3f MHz, PM=%.1f deg, FoM=%.2e [%.1fs]",
            eval_idx,
            spice_result.Adc_dB or 0,
            (spice_result.GBW_Hz or 0) / 1e6,
            spice_result.PM_deg or 0,
            fom,
            spice_result.sim_time_s,
        )

        return result

    def evaluate_sync(self, params: dict[str, float]) -> SpiceEvalResult:
        """Synchronous wrapper for evaluate()."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.evaluate(params))
        finally:
            loop.close()

    def to_json(self, result: SpiceEvalResult) -> str:
        """Serialize SpiceEvalResult to JSON string for agent consumption."""
        d = {
            "params": result.params,
            "eval_mode": result.eval_mode,
            "fom": result.fom,
            "valid": result.valid,
            "violations": result.violations,
        }

        if result.eval_mode == "spice":
            if result.spice:
                d["spice"] = {
                    k: round(v, 6) if isinstance(v, float) else v
                    for k, v in result.spice.items()
                    if v is not None
                }
            d["sim_time_s"] = round(result.sim_time_s, 2)

        if result.analytical:
            d["analytical"] = {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in result.analytical.items()
                if v is not None
            }

        if result.cached:
            d["cached"] = True

        d["budget_remaining"] = self.budget_remaining
        return json.dumps(d, default=str)

    def export_results(self, path: Path) -> None:
        """Export all results to a JSON file for reproducibility.

        Includes full traceability: params, sizing, netlist hash, sim_dir,
        SPICE stdout/stderr tails, and all measurement data.
        """
        data = []
        for r in self._results:
            entry = {
                "params": r.params,
                "eval_mode": r.eval_mode,
                "agent_id": r.agent_id,
                "analytical": r.analytical,
                "spice": r.spice,
                "transistor_sizing": r.transistor_sizing,
                "netlist_hash": r.netlist_hash,
                "fom": r.fom,
                "valid": r.valid,
                "violations": r.violations,
                "cached": r.cached,
                "sim_time_s": r.sim_time_s,
                "sim_dir": r.sim_dir,
            }
            # Only include stdout/stderr for non-cached SPICE evals
            if r.eval_mode == "spice" and not r.cached:
                if r.stdout_tail:
                    entry["stdout_tail"] = r.stdout_tail
                if r.stderr_tail:
                    entry["stderr_tail"] = r.stderr_tail
            data.append(entry)
        path.write_text(json.dumps(data, indent=2, default=str))
