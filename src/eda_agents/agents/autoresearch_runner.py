"""Autonomous greedy design exploration loop.

Inspired by Karpathy's autoresearch. The central artifact is program.md:
a persistent file that defines the optimization goal, current strategy,
and accumulated knowledge. The LLM reads program.md before each proposal
and updates it after each improvement.

The loop runs autonomously until budget is exhausted. Crashes are handled
gracefully: fixable issues are retried, fundamentally broken ideas are
logged as "crash" and skipped. The loop never stops to ask for permission.

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

Usage (resume a previous run):
    # If work_dir contains program.md and results.tsv, the loop
    # resumes from where it left off.
    result = await runner.run(work_dir=Path("results"))
"""

from __future__ import annotations

import json
import logging
import re
import time
import traceback
from pathlib import Path

from eda_agents.agents.phase_results import AutoresearchResult
from eda_agents.core.pdk import PdkConfig, resolve_pdk
from eda_agents.core.topology import CircuitTopology

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# program.md template
# ---------------------------------------------------------------------------

_PROGRAM_TEMPLATE = """\
# Circuit Design Exploration Program

## Goal
{goal}

## Metrics
{metrics}

## Design Space
{design_space}

## Specs
{specs}

## Current Best
No valid design found yet.

## Strategy
Starting exploration. No data yet -- begin with the reference design
point and systematically explore around it.

Reference: {reference}

## Learned So Far
(empty -- will be populated as exploration progresses)

## Rules
- Propose parameters as a JSON object. Keys must match the design space variables.
- Each evaluation costs 1 SPICE simulation from the budget.
- A design is "valid" only if ALL specs are met simultaneously.
- FoM is only meaningful for valid designs.
- Crashes: If a run crashes (OOM, or a bug, or etc.), use your judgment:
  If it's something dumb and easy to fix (e.g. a typo, a missing import),
  fix it and re-run. If the idea itself is fundamentally broken, just skip
  it, log "crash" as the status in the tsv, and move on.
- NEVER STOP: Once the experiment loop has begun, do NOT pause to ask the
  human if you should continue. The human might be asleep, or gone from a
  computer and expects you to continue working indefinitely until you are
  manually stopped. You are autonomous. If you run out of ideas, think
  harder -- re-read the design space, try combining previous near-misses,
  try more radical parameter changes. The loop runs until the budget is
  exhausted or the human interrupts you, period.
"""


