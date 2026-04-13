"""Autonomous greedy exploration loop for digital RTL-to-GDS flows.

Mirrors ``AutoresearchRunner`` (analog/SPICE) but evaluates by running
LibreLane partial flows instead of SPICE simulations.  Shares the
``ProgramStore`` / ``TsvLogger`` infrastructure from
``_autoresearch_core`` to persist state, resume, and log results.

The evaluation loop:
    1. LLM proposes flow config overrides (JSON)
    2. Config is written to the design project
    3. ``LibreLaneRunner.run_flow(to=stop_after)`` executes
    4. ``FlowMetrics.from_librelane_run_dir`` extracts metrics
    5. ``design.compute_fom(metrics)`` scores the result
    6. Greedy keep/discard

Per-eval cost is 10-100x higher than analog SPICE (~5-20 min vs ~2s),
so the default budget is low (5) and ``stop_after=ROUTE`` skips signoff
during exploration.  Full signoff is only run on kept designs at the end
(not yet implemented — Phase 4/6 territory).

Design space handling differs from analog:
- Analog: continuous (lo, hi) ranges with float clamping.
- Digital: discrete lists (non-monotonic response) with nearest-value
  snapping.  ``DigitalDesign.design_space()`` returns
  ``dict[str, list | tuple]``.

Usage:
    runner = DigitalAutoresearchRunner(
        design=FazyRvHachureDesign(),
        model="openrouter/anthropic/claude-haiku-4.5",
        budget=5,
    )
    result = await runner.run(work_dir=Path("digital_results"))

Mock mode (no LibreLane, for testing):
    runner = DigitalAutoresearchRunner(
        design=FazyRvHachureDesign(),
        model="test-model",
        budget=3,
        use_mock_metrics=Path("fixtures/mock_flow_metrics.json"),
    )
    result = await runner.run(work_dir=Path("test_results"))
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
from eda_agents.core.digital_design import DigitalDesign
from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.flow_stage import FlowStage
from eda_agents.core.stages.physical_slice_runner import STAGE_TO_LIBRELANE

logger = logging.getLogger(__name__)

# Digital measurement columns for TSV logging
_DIGITAL_MEASUREMENT_COLS = [
    "wns_worst_ns",
    "cell_count",
    "die_area_um2",
    "power_mw",
    "wire_length_um",
]


class DigitalAutoresearchRunner:
    """Autonomous greedy exploration for digital RTL-to-GDS flows.

    Parameters
    ----------
    design : DigitalDesign
        Design to optimize (defines design space, FoM, config paths).
    model : str
        LiteLLM model identifier for the proposal LLM.
    budget : int
        Maximum number of LibreLane evaluations.
    stop_after : FlowStage
        Stop after this stage during exploration.  Default: ROUTE
        (skips signoff for speed).  Must be in STAGE_TO_LIBRELANE.
    dedup : bool
        Reject proposals whose parameters exactly match a prior eval.
    use_mock_metrics : Path or None
        If set, load FlowMetrics from this JSON file instead of running
        LibreLane.  For testing only.
    top_n : int
        Number of top designs to return.
    """

    def __init__(
        self,
        design: DigitalDesign,
        model: str = "openrouter/anthropic/claude-haiku-4.5",
        budget: int = 5,
        stop_after: FlowStage = FlowStage.ROUTE,
        dedup: bool = True,
        use_mock_metrics: Path | None = None,
        top_n: int = 3,
    ):
        self.design = design
        self.model = model
        self.budget = budget
        self.stop_after = stop_after
        self.dedup = dedup
        self.use_mock_metrics = use_mock_metrics
        self.top_n = top_n

    # ------------------------------------------------------------------
    # program.md
    # ------------------------------------------------------------------

    def _generate_program(self) -> str:
        space = self.design.design_space()
        space_lines = []
        for name, values in space.items():
            if isinstance(values, list):
                space_lines.append(f"- {name}: one of {values}")
            elif isinstance(values, tuple) and len(values) == 2:
                space_lines.append(f"- {name}: [{values[0]}, {values[1]}]")

        return generate_program_content(
            domain_name=self.design.project_name(),
            pdk_display_name="GF180MCU",
            fom_description=self.design.fom_description(),
            specs_description=self.design.specs_description(),
            design_vars_description=self.design.design_vars_description(),
            design_space_lines="\n".join(space_lines),
            reference_description=self.design.reference_description(),
        )

    def _make_program_store(self, work_dir: Path) -> ProgramStore:
        return ProgramStore(work_dir, self._generate_program)

    def _make_tsv_logger(self, tsv_path: Path) -> TsvLogger:
        return TsvLogger(
            tsv_path=tsv_path,
            param_cols=list(self.design.design_space().keys()),
            measurement_cols=_DIGITAL_MEASUREMENT_COLS,
        )

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _system_prompt(self, program_content: str) -> str:
        space = self.design.design_space()
        example_keys = list(space.keys())
        example = ", ".join(f'"{k}": ...' for k in example_keys)
        return (
            f"You are an autonomous digital design optimizer. Your program "
            f"is defined below. Follow it exactly.\n\n"
            f"{program_content}\n\n"
            f"RESPONSE FORMAT: You must respond with ONLY a JSON object "
            f"containing the next design parameters to try. No explanation, "
            f"no markdown fences, no commentary. Just the raw JSON.\n"
            f"Example: {{{example}}}"
        )

    def _build_proposal_prompt(
        self,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> str:
        parts = [f"Evaluation {eval_num}/{self.budget}.\n"]

        if best:
            parts.append(
                f"Current best (eval #{best['eval']}): "
                f"FoM={best['fom']:.2e}, valid={best['valid']}\n"
                f"Params: {json.dumps(best['params'], indent=2)}\n"
                f"Measurements: WNS={best.get('wns_worst_ns', '?')}ns, "
                f"cells={best.get('cell_count', '?')}, "
                f"area={best.get('die_area_um2', '?')}um2, "
                f"power={best.get('power_mw', '?')}mW\n"
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
    ) -> dict[str, float | int]:
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

        try:
            response = await litellm.acompletion(
                **kwargs, response_format={"type": "json_object"}
            )
        except Exception as e:
            err_str = f"{type(e).__name__}: {e}"
            if "response_format" in err_str or "UnsupportedParams" in err_str:
                logger.info(
                    "response_format not supported by %s, retrying without",
                    self.model,
                )
                response = await litellm.acompletion(**kwargs)
            else:
                raise

        content = response.choices[0].message.content or ""
        content = extract_json_from_response(content)
        params = json.loads(content)

        return self._clamp_params(params)

    def _clamp_params(self, params: dict) -> dict[str, float | int]:
        """Validate and snap proposed params to the design space.

        Discrete lists: snap to nearest valid value.
        Continuous ranges: clamp to [lo, hi].
        """
        space = self.design.design_space()
        default = self.design.default_config()
        clean: dict[str, float | int] = {}

        for name, values in space.items():
            val = params.get(name)
            if val is None:
                clean[name] = default.get(name, values[0] if isinstance(values, list) else values[0])
                continue

            val = float(val)

            if isinstance(values, list):
                # Snap to nearest value in the discrete list
                clean[name] = min(values, key=lambda v: abs(v - val))
            elif isinstance(values, tuple) and len(values) == 2:
                lo, hi = values
                clean[name] = max(lo, min(hi, val))
            else:
                clean[name] = val

        return clean

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def _evaluate(
        self,
        params: dict[str, float | int],
        work_dir: Path,
        eval_num: int,
    ) -> dict:
        """Run LibreLane flow and extract metrics."""
        # Mock mode: load metrics from fixture file
        if self.use_mock_metrics:
            return self._evaluate_mock(params, eval_num)

        from eda_agents.core.librelane_runner import LibreLaneRunner

        # Apply config overrides
        config_path = self.design.librelane_config()
        runner = LibreLaneRunner(
            project_dir=self.design.project_dir(),
            config_file=config_path.name,
            pdk_root=str(self.design.pdk_root() or ""),
            timeout_s=1800,
        )

        # Write exploration params to config
        for key, value in params.items():
            try:
                runner.modify_config(key, value)
            except ValueError:
                logger.warning("Key %s not in SAFE_CONFIG_KEYS, skipping", key)

        # Also apply design-specific overrides
        for key, value in self.design.flow_config_overrides().items():
            runner.modify_config(key, value, force=True)

        # Determine the LibreLane stop step
        if self.stop_after in STAGE_TO_LIBRELANE:
            _, to_step = STAGE_TO_LIBRELANE[self.stop_after]
        else:
            to_step = None  # full flow

        tag = f"eval_{eval_num:03d}"
        flow_result = runner.run_flow(tag=tag, to=to_step)

        if not flow_result.success:
            return {
                "eval": eval_num,
                "params": params,
                "success": False,
                "error": flow_result.error or "LibreLane flow failed",
                "fom": 0.0,
                "valid": False,
                "violations": [],
                "status": "crash",
            }

        # Extract metrics from the run dir
        run_dir = Path(flow_result.run_dir) if flow_result.run_dir else None
        if run_dir is None or not run_dir.is_dir():
            return {
                "eval": eval_num,
                "params": params,
                "success": False,
                "error": "No run directory found after flow",
                "fom": 0.0,
                "valid": False,
                "violations": [],
                "status": "crash",
            }

        metrics = FlowMetrics.from_librelane_run_dir(run_dir)
        fom = self.design.compute_fom(metrics)
        valid, violations = self.design.check_validity(metrics)

        return {
            "eval": eval_num,
            "params": params,
            "success": True,
            "fom": fom,
            "valid": valid,
            "violations": violations,
            "wns_worst_ns": metrics.wns_worst_ns,
            "cell_count": metrics.synth_cell_count,
            "die_area_um2": metrics.die_area_um2,
            "power_mw": metrics.power_total_mw,
            "wire_length_um": metrics.wire_length_um,
            "run_dir": str(run_dir),
            "run_time_s": flow_result.run_time_s,
        }

    def _evaluate_mock(self, params: dict, eval_num: int) -> dict:
        """Load metrics from a JSON fixture instead of running LibreLane."""
        raw = json.loads(self.use_mock_metrics.read_text())

        # Support both flat dict and list-of-dicts (one per eval)
        if isinstance(raw, list):
            idx = (eval_num - 1) % len(raw)
            data = raw[idx]
        else:
            data = raw

        metrics = FlowMetrics(**{
            k: v for k, v in data.items()
            if k in FlowMetrics.__dataclass_fields__
        })
        fom = self.design.compute_fom(metrics)
        valid, violations = self.design.check_validity(metrics)

        return {
            "eval": eval_num,
            "params": params,
            "success": True,
            "fom": fom,
            "valid": valid,
            "violations": violations,
            "wns_worst_ns": metrics.wns_worst_ns,
            "cell_count": metrics.synth_cell_count,
            "die_area_um2": metrics.die_area_um2,
            "power_mw": metrics.power_total_mw,
            "wire_length_um": metrics.wire_length_um,
        }

    # ------------------------------------------------------------------
    # Dedup
    # ------------------------------------------------------------------

    def _is_duplicate(self, params: dict, history: list[dict]) -> bool:
        """Check if params exactly match a prior evaluation."""
        if not self.dedup:
            return False
        for h in history:
            if h["params"] == params:
                return True
        return False

    # ------------------------------------------------------------------
    # Format helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_digital_best(entry: dict) -> str:
        """Format the Current Best section body for digital metrics."""
        params_str = json.dumps(entry["params"], indent=2)
        return (
            f"Eval #{entry['eval']}: FoM={entry['fom']:.2e}\n"
            f"Parameters:\n```json\n{params_str}\n```\n"
            f"Measurements: WNS={entry.get('wns_worst_ns', '?')}ns, "
            f"cells={entry.get('cell_count', '?')}, "
            f"area={entry.get('die_area_um2', '?')}um2, "
            f"power={entry.get('power_mw', '?')}mW, "
            f"wire={entry.get('wire_length_um', '?')}um"
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, work_dir: Path) -> AutoresearchResult:
        """Run the autonomous exploration loop.

        Mirrors ``AutoresearchRunner.run()`` with digital-specific
        evaluation and discrete design space handling.
        """
        work_dir.mkdir(parents=True, exist_ok=True)

        program_store = self._make_program_store(work_dir)
        program_store.init()

        tsv_path = work_dir / "results.tsv"
        tsv_logger = self._make_tsv_logger(tsv_path)

        history, best, start_eval = tsv_logger.load_history()
        if not history:
            tsv_logger.write_header()
        kept_count = sum(1 for h in history if h.get("kept"))

        end_eval = start_eval + self.budget - 1

        logger.info(
            "DigitalAutoresearch: %s, model=%s, budget=%d (evals %d-%d), "
            "stop_after=%s",
            self.design.project_name(),
            self.model,
            self.budget,
            start_eval,
            end_eval,
            self.stop_after.name,
        )

        for eval_num in range(start_eval, end_eval + 1):
            t0 = time.monotonic()

            program_content = program_store.read()

            # Propose next params
            try:
                params = await self._propose_params(
                    program_content, history, best, eval_num
                )
            except Exception as e:
                logger.warning("LLM proposal failed at eval %d: %s", eval_num, e)
                params = self._clamp_params(self.design.default_config())

            # Dedup check
            if self._is_duplicate(params, history):
                logger.info("Eval %d: duplicate params, skipping", eval_num)
                entry = {
                    "eval": eval_num,
                    "params": params,
                    "success": False,
                    "error": "duplicate params",
                    "fom": 0.0,
                    "valid": False,
                    "violations": [],
                    "status": "dedup",
                }
                history.append(entry)
                tsv_logger.append_row(entry)
                continue

            # Evaluate
            try:
                entry = await self._evaluate(params, work_dir, eval_num)
            except Exception as e:
                logger.error(
                    "Eval %d CRASHED: %s\n%s",
                    eval_num,
                    e,
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

            # Keep or discard
            if entry["success"] and entry["valid"] and (
                best is None or entry["fom"] > best["fom"]
            ):
                entry["kept"] = True
                entry["status"] = "kept"
                best = entry.copy()
                kept_count += 1

                program_store.update_best(entry, self._format_digital_best)

                insight = (
                    f"Eval #{eval_num}: FoM improved to {entry['fom']:.2e} "
                    f"(WNS={entry.get('wns_worst_ns', '?')}ns, "
                    f"cells={entry.get('cell_count', '?')}) "
                    f"with {json.dumps(entry['params'])}"
                )
                program_store.update_learning(insight)

                logger.info(
                    "Eval %d: KEPT (FoM=%.2e, WNS=%sns, cells=%s, area=%sum2)",
                    eval_num,
                    entry["fom"],
                    entry.get("wns_worst_ns", "?"),
                    entry.get("cell_count", "?"),
                    entry.get("die_area_um2", "?"),
                )
            else:
                entry["kept"] = False
                entry["status"] = "discarded"

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

                if eval_num % 3 == 0 or not entry["success"]:
                    program_store.update_learning(reason)

                logger.debug(
                    "Eval %d: %s (fom=%.2e, valid=%s)",
                    eval_num,
                    entry["status"],
                    entry["fom"],
                    entry["valid"],
                )

            history.append(entry)
            tsv_logger.append_row(entry)

            elapsed = time.monotonic() - t0
            logger.debug("Eval %d took %.1fs", eval_num, elapsed)

        # Top-N
        valid_entries = sorted(
            [h for h in history if h.get("valid") and h.get("success")],
            key=lambda x: x["fom"],
            reverse=True,
        )
        top_n = valid_entries[: self.top_n]

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
