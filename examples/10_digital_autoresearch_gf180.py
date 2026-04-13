#!/usr/bin/env python3
"""Digital autoresearch greedy loop for GF180MCU.

Runs an LLM-guided exploration loop over flow config knobs
(PL_TARGET_DENSITY_PCT, CLOCK_PERIOD, etc.) using the autoresearch
greedy algorithm. Each evaluation runs LibreLane up to
``stop_after`` (default: ROUTE) to keep per-eval cost down.

Usage:
    # Dry run with mock metrics (no LLM, no LibreLane, CI-safe)
    python examples/10_digital_autoresearch_gf180.py \\
      --use-mock-metrics fixtures/fake_flow_metrics.json \\
      --budget 3

    # Real run with Gemini Flash (fazyrv frv_1 macro)
    python examples/10_digital_autoresearch_gf180.py \\
      --model google/gemini-3-flash-preview \\
      --budget 5

    # Config mode: optimize any LibreLane project (no Python class)
    python examples/10_digital_autoresearch_gf180.py \\
      --config /tmp/matmul_e2e/config.yaml \\
      --pdk-root /path/to/gf180mcu \\
      --model google/gemini-3-flash-preview \\
      --budget 5

    # Custom FoM weights (prioritize area over timing)
    python examples/10_digital_autoresearch_gf180.py \\
      --config /path/to/config.yaml \\
      --fom-weights timing=0.5,area=1.0,power=0.3 \\
      --budget 5

    # Real run, stop at synthesis only (faster per eval)
    python examples/10_digital_autoresearch_gf180.py \\
      --model google/gemini-3-flash-preview \\
      --stop-after SYNTH \\
      --budget 5

Requires:
    pip install eda-agents[adk]
    export OPENROUTER_API_KEY=sk-or-...
    scripts/fetch_digital_designs.sh  (for --design mode)
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


def parse_fom_weights(raw: str | None) -> dict[str, float] | None:
    """Parse FoM weights from CLI string like 'timing=1.0,area=0.5,power=0.3'."""
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
        # Normalize short names to internal keys
        key_map = {"timing": "timing_w", "area": "area_w", "power": "power_w"}
        key = key_map.get(key, key)
        if key not in ("timing_w", "area_w", "power_w"):
            print(f"Unknown FoM weight: {key!r}. Valid: timing, area, power")
            sys.exit(1)
        weights[key] = float(val)
    return weights


def load_design(name: str, macro: str = "frv_1"):
    """Load a DigitalDesign by name."""
    if name == "fazyrv_hachure":
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign
        return FazyRvHachureDesign(macro=macro)
    elif name == "systolic_mac":
        from eda_agents.core.designs.systolic_mac_dft import SystolicMacDftDesign
        return SystolicMacDftDesign()
    else:
        print(f"Unknown design: {name}")
        sys.exit(1)


def load_design_from_config(
    config_path: str,
    pdk_root: str | None,
    fom_weights: dict[str, float] | None = None,
):
    """Load a GenericDesign from a LibreLane config file."""
    from eda_agents.core.designs.generic import GenericDesign

    return GenericDesign(
        config_path=config_path,
        pdk_root=pdk_root,
        fom_weights=fom_weights,
    )


async def main():
    parser = argparse.ArgumentParser(
        description="Digital autoresearch greedy loop for GF180MCU"
    )

    # Entry mode: named design or config file (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--design", default=None,
        choices=["fazyrv_hachure", "systolic_mac"],
        help="Named design with Python wrapper (default if neither --design nor --config)",
    )
    mode_group.add_argument(
        "--config", default=None,
        help="Path to LibreLane config (YAML/JSON). Creates a GenericDesign.",
    )

    parser.add_argument(
        "--pdk-root", default=None,
        help="Explicit PDK_ROOT path (recommended for --config)",
    )
    parser.add_argument(
        "--macro", default="frv_1",
        help="Macro for fazyrv (default: frv_1)",
    )
    parser.add_argument(
        "--fom-weights", default=None,
        help="FoM weights as key=value pairs: timing=1.0,area=0.5,power=0.3",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"LLM model for proposals (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--budget", type=int, default=5,
        help="Number of evaluation iterations (default: 5). "
             "Each eval runs LibreLane (~5-20 min).",
    )
    parser.add_argument(
        "--strategy", default="flow",
        choices=["flow", "rtl", "hybrid"],
        help="Optimization strategy: flow (config-only), rtl (RTL edits), "
             "hybrid (RTL + config). Default: flow",
    )
    parser.add_argument(
        "--run-rtl-sim", action="store_true",
        help="Run RTL simulation after lint (rtl/hybrid only, needs testbench)",
    )
    parser.add_argument(
        "--stop-after", default="ROUTE",
        help="Stop flow at this stage (default: ROUTE). "
             "Options: SYNTH, FLOORPLAN, PLACE, CTS, ROUTE",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output directory (default: autoresearch_digital/)",
    )
    parser.add_argument(
        "--use-mock-metrics", default=None,
        help="Path to mock metrics JSON (CI mode, no real flow runs)",
    )
    parser.add_argument(
        "--top-n", type=int, default=3,
        help="Number of top designs to keep (default: 3)",
    )
    parser.add_argument(
        "--backend", default="adk",
        choices=["adk", "cc_cli"],
        help="Proposal backend: adk (litellm API), cc_cli (Claude Code CLI). "
             "Default: adk. Use cc_cli for RTL-aware strategies.",
    )
    parser.add_argument(
        "--allow-dangerous", action="store_true",
        help="Enable --dangerously-skip-permissions for CC CLI backend "
             "(also requires EDA_AGENTS_ALLOW_DANGEROUS=1)",
    )
    parser.add_argument(
        "--cli-path", default="claude",
        help="Path to claude CLI binary (default: claude)",
    )
    parser.add_argument(
        "--no-dedup", action="store_true",
        help="Disable parameter deduplication",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    # Default to fazyrv_hachure if no mode specified
    if not args.design and not args.config:
        args.design = "fazyrv_hachure"

    # Parse FoM weights
    fom_weights = parse_fom_weights(args.fom_weights)

    # Load design
    if args.config:
        design = load_design_from_config(args.config, args.pdk_root, fom_weights)
    else:
        design = load_design(args.design, macro=args.macro)

    # Validate
    problems = design.validate_clone()
    if problems and not args.use_mock_metrics:
        print("Design issues:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)

    # Check env
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
    from eda_agents.core.flow_stage import FlowStage

    try:
        stop_after = FlowStage[args.stop_after]
    except KeyError:
        valid = [s.name for s in FlowStage]
        print(f"Unknown stage: {args.stop_after}. Valid: {valid}")
        sys.exit(1)

    work_dir = Path(args.output) if args.output else Path("autoresearch_digital")
    mock_path = Path(args.use_mock_metrics) if args.use_mock_metrics else None

    mode = "config" if args.config else "expert"
    print("=" * 60)
    print("Digital Autoresearch")
    print("=" * 60)
    print(f"  Mode:        {mode}")
    print(f"  Strategy:    {args.strategy}")
    print(f"  Backend:     {args.backend}")
    print(f"  Design:      {design.project_name()}")
    if args.backend == "adk":
        print(f"  Model:       {args.model}")
    print(f"  Budget:      {args.budget} evals")
    print(f"  Stop after:  {stop_after.name}")
    print(f"  Output:      {work_dir}")
    if fom_weights:
        print(f"  FoM weights: {fom_weights}")
    if mock_path:
        print(f"  Mock mode:   {mock_path}")
    if args.strategy != "flow":
        rtl_lines = design.rtl_total_lines()
        print(f"  RTL lines:   {rtl_lines}")
        if args.run_rtl_sim:
            print("  RTL sim:     enabled")
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
            print(f"    #{i}: FoM={entry['fom']:.2e} -- {json.dumps(entry['params'])}")

    # Show program.md path
    program_path = work_dir / "program.md"
    if program_path.is_file():
        print(f"\n  Program:     {program_path}")
    results_tsv = work_dir / "results.tsv"
    if results_tsv.is_file():
        print(f"  Results:     {results_tsv}")

    print(f"\n  Improvement: {result.improvement_rate:.0%}")


if __name__ == "__main__":
    asyncio.run(main())
