#!/usr/bin/env python3
"""RTL-to-GDS walk-through: 4-bit counter on GF180MCU.

Self-contained mirror of rtl2gds_counter.ipynb. Pure stdlib. Drop it
on any machine with Docker + Python 3.9+ and it runs.

Takes the counter from Verilog source to a manufacturable GDSII using
LibreLane inside the hpretl/iic-osic-tools Docker image. Every step
prints the exact command it runs.

PDK: we use the GF180MCU that ``ciel`` already installed inside the
image at ``/foss/pdks/gf180mcuD/``. No external PDK fork is cloned --
for a bare block like this counter (no padring, no SRAM), the built-in
PDK is sufficient. The main deck shows when the wafer-space fork
matters (chip-level padring designs).

Seven steps, gated by RUN_* flags at the top. By default everything
prints but nothing executes -- flip the flags to run for real.

Usage:
    python rtl2gds_counter.py            # pauses between steps (narrate)
    python rtl2gds_counter.py --no-pause # run straight through
    python rtl2gds_counter.py --help
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import textwrap
from pathlib import Path


# --- toggles: flip to True to actually execute a step. ---
RUN_PULL_IMAGE  = False   # ~15 GB one-time download
RUN_START_CTR   = False   # starts the long-running container
RUN_LIBRELANE   = False   # runs the flow (~1-2 min for the counter)

# --- container + image ---
IMAGE          = "hpretl/iic-osic-tools:next"
CONTAINER_NAME = "gf180"

# --- paths: HOST_* on your machine, CONTAINER_* inside the container ---
HOST_WORKSPACE    = Path.home() / "eda" / "designs"
HOST_PROJECT_DIR  = HOST_WORKSPACE / "counter_demo"
CONTAINER_WS      = "/foss/designs"
CONTAINER_PROJECT = f"{CONTAINER_WS}/counter_demo"

# --- GF180MCU PDK: built-in, shipped with the image by ciel. ---
CONTAINER_PDK_ROOT = "/foss/pdks"
PDK_NAME           = "gf180mcuD"
STD_CELL_LIB       = "gf180mcu_fd_sc_mcu7t5v0"


COUNTER_V = """\
// 4-bit synchronous up-counter with active-high reset.
// Tiny on purpose: the full LibreLane flow runs in ~1-2 minutes.

module counter (
    input  wire       clk,
    input  wire       rst,
    output wire [3:0] q
);
    reg [3:0] cnt;

    always @(posedge clk) begin
        if (rst)
            cnt <= 4'b0;
        else
            cnt <= cnt + 4'b1;
    end

    assign q = cnt;
endmodule
"""


CONFIG_YAML = """\
# GF180MCU LibreLane config -- minimal walk-through.
meta:
  version: 3
  flow: Classic
  substituting_steps:
    Magic.StreamOut: null
    KLayout.XOR: null

RUN_MAGIC_STREAMOUT: false
RUN_KLAYOUT_XOR: false

DESIGN_NAME: counter
VERILOG_FILES:
  - dir::counter.v
CLOCK_PORT: clk
CLOCK_PERIOD: 50

# Die area: 300x300 um absolute (plenty of room for 4 flops).
FP_SIZING: absolute
DIE_AREA: [0.0, 0.0, 300.0, 300.0]

# Power / ground nets.
VDD_NETS:
  - VDD
GND_NETS:
  - VSS

# Signoff / streamout.
PRIMARY_GDSII_STREAMOUT_TOOL: klayout

# Routing / ESD.
DIODE_ON_PORTS: in
RT_MAX_LAYER: Metal4
PDN_MULTILAYER: false

# Power distribution network (straps + pitch).
PDN_VWIDTH: 5
PDN_HWIDTH: 5
PDN_VSPACING: 1
PDN_HSPACING: 1
PDN_VPITCH: 75
PDN_HPITCH: 75
PDN_EXTEND_TO: boundary

# Placement.
PL_TARGET_DENSITY_PCT: 65
MAX_FANOUT_CONSTRAINT: 10

# CTS defaults.
CTS_CLK_MAX_WIRE_LENGTH: 0
CTS_DISTANCE_BETWEEN_BUFFERS: 0
CTS_SINK_CLUSTERING_SIZE: 20
CTS_SINK_CLUSTERING_MAX_DIAMETER: 60

