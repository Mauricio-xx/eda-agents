"""Agents analog loop walk-through: Miller OTA on IHP SG13G2.

Plain-Python companion to agents_miller_ota.ipynb. Drives the eda-agents
autoresearch loop against the registered Miller OTA CircuitTopology,
with ngspice + gm/ID LUTs as the evaluator.

Default: all RUN_* flags are False -- the script prints what it WOULD do.
Flip RUN_AUTORESEARCH=True once you have PDK_ROOT set and an LLM API key.

Prerequisites:
  - Cloned the eda-agents repo (we run `pip install -e .` from the root).
  - ngspice on PATH (`which ngspice`).
  - IHP SG13G2 PDK at PDK_ROOT, with gm/ID LUTs staged (EDA_AGENTS_IHP_LUT_DIR).
  - OPENROUTER_API_KEY or ZAI_API_KEY for the LLM proposal model.

Usage:
  python agents_miller_ota.py            # pauses between steps
  python agents_miller_ota.py --no-pause # straight through
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
# Run-gates: flip these to True once you've confirmed the environment check.
# ---------------------------------------------------------------------------
RUN_PIP_INSTALL   = False   # pip install -e . from the repo root
RUN_AUTORESEARCH  = False   # real autoresearch run with LLM + SPICE
AUTORESEARCH_BUDGET = 6     # SPICE evaluations; 2-3 min total on a laptop
LLM_MODEL         = "zai/GLM-4.5-Flash"

REPO_ROOT = Path(__file__).resolve().parents[3]   # eda-agents/ root
WORK_DIR  = Path("./autoresearch_miller_ota").resolve()


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
        print("  WARNING   : no $VIRTUAL_ENV -- you should be in a venv.")
    cmd = [sys.executable, "-m", "pip", "install", "-e", str(REPO_ROOT)]
    print(f"  Command    : {' '.join(cmd)}")
    if not RUN_PIP_INSTALL:
        print("  [rehearse] RUN_PIP_INSTALL=False; skipping.")
        return
    subprocess.run(cmd, check=True)
    pause(args)


def step1_env_check(args) -> None:
    banner(1, "environment check (ngspice, PDK_ROOT, LUTs, API key)")

    ng = shutil.which("ngspice")
    print(f"  ngspice on PATH        : {ng or 'MISSING'}")

    pdk_root = os.environ.get("PDK_ROOT", "")
    print(f"  PDK_ROOT               : {pdk_root or 'UNSET'}")
    if pdk_root:
        ihp_models = Path(pdk_root) / "ihp-sg13g2" / "libs.tech" / "ngspice" / "models"
        print(f"  IHP models present     : {ihp_models.is_dir()}")

    lut_dir = os.environ.get("EDA_AGENTS_IHP_LUT_DIR", "")
    print(f"  EDA_AGENTS_IHP_LUT_DIR : {lut_dir or 'UNSET (required for Miller OTA)'}")

    for key in ("OPENROUTER_API_KEY", "ZAI_API_KEY"):
        val = os.environ.get(key)
        state = "set" if val else "unset"
        print(f"  {key:<22}: {state}")

    pause(args)


def step2_instantiate_topology(args):
    banner(2, "instantiate the Miller OTA CircuitTopology")
    try:
        from eda_agents.topologies.ota_miller import MillerOTATopology
    except ImportError as exc:
        print(f"  ERROR: {exc}")
        print("  -> run step 0 (pip install -e .) and retry.")
        return None

    topo = MillerOTATopology()
    print(f"  topology_name  : {topo.topology_name()}")
    print(f"  pdk            : {topo.pdk}")
    print(f"  design_space   : {list(topo.design_space().keys())}")
    print(f"  default_params :")
    for k, v in topo.default_params().items():
        print(f"    {k} = {v}")
    print(f"  specs          : {topo.specs_description()}")
    pause(args)
    return topo


def step3_dry_spice(args, topo) -> None:
    banner(3, "one SPICE eval at default params (sanity check)")
    if topo is None:
        print("  skipped (no topology instance).")
        return
    from eda_agents.core.spice_runner import SpiceRunner

    runner = SpiceRunner(pdk=topo.pdk)
    missing = runner.validate_pdk()
    if missing:
        print(f"  PDK problems: {missing}")
        print("  -> fix before running autoresearch.")
        return

    import tempfile
    params = topo.default_params()
    sizing = topo.params_to_sizing(params)
    cir = topo.generate_netlist(sizing, Path(tempfile.mkdtemp()))
    result = runner.run(cir)
    if result.success:
        fom = topo.compute_fom(result, sizing)
        print(f"  Adc = {result.Adc_dB:.1f} dB")
        print(f"  GBW = {result.GBW_Hz/1e6:.2f} MHz")
        print(f"  PM  = {result.PM_deg:.1f} deg")
        print(f"  FoM = {fom:.3e}")
    else:
        print(f"  SPICE failed: {result.error}")
    pause(args)


async def step4_autoresearch(args, topo) -> None:
    banner(4, f"autoresearch (budget={AUTORESEARCH_BUDGET})")
    if topo is None:
        print("  skipped (no topology instance).")
        return
    if not RUN_AUTORESEARCH:
        print("  [rehearse] RUN_AUTORESEARCH=False; skipping the real loop.")
        print(f"  When enabled, writes program.md + results.tsv under {WORK_DIR}")
        return

    from eda_agents.agents.autoresearch_runner import AutoresearchRunner

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    runner = AutoresearchRunner(
        topology=topo,
        model=LLM_MODEL,
        budget=AUTORESEARCH_BUDGET,
    )
    result = await runner.run(WORK_DIR)
    print(f"\n  {result.summary}")
    print(f"  validity_rate : {result.validity_rate:.0%}")
    print(f"  tsv_path      : {result.tsv_path}")
    if result.best_valid:
        print("  best_params   :")
        print(json.dumps(result.best_params, indent=4))


def step5_show_artifacts(args) -> None:
    banner(5, "inspect program.md + results.tsv")
    prog = WORK_DIR / "program.md"
    tsv  = WORK_DIR / "results.tsv"
    if not prog.exists():
        print(f"  {prog} not yet written. Flip RUN_AUTORESEARCH=True and re-run.")
        return
    print(f"  --- {prog} ---")
    print(prog.read_text()[:1500])
    print(f"\n  --- {tsv} (last 10 rows) ---")
    rows = tsv.read_text().splitlines()[-10:] if tsv.exists() else []
    for row in rows:
        print(f"  {row}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-pause", action="store_true",
                        help="run straight through without enter-to-continue")
    args = parser.parse_args()

    step0_pip_install(args)
    step1_env_check(args)
    topo = step2_instantiate_topology(args)
    step3_dry_spice(args, topo)
    await step4_autoresearch(args, topo)
    step5_show_artifacts(args)


if __name__ == "__main__":
    asyncio.run(main())
