"""Hierarchical design-space exploration runner for SSTADEX-style flows.

Sibling to :class:`eda_agents.agents.autoresearch_runner.AutoresearchRunner`
in that it persists artefacts in the same ``program.md`` /
``results.tsv`` convention, but the inner loop is fundamentally
different. Where AutoresearchRunner runs an LLM-in-the-loop greedy
search over a topology's *parameter* space, ``HierarchicalDseRunner``
wraps the deterministic ``dfs()`` explorer from
``eda_agents.topologies.sstadex``:

  * The macromodel and its specifications are the "topology".
  * One ``run()`` produces the full Pareto frontier in a single
    deterministic pass (no LLM call is required).
  * The optional ``run_greedy()`` mode uses the AutoresearchRunner
    backend dispatch (``litellm`` or ``cc_cli``) to iterate over a
    user-supplied macromodel-knob space; each iteration is one
    ``dfs()`` call and the runner keeps the configuration that
    produces the best Pareto FoM.

The persistence layout intentionally mirrors AutoresearchRunner so
that tooling that already consumes ``program.md`` / ``results.tsv``
(visualisers, MCP tools, downstream agents) keeps working.

Why not just call ``dfs()`` directly? Two reasons:

  1. Persistence + reproducibility. The runner records every Pareto
     point as a TSV row keyed by ``configuration_id`` so callers can
     re-load a prior run, pick a Pareto corner by ID, and feed it to
     ngspice for cross-validation. ``examples/17_sstadex_pareto_ihp.py``
     does exactly this.
  2. Backend uniformity. The MCP server exposes hierarchical-DSE
     runs alongside autoresearch runs; the latter advertises a
     ``litellm``/``cc_cli`` selector and the former needs one too for
     parity.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import sympy as sym

from eda_agents.agents._autoresearch_core import (
    ProgramStore,
    TsvLogger,
    extract_json_from_response,
)
from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.topologies.sstadex.dfs import ExplorationResult, dfs
from eda_agents.topologies.sstadex.macromodel import Macromodel

logger = logging.getLogger(__name__)


# Supported proposal backends. We mirror AutoresearchRunner so callers
# can swap runners without re-learning the constructor surface.
_SUPPORTED_BACKENDS: tuple[str, ...] = ("litellm", "cc_cli")


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


@dataclass
class HierarchicalDseResult:
    """Result of one ``HierarchicalDseRunner.run()`` call.

    For single-shot mode, ``best_knobs`` simply echoes the knobs that
    were used to build the macromodel. For ``run_greedy``, it is the
    knob set whose ``dfs()`` produced the highest FoM.
    """

    best_knobs: dict[str, Any]
    best_fom: float
    pareto_rows: int
    total_rows: int
    macromodel_name: str
    work_dir: str
    results_tsv: str
    program_md: str
    pareto_csv: str | None = None
    total_evals: int = 1
    kept: int = 1
    discarded: int = 0
    history: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    cost_usd: float | None = None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


_PROGRAM_TEMPLATE = """# Hierarchical DSE for {macromodel_name}

## Goal
Reproduce the SSTADEx-style Pareto frontier for {macromodel_name} and
keep the rows that meet all specification floors / ceilings.

## Design knobs
{design_knobs}

## Macromodel structure
- ports:        {ports}
- primitives:   {primitives}
- submacros:    {submacros}
- specs:        {specs}
- opt specs:    {opt_specs}
- shared_nodes: {shared_nodes}

## Metrics tracked per run
- pareto_rows        (number of points on the Pareto frontier)
- total_rows         (rows after spec + propagated_conditions filters)
- best_<spec_name>   (highest value for each opt spec on the Pareto)
- min_area_m         (smallest sum-of-widths achieved on the Pareto)

## Current Best
{current_best}

