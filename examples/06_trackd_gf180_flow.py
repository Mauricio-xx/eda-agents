#!/usr/bin/env python3
"""Track D multi-agent RTL-to-GDS flow for GF180MCU.

Demonstrates the TrackDOrchestrator: a master ADK agent that delegates
to specialized sub-agents (FlowRunner, DRCChecker, DRCFixer, LVSChecker,
and optionally SizingExplorer + CornerValidator for analog blocks).

Usage:
    # Dry run (validate agent setup, no LLM calls)
    python examples/06_trackd_gf180_flow.py \\
      --project data/gf180-template --dry-run

    # Digital-only hardening flow
    python examples/06_trackd_gf180_flow.py \\
      --project data/gf180-template \\
      --model openrouter/stepfun/step-3.5-flash:free \\
      --max-drc-iter 3

    # Analog + digital flow (sizing exploration + hardening)
    python examples/06_trackd_gf180_flow.py \\
      --project data/gf180-template \\
      --topology gf180_ota \\
      --budget 10 \\
      --model openrouter/stepfun/step-3.5-flash:free

    # Use Gemini directly (needs GOOGLE_API_KEY)
    python examples/06_trackd_gf180_flow.py \\
      --project data/gf180-template \\
      --model gemini-2.0-flash

Requires:
    pip install eda-agents[adk]
    export OPENROUTER_API_KEY=sk-or-...  (for non-Gemini models)
    # Or: export GOOGLE_API_KEY=...      (for Gemini)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

DEFAULT_MODEL = "zai/GLM-4.7-Flash"

TOPOLOGIES = {
    "gf180_ota": "eda_agents.topologies.ota_gf180:GF180OTATopology",
    "miller_ota": "eda_agents.topologies.ota_miller:MillerOTATopology",
    "analogacademy_ota": "eda_agents.topologies.ota_analogacademy:AnalogAcademyOTATopology",
}


def load_topology(name: str):
    """Load a topology class by name."""
    if name not in TOPOLOGIES:
        print(f"Unknown topology: {name}")
        print(f"Available: {', '.join(sorted(TOPOLOGIES))}")
        sys.exit(1)

    module_path, class_name = TOPOLOGIES[name].rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls()


def check_env(model: str):
    """Validate environment."""
    issues = []

    if model.startswith("openrouter/"):
        if not os.environ.get("OPENROUTER_API_KEY"):
            issues.append("OPENROUTER_API_KEY not set")
    elif model.startswith("gemini"):
        if not os.environ.get("GOOGLE_API_KEY"):
            issues.append("GOOGLE_API_KEY not set")

    try:
        from google.adk.agents import LlmAgent  # noqa: F401
    except ImportError:
        issues.append("google-adk not installed. Run: pip install eda-agents[adk]")

    return issues


def validate_project(project_dir: Path):
    """Check that the project directory is valid."""
    if not project_dir.is_dir():
        print(f"Project directory not found: {project_dir}")
        print("Clone the template: git clone https://github.com/wafer-space/gf180mcu-project-template data/gf180-template")
        sys.exit(1)

    config = project_dir / "config.json"
    if not config.is_file():
        print(f"Config not found: {config}")
        sys.exit(1)

    data = json.loads(config.read_text())
    print(f"  Design:  {data.get('DESIGN_NAME', 'unknown')}")
    print(f"  Config:  {config}")


async def run_dry(args):
    """Dry run: build agents, show hierarchy, no LLM calls."""
    from eda_agents.agents.adk_agents import TrackDOrchestrator

    topology = load_topology(args.topology) if args.topology else None
    project_dir = Path(args.project)

    print("=" * 60)
    print("Track D Dry Run")
    print("=" * 60)
    validate_project(project_dir)

    orch = TrackDOrchestrator(
        project_dir=project_dir,
        topology=topology,
        model=args.model,
        worker_model=args.worker_model,
        n_explorers=args.n_explorers,
        budget_per_explorer=args.budget,
        max_drc_iterations=args.max_drc_iter,
    )

    # Check LibreLane setup
    problems = orch.runner.validate_setup()
    if problems:
        print("\n  LibreLane setup issues:")
        for p in problems:
            print(f"    - {p}")
    else:
        print("  LibreLane: OK")

    work_dir = Path(args.output) if args.output else Path("trackd_results")
    result = await orch.run(work_dir, dry_run=True)

    print(f"\n  Master agent: {result['master_agent']}")
    print(f"  Sub-agents:   {', '.join(result['sub_agents'])}")
    print(f"  Topology:     {result.get('topology') or 'none (digital only)'}")
    print(f"  PDK:          {result['pdk']}")
    print("  PASS")


async def run_full(args):
    """Full run: execute the multi-agent flow."""
    from eda_agents.agents.adk_agents import TrackDOrchestrator

    topology = load_topology(args.topology) if args.topology else None
    project_dir = Path(args.project)

    print("=" * 60)
    print("Track D Multi-Agent Flow")
    print("=" * 60)
    validate_project(project_dir)

    issues = check_env(args.model)
    if issues:
        print("\nEnvironment issues:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)

    orch = TrackDOrchestrator(
        project_dir=project_dir,
        topology=topology,
        model=args.model,
        worker_model=args.worker_model,
        n_explorers=args.n_explorers,
        budget_per_explorer=args.budget,
        max_drc_iterations=args.max_drc_iter,
    )

    work_dir = Path(args.output) if args.output else Path("trackd_results")

    print(f"  Model:        {args.model}")
    if args.worker_model:
        print(f"  Worker model: {args.worker_model}")
    print(f"  Output:       {work_dir}")
    if topology:
        print(f"  Topology:     {topology.topology_name()}")
        print(f"  Explorers:    {args.n_explorers} x {args.budget} evals")
    print(f"  DRC iters:    {args.max_drc_iter}")
    print("\n  Launching orchestrator...\n")

    try:
        result = await orch.run(work_dir)

        print("\n" + "=" * 60)
        print("Results")
        print("=" * 60)
        print(f"  Project:  {result.get('project_dir')}")
        print(f"  Topology: {result.get('topology') or 'digital only'}")
        print(f"  PDK:      {result.get('pdk')}")

        spice_evals = result.get("spice_evals", [])
        if spice_evals:
            valid = sum(1 for e in spice_evals if e.get("valid"))
            print(f"  SPICE evals: {len(spice_evals)} ({valid} valid)")

        best = result.get("best_design")
        if best:
            print(f"  Best FoM:    {best.get('fom', 0):.2e}")
            print(f"  Best params: {best.get('params', {})}")

        # Show agent output (truncated)
        output = result.get("agent_output", "")
        if output:
            lines = output.strip().split("\n")
            print(f"\n  Agent output ({len(lines)} lines):")
            for line in lines[:20]:
                print(f"    {line}")
            if len(lines) > 20:
                print(f"    ... ({len(lines) - 20} more lines)")

        # Save full results
        results_file = work_dir / "trackd_results.json"
        results_file.parent.mkdir(parents=True, exist_ok=True)
        # Remove non-serializable fields
        serializable = {k: v for k, v in result.items() if k != "agent_output"}
        serializable["agent_output_lines"] = len(output.split("\n")) if output else 0
        results_file.write_text(json.dumps(serializable, indent=2, default=str))
        print(f"\n  Results saved: {results_file}")

        print("\n  PASS")

    except Exception as e:
        print(f"\n  FAIL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


async def main():
    parser = argparse.ArgumentParser(
        description="Track D multi-agent RTL-to-GDS flow"
    )
    parser.add_argument(
        "--project", required=True,
        help="Path to LibreLane project directory (with config.json)"
    )
    parser.add_argument(
        "--topology", default=None,
        help=f"Analog topology to include. Options: {', '.join(sorted(TOPOLOGIES))}"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"LLM model for orchestrator (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--worker-model", default=None,
        help="LLM model for worker agents (explorers, checkers). "
             "Defaults to --model. Use a different model to avoid "
             "concurrency limits (e.g., zai/GLM-4.5-Flash)."
    )
    parser.add_argument(
        "--budget", type=int, default=10,
        help="SPICE budget per explorer (default: 10)"
    )
    parser.add_argument(
        "--n-explorers", type=int, default=2,
        help="Number of parallel explorers (default: 2)"
    )
    parser.add_argument(
        "--max-drc-iter", type=int, default=3,
        help="Max DRC fix iterations (default: 3)"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output directory (default: trackd_results)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate setup without running LLM agents"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.dry_run:
        await run_dry(args)
    else:
        await run_full(args)


if __name__ == "__main__":
    asyncio.run(main())
