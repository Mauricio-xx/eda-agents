"""Agents digital loop walk-through: 4-bit counter on GF180MCU via ProjectManager.

Plain-Python companion to agents_rtl2gds_counter.ipynb. Shows how the
digital multi-agent hierarchy (project_manager + 4 specialists) runs on
top of a minimal LibreLane config.

Uses the same counter.v + config.yaml as the sibling
tutorials/rtl2gds-gf180-docker/demo/ so both tutorials agree.

Default mode is **dry-run**: wraps the design with GenericDesign, constructs
ProjectManager, prints what it would do (prompt length, sub-agents, tool
allowlists). No LLM call, no Docker launch, completes in seconds.

Flip RUN_REAL=True to launch the full flow end-to-end (~10-15 min, requires
Docker + hpretl/iic-osic-tools image + LLM API key + optionally claude CLI).

Usage:
  python agents_rtl2gds_counter.py            # pauses between steps
  python agents_rtl2gds_counter.py --no-pause # straight through
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
RUN_PIP_INSTALL  = False   # pip install -e . from the repo root
RUN_DRY_PM       = True    # construct ProjectManager + dry-run (safe, fast)
RUN_REAL         = False   # full LibreLane flow via ADK or CC CLI (minutes, $$)
RUN_DANGEROUSLY  = False   # gate for cc_cli --dangerously-skip-permissions; also
                           # requires EDA_AGENTS_ALLOW_DANGEROUS=1 in the env.

BACKEND = "cc_cli"                        # "adk" | "cc_cli"  (ProjectManager today)
                                          #   For OpenCode end-to-end use:
                                          #     opencode --agent gf180-docker-digital
                                          #   or use DigitalAutoresearchRunner(backend="opencode"),
                                          #   see demo/agents_digital_autoresearch.{ipynb,py}.

# Backend-aware default model. cc_cli forwards --model to `claude --print`,
# which only accepts Anthropic IDs; passing google/gemini or openrouter/* there
# returns API 404. adk and litellm/opencode use LiteLLM and accept provider-
# prefixed IDs. Override with EDA_AGENTS_MODEL if you have a specific model in
# mind for either backend.
_DEFAULT_MODEL = "claude-sonnet-4-6" if BACKEND == "cc_cli" else "google/gemini-3-flash-preview"
LLM_MODEL = os.environ.get("EDA_AGENTS_MODEL", _DEFAULT_MODEL)
MAX_BUDGET_USD = 1.00                     # only meaningful for cc_cli

REPO_ROOT = Path(__file__).resolve().parents[3]   # eda-agents/ root
WORK_DIR  = Path("./rtl2gds_counter_results").resolve()
PROJ_DIR  = WORK_DIR / "counter_project"

# ---------------------------------------------------------------------------
# Counter RTL + minimal GF180MCU LibreLane config (mirrors sibling tutorial)
# ---------------------------------------------------------------------------
COUNTER_V = """\
// 4-bit synchronous up-counter with active-high reset.
module counter (
    input  wire       clk,
    input  wire       rst,
    output wire [3:0] q
);
    reg [3:0] cnt;
    always @(posedge clk) begin
        if (rst) cnt <= 4'b0;
        else     cnt <= cnt + 4'b1;
    end
    assign q = cnt;
endmodule
"""

CONFIG_YAML = """\
# GF180MCU LibreLane config - minimal 4-bit counter walk-through.
meta:
  version: 3
  flow: Classic
  substituting_steps:
    Magic.StreamOut: null
    KLayout.XOR: null
    KLayout.DRC: null            # gf180mcuD ships no KLAYOUT_DRC_RUNSET; the step crashes
                                 # with "Unable to open file: .../None" when librelane
                                 # is invoked without --manual-pdk (the typical agent path).
                                 # Magic DRC at 0 is the authoritative DRC for this PDK.

RUN_MAGIC_STREAMOUT: false
RUN_KLAYOUT_XOR: false
RUN_KLAYOUT_DRC: false

DESIGN_NAME: counter
VERILOG_FILES:
  - dir::counter.v
CLOCK_PORT: clk
CLOCK_PERIOD: 50

FP_SIZING: absolute
DIE_AREA: [0.0, 0.0, 300.0, 300.0]

VDD_NETS:
  - VDD
GND_NETS:
  - VSS

PRIMARY_GDSII_STREAMOUT_TOOL: klayout

