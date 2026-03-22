#!/usr/bin/env python3
"""Multiple ADK agents explore a GF180 OTA design space in parallel.

Demonstrates TrackDOrchestrator launching multiple explorers that
independently search the design space via SPICE-in-the-loop.

Usage:
    export GOOGLE_API_KEY=...
    python examples/04_multi_agent_exploration.py [--n-agents 3] [--budget 15]

Requires: pip install eda-agents[adk]
"""

import argparse
import asyncio
from pathlib import Path


async def main(model: str, n_agents: int, budget: int, dry_run: bool):
    from eda_agents.topologies.ota_gf180 import GF180OTATopology

    topo = GF180OTATopology()
    print(f"Topology: {topo.topology_name()}")
    print(f"PDK: {topo.pdk.display_name}")
    print(f"Agents: {n_agents}")
    print(f"Budget per agent: {budget}")
    print(f"Model: {model}")

    if dry_run:
        print("\n[DRY RUN] Verifying configuration...")
        print(f"Design space: {topo.design_space()}")
        print(f"Specs: {topo.specs_description()}")
        print(f"Total SPICE budget: {n_agents * budget}")
        print("Pass --no-dry-run to run with LLMs.")
        return

    from eda_agents.agents.adk_agents import TrackDOrchestrator

    work_dir = Path("results/multi_agent")
    orch = TrackDOrchestrator(
        topology=topo,
        model=model,
        n_explorers=n_agents,
        budget_per_explorer=budget,
    )

    print(f"\nLaunching {n_agents} parallel explorers...")
    result = await orch.run(work_dir)

    print(f"\nResults from {len(result.get('explorer_results', []))} agents:")
    for r in result.get("explorer_results", []):
        print(f"  {r.get('agent', '?')}: {r.get('result', 'No result')[:200]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-agent OTA exploration")
    parser.add_argument("--model", default="gemini-2.0-flash")
    parser.add_argument("--n-agents", type=int, default=3)
    parser.add_argument("--budget", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    args = parser.parse_args()
    asyncio.run(main(args.model, args.n_agents, args.budget, args.dry_run))
