"""Autonomous greedy design exploration loop.

Inspired by autoresearch: fixed eval budget, propose -> evaluate -> keep/discard.
The LLM sees full history and proposes the next design point as JSON.
No tool calling overhead -- tight loop, minimal latency per iteration.

Can be used standalone or as the exploration engine inside TrackDOrchestrator
(hybrid mode), where autoresearch handles sizing exploration and ADK handles
corner validation, flow execution, and DRC/LVS verification.

Usage (standalone):
    runner = AutoresearchRunner(
        topology=GF180OTATopology(),
        model="zai/GLM-4.5-Flash",
        budget=50,
    )
    result = await runner.run(work_dir=Path("results"))

Usage (inside TrackDOrchestrator):
    orch = TrackDOrchestrator(
        project_dir=...,
        topology=GF180OTATopology(),
        exploration_mode="autoresearch",
    )
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from eda_agents.agents.phase_results import AutoresearchResult
from eda_agents.core.pdk import PdkConfig, resolve_pdk
from eda_agents.core.topology import CircuitTopology

logger = logging.getLogger(__name__)


class AutoresearchRunner:
    """Autonomous greedy circuit design exploration.

    Parameters
    ----------
    topology : CircuitTopology
        Circuit to optimize.
    model : str
        LiteLLM model identifier for the proposal LLM.
    budget : int
        Maximum number of SPICE evaluations.
    pdk : PdkConfig or str, optional
        PDK override. Defaults to topology's PDK.
    top_n : int
        Number of top designs to return for downstream use.
    """

    def __init__(
        self,
        topology: CircuitTopology,
        model: str = "zai/GLM-4.5-Flash",
        budget: int = 50,
        pdk: PdkConfig | str | None = None,
        top_n: int = 3,
    ):
        self.topology = topology
        self.model = model
        self.budget = budget
        self.pdk = resolve_pdk(pdk) if pdk else getattr(
            topology, "pdk", resolve_pdk(None)
        )
        self.top_n = top_n

    def _system_prompt(self) -> str:
        """Build the system prompt for the proposal LLM."""
        space = self.topology.design_space()
        ranges = "\n".join(
            f"  {name}: [{lo}, {hi}]" for name, (lo, hi) in space.items()
        )
        return (
            f"You are an analog circuit designer optimizing a {self.topology.topology_name()} "
            f"circuit.\n\n"
            f"Circuit: {self.topology.prompt_description()}\n\n"
            f"Design variables:\n{self.topology.design_vars_description()}\n\n"
            f"Parameter ranges:\n{ranges}\n\n"
            f"Specs: {self.topology.specs_description()}\n"
            f"FoM: {self.topology.fom_description()}\n"
            f"Reference: {self.topology.reference_description()}\n\n"
            f"Your task: propose the next set of design parameters as a JSON object "
            f"with keys matching the design variables above. No other text, just the "
            f"JSON object. Maximize FoM while meeting all specs.\n\n"
            f"Strategy tips:\n"
            f"- Look at the history to understand which directions improve FoM\n"
            f"- Explore systematically: vary one or two parameters at a time\n"
            f"- If recent attempts all fail specs, backtrack toward the best known design\n"
            f"- Balance exploration (new regions) with exploitation (refining best)"
        )

    def _build_proposal_prompt(
        self,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> str:
        """Build the user prompt showing history and requesting next params."""
        parts = [f"Evaluation {eval_num}/{self.budget}.\n"]

        if best:
            parts.append(
                f"Current best (eval #{best['eval']}): "
                f"FoM={best['fom']:.2e}, valid={best['valid']}\n"
                f"Params: {json.dumps(best['params'], indent=2)}\n"
                f"Measurements: Adc={best.get('Adc_dB', '?')}dB, "
                f"GBW={best.get('GBW_Hz', '?')}Hz, "
                f"PM={best.get('PM_deg', '?')}deg\n"
            )
        else:
            parts.append("No valid design found yet. Start exploring.\n")

        if history:
            parts.append("\nHistory (last 20):\n")
            for h in history[-20:]:
                status = "KEPT" if h.get("kept") else "discarded"
                valid = "valid" if h.get("valid") else "INVALID"
                violations = h.get("violations", [])
                viol_str = f" [{', '.join(violations)}]" if violations else ""
                parts.append(
                    f"  #{h['eval']}: FoM={h['fom']:.2e} {valid}{viol_str} "
                    f"({status}) -- {json.dumps(h['params'])}\n"
                )

        parts.append(
            f"\nPropose the next design parameters as a JSON object. "
            f"Budget remaining: {self.budget - eval_num + 1}."
        )

        return "".join(parts)

    async def _propose_params(
        self,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict[str, float]:
        """Ask LLM to propose next design parameters."""
        import litellm

        prompt = self._build_proposal_prompt(history, best, eval_num)

        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1024,
            "temperature": 0.7,
        }

        # response_format not supported by all providers (e.g., z.ai).
        # Try with it; on UnsupportedParamsError retry without.
        try:
            response = await litellm.acompletion(
                **kwargs, response_format={"type": "json_object"}
            )
        except Exception as e:
            err_str = f"{type(e).__name__}: {e}"
            if "response_format" in err_str or "UnsupportedParams" in err_str:
                logger.info("response_format not supported by %s, retrying without", self.model)
                response = await litellm.acompletion(**kwargs)
            else:
                raise

        content = response.choices[0].message.content

        # Extract JSON from response (may be wrapped in markdown code block)
        if "```" in content:
            # Strip markdown fences
            import re
            json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
            if json_match:
                content = json_match.group(1)

        params = json.loads(content)

        # Validate and clamp to design space
        space = self.topology.design_space()
        clean_params = {}
        for name, (lo, hi) in space.items():
            val = params.get(name)
            if val is None:
                # Use default if LLM omitted a parameter
                val = self.topology.default_params().get(name, (lo + hi) / 2)
            clean_params[name] = max(lo, min(hi, float(val)))

        return clean_params

    async def _evaluate(
        self,
        params: dict[str, float],
        work_dir: Path,
        eval_num: int,
    ) -> dict:
        """Run SPICE evaluation for a set of parameters."""
        from eda_agents.core.spice_runner import SpiceRunner

        sizing = self.topology.params_to_sizing(params)
        if isinstance(sizing, dict) and "error" in sizing:
            return {
                "eval": eval_num,
                "params": params,
                "success": False,
                "error": sizing["error"],
                "fom": 0.0,
                "valid": False,
                "violations": [],
            }

        sim_dir = work_dir / f"eval_{eval_num:03d}"
        sim_dir.mkdir(parents=True, exist_ok=True)

        cir = self.topology.generate_netlist(sizing, sim_dir)
        runner = SpiceRunner(pdk=self.pdk)
        result = await runner.run_async(cir, sim_dir)

        if not result.success:
            return {
                "eval": eval_num,
                "params": params,
                "success": False,
                "error": result.error,
                "fom": 0.0,
                "valid": False,
                "violations": [],
            }

        fom = self.topology.compute_fom(result, sizing)
        valid, violations = self.topology.check_validity(result, sizing)

        return {
            "eval": eval_num,
            "params": params,
            "success": True,
            "fom": fom,
            "valid": valid,
            "violations": violations,
            "Adc_dB": result.Adc_dB,
            "GBW_Hz": result.GBW_Hz,
            "PM_deg": result.PM_deg,
            "measurements": result.measurements,
        }

    def _write_tsv_header(self, tsv_path: Path):
        """Write TSV header line."""
        space = self.topology.design_space()
        param_cols = "\t".join(space.keys())
        tsv_path.write_text(
            f"eval\t{param_cols}\tAdc_dB\tGBW_Hz\tPM_deg\tfom\tvalid\tkept\n"
        )

    def _append_tsv_row(self, tsv_path: Path, entry: dict):
        """Append one row to the TSV log."""
        space = self.topology.design_space()
        param_vals = "\t".join(
            f"{entry['params'].get(k, 0):.4f}" for k in space
        )
        with open(tsv_path, "a") as f:
            f.write(
                f"{entry['eval']}\t{param_vals}\t"
                f"{entry.get('Adc_dB', '')}\t"
                f"{entry.get('GBW_Hz', '')}\t"
                f"{entry.get('PM_deg', '')}\t"
                f"{entry['fom']:.6e}\t"
                f"{entry['valid']}\t"
                f"{entry.get('kept', False)}\n"
            )

    async def run(self, work_dir: Path) -> AutoresearchResult:
        """Run the autonomous exploration loop.

        Loop:
        1. Show LLM: current best + all history (params, FoM, valid)
        2. LLM proposes next params (JSON response)
        3. Run SPICE evaluation
        4. If FoM > best_fom and valid: keep (update baseline)
        5. Else: discard
        6. Log to results.tsv
        7. Repeat until budget exhausted

        Returns AutoresearchResult with top-N designs for downstream use.
        """
        work_dir.mkdir(parents=True, exist_ok=True)
        tsv_path = work_dir / "results.tsv"
        self._write_tsv_header(tsv_path)

        history: list[dict] = []
        best: dict | None = None
        kept_count = 0

        logger.info(
            "Autoresearch: %s, model=%s, budget=%d",
            self.topology.topology_name(), self.model, self.budget,
        )

        for eval_num in range(1, self.budget + 1):
            t0 = time.monotonic()

            # 1. Propose next params
            try:
                params = await self._propose_params(history, best, eval_num)
            except Exception as e:
                logger.warning("LLM proposal failed at eval %d: %s", eval_num, e)
                # Fall back to default params with small random perturbation
                params = self.topology.default_params()

            # 2. Evaluate
            entry = await self._evaluate(params, work_dir, eval_num)

            # 3. Keep or discard
            if entry["success"] and entry["valid"] and (
                best is None or entry["fom"] > best["fom"]
            ):
                entry["kept"] = True
                best = entry.copy()
                kept_count += 1
                logger.info(
                    "Eval %d: KEPT (FoM=%.2e, Adc=%.1fdB, GBW=%.0fHz, PM=%.1fdeg)",
                    eval_num, entry["fom"],
                    entry.get("Adc_dB", 0), entry.get("GBW_Hz", 0),
                    entry.get("PM_deg", 0),
                )
            else:
                entry["kept"] = False
                logger.debug(
                    "Eval %d: discarded (fom=%.2e, valid=%s)",
                    eval_num, entry["fom"], entry["valid"],
                )

            history.append(entry)
            self._append_tsv_row(tsv_path, entry)

            elapsed = time.monotonic() - t0
            logger.debug("Eval %d took %.1fs", eval_num, elapsed)

        # Extract top-N valid designs
        valid_entries = sorted(
            [h for h in history if h.get("valid") and h.get("success")],
            key=lambda x: x["fom"],
            reverse=True,
        )
        top_n = valid_entries[: self.top_n]

        # Build result
        if best is None:
            # No valid design found -- return best invalid
            all_sorted = sorted(history, key=lambda x: x["fom"], reverse=True)
            fallback = all_sorted[0] if all_sorted else {"params": {}, "fom": 0.0}
            return AutoresearchResult(
                best_params=fallback.get("params", {}),
                best_fom=fallback.get("fom", 0.0),
                best_valid=False,
                total_evals=len(history),
                kept=kept_count,
                discarded=len(history) - kept_count,
                top_n=[],
                history=history,
                tsv_path=str(tsv_path),
            )

        return AutoresearchResult(
            best_params=best["params"],
            best_fom=best["fom"],
            best_valid=True,
            total_evals=len(history),
            kept=kept_count,
            discarded=len(history) - kept_count,
            top_n=top_n,
            history=history,
            tsv_path=str(tsv_path),
        )
