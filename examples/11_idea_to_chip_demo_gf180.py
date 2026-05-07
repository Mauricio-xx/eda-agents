#!/usr/bin/env python3
"""End-to-end demo of the digital idea-to-chip flow on GF180MCU.

This script wraps the existing library entry points so a single
invocation exercises the three concrete demo cases the user cares
about, and emits FoM evolution plots alongside the canonical
artefacts (``loop_result.json`` for the idea loop, ``results.tsv`` for
autoresearch). It is the script-mode counterpart of the
``gf180-idea-to-chip`` agent.

Cases:

- ``idea_loop``: fresh natural-language idea (single-bin Goertzel
  resonator in IEEE 754 binary32 floating-point) driven through
  ``run_idea_to_rtl_loop`` with sim and flow critique between turns.
  Emits ``loop_result.json`` plus ``idea_loop_status.png`` and
  ``idea_loop_cost.png``. The default description is intentionally
  ambitious for GF180MCU at 25 MHz: the agent has to size the die,
  choose between iterative (multi-cycle) FP units and full pipelines,
  and survive FP corner cases (NaN, denormals, rounding). Allow the
  loop several turns; honest-fail is an acceptable scientific result
  if the agent runs out of budget.
- ``idea_to_optimize``: the autoresearch chain. Phase 1 is identical
  to ``idea_loop`` (same Goertzel FP32 spec, same sim-in-the-loop).
  Phase 2 takes whatever ``config.yaml`` + RTL phase 1 produced
  (regardless of whether phase 1 converged) and feeds it into
  :class:`DigitalAutoresearchRunner` as a :class:`GenericDesign`.
  Autoresearch then iterates over flow knobs (PL_TARGET_DENSITY_PCT,
  CLOCK_PERIOD) seeing the prior eval metrics on each turn — this is
  the RL-emulated feedback loop where the LLM is the policy and
  ``program.md`` plus ``results.tsv`` are the persistent memory.
  Phase 2 runs unconditionally so partial / blocked phase-1 outputs
  still feed the optimizer.
  Pair with ``--skip-phase-idea`` to point at an existing phase-1
  workspace (``case_dir/phase_idea/`` must already contain
  ``config.yaml`` and the RTL/TB tree) and run only phase 2; useful
  for re-optimizing an existing converged design without spending
  another idea_loop turn.
- ``fazyrv_flow``: improve the FazyRV-Hachure RV32I SoC baseline by
  sweeping flow knobs (``PL_TARGET_DENSITY_PCT``, ``CLOCK_PERIOD``).
  Emits ``results.tsv`` plus ``fom_evolution.png``,
  ``metrics_grid.png``, ``params_evolution.png``.
- ``fazyrv_hybrid``: same baseline, but the agent is allowed to
  micro-edit RTL (resource sharing, FSM re-encoding) on top of flow
  knobs. Same plots; longer wall time.
- ``all``: sequential run of the three above plus a top-level
  ``summary.md``.

Wall-time envelope (rough, for scheduling). Both backends run in CLI
mode under a subscription quota, so per-turn USD cost is irrelevant
and not tracked here; the loop's only correctness gate is the cocotb
testbench plus the LibreLane signoff metrics (DRC / LVS / setup /
hold). Failing tests block the loop from converging regardless of
how many turns are spent.

- ``--dry-run``: ~5 s. Copies fixture artefacts into the work
  directory and exercises the plotting library only; no LLM, no
  LibreLane.
- ``idea_loop`` (Goertzel FP32) under ``cc_cli``: 60-120 min
  LibreLane wall at ``--budget 6``. Larger die area (~800x800 um)
  versus the wafer-space template default; FP units alone are
  ~10-20 k cells. Honest-fail is plausible at this complexity; raise
  ``--budget`` to 8 if you want more headroom.
- ``fazyrv_flow``: 45-100 min LibreLane wall (4 evaluations of the
  full bare-block flow per eval: synth + P&R + Magic/KLayout DRC +
  Netgen LVS + STAPostPNR + GL sim post-synth + GL sim post-PnR
  with SDF).
- ``fazyrv_hybrid``: 80-200 min LibreLane wall (4 evaluations,
  RTL micro-edits plus flow knobs, same full bare-block flow each).
- ``idea_to_optimize``: phase 1 wall is identical to ``idea_loop``
  (60-120 min); phase 2 wall is identical to ``fazyrv_flow``
  (45-100 min). Total roughly 100-220 min. Pass
  ``--skip-phase-idea`` to skip phase 1 wall when the workspace is
  pre-staged.

Stop-after policy:

The runner runs the full bare-block LibreLane flow per eval (no
chip-top, no padring, no precheck). That gives macro-level signoff
metrics (DRC, LVS, antenna, STA per PVT corner) plus post-synth and
post-PnR GL sim gates. A design is only ``valid`` when every gate
passes; FoM is 0.0 otherwise. There is intentionally no
``--stop-after`` knob: skipping signoff hides the failure modes the
loop is supposed to detect.

Verification gate (always on, regardless of backend):

- The cocotb testbench MUST pass against the RTL stage.
- The flow MUST close timing on every PVT corner LibreLane reports.
- DRC, LVS, antenna and setup/hold violations MUST all be 0.
- Post-synth and post-PnR (with SDF) GL sim MUST pass against the
  same testbench. The loop refuses to mark a turn as ``all_passed``
  unless every gate is green.

Usage:

    # Dry run (no LLM / no LibreLane); exercises the wiring + plots.
    python examples/11_idea_to_chip_demo_gf180.py --case all --dry-run

    # Live Goertzel FP32 run on Claude Code (Opus 4.7).
    python examples/11_idea_to_chip_demo_gf180.py \\
        --case idea_loop --backend cc_cli --budget 6 \\
        --allow-dangerous

    # Live FazyRV flow-knob exploration on OpenCode (gpt codex 5.3).
    python examples/11_idea_to_chip_demo_gf180.py \\
        --case fazyrv_flow --backend opencode --model <gpt-codex-5.3-id> \\
        --budget 4

Requires (live runs only):

    pip install -e ".[adk,plots,mcp]"
    export PDK_ROOT=/path/to/gf180mcu
    export EDA_AGENTS_ALLOW_DANGEROUS=1   # paired with --allow-dangerous
    scripts/fetch_digital_designs.sh       # for fazyrv_* cases
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"

DEFAULT_WORK_DIR = "rtl2gds_results/idea_to_chip_demo"
DEFAULT_DESCRIPTION = (
    "Single-bin Goertzel resonator computing the squared magnitude of one "
    "target frequency bin from an 8-bit signed PCM input stream, using "
    "IEEE 754 binary32 (single-precision) floating-point arithmetic for "
    "the recurrence and the final magnitude.\n\n"
    "Architecture requirements:\n"
    "- Streaming interface: 1 sample per `valid_in` strobe, output `done` "
    "  pulses high for one cycle when the result is ready after N samples.\n"
    "- Goertzel recurrence: s_k = x_k + 2 * cos(omega) * s_{k-1} - s_{k-2}, "
    "  with s_0 = s_{-1} = 0.0f and the constant 2*cos(omega) provided as "
    "  a pre-computed FP32 input port `coeff_2cosw`.\n"
    "- Final magnitude: |Y|^2 = s_N^2 + s_{N-1}^2 - coeff_2cosw * s_N * "
    "  s_{N-1}, emitted as a 32-bit IEEE 754 binary32 on `mag_sq_out`.\n"
    "- Block length N is parametrised (default 16); a `block_len` input "
    "  port lets the testbench vary it.\n"
    "- Sub-blocks: an iterative (multi-cycle) FP32 multiplier, an "
    "  iterative FP32 adder, and a finite state machine that schedules the "
    "  Goertzel recurrence over multiple clock cycles per sample. Iterative "
    "  units keep the area within reach on GF180MCU 7T5V0 standard cells "
    "  (target die roughly 800 x 800 um at 25 MHz).\n"
    "- IEEE 754 corner cases: NaN propagation, +/- Inf, subnormals "
    "  (flush-to-zero is acceptable if documented in the cocotb test), "
    "  round-to-nearest-even on the final mantissa.\n"
    "- Synchronous active-low reset on `rst_n` clears every state register "
    "  to +0.0f; outputs are zeroed during reset.\n"
    "- Top module name MUST be 'demo_goertzel_fp32'.\n\n"
    "The cocotb testbench MUST exercise: a pure tone whose frequency hits "
    "the target bin (expect large magnitude), a pure tone off the target "
    "bin (expect small magnitude), an all-zero input (expect 0.0f), and at "
    "least one NaN propagation case. Compare floating-point results with "
    "an explicit absolute-and-relative tolerance (no exact bit equality). "
    "All checks via plain Python assertions per the "
    "digital.cocotb_testbench skill rules so the same testbench runs "
    "against RTL, post-synth gate-level, and post-PnR gate-level (with "
    "SDF) without modification."
)
DEFAULT_DESIGN_NAME = "demo_goertzel_fp32"

CASE_PLOT_DIR_NAME = "plots"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_allow_dangerous(args: argparse.Namespace) -> bool:
    """Surface a clear error if --allow-dangerous is missing the env gate."""
    if not args.allow_dangerous:
        return False
    if os.environ.get("EDA_AGENTS_ALLOW_DANGEROUS") != "1":
        print(
            "--allow-dangerous requires EDA_AGENTS_ALLOW_DANGEROUS=1 in the "
            "environment (double-gating).",
            file=sys.stderr,
        )
        sys.exit(2)
    return True


def _resolve_pdk_root(arg_value: str | None) -> str | None:
    if arg_value:
        return arg_value
    return os.environ.get("PDK_ROOT")


def _resolve_runner_model_kwargs(
    args: argparse.Namespace,
) -> dict[str, str]:
    """Resolve ``model`` / ``opencode_model`` kwargs for the runner.

    The backend-dispatch refactor in ``_propose_params`` means
    ``cc_cli`` and ``opencode`` backends never read ``runner.model``
    for proposals; they go through the corresponding CLI harness with
    its own model selection (subscription default for cc_cli, the
    ``--model`` flag for opencode). Defaulting ``runner.model`` to
    gemini in those cases is misleading, both in the printed log and
    if any code path ever falls back to LiteLLM.

    Decision tree:

    * ``--backend cc_cli`` no ``--model`` -> omit ``model`` (constructor
      default of ``openrouter/anthropic/claude-haiku-4.5`` applies but
      is unused by the cc_cli proposal path).
    * ``--backend opencode --model openai/gpt-5.3-codex`` -> set both
      ``model`` and ``opencode_model`` to that string. The
      ``opencode_model`` is the one actually consumed.
    * ``--backend litellm`` / ``adk`` no ``--model`` -> default to
      ``openrouter/google/gemini-2.5-flash`` (legacy behaviour).
    * Any backend with ``--model`` -> use that string verbatim.
    """
    out: dict[str, str] = {}
    if args.model:
        out["model"] = args.model
        if args.backend == "opencode":
            out["opencode_model"] = args.model
    elif args.backend in ("litellm", "adk"):
        out["model"] = "openrouter/google/gemini-2.5-flash"
    return out


def _emit_plots(
    case: str,
    *,
    artefact_path: Path,
    plots_dir: Path,
    design_label: str,
) -> dict[str, Path]:
    """Dispatch to the right plot helper for the given case."""
    from eda_agents.utils.plot_autoresearch import (
        plot_autoresearch_evolution,
        plot_idea_loop_evolution,
    )

    plots_dir.mkdir(parents=True, exist_ok=True)
    if case == "idea_loop":
        return plot_idea_loop_evolution(
            artefact_path, plots_dir, design_label=design_label
        )
    return plot_autoresearch_evolution(
        artefact_path, plots_dir, design_label=design_label
    )


# ---------------------------------------------------------------------------
# Dry-run path: copy fixtures, exercise plotting only
# ---------------------------------------------------------------------------


def _dry_run_case(
    case: str,
    *,
    case_dir: Path,
    emit_plots: bool,
) -> dict[str, Any]:
    """Materialise canonical fixture artefacts and (optionally) plots."""
    case_dir.mkdir(parents=True, exist_ok=True)

    if case == "idea_to_optimize":
        # Stage both phases from the fixtures so the chained plot
        # path (idea-loop status/cost + FoM/metrics evolution) is
        # exercised end-to-end without any LLM or LibreLane.
        from eda_agents.utils.plot_autoresearch import (
            plot_autoresearch_evolution,
            plot_idea_loop_evolution,
        )

        phase_idea_dir = case_dir / "phase_idea"
        phase_optimize_dir = case_dir / "phase_optimize"
        phase_idea_dir.mkdir(parents=True, exist_ok=True)
        phase_optimize_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            FIXTURES_DIR / "loop_result_sample.json",
            phase_idea_dir / "loop_result.json",
        )
        shutil.copyfile(
            FIXTURES_DIR / "autoresearch_results_sample.tsv",
            phase_optimize_dir / "results.tsv",
        )

        plots: dict[str, dict[str, str]] = {}
        if emit_plots:
            plots_root = case_dir / CASE_PLOT_DIR_NAME
            p1 = plot_idea_loop_evolution(
                phase_idea_dir / "loop_result.json",
                plots_root / "phase_idea",
                design_label=f"{DEFAULT_DESIGN_NAME} (phase_idea)",
            )
            plots["phase_idea"] = {k: str(v) for k, v in p1.items()}
            p2 = plot_autoresearch_evolution(
                phase_optimize_dir / "results.tsv",
                plots_root / "phase_optimize",
                design_label=f"{DEFAULT_DESIGN_NAME} (phase_optimize)",
            )
            plots["phase_optimize"] = {k: str(v) for k, v in p2.items()}
        return {
            "case": case,
            "dry_run": True,
            "phase1_artefact": str(phase_idea_dir / "loop_result.json"),
            "phase2_artefact": str(phase_optimize_dir / "results.tsv"),
            "plots": plots,
        }

    if case == "idea_loop":
        src = FIXTURES_DIR / "loop_result_sample.json"
        dst = case_dir / "loop_result.json"
        shutil.copyfile(src, dst)
        artefact = dst
        design_label = DEFAULT_DESIGN_NAME
    else:
        src = FIXTURES_DIR / "autoresearch_results_sample.tsv"
        dst = case_dir / "results.tsv"
        shutil.copyfile(src, dst)
        artefact = dst
        design_label = case

    out: dict[str, Any] = {
        "case": case,
        "dry_run": True,
        "artefact": str(artefact),
        "design_label": design_label,
        "plots": {},
    }
    if emit_plots:
        plot_paths = _emit_plots(
            case,
            artefact_path=artefact,
            plots_dir=case_dir / CASE_PLOT_DIR_NAME,
            design_label=design_label,
        )
        out["plots"] = {k: str(v) for k, v in plot_paths.items()}
    return out


# ---------------------------------------------------------------------------
# Live cases
# ---------------------------------------------------------------------------


async def run_case_idea_loop(
    args: argparse.Namespace, *, case_dir: Path
) -> dict[str, Any]:
    """Drive a fresh-idea sim-in-the-loop run for the Goertzel FP32 demo."""
    from eda_agents.agents.idea_to_rtl_loop import run_idea_to_rtl_loop

    pdk_root = _resolve_pdk_root(args.pdk_root)
    work_dir = case_dir
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"[idea_loop] starting loop, max_turns={args.budget}")
    t0 = time.monotonic()
    loop_result = await run_idea_to_rtl_loop(
        description=DEFAULT_DESCRIPTION,
        design_name=DEFAULT_DESIGN_NAME,
        work_dir=work_dir,
        max_turns=args.budget,
        max_budget_usd=args.max_budget_usd,
        pdk="gf180mcu",
        pdk_root=pdk_root,
        librelane_python=args.librelane_python,
        backend=args.backend,
        allow_dangerous=args.allow_dangerous,
        cli_path=args.cli_path,
        timeout_s=args.timeout_s,
        model=args.model,
        tb_framework="cocotb",
    )
    elapsed = time.monotonic() - t0
    artefact = work_dir / "loop_result.json"

    summary = {
        "case": "idea_loop",
        "wall_time_s": elapsed,
        "reason": loop_result.reason,
        "converged_turn": loop_result.converged_turn,
        "all_passed": loop_result.idea_result.all_passed,
        "tb_validated": (
            loop_result.idea_result.gl_sim is not None
            and loop_result.idea_result.gl_sim.get("all_passed", False)
        ),
        "artefact": str(artefact) if artefact.is_file() else None,
        "gds_path": (
            str(loop_result.idea_result.gds_path)
            if loop_result.idea_result.gds_path
            else None
        ),
    }
    print(
        f"[idea_loop] done: reason={summary['reason']}, "
        f"converged_turn={summary['converged_turn']}, "
        f"all_passed={summary['all_passed']}, "
        f"tb_validated={summary['tb_validated']}, "
        f"wall={elapsed:.1f}s"
    )
    return summary


async def run_case_idea_to_optimize(
    args: argparse.Namespace, *, case_dir: Path
) -> dict[str, Any]:
    """Chain ``idea_loop`` -> autoresearch on the same design.

    Phase 1 = ``run_idea_to_rtl_loop`` produces RTL + TB + config from
    the natural-language Goertzel FP32 spec. Phase 2 wraps that
    ``config.yaml`` as a :class:`GenericDesign` and runs
    :class:`DigitalAutoresearchRunner` over it; the LLM proposes
    flow-knob settings (PL_TARGET_DENSITY_PCT, CLOCK_PERIOD) seeing
    each prior evaluation's metrics. This is the RL-emulated feedback
    loop where ``program.md`` plus ``results.tsv`` are the persistent
    memory and the LLM is the policy.

    Phase 2 runs unconditionally so partial / blocked phase-1 outputs
    still feed the optimizer (the framework's
    ``IdeaToRTLResult.all_passed`` is intentionally not gated on
    per-corner WNS, so a "BLOCKED on slow corner" phase 1 is a normal
    starting point for FoM optimisation).

    ``--skip-phase-idea`` skips phase 1 entirely; in that case the
    caller is responsible for staging ``case_dir/phase_idea/`` with a
    valid ``config.yaml``, ``src/<design>.v`` and ``tb/`` tree.
    """
    from eda_agents.agents.digital_autoresearch import (
        DigitalAutoresearchRunner,
    )
    from eda_agents.agents.idea_to_rtl_loop import run_idea_to_rtl_loop
    from eda_agents.core.designs.generic import GenericDesign
    from eda_agents.utils.plot_autoresearch import (
        plot_autoresearch_evolution,
        plot_idea_loop_evolution,
    )

    case_dir.mkdir(parents=True, exist_ok=True)
    phase_idea_dir = case_dir / "phase_idea"
    phase_optimize_dir = case_dir / "phase_optimize"

    pdk_root = _resolve_pdk_root(args.pdk_root)

    # ---------- Phase 1: idea_loop (or skipped) ----------
    phase1_summary: dict[str, Any] = {}
    if args.skip_phase_idea:
        if not (phase_idea_dir / "config.yaml").is_file():
            return {
                "case": "idea_to_optimize",
                "error": (
                    "--skip-phase-idea set but no config.yaml at "
                    f"{phase_idea_dir / 'config.yaml'}. Stage phase 1 "
                    "artefacts before re-running."
                ),
            }
        phase1_summary = {
            "skipped": True,
            "config_path": str(phase_idea_dir / "config.yaml"),
        }
        print(
            f"[idea_to_optimize] phase 1 skipped: reusing artefacts at "
            f"{phase_idea_dir}"
        )
    else:
        phase_idea_dir.mkdir(parents=True, exist_ok=True)
        print(
            f"[idea_to_optimize] phase 1: idea_loop on Goertzel FP32 "
            f"(max_turns={args.budget})"
        )
        t0 = time.monotonic()
        loop_result = await run_idea_to_rtl_loop(
            description=DEFAULT_DESCRIPTION,
            design_name=DEFAULT_DESIGN_NAME,
            work_dir=phase_idea_dir,
            max_turns=args.budget,
            max_budget_usd=args.max_budget_usd,
            pdk="gf180mcu",
            pdk_root=pdk_root,
            librelane_python=args.librelane_python,
            backend=args.backend,
            allow_dangerous=args.allow_dangerous,
            cli_path=args.cli_path,
            timeout_s=args.timeout_s,
            model=args.model,
            tb_framework="cocotb",
        )
        phase1_wall = time.monotonic() - t0
        phase1_summary = {
            "skipped": False,
            "wall_time_s": phase1_wall,
            "reason": loop_result.reason,
            "converged_turn": loop_result.converged_turn,
            "all_passed": loop_result.idea_result.all_passed,
            "config_path": (
                str(loop_result.idea_result.config_path)
                if loop_result.idea_result.config_path
                else None
            ),
            "gds_path": (
                str(loop_result.idea_result.gds_path)
                if loop_result.idea_result.gds_path
                else None
            ),
        }
        print(
            f"[idea_to_optimize] phase 1 done: reason={loop_result.reason}, "
            f"all_passed={loop_result.idea_result.all_passed}, "
            f"wall={phase1_wall:.1f}s"
        )

    # ---------- Phase 2: autoresearch over the produced design ----------
    config_path = phase_idea_dir / "config.yaml"
    if not config_path.is_file():
        # Phase 1 produced no config; cannot continue. Surface honestly.
        return {
            "case": "idea_to_optimize",
            "phase1": phase1_summary,
            "error": (
                f"phase 1 did not produce a config.yaml at {config_path}; "
                "phase 2 cannot start."
            ),
            "phase1_artefact": str(phase_idea_dir / "loop_result.json"),
        }

    phase_optimize_dir.mkdir(parents=True, exist_ok=True)
    strategy = args.strategy or "flow"
    print(
        f"[idea_to_optimize] phase 2: autoresearch over flow knobs "
        f"(strategy={strategy}, budget={args.budget})"
    )

    # GenericDesign defaults to IHP_SG13G2 when ``pdk_config`` is None
    # because :func:`resolve_pdk` falls back to the global default. The
    # demo is GF180MCU-only (the description, the wafer-space template,
    # and the prompt all assume gf180mcuD), so pin the PDK explicitly
    # — otherwise autoresearch ends up injecting PDK=ihp-sg13g2 with
    # PDK_ROOT pointing at the GF180 fork and LibreLane crashes at
    # init before producing any run directory.
    design = GenericDesign(
        config_path=config_path,
        pdk_root=pdk_root,
        pdk_config="gf180mcu",
    )
    mock_path = (
        Path(args.use_mock_metrics) if args.use_mock_metrics else None
    )
    runner_kwargs = _resolve_runner_model_kwargs(args)
    runner = DigitalAutoresearchRunner(
        design=design,
        budget=args.budget,
        stop_after=None,
        strategy=strategy,
        backend=args.backend,
        allow_dangerous=args.allow_dangerous,
        cli_path=args.cli_path,
        use_mock_metrics=mock_path,
        **runner_kwargs,
    )
    t0 = time.monotonic()
    optimize_result = await runner.run(phase_optimize_dir)
    phase2_wall = time.monotonic() - t0

    phase2_summary: dict[str, Any] = {
        "wall_time_s": phase2_wall,
        "strategy": strategy,
        "total_evals": optimize_result.total_evals,
        "kept": optimize_result.kept,
        "discarded": optimize_result.discarded,
        "best_fom": optimize_result.best_fom,
        "best_valid": optimize_result.best_valid,
        "best_params": optimize_result.best_params,
        "improvement_rate": optimize_result.improvement_rate,
        "results_tsv": str(phase_optimize_dir / "results.tsv"),
        "program_md": str(phase_optimize_dir / "program.md"),
    }
    print(
        f"[idea_to_optimize] phase 2 done: evals={optimize_result.total_evals}, "
        f"kept={optimize_result.kept}, best_fom={optimize_result.best_fom:.4f}, "
        f"valid={optimize_result.best_valid}, wall={phase2_wall:.1f}s"
    )

    # ---------- Plots (both phases) ----------
    plots: dict[str, dict[str, str]] = {}
    if args.plots:
        plots_root = case_dir / CASE_PLOT_DIR_NAME
        # Phase 1 plots
        phase1_loop_json = phase_idea_dir / "loop_result.json"
        if phase1_loop_json.is_file():
            try:
                p1 = plot_idea_loop_evolution(
                    phase1_loop_json,
                    plots_root / "phase_idea",
                    design_label=f"{DEFAULT_DESIGN_NAME} (phase_idea)",
                )
                plots["phase_idea"] = {k: str(v) for k, v in p1.items()}
            except ValueError as exc:
                plots["phase_idea_error"] = {"reason": str(exc)}
        # Phase 2 plots
        phase2_tsv = phase_optimize_dir / "results.tsv"
        if phase2_tsv.is_file():
            try:
                p2 = plot_autoresearch_evolution(
                    phase2_tsv,
                    plots_root / "phase_optimize",
                    design_label=f"{DEFAULT_DESIGN_NAME} (phase_optimize)",
                )
                plots["phase_optimize"] = {k: str(v) for k, v in p2.items()}
            except ValueError as exc:
                plots["phase_optimize_error"] = {"reason": str(exc)}

    return {
        "case": "idea_to_optimize",
        "phase1": phase1_summary,
        "phase2": phase2_summary,
        "plots": plots,
    }


async def run_case_fazyrv(
    args: argparse.Namespace,
    *,
    case_dir: Path,
    strategy: str,
) -> dict[str, Any]:
    """Drive a DigitalAutoresearchRunner over FazyRV-Hachure baseline.

    Each evaluation runs the full bare-block LibreLane flow on the
    selected macro (no chip-top, no precheck). That gives:

    * Hardened macro -> GDS streamout.
    * Magic.DRC + KLayout.DRC -> ``klayout_drc_count`` /
      ``magic_drc_count`` populated.
    * Netgen.LVS -> ``lvs_match`` populated.
    * OpenROAD.STAPostPNR -> ``wns_worst_ns`` per corner populated.
    * GL sim post-synth + post-PnR (SDF) -> ``gl_sim_post_synth_ok``
      and ``gl_sim_post_pnr_ok`` gates blocking convergence.

    ``check_validity`` therefore rejects any design that has DRC
    violations, LVS mismatches, antenna issues, or fails GL sim,
    regardless of timing/area/power.
    """
    from eda_agents.agents.digital_autoresearch import DigitalAutoresearchRunner
    from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign

    case_dir.mkdir(parents=True, exist_ok=True)
    design = FazyRvHachureDesign(macro="frv_1")

    mock_path = (
        Path(args.use_mock_metrics) if args.use_mock_metrics else None
    )

    print(f"[fazyrv_{strategy}] starting autoresearch, budget={args.budget}")
    runner_kwargs = _resolve_runner_model_kwargs(args)
    runner = DigitalAutoresearchRunner(
        design=design,
        budget=args.budget,
        stop_after=None,
        strategy=strategy,
        backend=args.backend,
        allow_dangerous=args.allow_dangerous,
        cli_path=args.cli_path,
        use_mock_metrics=mock_path,
        **runner_kwargs,
    )

    t0 = time.monotonic()
    result = await runner.run(case_dir)
    elapsed = time.monotonic() - t0

    artefact = case_dir / "results.tsv"
    summary = {
        "case": f"fazyrv_{strategy}",
        "wall_time_s": elapsed,
        "total_evals": result.total_evals,
        "kept": result.kept,
        "discarded": result.discarded,
        "best_fom": result.best_fom,
        "best_valid": result.best_valid,
        "best_params": result.best_params,
        "improvement_rate": result.improvement_rate,
        "artefact": str(artefact) if artefact.is_file() else None,
    }
    print(
        f"[fazyrv_{strategy}] done: evals={result.total_evals}, "
        f"kept={result.kept}, best_fom={result.best_fom:.4f}, "
        f"valid={result.best_valid}, wall={elapsed:.1f}s"
    )
    return summary


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


CASE_DIR_NAMES = {
    "idea_loop": "idea_loop",
    "fazyrv_flow": "fazyrv_flow",
    "fazyrv_hybrid": "fazyrv_hybrid",
    "idea_to_optimize": "idea_to_optimize",
}


async def _run_one_case(
    case: str, args: argparse.Namespace, work_dir: Path
) -> dict[str, Any]:
    case_dir = work_dir / CASE_DIR_NAMES[case]

    if args.dry_run:
        result = _dry_run_case(
            case, case_dir=case_dir, emit_plots=args.plots
        )
        if args.plots and result["plots"]:
            print(f"[{case}] plots: {list(result['plots'].keys())}")
        return result

    if case == "idea_loop":
        result = await run_case_idea_loop(args, case_dir=case_dir)
    elif case == "fazyrv_flow":
        result = await run_case_fazyrv(args, case_dir=case_dir, strategy="flow")
    elif case == "fazyrv_hybrid":
        result = await run_case_fazyrv(
            args, case_dir=case_dir, strategy="hybrid"
        )
    elif case == "idea_to_optimize":
        # The chained handler emits its own plots inline so the dispatch
        # below is skipped via the early return.
        return await run_case_idea_to_optimize(args, case_dir=case_dir)
    else:
        raise ValueError(f"unknown case {case!r}")

    if args.plots and result.get("artefact"):
        plot_paths = _emit_plots(
            case,
            artefact_path=Path(result["artefact"]),
            plots_dir=case_dir / CASE_PLOT_DIR_NAME,
            design_label=(
                CASE_DIR_NAMES[case]
                if case != "idea_loop"
                else DEFAULT_DESIGN_NAME
            ),
        )
        result["plots"] = {k: str(v) for k, v in plot_paths.items()}
    return result


def _write_summary_md(work_dir: Path, results: list[dict[str, Any]]) -> Path:
    """Write a text-only summary across all cases."""
    lines = ["# idea-to-chip demo summary", ""]
    for r in results:
        lines.append(f"## {r['case']}")
        for k, v in r.items():
            if k == "case":
                continue
            if isinstance(v, dict):
                lines.append(f"- {k}:")
                for kk, vv in v.items():
                    lines.append(f"  - {kk}: {vv}")
            else:
                lines.append(f"- {k}: {v}")
        lines.append("")
    summary = work_dir / "summary.md"
    summary.write_text("\n".join(lines))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "End-to-end idea-to-chip demo on GF180MCU "
            "(idea_loop + fazyrv_flow + fazyrv_hybrid)."
        )
    )
    parser.add_argument(
        "--case",
        required=True,
        choices=[
            "idea_loop",
            "fazyrv_flow",
            "fazyrv_hybrid",
            "idea_to_optimize",
            "all",
        ],
        help="Which demo case to run.",
    )
    parser.add_argument(
        "--skip-phase-idea",
        action="store_true",
        help="Only meaningful for --case idea_to_optimize: assume "
        "case_dir/phase_idea/ already contains config.yaml + RTL + TB "
        "from a prior idea_loop run, and start directly from phase 2 "
        "(autoresearch) over those artefacts.",
    )
    parser.add_argument(
        "--backend",
        default="cc_cli",
        choices=["cc_cli", "opencode"],
        help="LLM backend: cc_cli (Claude Code, Opus 4.7) or opencode "
        "(OpenCode CLI; pair with --model). Default: cc_cli.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id passed to the harness (cc_cli leaves default; "
        "opencode requires the user-supplied gpt codex 5.3 id).",
    )
    parser.add_argument(
        "--work-dir",
        default=DEFAULT_WORK_DIR,
        help=f"Top-level work directory (default: {DEFAULT_WORK_DIR}).",
    )
    parser.add_argument(
        "--pdk-root",
        default=None,
        help="Explicit PDK_ROOT path. Falls back to $PDK_ROOT.",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=6,
        help="idea_loop -> max_turns; fazyrv_* -> autoresearch budget. "
        "Default: 6 (Goertzel FP32 typically needs 4-6 turns to settle "
        "FP corner cases; lower to 4 for fixed-point designs).",
    )
    parser.add_argument(
        "--max-budget-usd",
        type=float,
        default=None,
        help="Optional safety cap on cumulative cost (USD). Irrelevant "
        "under Claude Code / OpenCode CLI subscription quota; only "
        "exposed for callers driving the API backend directly.",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=None,
        help="Per-turn wall-clock cap in seconds. Default: no cap "
        "(wait until the harness or the LLM exits on its own). Set "
        "an explicit value such as 1800 only for simple designs "
        "where you want to fail fast on a hung turn.",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        choices=["flow", "rtl", "hybrid"],
        help="Override autoresearch strategy (default: per-case).",
    )
    parser.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="Pass through --dangerously-skip-permissions to the harness "
        "(also requires EDA_AGENTS_ALLOW_DANGEROUS=1).",
    )
    parser.add_argument(
        "--cli-path",
        default=None,
        help="Path to the LLM CLI binary. Default: auto-resolves to "
        "'claude' for --backend cc_cli and 'opencode' for "
        "--backend opencode.",
    )
    parser.add_argument(
        "--librelane-python",
        default="python3",
        help="Python interpreter that knows how to run "
        "'python -m librelane' inside the agent prompt. Default "
        "'python3'; on this dev box typically "
        "/home/montanares/git/librelane/.venv/bin/python.",
    )
    parser.add_argument(
        "--plots",
        dest="plots",
        action="store_true",
        default=True,
        help="Emit FoM/metric evolution plots (default).",
    )
    parser.add_argument(
        "--no-plots",
        dest="plots",
        action="store_false",
        help="Skip plot generation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Copy fixture artefacts into the work dir and exercise "
        "plotting only. No LLM, no LibreLane.",
    )
    parser.add_argument(
        "--use-mock-metrics",
        default=None,
        help="Pass through to DigitalAutoresearchRunner for fazyrv_* "
        "cases. Skips LibreLane.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args(argv)


async def amain(args: argparse.Namespace) -> int:
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if not args.dry_run:
        _check_allow_dangerous(args)
        if args.backend == "opencode" and not args.model:
            print(
                "--backend opencode requires --model <gpt-codex-5.3-id>",
                file=sys.stderr,
            )
            return 2

    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    cases = (
        ["idea_loop", "fazyrv_flow", "fazyrv_hybrid"]
        if args.case == "all"
        else [args.case]
    )

    print("=" * 60)
    print("idea-to-chip demo on GF180MCU")
    print("=" * 60)
    print(f"  Cases:       {cases}")
    print(f"  Backend:     {args.backend}")
    if args.model:
        print(f"  Model:       {args.model}")
    print(f"  Budget:      {args.budget}")
    print(f"  Work dir:    {work_dir}")
    print(f"  Plots:       {args.plots}")
    print(f"  Dry run:     {args.dry_run}")
    print()

    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            r = await _run_one_case(case, args, work_dir)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("case %s failed", case)
            r = {"case": case, "error": str(exc)}
        results.append(r)

    if len(results) > 1:
        summary_path = _write_summary_md(work_dir, results)
        print(f"\n[summary] wrote {summary_path}")

    print("\n" + json.dumps(results, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
