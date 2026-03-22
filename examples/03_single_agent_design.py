#!/usr/bin/env python3
"""Single ADK agent designs a GF180 OTA circuit.

Demonstrates using DesignExplorerAgent with a GF180 OTA topology.
The agent uses SPICE-in-the-loop evaluation to find optimal sizing.

Usage:
    export GOOGLE_API_KEY=...  # or OPENROUTER_API_KEY
    python examples/03_single_agent_design.py [--model gemini-2.0-flash] [--budget 20]

Requires: pip install eda-agents[adk]
"""

import argparse
import asyncio
from pathlib import Path


async def main(model: str, budget: int, dry_run: bool):
    from eda_agents.topologies.ota_gf180 import GF180OTATopology

    topo = GF180OTATopology()
    print(f"Topology: {topo.topology_name()}")
    print(f"PDK: {topo.pdk.display_name}")
    print(f"Model: {model}")
    print(f"Budget: {budget} SPICE evals")
    print(f"Specs: {topo.specs_description()}")

    if dry_run:
        print("\n[DRY RUN] Would launch agent. Verifying netlist generation...")
        import tempfile
        work = Path(tempfile.mkdtemp(prefix="agent-dry-"))
        sizing = topo.params_to_sizing(topo.default_params())
        cir = topo.generate_netlist(sizing, work)
        print(f"Netlist generated: {cir}")
        print(f"Design space: {topo.design_space()}")
        print("Dry run complete. Pass --no-dry-run to run with LLM.")
        return

    from eda_agents.agents.adk_agents import DesignExplorerAgent

    work_dir = Path("results/single_agent")
    agent = DesignExplorerAgent(
        topology=topo,
        model=model,
        budget=budget,
    )

    print(f"\nLaunching agent...")
    result = await agent.run(work_dir)
    print(f"\nResult:\n{result.get('result', 'No result')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Single agent OTA design")
    parser.add_argument("--model", default="gemini-2.0-flash")
    parser.add_argument("--budget", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    args = parser.parse_args()
    asyncio.run(main(args.model, args.budget, args.dry_run))
