"""Chained analog agents walkthrough: NL spec -> topology -> sizing -> SPICE -> corners.

Plain-Python companion to agents_analog_topology_to_sizing.ipynb. Deeper than the
miller_ota demo: exercises the full analog agent chain end-to-end.

  1. Editable install + env check.
  2. analog-topology-recommender     (NL spec -> topology JSON)
  3. analog-sizing-advisor           (topology + gm/ID LUTs -> starter vector)
  4. AutoresearchRunner              (greedy SPICE refinement, budget=8)
  5. analog.corner_validator         (PVT sweep on the winner)
  6. Tail program.md + print verdict.

Default: all RUN_* flags False; the script prints what it WOULD do.

Usage:
  python agents_analog_topology_to_sizing.py
  python agents_analog_topology_to_sizing.py --no-pause
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Run-gates
# ---------------------------------------------------------------------------
RUN_PIP_INSTALL        = False
RUN_RECOMMENDER        = False
RUN_SIZING_ADVISOR     = False
RUN_AUTORESEARCH       = False
RUN_CORNER_VALIDATOR   = False
AUTORESEARCH_BUDGET    = 8
LLM_MODEL              = "zai/GLM-4.5-Flash"
DEFAULT_SPEC           = "Rail-to-rail OTA, 10 MHz GBW, 60 dB gain, 1 pF load, IHP SG13G2."

REPO_ROOT = Path(__file__).resolve().parents[3]
WORK_DIR  = Path("./analog_chain_results").resolve()


def banner(step: int, title: str) -> None:
    line = "=" * 72
    print(f"\n{line}\nStep {step} | {title}\n{line}")


def pause(args) -> None:
    if args.no_pause:
        return
    try:
        input("  [enter] to continue, ctrl-c to stop ")
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)


def step0_pip_install(args) -> None:
    banner(0, "venv + editable install")
    print(f"  Repo root  : {REPO_ROOT}")
    print(f"  Python exe : {sys.executable}")
    if "VIRTUAL_ENV" not in os.environ:
        print("  WARNING   : no $VIRTUAL_ENV -- activate a venv first.")
    cmd = [sys.executable, "-m", "pip", "install", "-e", str(REPO_ROOT)]
    print(f"  Command    : {' '.join(cmd)}")
    if not RUN_PIP_INSTALL:
        print("  [rehearse] RUN_PIP_INSTALL=False; skipping.")
        return
    subprocess.run(cmd, check=True)
    pause(args)


def step1_env_check(args) -> None:
    banner(1, "environment check")
    print(f"  ngspice on PATH        : {shutil.which('ngspice') or 'MISSING'}")
    print(f"  PDK_ROOT               : {os.environ.get('PDK_ROOT', '') or 'UNSET'}")
    print(f"  EDA_AGENTS_IHP_LUT_DIR : {os.environ.get('EDA_AGENTS_IHP_LUT_DIR', '') or 'UNSET'}")
    for key in ("OPENROUTER_API_KEY", "ZAI_API_KEY"):
        print(f"  {key:<22}: {'set' if os.environ.get(key) else 'unset'}")
    pause(args)


async def step2_recommender(args, spec: str):
    banner(2, "analog-topology-recommender (NL spec -> topology JSON)")
    print(f"  spec: {spec}")
    if not RUN_RECOMMENDER:
        print("  [rehearse] RUN_RECOMMENDER=False; emitting a canned response.")
        return {
            "topology":      "miller_ota",
            "rationale":     "Miller compensation gives a predictable roll-off at the frequencies asked for.",
            "starter_specs": {"gbw_hz": 10e6, "pm_deg": 60, "cl_f": 1e-12, "pdk": "ihp_sg13g2"},
            "confidence":    0.82,
        }
    # Real invocation uses the idea_to_topology skill; call via MCP runner or
    # via the Claude Code / OpenCode CLI. Kept as a stub here to avoid
    # forcing a specific backend dependency on the demo.
    print("  -> call the recommender via /agents analog-topology-recommender")
    print("     (or opencode run --agent analog-topology-recommender)")
    print("     paste its JSON into topology_rec below and re-run.")
    return None


async def step3_sizing_advisor(args, topology_name: str):
    banner(3, "analog-sizing-advisor (topology + gm/ID -> starter vector)")
    try:
        from eda_agents.topologies.ota_miller import MillerOTATopology
    except ImportError as exc:
        print(f"  ERROR: {exc}"); return None

    if topology_name != "miller_ota":
        print(f"  NOTE: only miller_ota is wired in this demo; got {topology_name}.")
    topo = MillerOTATopology()
    params = topo.default_params()
    print("  starter params (from topology default; advisor would refine via gm/ID):")
    for k, v in params.items():
        print(f"    {k} = {v}")

    if RUN_SIZING_ADVISOR:
        # The sizing advisor agent would normally be invoked here via
        # the analog-sizing-advisor CLI entry. For now we surface the
        # default params as the seed for autoresearch.
        pass
    pause(args)
    return topo


async def step4_autoresearch(args, topo) -> None:
    banner(4, f"AutoresearchRunner (budget={AUTORESEARCH_BUDGET})")
    if topo is None or not RUN_AUTORESEARCH:
        print("  [rehearse] skipping greedy loop.")
        print(f"  When enabled, writes program.md + results.tsv under {WORK_DIR}")
        return

    from eda_agents.agents.autoresearch_runner import AutoresearchRunner
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    runner = AutoresearchRunner(topology=topo, model=LLM_MODEL, budget=AUTORESEARCH_BUDGET)
    result = await runner.run(WORK_DIR)
    print(f"\n  {result.summary}")
    if result.best_valid:
        print(json.dumps(result.best_params, indent=4))


async def step5_corners(args) -> None:
    banner(5, "analog.corner_validator (PVT sweep on the winner)")
    if not RUN_CORNER_VALIDATOR:
        print("  [rehearse] skipping corner sweep.")
        print("  Typical corner count: 5 process x 3 voltage x 3 temperature = 45 re-runs.")
        print("  On failure, runner backs off to eval n-1 and re-validates.")
        return
    # When implemented end-to-end, this calls the corner_validator skill.
    print("  -> invoke analog.corner_validator through the MCP runner or the CLI.")


def step6_read_program(args) -> None:
    banner(6, "inspect program.md")
    prog = WORK_DIR / "program.md"
    if not prog.exists():
        print(f"  {prog} not yet written; enable RUN_AUTORESEARCH first.")
        return
    print(prog.read_text()[:2000])


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-pause", action="store_true")
    parser.add_argument("--spec", default=DEFAULT_SPEC,
                        help="natural-language spec for the recommender")
    args = parser.parse_args()

    step0_pip_install(args)
    step1_env_check(args)
    topology_rec = await step2_recommender(args, args.spec)
    if topology_rec is None:
        print("  no recommendation available; aborting chain.")
        return
    print(f"\n  recommender -> {topology_rec['topology']}  (confidence {topology_rec['confidence']})")
    topo = await step3_sizing_advisor(args, topology_rec["topology"])
    await step4_autoresearch(args, topo)
    await step5_corners(args)
    step6_read_program(args)


if __name__ == "__main__":
    asyncio.run(main())