DIODE_ON_PORTS: in
RT_MAX_LAYER: Metal4
PDN_MULTILAYER: false
PDN_VWIDTH: 5
PDN_HWIDTH: 5
PDN_VSPACING: 1
PDN_HSPACING: 1
PDN_VOFFSET: 10
PDN_HOFFSET: 10
"""


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
    print(f"  Repo root : {REPO_ROOT}")
    print(f"  Python    : {sys.executable}")
    if "VIRTUAL_ENV" not in os.environ:
        print("  WARNING   : no $VIRTUAL_ENV -- you should be in a venv.")
    cmd = [sys.executable, "-m", "pip", "install", "-e", str(REPO_ROOT)]
    print(f"  Command   : {' '.join(cmd)}")
    if not RUN_PIP_INSTALL:
        print("  [rehearse] RUN_PIP_INSTALL=False; skipping.")
        return
    subprocess.run(cmd, check=True)
    pause(args)


def step1_docker_check(args) -> None:
    banner(1, "docker + GF180 image + CLI availability")
    docker = shutil.which("docker")
    print(f"  docker on PATH         : {docker or 'MISSING'}")
    if docker:
        try:
            out = subprocess.run(
                ["docker", "images", "-q", "hpretl/iic-osic-tools:next"],
                capture_output=True, text=True, timeout=10,
            )
            img = out.stdout.strip()
            print(f"  hpretl image present   : {'yes (' + img[:12] + ')' if img else 'no -- docker pull required'}")
        except subprocess.SubprocessError as exc:
            print(f"  docker images query failed: {exc}")

    claude = shutil.which("claude")
    print(f"  claude CLI on PATH     : {claude or 'MISSING (needed for cc_cli backend)'}")

    for key in ("OPENROUTER_API_KEY", "GOOGLE_API_KEY", "ZAI_API_KEY"):
        print(f"  {key:<22}: {'set' if os.environ.get(key) else 'unset'}")

    pause(args)


def step2_stage_project(args) -> None:
    banner(2, "stage counter.v + config.yaml on the host")
    PROJ_DIR.mkdir(parents=True, exist_ok=True)
    (PROJ_DIR / "counter.v").write_text(COUNTER_V)
    (PROJ_DIR / "config.yaml").write_text(CONFIG_YAML)
    for p in PROJ_DIR.iterdir():
        print(f"  wrote {p}")
    pause(args)


async def step3_construct_pm_dry(args) -> None:
    banner(3, "construct ProjectManager + dry-run")
    if not RUN_DRY_PM:
        print("  [rehearse] RUN_DRY_PM=False; skipping.")
        return
    try:
        from eda_agents.core.designs.generic import GenericDesign
        from eda_agents.agents.digital_adk_agents import ProjectManager
    except ImportError as exc:
        print(f"  ERROR: {exc}")
        print("  -> run step 0 (pip install -e .) and retry.")
        return

    pdk_root = os.environ.get("PDK_ROOT") or None
    design = GenericDesign(
        config_path=str(PROJ_DIR / "config.yaml"),
        pdk_root=pdk_root,
        pdk_config="gf180mcu",
    )
    pm = ProjectManager(
        design=design,
        model=LLM_MODEL,
        backend=BACKEND,
        max_budget_usd=MAX_BUDGET_USD,
        allow_dangerous=RUN_DANGEROUSLY,
    )
    result = await pm.run(WORK_DIR, dry_run=True)
    print(f"  design          : {design.project_name()}")
    print(f"  specs           : {design.specs_description()}")
    print(f"  FoM             : {design.fom_description()}")
    print(f"  backend         : {BACKEND}")
    if BACKEND == "cc_cli":
        print(f"  prompt length   : {result.get('prompt_length', 0)} chars")
    else:
        subs = result.get("sub_agent_names") or result.get("sub_agents") or []
        print(f"  master          : {result.get('master_agent', 'N/A')}")
        print(f"  sub-agents      : {', '.join(str(s) for s in subs)}")
    pause(args)


async def step4_real_run(args) -> None:
    banner(4, f"full RTL-to-GDS flow via {BACKEND} backend")
    if not RUN_REAL:
        print("  [rehearse] RUN_REAL=False; skipping.")
        print("  Flip RUN_REAL=True after a dry-run completes cleanly.")
        print(f"  Expected wall time: ~10-15 min for this counter.")
        print(f"  Artifacts will land under {WORK_DIR}")
        return

    if BACKEND == "cc_cli" and not (RUN_DANGEROUSLY and os.environ.get("EDA_AGENTS_ALLOW_DANGEROUS") == "1"):
        print("  ERROR: cc_cli backend in non-interactive subprocess mode needs")
        print("  --dangerously-skip-permissions to allow the agent to call docker/")
        print("  file tools without per-call approval. Both gates are required:")
        print("    1. Set RUN_DANGEROUSLY = True at the top of this script.")
        print("    2. export EDA_AGENTS_ALLOW_DANGEROUS=1 in the shell.")
        print("  Or switch BACKEND to 'adk' (no permission layer).")
        return

    from eda_agents.core.designs.generic import GenericDesign
    from eda_agents.agents.digital_adk_agents import ProjectManager

    pdk_root = os.environ.get("PDK_ROOT") or None
    design = GenericDesign(
        config_path=str(PROJ_DIR / "config.yaml"),
        pdk_root=pdk_root,
        pdk_config="gf180mcu",
    )
    pm = ProjectManager(
        design=design,
        model=LLM_MODEL,
        backend=BACKEND,
        max_budget_usd=MAX_BUDGET_USD,
        allow_dangerous=RUN_DANGEROUSLY,
    )
    result = await pm.run(WORK_DIR)
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("agent_output", "prompt")},
                     indent=2, default=str))


def step5_show_artifacts(args) -> None:
    banner(5, "inspect results")
    results = WORK_DIR / "rtl2gds_results.json"
    if results.exists():
        print(results.read_text()[:2000])
    else:
        print(f"{results} not yet written; run step 4 first.")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-pause", action="store_true",
                        help="run straight through without enter-to-continue")
    args = parser.parse_args()

    step0_pip_install(args)
    step1_docker_check(args)
    step2_stage_project(args)
    await step3_construct_pm_dry(args)
    await step4_real_run(args)
    step5_show_artifacts(args)


if __name__ == "__main__":
    asyncio.run(main())
