#!/usr/bin/env python3
"""Goertzel DSP autoresearch greedy loop on GF180MCU.

Drives the throughput-aware ``GoertzelDspDesign`` through the digital
autoresearch loop. The Nyquist hard floor on the FoM kills evals that
relax CLOCK_PERIOD past the audio sample rate, so the LLM cannot win
by parking at the design-space centroid -- the score requires a real
cycles-per-sample measurement that the cocotb throughput tb produces
in ``tb/sim_build/meas.json`` (or ``tb/meas.json`` depending on
where vvp runs).

Usage:

    # Plumbing smoke (no LLM, no LibreLane, no cocotb -- CI safe).
    # The mock path injects FlowMetrics PPA only; DSP columns will be
    # None and every eval will be invalid (FoM=0), which is the
    # correct semantics for "no cocotb sidecar". The smoke still
    # exercises runner construction, TSV header layout, and the
    # anti-centroid program.md regression checks.
    python examples/12_goertzel_dsp_autoresearch.py \\
      --use-mock-metrics fixtures/fake_flow_metrics.json \\
      --output /tmp/goertzel_smoke \\
      --budget 3

    # Real run with Gemini Flash. Requires a phase_idea-shaped project
    # with cocotb tb at <project>/tb/test_demo_goertzel_throughput.py
    # and a LibreLane config.yaml at <project>/config.yaml.
    python examples/12_goertzel_dsp_autoresearch.py \\
      --project-dir /home/montanares/i2o_claude_demo/idea_to_optimize/phase_idea \\
      --pdk-root /path/to/gf180mcu \\
      --model google/gemini-3-flash-preview \\
      --budget 5

    # Real run with Claude Code CLI as the proposer.
    python examples/12_goertzel_dsp_autoresearch.py \\
      --project-dir /path/to/phase_idea \\
      --pdk-root /path/to/gf180mcu \\
      --backend cc_cli \\
      --budget 5

    # Custom Nyquist target (e.g. 44.1 kHz audio).
    python examples/12_goertzel_dsp_autoresearch.py \\
      --project-dir /path/to/phase_idea \\
      --pdk-root /path/to/gf180mcu \\
      --fs-target 44100 \\
      --budget 5

Requires:
    pip install eda-agents[adk]
    export OPENROUTER_API_KEY=sk-or-...   # for --backend adk
    # GoertzelDspDesign expects the cocotb throughput tb to drop a
    # meas.json sidecar; see the digital-testbench-author agent +
    # digital.cocotb_testbench skill for the GL/SDF-safe pattern.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

DEFAULT_MODEL = "google/gemini-3-flash-preview"
DEFAULT_PROJECT_DIR = (
    "/home/montanares/i2o_claude_demo/idea_to_optimize/phase_idea"
)


def parse_fom_weights(raw: str | None) -> dict[str, float] | None:
    """Parse FoM weights from CLI string like 'timing=1.0,area=0.5,power=0.3'.

    Note: GoertzelDspDesign uses its own DSP-aware FoM formula and does
    not honour these weights -- the flag is accepted for symmetry with
    example 10 but currently has no effect on this design. Kept so
    users muscle-memory carries over.
    """
    if not raw:
        return None
    weights = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            print(f"Invalid FoM weight format: {pair!r}. Expected key=value.")
            sys.exit(1)
        key, val = pair.split("=", 1)
        key = key.strip()
        key_map = {"timing": "timing_w", "area": "area_w", "power": "power_w"}
        key = key_map.get(key, key)
        if key not in ("timing_w", "area_w", "power_w"):
            print(f"Unknown FoM weight: {key!r}. Valid: timing, area, power")
            sys.exit(1)
        weights[key] = float(val)
    return weights


async def main():
    parser = argparse.ArgumentParser(
        description=(
            "Goertzel DSP autoresearch greedy loop on GF180MCU "
            "(Nyquist-aware FoM)."
        )
    )
    parser.add_argument(
        "--project-dir", default=DEFAULT_PROJECT_DIR,
        help=(
            "Project root containing the Goertzel RTL, cocotb tb, and "
            f"LibreLane config.yaml. Default: {DEFAULT_PROJECT_DIR}"
        ),
    )
    parser.add_argument(
        "--pdk-root", default=None,
        help="Explicit PDK_ROOT path (required for live LibreLane runs).",
    )
    parser.add_argument(
        "--fs-target", type=float, default=8000.0,
        help=(
            "Nyquist floor in Hz; the FoM rejects designs producing "
            "throughput below this (default: 8000)."
        ),
    )
    parser.add_argument(
        "--dsp-w", type=float, default=2.0,
        help=(
            "Weight on the log10(throughput / fs_target) margin term "
            "in the FoM (default: 2.0; 10x margin -> +2.0, 100x -> +4.0)."
        ),
    )
    parser.add_argument(
        "--tb-module", default="test_demo_goertzel_throughput",
        help=(
            "cocotb test module name (without .py). "
            "Default: test_demo_goertzel_throughput."
        ),
    )
    parser.add_argument(
        "--tb-dir", default="tb",
        help="Sub-directory of project-dir holding the cocotb Makefile.",
    )
    parser.add_argument(
        "--fom-weights", default=None,
        help=(
            "Accepted for symmetry with example 10; GoertzelDspDesign "
            "uses its own DSP-aware FoM and ignores these weights."
        ),
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"LLM model for proposals (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--budget", type=int, default=5,
        help=(
            "Number of evaluation iterations (default: 5). Each eval "
            "runs LibreLane to stop_after, then cocotb if RTL sim is on."
        ),
    )
    parser.add_argument(
        "--strategy", default="flow",
        choices=["flow", "rtl", "hybrid"],
        help=(
            "Optimization strategy: flow (config-only), rtl (RTL "
            "edits), hybrid (RTL+config). Default: flow."
        ),
    )
    parser.add_argument(
        "--run-rtl-sim", action="store_true",
        help=(
            "Run cocotb post-LibreLane (rtl/hybrid only). The "
            "throughput tb writes meas.json which the GoertzelDspDesign "
            "sidecar reader picks up."
        ),
    )
    parser.add_argument(
        "--stop-after", default="ROUTE",
        help="LibreLane stop stage (SYNTH, FLOORPLAN, PLACE, CTS, ROUTE).",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output directory (default: autoresearch_goertzel/).",
    )
    parser.add_argument(
        "--use-mock-metrics", default=None,
        help=(
            "Path to mock FlowMetrics JSON (CI / plumbing smoke). "
            "DSP columns will be None in this path."
        ),
    )
    parser.add_argument(
        "--top-n", type=int, default=3,
        help="Number of top designs to keep (default: 3).",
    )
    parser.add_argument(
        "--backend", default="adk",
        choices=["adk", "cc_cli"],
        help=(
            "Proposal backend: adk (litellm API) or cc_cli (Claude "
            "Code CLI). Default: adk."
        ),
    )
    parser.add_argument(
        "--allow-dangerous", action="store_true",
        help=(
            "Enable --dangerously-skip-permissions for the cc_cli "
            "backend (also requires EDA_AGENTS_ALLOW_DANGEROUS=1)."
        ),
    )
    parser.add_argument(
        "--cli-path", default="claude",
        help="Path to claude CLI binary (default: claude).",
    )
    parser.add_argument(
        "--no-dedup", action="store_true",
        help="Disable parameter deduplication.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    project_dir = Path(args.project_dir).expanduser().resolve()
    if not args.use_mock_metrics:
        if not project_dir.is_dir():
            print(f"--project-dir does not exist: {project_dir}")
            sys.exit(1)
        cfg = project_dir / "config.yaml"
        if not cfg.is_file():
            print(f"Missing LibreLane config: {cfg}")
            sys.exit(1)

    if not args.use_mock_metrics:
        if args.backend == "adk" and not os.environ.get("OPENROUTER_API_KEY"):
            print("OPENROUTER_API_KEY not set (required for --backend adk)")
            sys.exit(1)
        if args.backend == "cc_cli":
            import shutil
            cli = shutil.which(args.cli_path)
            if not cli:
                print(f"Claude CLI not found: {args.cli_path}")
                sys.exit(1)

    from eda_agents.agents.digital_autoresearch import (
        DigitalAutoresearchRunner,
    )
    from eda_agents.core.designs.goertzel_dsp import GoertzelDspDesign
    from eda_agents.core.flow_stage import FlowStage

    try:
        stop_after = FlowStage[args.stop_after]
    except KeyError:
        valid = [s.name for s in FlowStage]
        print(f"Unknown stage: {args.stop_after}. Valid: {valid}")
        sys.exit(1)

    work_dir = (
        Path(args.output) if args.output else Path("autoresearch_goertzel")
    )
    mock_path = (
        Path(args.use_mock_metrics) if args.use_mock_metrics else None
    )

    design = GoertzelDspDesign(
        project_dir=project_dir,
        fs_target=args.fs_target,
        dsp_w=args.dsp_w,
        pdk_root=args.pdk_root,
        tb_module=args.tb_module,
        tb_dir=args.tb_dir,
    )

    print("=" * 60)
    print("Goertzel DSP Autoresearch")
    print("=" * 60)
    print(f"  Project:     {project_dir}")
    print(f"  Strategy:    {args.strategy}")
    print(f"  Backend:     {args.backend}")
    if args.backend == "adk":
        print(f"  Model:       {args.model}")
    print(f"  Budget:      {args.budget} evals")
    print(f"  Stop after:  {stop_after.name}")
    print(f"  fs_target:   {args.fs_target:.0f} Hz (Nyquist floor)")
    print(f"  dsp_w:       {args.dsp_w}")
    print(f"  Output:      {work_dir}")
    if mock_path:
        print(f"  Mock mode:   {mock_path}")
        print("               (DSP columns will be None; FoM=0 for "
              "every eval. This run validates plumbing only.)")
    if args.run_rtl_sim:
        print(f"  RTL sim:     enabled (tb={args.tb_module} in {args.tb_dir}/)")
    print(f"  Dedup:       {not args.no_dedup}")
    print()

    runner = DigitalAutoresearchRunner(
        design=design,
        model=args.model,
        budget=args.budget,
        stop_after=stop_after,
        dedup=not args.no_dedup,
        use_mock_metrics=mock_path,
        top_n=args.top_n,
        strategy=args.strategy,
        run_rtl_sim=args.run_rtl_sim,
        backend=args.backend,
        allow_dangerous=args.allow_dangerous,
        cli_path=args.cli_path,
    )

    t0 = time.monotonic()
    result = await runner.run(work_dir)
    elapsed = time.monotonic() - t0

    print("\n" + "=" * 60)
    print("Results")
    print("=" * 60)
    print(f"  Wall time:   {elapsed:.1f}s")
    print(f"  Evals done:  {result.total_evals}")
    print(f"  Kept:        {result.kept}")
    print(f"  Discarded:   {result.discarded}")
    print(f"  Best FoM:    {result.best_fom:.4f}")
    print(f"  Best valid:  {result.best_valid}")

    if result.best_params:
        print(f"  Best params: {json.dumps(result.best_params, indent=2)}")

    if result.top_n:
        print(f"\n  Top-{len(result.top_n)} designs:")
        for i, entry in enumerate(result.top_n, 1):
            params = json.dumps(entry["params"])
            print(f"    #{i}: FoM={entry['fom']:.2e} -- {params}")

    program_path = work_dir / "program.md"
    if program_path.is_file():
        print(f"\n  Program:     {program_path}")
    results_tsv = work_dir / "results.tsv"
    if results_tsv.is_file():
        print(f"  Results:     {results_tsv}")

    print(f"\n  Improvement: {result.improvement_rate:.0%}")


if __name__ == "__main__":
    asyncio.run(main())
