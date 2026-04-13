"""Autonomous greedy design exploration loop for analog circuits.

Adapted from Karpathy's autoresearch [1] to the analog IC design domain.
The original autoresearch optimizes LLM training loss (val_bpb) by
autonomously modifying train.py. This adaptation replaces the training
loop with a SPICE simulation loop: the LLM proposes circuit sizing
parameters, SPICE evaluates them, and the loop keeps improvements.

The core mechanism is identical:
- program.md as persistent brain (goal, strategy, accumulated knowledge)
- Greedy keep/discard loop (new FoM > best FoM? keep : discard)
- Fully autonomous execution (never stops to ask, crashes are logged and skipped)
- Resume support (program.md + results.tsv persist across sessions)

The runner is topology-agnostic: it takes any CircuitTopology subclass
(see ``eda_agents.core.topology.CircuitTopology``). Adding a new circuit
type (comparator, LDO, bandgap, PLL building blocks, etc.) requires
only a new CircuitTopology implementation -- zero changes to the runner
or prompt generation code.

Existing topologies:
- GF180OTATopology:        PMOS-input two-stage OTA on GF180MCU 180nm
- MillerOTATopology:       NMOS-input Miller OTA on IHP SG13G2 130nm
- AnalogAcademyOTATopology: PMOS-input OTA from IHP AnalogAcademy

Can be used standalone or as the exploration engine inside
TrackDOrchestrator (hybrid mode), where autoresearch handles sizing
exploration and ADK handles corner validation, flow execution, and
DRC/LVS verification.

[1] https://github.com/karpathy/autoresearch

Usage (standalone):
    runner = AutoresearchRunner(
        topology=GF180OTATopology(),
        model="zai/GLM-4.5-Flash",
        budget=50,
    )
    result = await runner.run(work_dir=Path("results"))

Usage (resume a previous run):
    # If work_dir contains program.md and results.tsv from a previous
    # run, the loop resumes from where it left off.
    result = await runner.run(work_dir=Path("results"))

Usage (new topology -- e.g., a comparator):
    class MyComparatorTopology(CircuitTopology):
        def topology_name(self): return "strong_arm_comp"
        def design_space(self): return {"Ibias_uA": (5, 100), ...}
        def params_to_sizing(self, params): ...
        def generate_netlist(self, sizing, work_dir): ...
        def compute_fom(self, spice_result, sizing): ...
        def check_validity(self, spice_result, sizing): ...
        # + prompt metadata methods

    runner = AutoresearchRunner(
        topology=MyComparatorTopology(),
        model="your-model",
        budget=30,
    )
    result = await runner.run(work_dir=Path("comparator_results"))
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path

from eda_agents.agents._autoresearch_core import (
    ProgramStore,
    TsvLogger,
    extract_json_from_response,
    generate_program_content,
)
from eda_agents.agents.phase_results import AutoresearchResult
from eda_agents.core.pdk import PdkConfig, resolve_pdk
from eda_agents.core.topology import CircuitTopology

logger = logging.getLogger(__name__)

# Analog measurement columns (Adc, GBW, PM)
_ANALOG_MEASUREMENT_COLS = ["Adc_dB", "GBW_Hz", "PM_deg"]


class AutoresearchRunner:
    """Autonomous greedy circuit design exploration.

    Topology-agnostic: works with any ``CircuitTopology`` subclass.
    The runner knows nothing about the circuit being optimized -- all
    circuit-specific logic (design space, sizing, netlist format, FoM,
    specs) lives in the topology. To explore a new circuit type, implement
    a new CircuitTopology and pass it here.

    Central artifact: ``program.md`` in the work_dir. This file persists
    across sessions and contains the goal, metrics, strategy, and
    accumulated knowledge. The LLM reads it before each proposal and
    the runner updates it after each kept improvement. The program is
    auto-generated from the topology's prompt metadata, so it adapts
    to any circuit type without manual editing.

    Parameters
    ----------
    topology : CircuitTopology
        Circuit to optimize. Defines the design space, SPICE netlist
        format, FoM formula, and validity specs.
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

    # ------------------------------------------------------------------
    # program.md management (delegates to ProgramStore)
    # ------------------------------------------------------------------

    def _generate_program(self) -> str:
        """Generate the initial program.md content from topology metadata."""
        space = self.topology.design_space()
        space_lines = "\n".join(
            f"- {name}: [{lo}, {hi}]" for name, (lo, hi) in space.items()
        )
        return generate_program_content(
            domain_name=self.topology.topology_name(),
            pdk_display_name=self.pdk.display_name,
            fom_description=self.topology.fom_description(),
            specs_description=self.topology.specs_description(),
            design_vars_description=self.topology.design_vars_description(),
            design_space_lines=space_lines,
            reference_description=self.topology.reference_description(),
        )

    def _make_program_store(self, work_dir: Path) -> ProgramStore:
        return ProgramStore(work_dir, self._generate_program)

    def _init_program(self, work_dir: Path) -> Path:
        """Create or load program.md."""
        store = self._make_program_store(work_dir)
        return store.init()

    def _read_program(self, program_path: Path) -> str:
        """Read the current program.md content."""
        return program_path.read_text()

    def _format_analog_best(self, entry: dict) -> str:
        """Format the Current Best section body for analog metrics."""
        params_str = json.dumps(entry["params"], indent=2)
        return (
            f"Eval #{entry['eval']}: FoM={entry['fom']:.2e}\n"
            f"Parameters:\n```json\n{params_str}\n```\n"
            f"Measurements: Adc={entry.get('Adc_dB', '?'):.1f}dB, "
            f"GBW={entry.get('GBW_Hz', '?'):.0f}Hz, "
            f"PM={entry.get('PM_deg', '?'):.1f}deg"
        )

    def _update_program_best(
        self, program_path: Path, entry: dict
    ) -> None:
        """Update the 'Current Best' section of program.md after a kept improvement."""
        store = ProgramStore(program_path.parent, self._generate_program)
        store._path = program_path  # point to exact path
        store.update_best(entry, self._format_analog_best)

    def _update_program_learning(
        self, program_path: Path, insight: str
    ) -> None:
        """Append a learning to the 'Learned So Far' section."""
        store = ProgramStore(program_path.parent, self._generate_program)
        store._path = program_path
        store.update_learning(insight)

    def _update_program_strategy(
        self, program_path: Path, strategy: str
    ) -> None:
        """Replace the 'Strategy' section with updated strategy from the LLM."""
        store = ProgramStore(program_path.parent, self._generate_program)
        store._path = program_path
        store.update_strategy(strategy)

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _system_prompt(self, program_content: str) -> str:
        """Build the system prompt: program.md IS the system prompt."""
        return (
            f"You are an autonomous circuit design optimizer. Your program "
            f"is defined below. Follow it exactly.\n\n"
            f"{program_content}\n\n"
            f"RESPONSE FORMAT: You must respond with ONLY a JSON object "
            f"containing the next design parameters to try. No explanation, "
            f"no markdown fences, no commentary. Just the raw JSON.\n"
            f"Example: {{\"Ibias_uA\": 150.0, \"L_dp_um\": 3.0, ...}}"
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
                status = h.get("status", "kept" if h.get("kept") else "discarded")
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
        program_content: str,
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
                {"role": "system", "content": self._system_prompt(program_content)},
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

        content = response.choices[0].message.content or ""
        content = extract_json_from_response(content)
        params = json.loads(content)

        # Validate and clamp to design space
        space = self.topology.design_space()
        clean_params = {}
        for name, (lo, hi) in space.items():
            val = params.get(name)
            if val is None:
                val = self.topology.default_params().get(name, (lo + hi) / 2)
            clean_params[name] = max(lo, min(hi, float(val)))

        return clean_params

    # ------------------------------------------------------------------
    # SPICE evaluation
    # ------------------------------------------------------------------

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
                "status": "crash",
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
                "status": "crash",
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

    # ------------------------------------------------------------------
    # TSV logging (delegates to TsvLogger)
    # ------------------------------------------------------------------

    def _make_tsv_logger(self, tsv_path: Path) -> TsvLogger:
        return TsvLogger(
            tsv_path=tsv_path,
            param_cols=list(self.topology.design_space().keys()),
            measurement_cols=_ANALOG_MEASUREMENT_COLS,
        )

    def _write_tsv_header(self, tsv_path: Path):
        """Write TSV header line."""
        self._make_tsv_logger(tsv_path).write_header()

    def _append_tsv_row(self, tsv_path: Path, entry: dict):
        """Append one row to the TSV log."""
        self._make_tsv_logger(tsv_path).append_row(entry)

    # ------------------------------------------------------------------
    # Resume support (delegates to TsvLogger)
    # ------------------------------------------------------------------

    def _load_history(self, tsv_path: Path) -> tuple[list[dict], dict | None, int]:
        """Load history from an existing results.tsv for resume.

        Returns (history, best, start_eval).
        """
        return self._make_tsv_logger(tsv_path).load_history()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, work_dir: Path) -> AutoresearchResult:
        """Run the autonomous exploration loop.

        If work_dir contains program.md and results.tsv from a previous
        run, the loop resumes from where it left off.

        Loop:
        1. Read program.md (the persistent brain)
        2. Show LLM: program + history + current best
        3. LLM proposes next params (JSON response)
        4. Run SPICE evaluation
        5. If crash: log "crash" in TSV, move on
        6. If FoM > best_fom and valid: keep, update program.md
        7. Else: discard
        8. Log to results.tsv
        9. Repeat until budget exhausted (NEVER STOP)

        Returns AutoresearchResult with top-N designs for downstream use.
        """
        work_dir.mkdir(parents=True, exist_ok=True)

        program_store = self._make_program_store(work_dir)
        program_store.init()

        tsv_path = work_dir / "results.tsv"
        tsv_logger = self._make_tsv_logger(tsv_path)

        # Resume support: load existing history if present
        history, best, start_eval = tsv_logger.load_history()
        if not history:
            tsv_logger.write_header()
        kept_count = sum(1 for h in history if h.get("kept"))

        end_eval = start_eval + self.budget - 1

        logger.info(
            "Autoresearch: %s, model=%s, budget=%d (evals %d-%d)",
            self.topology.topology_name(), self.model, self.budget,
            start_eval, end_eval,
        )

        for eval_num in range(start_eval, end_eval + 1):
            t0 = time.monotonic()

            # 1. Read program.md (fresh each iteration -- it may have been updated)
            program_content = program_store.read()

            # 2. Propose next params
            try:
                params = await self._propose_params(
                    program_content, history, best, eval_num
                )
            except Exception as e:
                logger.warning("LLM proposal failed at eval %d: %s", eval_num, e)
                # Fall back to default params
                params = self.topology.default_params()

            # 3. Evaluate (with crash handling)
            try:
                entry = await self._evaluate(params, work_dir, eval_num)
            except Exception as e:
                # Crash -- log it, don't stop
                logger.error(
                    "Eval %d CRASHED: %s\n%s", eval_num, e,
                    traceback.format_exc(),
                )
                entry = {
                    "eval": eval_num,
                    "params": params,
                    "success": False,
                    "error": str(e),
                    "fom": 0.0,
                    "valid": False,
                    "violations": [],
                    "status": "crash",
                }
                history.append(entry)
                tsv_logger.append_row(entry)
                continue

            # 4. Keep or discard
            if entry["success"] and entry["valid"] and (
                best is None or entry["fom"] > best["fom"]
            ):
                entry["kept"] = True
                entry["status"] = "kept"
                best = entry.copy()
                kept_count += 1

                # Update program.md with new best
                program_store.update_best(entry, self._format_analog_best)

                # Add learning about what worked
                insight = (
                    f"Eval #{eval_num}: FoM improved to {entry['fom']:.2e} "
                    f"with {json.dumps(entry['params'])}"
                )
                program_store.update_learning(insight)

                logger.info(
                    "Eval %d: KEPT (FoM=%.2e, Adc=%.1fdB, GBW=%.0fHz, PM=%.1fdeg)",
                    eval_num, entry["fom"],
                    entry.get("Adc_dB", 0), entry.get("GBW_Hz", 0),
                    entry.get("PM_deg", 0),
                )
            else:
                entry["kept"] = False
                entry["status"] = "discarded"

                # Log why it was discarded as a learning
                if not entry["success"]:
                    reason = f"Eval #{eval_num}: crash -- {entry.get('error', 'unknown')}"
                    entry["status"] = "crash"
                elif not entry["valid"]:
                    viols = ", ".join(entry.get("violations", []))
                    reason = f"Eval #{eval_num}: invalid ({viols})"
                else:
                    reason = (
                        f"Eval #{eval_num}: valid but FoM={entry['fom']:.2e} "
                        f"< best {best['fom']:.2e}"
                    )

                # Only log non-trivial learnings (every ~5 evals to avoid bloat)
                if eval_num % 5 == 0 or not entry["success"]:
                    program_store.update_learning(reason)

                logger.debug(
                    "Eval %d: %s (fom=%.2e, valid=%s)",
                    eval_num, entry["status"], entry["fom"], entry["valid"],
                )

            history.append(entry)
            tsv_logger.append_row(entry)

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