## Strategy
Run ``dfs()`` once per knob configuration; record one TSV row per
Pareto point keyed by (configuration_id, row_id) so downstream code
can replay any operating point through ngspice for cross-validation.
"""


def _summarize_macromodel(m: Macromodel) -> dict[str, Any]:
    return {
        "ports": list(m.ports),
        "primitives": [p.name for p in m.primitives],
        "submacros": [s.name for s in m.submacromodels],
        "specs": [s.name for s in m.specifications],
        "opt_specs": [s.name for s in m.opt_specifications],
        "shared_nodes": dict(m.shared_nodes),
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


MacromodelBuilder = Callable[..., Macromodel]


class HierarchicalDseRunner:
    """Run SSTADEx-style hierarchical DSE with eda-agents persistence.

    Parameters
    ----------
    macromodel_builder
        Callable that returns a fresh ``Macromodel`` given the user's
        knob dict. Signature: ``builder(lut, **knobs) -> Macromodel``.
        The builder is responsible for instantiating primitives,
        binding port voltages, calling ``Library.get`` etc. -- the
        runner just consumes the assembled macromodel.
    lut
        ``GmIdLookup`` for the active PDK. Passed verbatim to
        ``builder`` and to ``dfs()``.
    knob_defaults
        Knob values for single-shot ``run()`` mode. Optional in
        ``run_greedy()`` mode where the LLM proposes knobs.
    knob_ranges
        Per-knob ``(low, high)`` ranges used in greedy mode for both
        LLM prompting and post-proposal clamping. Required only when
        ``run_greedy()`` is invoked.
    fom_fn
        Callable that maps a Pareto DataFrame to a scalar FoM. Default
        picks the maximum value of the first ``opt_specifications`` spec.
    model
        Backend model identifier. ``"zai/GLM-4.5-Flash"`` for LiteLLM
        cost-efficient runs; ``"claude-sonnet-4-6"`` etc. for the
        CC CLI backend.
    backend
        ``"litellm"`` or ``"cc_cli"``. Single-shot ``run()`` ignores
        the backend; greedy ``run_greedy()`` honours it.
    """

    def __init__(
        self,
        macromodel_builder: MacromodelBuilder,
        lut: GmIdLookup,
        *,
        knob_defaults: dict[str, Any] | None = None,
        knob_ranges: dict[str, tuple[float, float]] | None = None,
        fom_fn: Callable[[pd.DataFrame, Macromodel], float] | None = None,
        model: str = "zai/GLM-4.5-Flash",
        backend: str = "litellm",
    ) -> None:
        if backend not in _SUPPORTED_BACKENDS:
            raise ValueError(
                f"backend must be one of {_SUPPORTED_BACKENDS}, got "
                f"{backend!r}"
            )
        self.builder = macromodel_builder
        self.lut = lut
        self.knob_defaults = dict(knob_defaults or {})
        self.knob_ranges = dict(knob_ranges or {})
        self.fom_fn = fom_fn or _default_fom_fn
        self.model = model
        self.backend = backend

        self._tokens_this_run = 0
        self._cost_usd_this_run = 0.0
        self._work_dir: Path | None = None

    # ------------------------------------------------------------------
    # Single-shot run
    # ------------------------------------------------------------------

    def run(
        self,
        work_dir: Path,
        knobs: dict[str, Any] | None = None,
        *,
        debug: bool = False,
    ) -> HierarchicalDseResult:
        """One deterministic dfs() invocation. Persists results.tsv +
        program.md."""
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        self._work_dir = work_dir

        knobs = dict(self.knob_defaults)
        knobs.update(knobs or {})

        return self._do_one_eval(work_dir, knobs, eval_num=1, debug=debug)

    # ------------------------------------------------------------------
    # Greedy run (LLM-in-the-loop)
    # ------------------------------------------------------------------

    async def run_greedy(
        self,
        work_dir: Path,
        budget: int,
        *,
        debug: bool = False,
    ) -> HierarchicalDseResult:
        """LLM-in-the-loop hierarchical DSE.

        Each iteration:
          1. The LLM proposes a knob dict within ``self.knob_ranges``.
          2. ``self.builder`` constructs a macromodel from the knobs.
          3. ``dfs()`` runs once.
          4. ``self.fom_fn`` extracts a scalar FoM from the Pareto.
          5. Keep if FoM exceeds the current best; persist either way.

        Persistence semantics match ``AutoresearchRunner.run``:
        ``program.md`` accumulates strategy + best; ``results.tsv``
        records one row per iteration with ``knobs JSON`` + ``fom``.
        """
        if not self.knob_ranges:
            raise ValueError(
                "run_greedy() requires knob_ranges set on the runner."
            )
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        self._work_dir = work_dir
        self._tokens_this_run = 0
        self._cost_usd_this_run = 0.0

        program_store = ProgramStore(
            work_dir,
            lambda: _PROGRAM_TEMPLATE.format(
                macromodel_name="<deferred>",
                design_knobs=json.dumps(self.knob_ranges, indent=2),
                ports="<deferred>",
                primitives="<deferred>",
                submacros="<deferred>",
                specs="<deferred>",
                opt_specs="<deferred>",
                shared_nodes="<deferred>",
                current_best="(no iterations yet)",
            ),
        )
        program_store.init()

        tsv_path = work_dir / "results.tsv"
        tsv_logger = TsvLogger(
            tsv_path=tsv_path,
            param_cols=list(self.knob_ranges.keys()),
            measurement_cols=["pareto_rows", "total_rows", "best_fom"],
        )
        history, best_entry, start_eval = tsv_logger.load_history()
        if not history:
            tsv_logger.write_header()
        kept = sum(1 for h in history if h.get("kept"))

        end_eval = start_eval + budget - 1
        for eval_num in range(start_eval, end_eval + 1):
            t0 = time.monotonic()
            try:
                knobs = await self._propose_knobs(
                    program_store.read(), history, best_entry, eval_num
                )
            except Exception as exc:
                logger.warning(
                    "LLM proposal failed at eval %d: %s", eval_num, exc
                )
                knobs = self._clamp_to_ranges(self.knob_defaults)
            try:
                result = self._do_one_eval(
                    work_dir, knobs, eval_num=eval_num, debug=debug,
                    persist_program=False,
                )
            except Exception as exc:
                logger.error("Eval %d crashed: %s", eval_num, exc)
                entry = {
                    "eval": eval_num,
                    "params": knobs,
                    "success": False,
                    "error": str(exc),
                    "fom": 0.0,
                    "valid": False,
                    "violations": [],
                    "status": "crash",
                }
                history.append(entry)
                tsv_logger.append_row(entry)
                continue

            entry = {
                "eval": eval_num,
                "params": knobs,
                "success": True,
                "fom": result.best_fom,
                "valid": result.pareto_rows > 0,
                "violations": [],
                "pareto_rows": result.pareto_rows,
                "total_rows": result.total_rows,
                "best_fom": result.best_fom,
            }
            if entry["valid"] and (
                best_entry is None or entry["fom"] > best_entry["fom"]
            ):
                entry["kept"] = True
                entry["status"] = "kept"
                best_entry = entry.copy()
                kept += 1
                program_store.update_best(
                    entry,
                    lambda e: (
                        f"Eval #{e['eval']}: FoM={e['fom']:.4g}, "
                        f"pareto_rows={e.get('pareto_rows', 0)}, "
                        f"params={json.dumps(e['params'])}"
                    ),
                )
            else:
                entry["kept"] = False
                entry["status"] = "discarded"
            history.append(entry)
            tsv_logger.append_row(entry)
            logger.info(
                "Hier-DSE eval %d: fom=%.4g pareto=%d (%.1fs)",
                eval_num, entry["fom"], entry.get("pareto_rows", 0),
                time.monotonic() - t0,
            )

        return HierarchicalDseResult(
            best_knobs=best_entry["params"] if best_entry else {},
            best_fom=best_entry["fom"] if best_entry else 0.0,
            pareto_rows=int(best_entry.get("pareto_rows", 0)) if best_entry else 0,
            total_rows=int(best_entry.get("total_rows", 0)) if best_entry else 0,
            macromodel_name="<greedy>",
            work_dir=str(work_dir),
            results_tsv=str(tsv_path),
            program_md=str(work_dir / "program.md"),
            pareto_csv=None,
            total_evals=len(history),
            kept=kept,
            discarded=len(history) - kept,
            history=history,
            total_tokens=self._tokens_this_run,
            cost_usd=(
                self._cost_usd_this_run
                if self.backend == "cc_cli"
                else None
            ),
        )

    # ------------------------------------------------------------------
    # Shared eval body
    # ------------------------------------------------------------------

    def _do_one_eval(
        self,
        work_dir: Path,
        knobs: dict[str, Any],
        *,
        eval_num: int,
        debug: bool,
        persist_program: bool = True,
    ) -> HierarchicalDseResult:
        macromodel = self.builder(self.lut, **knobs)
        if not isinstance(macromodel, Macromodel):
            raise TypeError(
                f"macromodel_builder must return a Macromodel; got "
                f"{type(macromodel).__name__}"
            )

        result: ExplorationResult = dfs(macromodel, self.lut, debug=debug)

        pareto_df = result.masked_df
        total_rows = len(result.df.index)
        pareto_rows = len(pareto_df.index)
        fom = float(self.fom_fn(pareto_df, macromodel))

        # Persist results.tsv (one row per Pareto point).
        results_tsv = work_dir / "results.tsv"
        pareto_csv = work_dir / "pareto.csv"
        _persist_dfs(pareto_df, results_tsv, pareto_csv, eval_num)

        program_md = work_dir / "program.md"
        if persist_program:
            program_md.write_text(
                _PROGRAM_TEMPLATE.format(
                    macromodel_name=macromodel.name,
                    design_knobs=json.dumps(knobs, indent=2, default=_json_default),
                    current_best=_format_best_block(pareto_df, macromodel, fom),
                    **_summarize_macromodel(macromodel),
                ),
                encoding="utf-8",
            )

        return HierarchicalDseResult(
            best_knobs=knobs,
            best_fom=fom,
            pareto_rows=pareto_rows,
            total_rows=total_rows,
            macromodel_name=macromodel.name,
            work_dir=str(work_dir),
            results_tsv=str(results_tsv),
            program_md=str(program_md),
            pareto_csv=str(pareto_csv) if pareto_rows > 0 else None,
        )

    # ------------------------------------------------------------------
    # Backend dispatch (greedy mode only)
    # ------------------------------------------------------------------

    async def _propose_knobs(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict[str, Any]:
        if self.backend == "cc_cli":
            return await self._propose_via_cc_cli(
                program_content, history, best, eval_num
            )
        return await self._propose_via_litellm(
            program_content, history, best, eval_num
        )

    async def _propose_via_litellm(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict[str, Any]:
        import litellm

        user_prompt = self._build_user_prompt(history, best, eval_num)
        system_prompt = self._build_system_prompt(program_content)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 1024,
            "temperature": 0.7,
        }
        try:
            response = await litellm.acompletion(
                **kwargs, response_format={"type": "json_object"}
            )
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            if "response_format" in err or "UnsupportedParams" in err:
                response = await litellm.acompletion(**kwargs)
            else:
                raise
        usage = getattr(response, "usage", None)
        if usage is not None:
            self._tokens_this_run += int(getattr(usage, "total_tokens", 0) or 0)
        content = response.choices[0].message.content or ""
        content = extract_json_from_response(content)
        knobs = json.loads(content)
        return self._clamp_to_ranges(knobs)

    async def _propose_via_cc_cli(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict[str, Any]:
        from eda_agents.agents.claude_code_harness import ClaudeCodeHarness

        work_dir = self._work_dir or Path.cwd()
        system_prompt = self._build_system_prompt(program_content)
        user_prompt = self._build_user_prompt(history, best, eval_num)
        full = (
            f"{system_prompt}\n\n---\n\n{user_prompt}\n\n---\n\n"
            "Reply with one PROPOSAL_BEGIN/PROPOSAL_END block containing "
            "valid JSON for the knobs.\nExample:\nPROPOSAL_BEGIN\n"
            "{\"key\": 1.23}\nPROPOSAL_END"
        )
        harness = ClaudeCodeHarness(
            prompt=full, work_dir=work_dir, model=self.model
        )
        result = await harness.run()
        self._cost_usd_this_run += result.total_cost_usd or 0.0
        if not result.success:
            raise RuntimeError(
                f"Claude CLI failed at eval {eval_num}: {result.error}"
            )
        import re

        block = re.search(
            r"PROPOSAL_BEGIN\s*(.+?)\s*PROPOSAL_END",
            result.result_text,
            re.DOTALL,
        )
        if block:
            knobs = json.loads(block.group(1))
        else:
            knobs = json.loads(extract_json_from_response(result.result_text))
        return self._clamp_to_ranges(knobs)

    def _build_system_prompt(self, program_content: str) -> str:
        return (
            "You are an analog hierarchical DSE planner. Your job is to "
            "propose macromodel-level knob values that drive a faithful "
            "SSTADEx-style dfs() exploration. The program below describes "
            "the macromodel, its specifications, and the metrics tracked.\n\n"
            f"{program_content}\n\n"
            "Respond with a JSON object that ONLY contains knob keys "
            f"from {sorted(self.knob_ranges)}. No commentary."
        )

    def _build_user_prompt(
        self,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> str:
        lines = [f"Iteration {eval_num}.\n"]
        if best:
            lines.append(
                f"Current best (eval #{best['eval']}): FoM={best['fom']:.4g}, "
                f"pareto_rows={best.get('pareto_rows', 0)}, "
                f"params={json.dumps(best['params'])}\n"
            )
        else:
            lines.append("No valid configuration found yet.\n")
        if history:
            lines.append("\nHistory (last 10):\n")
            for h in history[-10:]:
                status = h.get("status", "?")
                lines.append(
                    f"  #{h['eval']}: fom={h.get('fom', 0):.4g} "
                    f"pareto={h.get('pareto_rows', 0)} ({status}) "
                    f"{json.dumps(h.get('params', {}))}\n"
                )
        lines.append(
            f"\nKnob ranges: {json.dumps({k: list(v) for k, v in self.knob_ranges.items()})}\n"
            "Propose the next knob JSON."
        )
        return "".join(lines)

    def _clamp_to_ranges(self, knobs: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for k, (lo, hi) in self.knob_ranges.items():
            v = knobs.get(k, self.knob_defaults.get(k, (lo + hi) / 2.0))
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = float(self.knob_defaults.get(k, (lo + hi) / 2.0))
            clean[k] = max(float(lo), min(float(hi), v))
        return clean


# ---------------------------------------------------------------------------
# Default FoM
# ---------------------------------------------------------------------------


def _default_fom_fn(pareto_df: pd.DataFrame, macromodel: Macromodel) -> float:
    """Default FoM: peak value of the first ``opt_specifications`` spec.

    Returns ``0.0`` when the Pareto is empty or no opt spec is set --
    caller may then mark the config as discarded.
    """
    if pareto_df is None or len(pareto_df.index) == 0:
        return 0.0
    opt_specs = macromodel.opt_specifications or macromodel.specifications
    if not opt_specs:
        return float(len(pareto_df.index))
    spec = opt_specs[0]
    if spec.name not in pareto_df.columns:
        return float(len(pareto_df.index))
    series = pd.to_numeric(pareto_df[spec.name], errors="coerce").dropna()
    if series.empty:
        return 0.0
    if spec.opt_goal == "min":
        return float(series.min())
    return float(series.max())


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _persist_dfs(
    pareto_df: pd.DataFrame,
    results_tsv: Path,
    pareto_csv: Path,
    eval_num: int,
) -> None:
    """Write the Pareto DataFrame to TSV + CSV.

    TSV (``results.tsv``) is the canonical AutoresearchRunner-style
    log: one row per Pareto point, with ``configuration_id`` linking
    rows back to a parent run. CSV is a verbatim Pareto dump (sympy
    Symbol column names converted to strings) so external tools that
    expect comma-separated input can ingest it without preprocessing.
    """
    if pareto_df is None or len(pareto_df.index) == 0:
        return

    stringified = pareto_df.copy()
    stringified.columns = [_col_to_str(c) for c in stringified.columns]
    stringified.insert(0, "configuration_id", eval_num)
    stringified.insert(1, "row_id", np.arange(len(stringified)))

    # TSV: header on first write, append rows. Use \t separator with
    # the unified column order so resume / append works cleanly.
    write_header = not results_tsv.exists() or results_tsv.stat().st_size == 0
    stringified.to_csv(
        results_tsv,
        sep="\t",
        index=False,
        mode="a",
        header=write_header,
    )
    # CSV gets a fresh write each call (one Pareto front per file).
    stringified.to_csv(pareto_csv, index=False)


def _col_to_str(col: Any) -> str:
    if isinstance(col, sym.Symbol):
        return col.name
    return str(col)


def _format_best_block(
    pareto_df: pd.DataFrame, macromodel: Macromodel, fom: float
) -> str:
    if pareto_df is None or len(pareto_df.index) == 0:
        return "(empty Pareto -- no valid configurations)"
    lines = [f"FoM={fom:.4g} ({len(pareto_df.index)} Pareto points)"]
    opt_specs = macromodel.opt_specifications or macromodel.specifications
    for spec in opt_specs:
        if spec.name in pareto_df.columns:
            arr = pd.to_numeric(pareto_df[spec.name], errors="coerce").dropna()
            if not arr.empty:
                lines.append(
                    f"- {spec.name}: min={arr.min():.4g}, max={arr.max():.4g}, "
                    f"opt_goal={spec.opt_goal}"
                )
    if "area" in pareto_df.columns:
        arr = pd.to_numeric(pareto_df["area"], errors="coerce").dropna()
        if not arr.empty:
            lines.append(
                f"- area (m): min={arr.min():.4g}, max={arr.max():.4g}"
            )
    return "\n".join(lines)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return str(obj)