# Margins.
TOP_MARGIN_MULT: 1
BOTTOM_MARGIN_MULT: 1
LEFT_MARGIN_MULT: 6
RIGHT_MARGIN_MULT: 6
"""


def run_or_print(cmd, do_it, *, shell_on_container=False, timeout=None):
    """Print the command; execute only if do_it is True.

    cmd: list of argv tokens, OR (when shell_on_container=True) a
    single bash script string to feed to `docker exec ... bash -lc`.
    """
    if shell_on_container:
        print(f"$ docker exec {CONTAINER_NAME} bash -lc '<script>'")
        print(textwrap.indent(cmd, "  | "))
    else:
        print("$ " + " ".join(cmd))
    if not do_it:
        print("  (skipped -- flip the RUN_* flag to execute)\n")
        return None
    print("  (executing...)")
    args = (
        ["docker", "exec", CONTAINER_NAME, "bash", "-lc", cmd]
        if shell_on_container else cmd
    )
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if proc.stdout.strip():
        print(proc.stdout[-4000:])
    if proc.returncode != 0 and proc.stderr.strip():
        print("STDERR (tail):")
        print(proc.stderr[-2000:])
    print(f"  returncode={proc.returncode}\n")
    return proc


# ------------------------------------------------------------------
# Steps
# ------------------------------------------------------------------

def step0_setup():
    HOST_WORKSPACE.mkdir(parents=True, exist_ok=True)
    HOST_PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Workspace on host: {HOST_WORKSPACE}")
    print(f"Project dir:       {HOST_PROJECT_DIR}")
    print(f"PDK (inside ctr):  {CONTAINER_PDK_ROOT}/{PDK_NAME}")


def step1_pull_image():
    run_or_print(["docker", "pull", IMAGE], RUN_PULL_IMAGE, timeout=1800)


def step2_start_container():
    start_cmd = [
        "docker", "run", "-d", "--name", CONTAINER_NAME,
        "-v", f"{HOST_WORKSPACE}:{CONTAINER_WS}:rw",
        "--user", f"{os.getuid()}:{os.getgid()}",
        IMAGE,
        "--skip", "sleep", "infinity",
    ]
    run_or_print(start_cmd, RUN_START_CTR)
    # Cheap status check either way.
    subprocess.run(["docker", "ps", "--filter", f"name={CONTAINER_NAME}"])


def step3_write_rtl():
    (HOST_PROJECT_DIR / "counter.v").write_text(COUNTER_V)
    print(f"Wrote {HOST_PROJECT_DIR / 'counter.v'}")
    print(COUNTER_V)


def step4_write_config():
    config_path = HOST_PROJECT_DIR / "config.yaml"
    config_path.write_text(CONFIG_YAML)
    print(f"Wrote {config_path} ({len(CONFIG_YAML.splitlines())} lines)")
    for i, line in enumerate(CONFIG_YAML.splitlines()[:25], 1):
        print(f"{i:>2}  {line}")
    print("  ...")


def step5_run_librelane():
    script = textwrap.dedent(f"""
        set -euo pipefail
        cd {CONTAINER_PROJECT}
        source sak-pdk-script.sh {PDK_NAME} {STD_CELL_LIB}
        librelane config.yaml \\
            --pdk {PDK_NAME} \\
            --pdk-root {CONTAINER_PDK_ROOT} \\
            --manual-pdk \\
            --run-tag demo
    """).strip()
    run_or_print(script, RUN_LIBRELANE, shell_on_container=True, timeout=900)


def step6_parse_metrics():
    metrics_path = HOST_PROJECT_DIR / "runs" / "demo" / "final" / "metrics.csv"
    wanted = [
        "design__die__area__um2",
        "design__instance__count__stdcell",
        "timing__setup_vio__count",
        "timing__hold_vio__count",
        "magic__drc_error__count",
        "klayout__drc_error__count",
        "design__lvs_error__count",
        "power__total",
    ]
    if not metrics_path.exists():
        print(f"metrics.csv not found: {metrics_path}")
        print("Did Step 5 complete? Set RUN_LIBRELANE = True and re-run.")
        return
    print(f"Reading {metrics_path}\n")
    found = {}
    with metrics_path.open() as fh:
        for row in csv.reader(fh):
            if row and row[0] in wanted:
                found[row[0]] = row[1] if len(row) > 1 else ""
    for key in wanted:
        print(f"  {key:45s} {found.get(key, '(missing)')}")


def step7_show_render():
    """LibreLane's KLayout.Render step already produces the PNG we want."""
    png_path = HOST_PROJECT_DIR / "runs/demo/final/render/counter.png"
    gds_path = HOST_PROJECT_DIR / "runs/demo/final/gds/counter.gds"
    if png_path.exists():
        print(f"LibreLane render: {png_path}")
    elif gds_path.exists():
        print(f"Render PNG missing: {png_path}")
        print(f"GDS is there though: {gds_path}")
        print(f"  open it with:  klayout {gds_path}")
    else:
        print("No artifacts found. Did Step 5 succeed?")


STEPS = [
    ("Step 0  Configuration + workspace",      step0_setup),
    ("Step 1  Pull the toolchain image",       step1_pull_image),
    ("Step 2  Start a long-running container", step2_start_container),
    ("Step 3  Write the counter RTL",          step3_write_rtl),
    ("Step 4  Write the LibreLane config",     step4_write_config),
    ("Step 5  Run LibreLane",                  step5_run_librelane),
    ("Step 6  Read the metrics",               step6_parse_metrics),
    ("Step 7  Show the final-layout render",   step7_show_render),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="Do not pause between steps.",
    )
    args = parser.parse_args(argv)
    paused = not args.no_pause

    for title, func in STEPS:
        print(f"\n=== {title} ===")
        if paused:
            input("   (press Enter to continue) ")
        func()

    print("\nDone. Artifacts under:", HOST_PROJECT_DIR / "runs" / "demo" / "final")
    return 0


if __name__ == "__main__":
    sys.exit(main())
