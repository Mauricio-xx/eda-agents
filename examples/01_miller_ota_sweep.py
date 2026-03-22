#!/usr/bin/env python3
"""Example 01: Sweep Miller OTA design space and find best design.

This example requires NO external dependencies beyond numpy.
No LLM API keys, no ngspice, no PDK needed.
"""
from eda_agents.topologies.miller_ota import MillerOTADesigner


def main():
    designer = MillerOTADesigner()

    # Sweep a small grid
    results = designer.sweep_design_space(
        gmid_input_range=(8, 18, 3),
        gmid_load_range=(8, 14, 3),
        L_input_range=(0.3e-6, 1.5e-6, 3),
        L_load_range=(0.3e-6, 1.5e-6, 3),
        Cc_range=(0.3e-12, 2.0e-12, 3),
    )

    print(f"Evaluated {len(results)} design points")

    # Filter valid designs
    valid = [r for r in results if r.valid]
    print(f"Valid designs: {len(valid)} / {len(results)}")

    if valid:
        best = max(valid, key=lambda r: r.FoM)
        print(f"\nBest valid design:")
        print(f"  {best.summary()}")
        print(f"  gmid_input={best.gmid_input:.1f} S/A")
        print(f"  gmid_load={best.gmid_load:.1f} S/A")
        print(f"  L_input={best.L_input*1e6:.2f} um")
        print(f"  L_load={best.L_load*1e6:.2f} um")
        print(f"  Cc={best.Cc*1e12:.2f} pF")
    else:
        # Show best invalid design
        best = max(results, key=lambda r: r.FoM)
        print(f"\nNo valid designs found. Best overall:")
        print(f"  {best.summary()}")


if __name__ == "__main__":
    main()
