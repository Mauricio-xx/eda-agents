#!/usr/bin/env python3
"""GF180MCU gm/ID Lookup Table Generator.

Generates MOSFET characterization lookup tables for the wafer-space
GF180MCU 180nm PDK using the vendorized mosplot NgspiceSimulator backend.

Usage:
    python scripts/generate_gf180_luts.py [--output-dir data/gmid_luts] [--n-process 4]

Requirements:
    - PDK_ROOT env var or --pdk-root pointing to wafer-space-gf180mcu
    - ngspice installed and in PATH
    - ihp-gmid-kit vendorized mosplot in sys.path

Output:
    - gf180_nfet_03v3.npz
    - gf180_pfet_03v3.npz
"""

import argparse
import os
import sys

# Use vendorized mosplot from ihp-gmid-kit
_MOSPLOT_VENDOR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "ihp-gmid-kit", "vendor")
)
if os.path.isdir(_MOSPLOT_VENDOR):
    sys.path.insert(0, _MOSPLOT_VENDOR)
else:
    # Try relative to home
    alt = os.path.expanduser("~/personal_exp/ihp-gmid-kit/vendor")
    if os.path.isdir(alt):
        sys.path.insert(0, alt)

from mosplot.lookup_table_generator import LookupTableGenerator
from mosplot.lookup_table_generator.simulators import NgspiceSimulator
from mosplot.lookup_table_generator import TransistorSweep


# ---------------------------------------------------------------------------
# GF180MCU sweep configuration
# ---------------------------------------------------------------------------

# Length grid: 280nm to ~10um in 280nm steps (36 values)
_LENGTH_VALUES = [280e-9 + i * 280e-9 for i in range(36)]

# NMOS sweep: VGS 0-3.3V, VDS 0-3.3V, VBS 0 to -3.3V
nmos_sweep = TransistorSweep(
    mos_type="nmos",
    length=_LENGTH_VALUES,
    vgs=(0, 3.3, 0.02),      # 166 points
    vds=(0, 3.3, 0.1),       # 34 points
    vbs=(0, -3.3, -0.3),     # 12 points
)

# PMOS sweep: VGS 0 to -3.3V, VDS 0 to -3.3V, VBS 0 to 3.3V
pmos_sweep = TransistorSweep(
    mos_type="pmos",
    length=_LENGTH_VALUES,
    vgs=(0, -3.3, -0.02),
    vds=(0, -3.3, -0.1),
    vbs=(0, 3.3, 0.3),
)


def get_pdk_root(explicit: str | None = None) -> str:
    """Resolve GF180MCU PDK root."""
    if explicit:
        return explicit
    root = os.environ.get("PDK_ROOT")
    if root:
        return root
    # Default local installation
    default = os.path.expanduser("~/git/wafer-space-gf180mcu")
    if os.path.isdir(default):
        return default
    raise EnvironmentError(
        "GF180MCU PDK not found. Set PDK_ROOT or pass --pdk-root."
    )


def create_nmos_simulator(pdk_root: str) -> NgspiceSimulator:
    """Configure NgspiceSimulator for GF180MCU nfet_03v3."""
    design_ngspice = os.path.join(
        pdk_root, "gf180mcuD/libs.tech/ngspice/design.ngspice"
    )
    model_lib = os.path.join(
        pdk_root, "gf180mcuD/libs.tech/ngspice/sm141064.ngspice"
    )

    return NgspiceSimulator(
        simulator_path="ngspice",
        temperature=27,
        include_paths=[design_ngspice],          # .include for global params
        lib_mappings=[(model_lib, "typical")],   # .lib ... typical
        # GF180 subcircuit nfet_03v3 contains internal MOSFET m0
        mos_spice_symbols=("x1", "m.x1.m0"),
        device_parameters={
            "w": 10e-6,
            "nf": 1,
            "m": 1,
        },
        parameters_to_save=[
            "id", "gm", "gds", "vth", "vdsat",
            "cgg", "cgs", "cgd",
        ],
    )


def create_pmos_simulator(pdk_root: str) -> NgspiceSimulator:
    """Configure NgspiceSimulator for GF180MCU pfet_03v3."""
    design_ngspice = os.path.join(
        pdk_root, "gf180mcuD/libs.tech/ngspice/design.ngspice"
    )
    model_lib = os.path.join(
        pdk_root, "gf180mcuD/libs.tech/ngspice/sm141064.ngspice"
    )

    return NgspiceSimulator(
        simulator_path="ngspice",
        temperature=27,
        include_paths=[design_ngspice],
        lib_mappings=[(model_lib, "typical")],
        mos_spice_symbols=("x1", "m.x1.m0"),
        device_parameters={
            "w": 10e-6,
            "nf": 1,
            "m": 1,
        },
        parameters_to_save=[
            "id", "gm", "gds", "vth", "vdsat",
            "cgg", "cgs", "cgd",
        ],
    )


def generate(output_dir: str, pdk_root: str, n_process: int = 4):
    """Generate LUTs for both NMOS and PMOS."""
    os.makedirs(output_dir, exist_ok=True)

    model_lib = os.path.join(
        pdk_root, "gf180mcuD/libs.tech/ngspice/sm141064.ngspice"
    )
    if not os.path.exists(model_lib):
        raise FileNotFoundError(f"Model lib not found: {model_lib}")

    print("=" * 60)
    print("GF180MCU gm/ID Lookup Table Generator")
    print(f"PDK root: {pdk_root}")
    print(f"Output:   {output_dir}")
    print(f"Lengths:  {len(_LENGTH_VALUES)} ({_LENGTH_VALUES[0]*1e6:.2f} - {_LENGTH_VALUES[-1]*1e6:.2f} um)")
    print("=" * 60)

    # NMOS
    print("\n[1/2] Generating NMOS (nfet_03v3) lookup table...")
    nmos_sim = create_nmos_simulator(pdk_root)
    nmos_gen = LookupTableGenerator(
        description="GF180MCU nfet_03v3",
        simulator=nmos_sim,
        model_sweeps={"nfet_03v3": nmos_sweep},
        n_process=n_process,
    )
    nmos_out = os.path.join(output_dir, "gf180_nfet_03v3")
    nmos_gen.build(nmos_out)
    print(f"    Saved: {nmos_out}.npz")

    # PMOS
    print("\n[2/2] Generating PMOS (pfet_03v3) lookup table...")
    pmos_sim = create_pmos_simulator(pdk_root)
    pmos_gen = LookupTableGenerator(
        description="GF180MCU pfet_03v3",
        simulator=pmos_sim,
        model_sweeps={"pfet_03v3": pmos_sweep},
        n_process=n_process,
    )
    pmos_out = os.path.join(output_dir, "gf180_pfet_03v3")
    pmos_gen.build(pmos_out)
    print(f"    Saved: {pmos_out}.npz")

    print("\n" + "=" * 60)
    print("LUT generation complete!")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Generate GF180MCU gm/ID lookup tables"
    )
    parser.add_argument(
        "--output-dir", default="data/gmid_luts",
        help="Output directory (default: data/gmid_luts)",
    )
    parser.add_argument(
        "--pdk-root", default=None,
        help="Path to wafer-space-gf180mcu PDK root",
    )
    parser.add_argument(
        "--n-process", type=int, default=4,
        help="Number of parallel ngspice processes (default: 4)",
    )
    args = parser.parse_args()

    pdk_root = get_pdk_root(args.pdk_root)
    generate(args.output_dir, pdk_root, args.n_process)


if __name__ == "__main__":
    main()
