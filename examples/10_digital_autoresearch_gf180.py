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

    # Real run, stop at synthesis only (faster per eval)
    python examples/10_digital_autoresearch_gf180.py \\
      --model google/gemini-3-flash-preview \\
      --stop-after SYNTH \\
      --budget 5

Requires:
    pip install eda-agents[adk]
    export OPENROUTER_API_KEY=sk-or-...
    scripts/fetch_digital_designs.sh
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


async def main():
    parser = argparse.ArgumentParser(
        description="Digital autoresearch greedy loop for GF180MCU"
    )
    parser.add_argument(
        "--design", default="fazyrv_hachure",
        choices=["fazyrv_hachure", "systolic_mac"],
        help="Target design (default: fazyrv_hachure)",
    )
    parser.add_argument(
        "--macro", default="frv_1",
        help="Macro for fazyrv (default: frv_1)",
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
        if not os.environ.get("OPENROUTER_API_KEY"):
            print("OPENROUTER_API_KEY not set")
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

    print("=" * 60)
    print("Digital Autoresearch")
    print("=" * 60)
    print(f"  Design:      {design.project_name()}")
    print(f"  Model:       {args.model}")
    print(f"  Budget:      {args.budget} evals")
    print(f"  Stop after:  {stop_after.name}")
    print(f"  Output:      {work_dir}")
    if mock_path:
        print(f"  Mock mode:   {mock_path}")
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
    )

    t0 = time.monotonic()
    result = await runner.run(work_dir)
    elapsed = time.monotonic() - t0

    print("\n" + "=" * 60)
    print("Results")
    print("=" * 60)
    print(f"  Wall time:   {elapsed:.1f}s")
    print(f"  Evals done:  {result.get('evals_completed', 0)}")
    print(f"  Best FoM:    {result.get('best_fom', 0):.4f}")

    best = result.get("best_params", {})
    if best:
        print(f"  Best params: {json.dumps(best, indent=2)}")

    # Show program.md path
    program_path = work_dir / "program.md"
    if program_path.is_file():
        print(f"\n  Program:     {program_path}")
    results_tsv = work_dir / "results.tsv"
    if results_tsv.is_file():
        print(f"  Results:     {results_tsv}")

    print(f"\n  Total cost:  {elapsed:.0f}s wall time")


if __name__ == "__main__":
    asyncio.run(main())
