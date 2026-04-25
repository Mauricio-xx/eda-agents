"""DigitalAutoresearchRunner walkthrough: knob sweep + OpenCode backend.

Plain-Python companion to agents_digital_autoresearch.ipynb. Demonstrates
DigitalAutoresearchRunner on the 4-bit counter design (same RTL + config as
the sibling rtl2gds_counter demo), with backend='opencode' plus provider/model
selection.

Steps:
  1. Editable install + env check (docker, opencode CLI, API keys).
  2. Stage counter.v + config.yaml.
  3. Wrap with GenericDesign.
  4. Construct DigitalAutoresearchRunner with backend='opencode'.
  5. Dry-run (safe).
  6. Real run (gated, ~5 LibreLane evals; minutes x 5).

Default: all RUN_* False. Flip per step.

Usage:
  python agents_digital_autoresearch.py
  python agents_digital_autoresearch.py --no-pause
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
RUN_PIP_INSTALL     = False
RUN_DRY             = True
RUN_REAL            = False
BACKEND             = "opencode"   # "adk" | "cc_cli" | "litellm" | "opencode"
OPENCODE_MODEL      = "openrouter/google/gemini-3-flash-preview"
OPENCODE_CLI_PATH   = "opencode"
BUDGET              = 5

REPO_ROOT = Path(__file__).resolve().parents[3]
WORK_DIR  = Path("./digital_autoresearch_results").resolve()
PROJ_DIR  = WORK_DIR / "counter_project"

COUNTER_V = """\
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
meta:
  version: 3
  flow: Classic
  substituting_steps:
    Magic.StreamOut: null
    KLayout.XOR: null
    KLayout.DRC: null            # gf180mcuD ships no KLAYOUT_DRC_RUNSET; the step crashes
                                 # with "Unable to open file: .../None" when librelane is
                                 # invoked without --manual-pdk (the typical agent path).
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
        print("  WARNING   : no $VIRTUAL_ENV")
    if not RUN_PIP_INSTALL:
        print("  [rehearse] RUN_PIP_INSTALL=False; skipping.")
        return
    subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(REPO_ROOT)], check=True)
    pause(args)


def step1_env_check(args) -> None:
    banner(1, "docker + opencode CLI + API keys")
    print(f"  docker on PATH          : {shutil.which('docker') or 'MISSING'}")
    print(f"  opencode CLI on PATH    : {shutil.which(OPENCODE_CLI_PATH) or 'MISSING -- npm i -g opencode-ai'}")
    for key in ("OPENROUTER_API_KEY", "GOOGLE_API_KEY", "ZAI_API_KEY", "ANTHROPIC_API_KEY"):
        print(f"  {key:<22} : {'set' if os.environ.get(key) else 'unset'}")
    print(f"  selected backend        : {BACKEND}")
    if BACKEND == "opencode":
        print(f"  selected opencode model : {OPENCODE_MODEL}")
    pause(args)


def step2_stage_project(args) -> None:
    banner(2, "stage counter.v + config.yaml")
    PROJ_DIR.mkdir(parents=True, exist_ok=True)
    (PROJ_DIR / "counter.v").write_text(COUNTER_V)
    (PROJ_DIR / "config.yaml").write_text(CONFIG_YAML)
    for p in PROJ_DIR.iterdir():
        print(f"  wrote {p}")
    pause(args)


async def step3_dry(args) -> None:
    banner(3, f"DigitalAutoresearchRunner dry-run (backend={BACKEND})")
    if not RUN_DRY:
        print("  [rehearse] RUN_DRY=False; skipping.")
        return
    try:
        from eda_agents.core.designs.generic import GenericDesign
        from eda_agents.agents.digital_autoresearch import DigitalAutoresearchRunner
    except ImportError as exc:
        print(f"  ERROR: {exc}")
        return

    design = GenericDesign(
        config_path=str(PROJ_DIR / "config.yaml"),
        pdk_root=os.environ.get("PDK_ROOT") or None,
        pdk_config="gf180mcu",
    )
    kwargs = dict(design=design, backend=BACKEND, budget=BUDGET)
    if BACKEND == "opencode":
        kwargs.update(opencode_cli_path=OPENCODE_CLI_PATH, opencode_model=OPENCODE_MODEL)
    runner = DigitalAutoresearchRunner(**kwargs)

    print(f"  design : {design.project_name()}")
    print(f"  specs  : {design.specs_description()}")
    print(f"  FoM    : {design.fom_description()}")
    print(f"  budget : {BUDGET} LibreLane evaluations")
    print(f"  knobs  : density x clock period x PDN pitch (discrete)")
    print(f"  backend: {BACKEND}")
    if BACKEND == "opencode":
        print(f"  model  : {OPENCODE_MODEL}")
    pause(args)


async def step4_real(args) -> None:
    banner(4, f"real DigitalAutoresearchRunner run (~{BUDGET} LibreLane evals)")
    if not RUN_REAL:
        print("  [rehearse] RUN_REAL=False; skipping.")
        print("  Flip RUN_REAL=True when env check + docker pull are clean.")
        print(f"  Budget = {BUDGET}, ~5-10 min per eval on this small counter.")
        print("  Artifacts: program.md + results.tsv under the runner's work dir.")
        return
    from eda_agents.core.designs.generic import GenericDesign
    from eda_agents.agents.digital_autoresearch import DigitalAutoresearchRunner

    design = GenericDesign(
        config_path=str(PROJ_DIR / "config.yaml"),
        pdk_root=os.environ.get("PDK_ROOT") or None,
        pdk_config="gf180mcu",
    )
    kwargs = dict(design=design, backend=BACKEND, budget=BUDGET)
    if BACKEND == "opencode":
        kwargs.update(opencode_cli_path=OPENCODE_CLI_PATH, opencode_model=OPENCODE_MODEL)
    runner = DigitalAutoresearchRunner(**kwargs)

    result = await runner.run(WORK_DIR)
    print(json.dumps({k: v for k, v in result.__dict__.items() if not k.startswith("_")},
                     indent=2, default=str))


def step5_read(args) -> None:
    banner(5, "inspect program.md + results.tsv")
    for fname in ("program.md", "results.tsv"):
        p = WORK_DIR / fname
        if p.exists():
            print(f"--- {p} ---")
            print(p.read_text()[:2000])
            print()
        else:
            print(f"{p} not yet written.")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-pause", action="store_true")
    args = parser.parse_args()

    step0_pip_install(args)
    step1_env_check(args)
    step2_stage_project(args)
    await step3_dry(args)
    await step4_real(args)
    step5_read(args)


if __name__ == "__main__":
    asyncio.run(main())
