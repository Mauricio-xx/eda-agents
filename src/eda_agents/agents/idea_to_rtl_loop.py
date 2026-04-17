"""Iterative idea-to-chip loop with sim + lint critique feedback.

Wraps :func:`eda_agents.agents.idea_to_rtl.generate_rtl_draft` so a
single natural-language idea can survive multiple LibreLane attempts.
The single-shot path inside ``generate_rtl_draft`` already uses
Claude's built-in 3-iteration retry inside a single CC CLI turn (see
``build_from_spec_prompt`` Phase 6 "FIX AND ITERATE"). That ceiling
holds for designs up to ~2k cells (S11 evidence). For ~10k+ cells the
prompt-internal retry runs out — the agent has no way to step back,
inspect failure logs, and re-architect between LibreLane runs.

This loop is the next layer up: between turns, it harvests the
previous attempt's failure signal (sim assertion, yosys error, GL-sim
mismatch), injects a critique prompt header authored by the
``digital.critique_sim_failure`` and ``digital.critique_synth_lint``
skills, and re-invokes ``generate_rtl_draft`` with the same
``work_dir`` so the agent sees its own prior artefacts and can apply
a minimal patch.

Stop conditions, in order of priority:

* ``IdeaToRTLResult.all_passed`` is True (convergence).
* ``turn == max_turns`` (budget exhausted).
* ``total_cost_usd >= max_budget_usd`` (cost cap, optional).
* The harness reports a non-recoverable error (CLI not found,
  rate-limit, etc.) — surfaced as ``reason="error"`` and the loop
  exits with the partial result attached.

The loop deliberately does NOT teach the agent to skip verification:
the critique skills forbid disabling sim, DRC, LVS, or STA. Per the
``feedback_full_verification`` auto-memory entry, an honest fail with
documented root cause is preferred to a green run that bypasses gates.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from eda_agents.agents.idea_to_rtl import (
    IdeaToRTLResult,
    generate_rtl_draft,
)
from eda_agents.skills.registry import get_skill

logger = logging.getLogger(__name__)


LoopReason = Literal[
    "converged",       # sim + flow + GL sim all passed
    "budget_exhausted",  # max_turns reached without convergence
    "cost_cap",        # total_cost_usd hit max_budget_usd
    "error",           # harness or infra failure mid-loop
]


@dataclass
class LoopIteration:
    """Per-turn record. Mirrors the IdeaToRTLResult subset we need
    for critique + reporting, plus loop-specific bookkeeping.
    """

    turn: int
    success: bool
    all_passed: bool
    sim_status: str  # "pass" | "fail" | "skipped" | "missing"
    flow_status: str  # "pass" | "fail" | "missing"
    gl_sim_status: str  # "pass" | "fail" | "skipped" | "missing"
    failure_excerpt: str = ""
    cost_usd: float = 0.0
    duration_s: float = 0.0
    num_turns: int = 0  # CC CLI sub-turns inside this loop turn
    work_subdir: str | None = None  # the LibreLane runs/RUN_* used
    error: str | None = None


@dataclass
class IdeaToRTLLoopResult:
    """Outcome of an :func:`run_idea_to_rtl_loop` invocation.

    ``idea_result`` is the LAST turn's :class:`IdeaToRTLResult` and is
    what callers that previously consumed ``generate_rtl_draft``
    output keep using — so the loop is a transparent wrapper for
    success-path consumers. ``iterations`` carries the per-turn
    failure trail for honest-fail diagnostics.
    """

    idea_result: IdeaToRTLResult
    iterations: list[LoopIteration] = field(default_factory=list)
    total_cost_usd: float = 0.0
    converged_turn: int | None = None
    budget_exhausted: bool = False
    reason: LoopReason = "budget_exhausted"

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation. Paths become strings."""
        return {
            "iterations": [asdict(it) for it in self.iterations],
            "total_cost_usd": self.total_cost_usd,
            "converged_turn": self.converged_turn,
            "budget_exhausted": self.budget_exhausted,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_idea_to_rtl_loop(
    description: str,
    design_name: str,
    work_dir: Path | str,
    *,
    max_turns: int = 8,
    max_budget_usd: float | None = None,
    pdk: str = "gf180mcu",
    pdk_root: str | Path | None = None,
    librelane_python: str = "python3",
    allow_dangerous: bool = False,
    cli_path: str = "claude",
    timeout_s: int = 7200,
    model: str | None = None,
    skip_gl_sim: bool = False,
    tb_framework: str = "iverilog",
) -> IdeaToRTLLoopResult:
    """Drive the idea-to-chip pipeline iteratively until convergence or budget.

    Parameters
    ----------
    description, design_name, work_dir, pdk, pdk_root,
    librelane_python, allow_dangerous, cli_path, timeout_s, model,
    skip_gl_sim, tb_framework:
        Pass-throughs to :func:`generate_rtl_draft`. The loop calls it
        once per turn with these unchanged; only the description gets
        a critique header on turns 2+.
    max_turns:
        Hard ceiling on loop iterations. ``max_turns=1`` makes the
        loop equivalent to one ``generate_rtl_draft`` call — useful
        for tests asserting byte-equivalence with the single-shot
        path.
    max_budget_usd:
        Optional cumulative spend cap. The loop sums each turn's
        ``cost_usd`` and stops when ``total >= max_budget_usd``. Set
        per-turn ``timeout_s`` separately on ``generate_rtl_draft``.
    """
    work_dir = Path(work_dir).resolve()
    iterations: list[LoopIteration] = []
    total_cost = 0.0
    last_result: IdeaToRTLResult | None = None
    reason: LoopReason = "budget_exhausted"
    converged_turn: int | None = None

    for turn in range(1, max_turns + 1):
        if turn == 1:
            turn_description = description
        else:
            assert last_result is not None  # previous iteration ran
            turn_description = _build_critique_description(
                base_description=description,
                previous_result=last_result,
                previous_iteration=iterations[-1],
                turn=turn,
                work_dir=work_dir,
            )

        logger.info(
            "IdeaToRTLLoop: turn %d/%d (cost so far: $%.2f)",
            turn, max_turns, total_cost,
        )

        t0 = time.monotonic()
        # Per-turn budget cap: when a global cost cap exists, cap the
        # next turn at the remainder so a single runaway invocation
        # cannot blow through the loop budget. Honour the caller's
        # per-turn timeout exactly.
        per_turn_budget = (
            max_budget_usd - total_cost
            if max_budget_usd is not None
            else None
        )

        result = await generate_rtl_draft(
            description=turn_description,
            design_name=design_name,
            work_dir=work_dir,
            pdk=pdk,
            pdk_root=pdk_root,
            librelane_python=librelane_python,
            complexity="complex",  # signals to the prompt that this is a hard run
            allow_dangerous=allow_dangerous,
            cli_path=cli_path,
            timeout_s=timeout_s,
            max_budget_usd=per_turn_budget,
            model=model,
            skip_gl_sim=skip_gl_sim,
            dry_run=False,
            tb_framework=tb_framework,
        )

        duration = time.monotonic() - t0
        last_result = result
        total_cost += result.cost_usd

        iteration = _build_iteration_record(
            turn=turn,
            result=result,
            duration_s=duration,
        )
        iterations.append(iteration)

        if result.all_passed:
            logger.info(
                "IdeaToRTLLoop: converged on turn %d (cost: $%.2f)",
                turn, total_cost,
            )
            converged_turn = turn
            reason = "converged"
            break

        # Hard infra error → bail. The agent itself should not retry
        # past CLI not found / rate-limit / timeout; the human
        # operator decides.
        if result.error and _is_infra_error(result.error):
            logger.warning(
                "IdeaToRTLLoop: infra error on turn %d, aborting: %s",
                turn, result.error,
            )
            reason = "error"
            break

        if (
            max_budget_usd is not None
            and total_cost >= max_budget_usd
        ):
            logger.warning(
                "IdeaToRTLLoop: cost cap $%.2f reached on turn %d",
                max_budget_usd, turn,
            )
            reason = "cost_cap"
            break
    else:
        # Loop exhausted max_turns without break.
        reason = "budget_exhausted"

    assert last_result is not None  # max_turns >= 1 guaranteed by the loop

    loop_result = IdeaToRTLLoopResult(
        idea_result=last_result,
        iterations=iterations,
        total_cost_usd=total_cost,
        converged_turn=converged_turn,
        budget_exhausted=(reason == "budget_exhausted"),
        reason=reason,
    )

    # Best-effort attach + persist a structured summary so honest-fail
    # diagnostics survive even when callers only have the IdeaToRTLResult.
    last_result.loop_result = loop_result  # type: ignore[attr-defined]
    try:
        (work_dir / "loop_result.json").write_text(
            json.dumps(loop_result.to_dict(), indent=2, default=str)
        )
    except OSError:
        logger.debug(
            "IdeaToRTLLoop: could not write loop_result.json under %s",
            work_dir,
        )

    return loop_result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_iteration_record(
    *,
    turn: int,
    result: IdeaToRTLResult,
    duration_s: float,
) -> LoopIteration:
    """Pull the loop-relevant fields out of a per-turn IdeaToRTLResult."""
    sim_status = _classify_sim(result)
    flow_status = "pass" if result.success else "fail"
    gl_sim_status = _classify_gl_sim(result)
    failure_excerpt = _extract_failure_excerpt(result)

    return LoopIteration(
        turn=turn,
        success=result.success,
        all_passed=result.all_passed,
        sim_status=sim_status,
        flow_status=flow_status,
        gl_sim_status=gl_sim_status,
        failure_excerpt=failure_excerpt,
        cost_usd=result.cost_usd,
        duration_s=duration_s,
        num_turns=result.num_turns,
        work_subdir=str(result.run_dir) if result.run_dir else None,
        error=result.error,
    )


def _classify_sim(result: IdeaToRTLResult) -> str:
    """Best-effort: did pre-synth simulation succeed?

    The single-shot prompt's Phase 2 makes the agent run RTL sim
    before LibreLane. ``result.result_text`` carries the agent's own
    summary. Use a coarse keyword scan rather than parsing the
    LibreLane log tree because the agent's output is the closest
    thing to a structured pre-synth verdict we get back.
    """
    text = (result.result_text or "").upper()
    if "PASS@1" in text or "RTL SIM PASS" in text or "SIM PASSED" in text:
        return "pass"
    if "RTL SIM FAIL" in text or "SIM FAILED" in text or "ASSERTION FAILED" in text:
        return "fail"
    return "missing"


def _classify_gl_sim(result: IdeaToRTLResult) -> str:
    if result.gl_sim is None:
        return "missing"
    if result.gl_sim.get("skipped"):
        return "skipped"
    return "pass" if result.gl_sim.get("all_passed") else "fail"


def _extract_failure_excerpt(result: IdeaToRTLResult, *, max_chars: int = 1800) -> str:
    """Pull a short, copy-able failure excerpt for the next turn's prompt.

    Priority order:
    1. ``result.error`` (harness-level).
    2. ``result.gl_sim.post_synth.error`` / ``post_pnr.error``.
    3. The tail of ``result.result_text`` (the agent's own report).
    """
    parts: list[str] = []
    if result.error:
        parts.append(f"[harness error] {result.error}")
    if result.gl_sim:
        ps = result.gl_sim.get("post_synth", {}) or {}
        pp = result.gl_sim.get("post_pnr", {}) or {}
        if ps.get("error"):
            parts.append(f"[post-synth GL sim] {ps['error']}")
        if pp.get("error"):
            parts.append(f"[post-PnR GL sim] {pp['error']}")
    if result.result_text:
        # Take the tail; the bottom of the agent's report is usually
        # the verdict + the lines explaining why the verdict is what
        # it is.
        tail = result.result_text.strip().splitlines()[-30:]
        parts.append("[agent report tail]\n" + "\n".join(tail))
    excerpt = "\n\n".join(parts)
    if len(excerpt) > max_chars:
        excerpt = excerpt[-max_chars:]
    return excerpt


def _is_infra_error(error: str) -> bool:
    """Detect non-recoverable harness errors that should abort the loop.

    A CLI binary missing, a 429 rate-limit, or a hard timeout will
    not improve by re-running. Everything else (sim fail, lint fail,
    DRC violation, LVS mismatch) IS recoverable and the loop should
    keep trying.
    """
    indicators = (
        "Claude CLI not found",
        "Claude CLI not found at resolved path",
        "Timeout after",
        "Subprocess error",
        "429",
        "rate limit",
        "rate-limit",
        "rate_limited",
    )
    e = error or ""
    return any(ind.lower() in e.lower() for ind in indicators)


def _build_critique_description(
    *,
    base_description: str,
    previous_result: IdeaToRTLResult,
    previous_iteration: LoopIteration,
    turn: int,
    work_dir: Path,
) -> str:
    """Stitch the critique skills + previous failure excerpt onto the spec.

    The agent receives the augmented description as its Phase 1 spec
    via ``build_from_spec_prompt``. The work_dir already contains
    the previous turn's ``src/``, ``tb/``, ``runs/RUN_*/`` artefacts;
    the prompt header tells the agent to apply a MINIMAL patch on
    top of those rather than starting from scratch.
    """
    # Pick the most relevant critique skill. If the failure looks like
    # a yosys / lint issue, use the synth-lint critique; otherwise use
    # the sim-failure critique. Both critiques reinforce the
    # full-verification mandate so we never teach the agent to skip
    # gates.
    sim_failed = previous_iteration.sim_status == "fail"
    gl_sim_failed = previous_iteration.gl_sim_status == "fail"
    flow_failed = previous_iteration.flow_status == "fail"
    excerpt_lower = previous_iteration.failure_excerpt.lower()
    looks_like_synth_lint = any(
        marker in excerpt_lower
        for marker in (
            "yosys",
            "synth",
            "logic loop",
            "combinational loop",
            "width mismatch",
            "is used but not driven",
            "multiple drivers",
            "lint",
        )
    )

    skill_names: list[str] = []
    if sim_failed or gl_sim_failed:
        skill_names.append("digital.critique_sim_failure")
    if looks_like_synth_lint or (flow_failed and not (sim_failed or gl_sim_failed)):
        skill_names.append("digital.critique_synth_lint")
    if not skill_names:
        # Fall back to the sim-failure critique — it is the
        # broadest-applicability discipline.
        skill_names.append("digital.critique_sim_failure")

    critique_blocks = [get_skill(name).render() for name in skill_names]

    header = (
        f"## PREVIOUS ITERATION FAILURE (turn {turn - 1} of "
        f"this loop)\n\n"
        f"sim={previous_iteration.sim_status} "
        f"flow={previous_iteration.flow_status} "
        f"gl_sim={previous_iteration.gl_sim_status}\n\n"
        f"Failure excerpt:\n```\n{previous_iteration.failure_excerpt}\n```\n\n"
        f"## ITERATIVE LOOP CONTEXT\n\n"
        f"You are inside an iterative idea-to-chip loop "
        f"(turn {turn}). The work directory `{work_dir}` already "
        "contains the previous attempt's `src/`, `tb/`, "
        "`config.yaml`, and `runs/RUN_*/` artefacts. Your job is "
        "to apply a MINIMAL patch on top of those artefacts so the "
        "next LibreLane run reaches signoff. Do NOT start from "
        "scratch; do NOT rewrite files that don't need to change.\n\n"
        "The critique discipline you must follow:\n\n"
        + "\n\n---\n\n".join(critique_blocks)
        + "\n\n## ORIGINAL DESIGN SPEC\n\n"
    )

    return header + base_description
