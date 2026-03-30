"""Quick SPICE sweep to find a good GF180OTA reference design point.

Runs default params through ngspice, then sweeps Ibias and L_dp to build
a performance table. Identifies the best valid design for reference_description().

Usage:
    python scripts/validate_gf180_ota.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from eda_agents.core.pdk import GF180MCU_D
from eda_agents.core.spice_runner import SpiceRunner
from eda_agents.topologies.ota_gf180 import GF180OTATopology


def main():
    topo = GF180OTATopology()
    runner = SpiceRunner(pdk=GF180MCU_D)

    missing = runner.validate_pdk()
    if missing:
        print(f"PDK not available: {missing}")
        return

    # Sweep grid
    ibias_values = [50.0, 100.0, 200.0, 300.0]
    ldp_values = [1.0, 2.0, 5.0]

    print(f"{'Ibias_uA':>10} {'L_dp_um':>8} {'Adc_dB':>8} {'GBW_kHz':>10} "
          f"{'PM_deg':>8} {'FoM':>12} {'Valid':>6}")
    print("-" * 72)

    best_fom = 0.0
    best_params = None
    best_result = None

    for ibias in ibias_values:
        for ldp in ldp_values:
            params = topo.default_params()
            params["Ibias_uA"] = ibias
            params["L_dp_um"] = ldp

            sizing = topo.params_to_sizing(params)
            work_dir = Path(tempfile.mkdtemp())
            cir = topo.generate_netlist(sizing, work_dir)
            result = runner.run(cir, work_dir)

            if not result.success:
                print(f"{ibias:>10.0f} {ldp:>8.1f} {'FAIL':>8} "
                      f"{'':>10} {'':>8} {'':>12} {'':>6}")
                continue

            fom = topo.compute_fom(result, sizing)
            valid, violations = topo.check_validity(result, sizing)
            gbw_khz = result.GBW_Hz / 1e3 if result.GBW_Hz else 0

            print(f"{ibias:>10.0f} {ldp:>8.1f} {result.Adc_dB:>8.1f} "
                  f"{gbw_khz:>10.1f} {result.PM_deg:>8.1f} "
                  f"{fom:>12.2e} {'Y' if valid else 'N':>6}")

            if valid and fom > best_fom:
                best_fom = fom
                best_params = params.copy()
                best_result = result

    print()
    if best_params:
        print(f"Best valid design:")
        print(f"  Params: Ibias={best_params['Ibias_uA']:.0f}uA, "
              f"L_dp={best_params['L_dp_um']:.1f}um, "
              f"L_load={best_params['L_load_um']:.1f}um, "
              f"Cc={best_params['Cc_pF']:.1f}pF, "
              f"W_dp={best_params['W_dp_um']:.1f}um")
        print(f"  Adc = {best_result.Adc_dB:.1f} dB")
        print(f"  GBW = {best_result.GBW_Hz/1e3:.1f} kHz")
        print(f"  PM  = {best_result.PM_deg:.1f} deg")
        print(f"  FoM = {best_fom:.2e}")
    else:
        print("No valid design found in sweep!")


if __name__ == "__main__":
    main()
