#!/usr/bin/env python3
"""GF180 OTA design space sweep (no LLM, no ngspice needed for analytical).

Demonstrates using GF180OTATopology to generate netlists and evaluate
designs analytically. Useful for understanding the design space before
launching agent-driven exploration.

Usage:
    python examples/02_gf180_ota_sweep.py
"""

from pathlib import Path
import tempfile

from eda_agents.topologies.ota_gf180 import GF180OTATopology


def main():
    topo = GF180OTATopology()
    print(f"Topology: {topo.topology_name()}")
    print(f"PDK: {topo.pdk.display_name}")
    print(f"VDD: {topo.pdk.VDD}V")
    print(f"\nDesign space:")
    for name, (lo, hi) in topo.design_space().items():
        print(f"  {name}: [{lo}, {hi}]")

    print(f"\nSpecs: {topo.specs_description()}")
    print(f"\nDefault params: {topo.default_params()}")

    # Generate a netlist at the default point
    work = Path(tempfile.mkdtemp(prefix="gf180-sweep-"))
    sizing = topo.params_to_sizing(topo.default_params())
    cir = topo.generate_netlist(sizing, work)
    print(f"\nGenerated netlist: {cir}")

    # Sweep Ibias
    print("\n--- Ibias sweep (other params at default) ---")
    print(f"{'Ibias_uA':>10} {'M1_W_um':>10} {'M6_W_um':>10} {'Cc_pF':>8}")
    for ibias in [20, 50, 100, 200, 500]:
        params = topo.default_params()
        params["Ibias_uA"] = ibias
        sz = topo.params_to_sizing(params)
        m1_w = sz["M1"]["W"] * 1e6
        m6_w = sz["M6"]["W"] * sz["M6"]["ng"] * 1e6
        cc = sz["_Cc"] * 1e12
        print(f"{ibias:>10.0f} {m1_w:>10.2f} {m6_w:>10.1f} {cc:>8.2f}")

    print(f"\nNetlist files in: {work}")


if __name__ == "__main__":
    main()
