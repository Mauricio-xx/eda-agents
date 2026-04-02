"""Autonomous circuit design exploration using autoresearch loop.

Adapted from Karpathy's autoresearch (https://github.com/karpathy/autoresearch)
to analog IC design. The topology is pluggable: pass --topology to select
the circuit type, or implement a new CircuitTopology subclass for your own
circuit (comparator, LDO, bandgap, etc.).

Demonstrates three exploration modes:
  - standalone: Pure autoresearch (tight LLM -> SPICE loop, no ADK)
  - hybrid: Autoresearch explores, then ADK validates + runs flow
  - adk: ADK-only (existing behavior)

Available topologies:
  - gf180_ota:  PMOS-input two-stage OTA on GF180MCU 180nm
  - miller_ota: NMOS-input Miller OTA on IHP SG13G2 130nm
  - aa_ota:     PMOS-input OTA from IHP AnalogAcademy 130nm

Usage:
    # Standalone autoresearch (no ADK, no project needed)
    python examples/07_autoresearch_circuit.py \
      --topology gf180_ota \
      --model zai/GLM-4.5-Flash \
      --budget 20

    # Dry run (validate setup, no LLM calls)
    python examples/07_autoresearch_circuit.py \
      --topology gf180_ota --budget 5 --dry-run

    # Hybrid: autoresearch explore + ADK downstream
    python examples/07_autoresearch_circuit.py \
      --topology gf180_ota \
      --model zai/GLM-4.7-Flash \
      --worker-model zai/GLM-4.5-Flash \
      --budget 30 \
      --mode hybrid \
      --project data/gf180-template

Environment:
    OPENROUTER_API_KEY or appropriate API key for the model provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path


def _resolve_topology(name: str):
    """Resolve topology name to class instance."""
    if name == "gf180_ota":
        from eda_agents.topologies.ota_gf180 import GF180OTATopology
        return GF180OTATopology()
    elif name == "miller_ota":
        from eda_agents.topologies.ota_miller import MillerOTATopology
        return MillerOTATopology()
    elif name == "aa_ota":
        from eda_agents.topologies.ota_analogacademy import AnalogAcademyOTATopology
        return AnalogAcademyOTATopology()
    else:
        raise ValueError(f"Unknown topology: {name}. Options: gf180_ota, miller_ota, aa_ota")


async def run_standalone(args):
    """Run standalone autoresearch (no ADK)."""
    from eda_agents.agents.autoresearch_runner import AutoresearchRunner

    topology = _resolve_topology(args.topology)
    work_dir = Path(args.output)

    if args.dry_run:
        print(f"Dry run: {topology.topology_name()}")
        print(f"  Model: {args.model}")
        print(f"  Budget: {args.budget}")
        print(f"  Design space: {topology.design_space()}")
        print(f"  Default params: {topology.default_params()}")
        print(f"  Specs: {topology.specs_description()}")

        # Validate SPICE pipeline
        from eda_agents.core.spice_runner import SpiceRunner
        runner = SpiceRunner(pdk=topology.pdk)
        missing = runner.validate_pdk()
        if missing:
            print(f"  PDK problems: {missing}")
        else:
            print("  PDK: OK")
            params = topology.default_params()
            sizing = topology.params_to_sizing(params)
            import tempfile
            cir = topology.generate_netlist(sizing, Path(tempfile.mkdtemp()))
            result = runner.run(cir)
            if result.success:
                fom = topology.compute_fom(result, sizing)
                print(f"  Default SPICE: Adc={result.Adc_dB:.1f}dB, "
                      f"GBW={result.GBW_Hz:.0f}Hz, PM={result.PM_deg:.1f}deg, "
                      f"FoM={fom:.2e}")
            else:
                print(f"  Default SPICE failed: {result.error}")
        return

    runner = AutoresearchRunner(
        topology=topology,
        model=args.model,
        budget=args.budget,
    )
    result = await runner.run(work_dir)

    print(f"\n{result.summary}")
    print(f"  Validity rate: {result.validity_rate:.0%}")
    print(f"  TSV log: {result.tsv_path}")
    if result.best_valid:
        print(f"  Best params: {json.dumps(result.best_params, indent=4)}")


async def run_hybrid(args):
    """Run hybrid mode (autoresearch + ADK)."""
    from eda_agents.agents.adk_agents import TrackDOrchestrator

    topology = _resolve_topology(args.topology)
    project_dir = Path(args.project) if args.project else None

    if project_dir is None:
        print("Error: --project required for hybrid mode")
        return

    orch = TrackDOrchestrator(
        project_dir=project_dir,
        topology=topology,
        model=args.model,
        worker_model=args.worker_model or args.model,
        budget_per_explorer=args.budget,
        exploration_mode="hybrid",
    )

    work_dir = Path(args.output)
    result = await orch.run(work_dir, dry_run=args.dry_run)

    if args.dry_run:
        print(f"Dry run (hybrid):")
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\nHybrid result:")
        if result.get("autoresearch_result"):
            ar = result["autoresearch_result"]
            print(f"  Autoresearch: {ar.summary}")
        if result.get("best_design"):
            print(f"  Best design: FoM={result['best_design']['fom']:.2e}")
        if result.get("agent_output"):
            print(f"  ADK output: {result['agent_output'][:500]}")


async def main():
    parser = argparse.ArgumentParser(
        description="Autonomous circuit design exploration"
    )
    parser.add_argument(
        "--topology", default="gf180_ota",
        help="Circuit topology (gf180_ota, miller_ota, aa_ota)",
    )
    parser.add_argument(
        "--model", default="zai/GLM-4.5-Flash",
        help="LLM model for proposals",
    )
    parser.add_argument(
        "--worker-model", default=None,
        help="Worker model for ADK sub-agents (hybrid mode)",
    )
    parser.add_argument(
        "--budget", type=int, default=20,
        help="SPICE evaluation budget",
    )
    parser.add_argument(
        "--mode", choices=["standalone", "hybrid", "adk"], default="standalone",
        help="Exploration mode",
    )
    parser.add_argument(
        "--project", default=None,
        help="LibreLane project directory (required for hybrid mode)",
    )
    parser.add_argument(
        "--output", default="autoresearch_results",
        help="Output directory",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate setup without running LLM",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    if args.mode == "hybrid":
        await run_hybrid(args)
    else:
        await run_standalone(args)


if __name__ == "__main__":
    asyncio.run(main())