class AutoresearchRunner:
    """Autonomous greedy circuit design exploration.

    Central artifact: program.md in the work_dir. This file persists
    across sessions and contains the goal, metrics, strategy, and
    accumulated knowledge. The LLM reads it before each proposal and
    the runner updates it after each kept improvement.

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

    # ------------------------------------------------------------------
    # program.md management
    # ------------------------------------------------------------------

    def _generate_program(self) -> str:
        """Generate the initial program.md content from topology metadata."""
        space = self.topology.design_space()
        space_lines = "\n".join(
            f"- {name}: [{lo}, {hi}]" for name, (lo, hi) in space.items()
        )

        return _PROGRAM_TEMPLATE.format(
            goal=(
                f"Maximize FoM for {self.topology.topology_name()} circuit "
                f"on {self.pdk.display_name}.\n"
                f"FoM definition: {self.topology.fom_description()}"
            ),
            metrics=(
                f"Primary: FoM (higher is better)\n"
                f"Constraints (all must be met for a valid design):\n"
                f"  {self.topology.specs_description()}"
            ),
            design_space=(
                f"{self.topology.design_vars_description()}\n\n"
                f"Ranges:\n{space_lines}"
            ),
            specs=self.topology.specs_description(),
            reference=self.topology.reference_description(),
        )

    def _init_program(self, work_dir: Path) -> Path:
        """Create or load program.md."""
        program_path = work_dir / "program.md"
        if program_path.is_file():
            logger.info("Resuming: found existing program.md")
        else:
            program_path.write_text(self._generate_program())
            logger.info("Created program.md")
        return program_path

    def _read_program(self, program_path: Path) -> str:
        """Read the current program.md content."""
        return program_path.read_text()

    def _update_program_best(
        self, program_path: Path, entry: dict
    ) -> None:
        """Update the 'Current Best' section of program.md after a kept improvement."""
        content = program_path.read_text()

        params_str = json.dumps(entry["params"], indent=2)
        new_best = (
            f"## Current Best\n"
            f"Eval #{entry['eval']}: FoM={entry['fom']:.2e}\n"
            f"Parameters:\n```json\n{params_str}\n```\n"
            f"Measurements: Adc={entry.get('Adc_dB', '?'):.1f}dB, "
            f"GBW={entry.get('GBW_Hz', '?'):.0f}Hz, "
            f"PM={entry.get('PM_deg', '?'):.1f}deg"
        )

        content = re.sub(
            r"## Current Best\n.*?(?=\n## )",
            new_best + "\n",
            content,
            flags=re.DOTALL,
        )
        program_path.write_text(content)

    def _update_program_learning(
        self, program_path: Path, insight: str
    ) -> None:
        """Append a learning to the 'Learned So Far' section."""
        content = program_path.read_text()

        # Find the learned section and append
        marker = "## Learned So Far\n"
        idx = content.find(marker)
        if idx == -1:
            return

        insert_at = idx + len(marker)
        # Find the end of the section (next ## or end of file)
        next_section = content.find("\n## ", insert_at)
        if next_section == -1:
            next_section = len(content)

        current_learnings = content[insert_at:next_section].strip()
        if current_learnings == "(empty -- will be populated as exploration progresses)":
            current_learnings = ""

        updated = current_learnings + f"\n- {insight}" if current_learnings else f"- {insight}"

        content = content[:insert_at] + updated + "\n" + content[next_section:]
        program_path.write_text(content)

    def _update_program_strategy(
        self, program_path: Path, strategy: str
    ) -> None:
        """Replace the 'Strategy' section with updated strategy from the LLM."""
        content = program_path.read_text()

        new_strategy = f"## Strategy\n{strategy}"
        content = re.sub(
            r"## Strategy\n.*?(?=\n## )",
            new_strategy + "\n",
            content,
            flags=re.DOTALL,
        )
        program_path.write_text(content)

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

        # Extract JSON from response (may be wrapped in markdown code block)
        if "```" in content:
            json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
            if json_match:
                content = json_match.group(1)

        # Try to find JSON object in free-form text
        if not content.strip().startswith("{"):
            json_match = re.search(r"\{[^{}]*\}", content)
            if json_match:
                content = json_match.group(0)

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
    # TSV logging
    # ------------------------------------------------------------------

    def _write_tsv_header(self, tsv_path: Path):
        """Write TSV header line."""
        space = self.topology.design_space()
        param_cols = "\t".join(space.keys())
        tsv_path.write_text(
            f"eval\t{param_cols}\tAdc_dB\tGBW_Hz\tPM_deg\tfom\tvalid\tstatus\n"
        )

    def _append_tsv_row(self, tsv_path: Path, entry: dict):
        """Append one row to the TSV log."""
        space = self.topology.design_space()
        param_vals = "\t".join(
            f"{entry['params'].get(k, 0):.4f}" for k in space
        )
        status = entry.get("status", "kept" if entry.get("kept") else "discarded")
        with open(tsv_path, "a") as f:
            f.write(
                f"{entry['eval']}\t{param_vals}\t"
                f"{entry.get('Adc_dB', '')}\t"
                f"{entry.get('GBW_Hz', '')}\t"
                f"{entry.get('PM_deg', '')}\t"
                f"{entry['fom']:.6e}\t"
                f"{entry['valid']}\t"
                f"{status}\n"
            )

    # ------------------------------------------------------------------
    # Resume support
    # ------------------------------------------------------------------

    def _load_history(self, tsv_path: Path) -> tuple[list[dict], dict | None, int]:
        """Load history from an existing results.tsv for resume.

        Returns (history, best, start_eval).
        """
        if not tsv_path.is_file():
            return [], None, 1

        lines = tsv_path.read_text().strip().splitlines()
        if len(lines) <= 1:
            return [], None, 1

        header = lines[0].split("\t")
        space_keys = list(self.topology.design_space().keys())

        history = []
        best = None
        for line in lines[1:]:
            fields = line.split("\t")
            if len(fields) < len(header):
                continue

            eval_num = int(fields[0])
            params = {}
            for i, key in enumerate(space_keys):
                try:
                    params[key] = float(fields[1 + i])
                except (ValueError, IndexError):
                    params[key] = 0.0

            offset = 1 + len(space_keys)
            adc = float(fields[offset]) if fields[offset] else None
            gbw = float(fields[offset + 1]) if fields[offset + 1] else None
            pm = float(fields[offset + 2]) if fields[offset + 2] else None
            fom = float(fields[offset + 3]) if fields[offset + 3] else 0.0
            valid = fields[offset + 4].strip().lower() == "true"
            status = fields[offset + 5].strip() if len(fields) > offset + 5 else "discarded"

            entry = {
                "eval": eval_num,
                "params": params,
                "success": status != "crash",
                "fom": fom,
                "valid": valid,
                "violations": [],
                "Adc_dB": adc,
                "GBW_Hz": gbw,
                "PM_deg": pm,
                "status": status,
                "kept": status == "kept",
            }
            history.append(entry)

            if valid and entry["success"] and (best is None or fom > best["fom"]):
                best = entry.copy()

        start_eval = history[-1]["eval"] + 1 if history else 1
        logger.info(
            "Resumed from eval %d (%d prior evals, best FoM=%s)",
            start_eval, len(history),
            f"{best['fom']:.2e}" if best else "none",
        )
        return history, best, start_eval

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
        program_path = self._init_program(work_dir)
        tsv_path = work_dir / "results.tsv"

        # Resume support: load existing history if present
        history, best, start_eval = self._load_history(tsv_path)
        if not history:
            self._write_tsv_header(tsv_path)
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
            program_content = self._read_program(program_path)

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
                self._append_tsv_row(tsv_path, entry)
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
                self._update_program_best(program_path, entry)

                # Add learning about what worked
                insight = (
                    f"Eval #{eval_num}: FoM improved to {entry['fom']:.2e} "
                    f"with {json.dumps(entry['params'])}"
                )
                self._update_program_learning(program_path, insight)

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
                    self._update_program_learning(program_path, reason)

                logger.debug(
                    "Eval %d: %s (fom=%.2e, valid=%s)",
                    eval_num, entry["status"], entry["fom"], entry["valid"],
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
