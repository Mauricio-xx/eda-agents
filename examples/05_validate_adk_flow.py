#!/usr/bin/env python3
"""End-to-end validation of the ADK agent flow with GF180 OTA.

Validates:
  Phase 1: SPICE pipeline (topology -> sizing -> netlist -> ngspice -> FoM)
  Phase 2: Single DesignExplorerAgent with a free model via OpenRouter
  Phase 3: TrackDOrchestrator multi-agent pipeline (dry-run or full)

Usage:
    # Dry run (no LLM, just SPICE pipeline)
    python examples/05_validate_adk_flow.py --dry-run

    # Single agent with free model (needs OPENROUTER_API_KEY)
    python examples/05_validate_adk_flow.py --budget 5

    # Full orchestrator
    python examples/05_validate_adk_flow.py --orchestrator --budget 5

    # Custom model
    python examples/05_validate_adk_flow.py --model openrouter/google/gemini-2.0-flash-exp:free

Requires:
    pip install eda-agents[adk]
    export OPENROUTER_API_KEY=sk-or-...
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

# Default free model via OpenRouter/LiteLLM
# Free models have aggressive rate limits; retry logic is built in.
DEFAULT_MODEL = "zai/GLM-4.7-Flash"
FALLBACK_MODELS = [
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/qwen/qwen3-coder:free",
    "openrouter/mistralai/mistral-small-3.1-24b-instruct:free",
]


def check_env():
    """Validate environment before running."""
    issues = []

    # Check API key
    if not os.environ.get("OPENROUTER_API_KEY"):
        issues.append(
            "OPENROUTER_API_KEY not set. "
            "Export it: export OPENROUTER_API_KEY=sk-or-..."
        )

    # Check ngspice
    import shutil
    if not shutil.which("ngspice"):
        issues.append("ngspice not found in PATH")

    # Check ADK
    try:
        from google.adk.agents import LlmAgent  # noqa: F401
    except ImportError:
        issues.append("google-adk not installed. Run: pip install eda-agents[adk]")

    return issues


def validate_spice_pipeline():
    """Phase 1: Validate the full SPICE pipeline without LLM."""
    from eda_agents.core.spice_runner import SpiceRunner
    from eda_agents.topologies.ota_gf180 import GF180OTATopology

    print("=" * 60)
    print("Phase 1: SPICE Pipeline Validation")
    print("=" * 60)

    topo = GF180OTATopology()
    print(f"  Topology: {topo.topology_name()}")
    print(f"  PDK:      {topo.pdk.display_name}")
    print(f"  Specs:    {topo.specs_description()}")

    runner = SpiceRunner(pdk=topo.pdk)
    missing = runner.validate_pdk()
    if missing:
        print(f"  FAIL: Missing PDK files: {missing}")
        return False

    print(f"  PDK root: {runner.pdk_root}")

    work = Path(tempfile.mkdtemp(prefix="validate-spice-"))
    params = topo.default_params()
    sizing = topo.params_to_sizing(params)
    cir = topo.generate_netlist(sizing, work)
    result = runner.run(cir, work)

    if not result.success:
        print(f"  FAIL: {result.error}")
        if result.stderr_tail:
            print(f"  stderr: {result.stderr_tail[-300:]}")
        return False

    fom = topo.compute_fom(result, sizing)
    valid, violations = topo.check_validity(result, sizing)

    print(f"  Adc:   {result.Adc_dB:.1f} dB")
    print(f"  GBW:   {result.GBW_Hz / 1e3:.0f} kHz")
    print(f"  PM:    {result.PM_deg:.1f} deg")
    print(f"  FoM:   {fom:.2e}")
    print(f"  Valid: {valid}  Violations: {violations}")
    print(f"  Time:  {result.sim_time_s:.2f}s")
    print("  PASS")
    return True


def validate_simulate_tool():
    """Validate that _make_simulate_tool works correctly."""
    print("\n" + "=" * 60)
    print("Phase 1b: Simulate Tool Validation")
    print("=" * 60)

    from eda_agents.agents.adk_agents import _make_simulate_tool
    from eda_agents.topologies.ota_gf180 import GF180OTATopology

    topo = GF180OTATopology()
    work = Path(tempfile.mkdtemp(prefix="validate-tool-"))

    tool = _make_simulate_tool(topo, topo.pdk, work, budget=3)
    fn = tool.func  # access underlying callable

    # Call with default params
    params = topo.default_params()
    result = fn(**params)

    print(f"  Tool result keys: {list(result.keys())}")
    if result.get("success"):
        print(f"  FoM:    {result['fom']:.2e}")
        print(f"  Valid:  {result['valid']}")
        print(f"  Budget remaining: {result['budget_remaining']}")
    else:
        print(f"  Error: {result.get('error')}")

    # Test budget exhaustion
    fn(**params)  # eval 2
    fn(**params)  # eval 3
    exhausted = fn(**params)  # eval 4 > budget 3
    assert "Budget exhausted" in exhausted.get("error", ""), "Budget enforcement broken"
    print("  Budget enforcement: OK")
    print("  PASS")
    return True


async def validate_single_agent(model: str, budget: int):
    """Phase 2: Run a single DesignExplorerAgent with a free model."""
    print("\n" + "=" * 60)
    print(f"Phase 2: Single Agent (model={model}, budget={budget})")
    print("=" * 60)

    from eda_agents.agents.adk_agents import DesignExplorerAgent
    from eda_agents.topologies.ota_gf180 import GF180OTATopology

    topo = GF180OTATopology()
    work_dir = Path(tempfile.mkdtemp(prefix="validate-agent-"))

    agent = DesignExplorerAgent(
        topology=topo,
        model=model,
        budget=budget,
    )

    print(f"  Work dir: {work_dir}")
    print(f"  Launching agent...")

    try:
        result = await agent.run(work_dir)
        print(f"  Agent:    {result.get('agent')}")
        print(f"  Topology: {result.get('topology')}")
        print(f"  PDK:      {result.get('pdk')}")

        result_text = result.get("result", "")
        if result_text:
            # Truncate for display
            lines = result_text.strip().split("\n")
            print(f"  Response ({len(lines)} lines):")
            for line in lines[:15]:
                print(f"    {line}")
            if len(lines) > 15:
                print(f"    ... ({len(lines) - 15} more lines)")

        # Check what evals were generated
        eval_dirs = sorted(work_dir.glob("eval_*"))
        print(f"  SPICE evals run: {len(eval_dirs)}")
        print("  PASS")
        return True

    except Exception as e:
        print(f"  FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


async def validate_orchestrator(model: str, budget: int, dry_run: bool):
    """Phase 3: Run TrackDOrchestrator."""
    mode = "dry-run" if dry_run else "full"
    print("\n" + "=" * 60)
    print(f"Phase 3: Orchestrator ({mode}, model={model})")
    print("=" * 60)

    from eda_agents.agents.adk_agents import TrackDOrchestrator
    from eda_agents.topologies.ota_gf180 import GF180OTATopology

    topo = GF180OTATopology()
    work_dir = Path(tempfile.mkdtemp(prefix="validate-orch-"))

    orch = TrackDOrchestrator(
        topology=topo,
        model=model,
        n_explorers=1,
        budget_per_explorer=budget,
    )

    print(f"  Work dir: {work_dir}")
    print(f"  Launching orchestrator...")

    try:
        result = await orch.run(work_dir, dry_run=dry_run)
        print(f"  Topology: {result.get('topology')}")
        print(f"  PDK:      {result.get('pdk')}")
        print(f"  Phases:   {list(result.get('phases', {}).keys())}")

        if dry_run:
            print(f"  Dry run:  {result.get('dry_run')}")
        else:
            phases = result.get("phases", {})
            for phase_name, phase_data in phases.items():
                if isinstance(phase_data, dict):
                    status = "skipped" if phase_data.get("skipped") else "done"
                    print(f"  {phase_name}: {status}")

        print("  PASS")
        return True

    except Exception as e:
        print(f"  FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    parser = argparse.ArgumentParser(
        description="Validate ADK agent flow end-to-end"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"LLM model to use (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--budget", type=int, default=5,
        help="SPICE evaluation budget per agent (default: 5)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Only validate SPICE pipeline, skip LLM"
    )
    parser.add_argument(
        "--orchestrator", action="store_true",
        help="Run full orchestrator instead of single agent"
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

    # Phase 1: SPICE pipeline (always runs)
    if not validate_spice_pipeline():
        print("\nSPICE pipeline validation failed. Fix before proceeding.")
        sys.exit(1)

    if not validate_simulate_tool():
        print("\nSimulate tool validation failed.")
        sys.exit(1)

    if args.dry_run:
        print("\n" + "=" * 60)
        print("Dry run complete. SPICE pipeline is working.")
        print(f"To run with LLM: export OPENROUTER_API_KEY=sk-or-...")
        print(f"Then: python examples/05_validate_adk_flow.py --budget {args.budget}")
        print("=" * 60)
        sys.exit(0)

    # Check env for LLM phases
    issues = check_env()
    if issues:
        print(f"\nEnvironment issues:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)

    # Phase 2/3: Agent execution
    if args.orchestrator:
        ok = await validate_orchestrator(args.model, args.budget, dry_run=False)
    else:
        ok = await validate_single_agent(args.model, args.budget)

    if not ok:
        # Try fallback models
        for fallback in FALLBACK_MODELS:
            print(f"\nRetrying with fallback model: {fallback}")
            if args.orchestrator:
                ok = await validate_orchestrator(fallback, args.budget, dry_run=False)
            else:
                ok = await validate_single_agent(fallback, args.budget)
            if ok:
                break

    print("\n" + "=" * 60)
    print(f"Final result: {'PASS' if ok else 'FAIL'}")
    print("=" * 60)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
