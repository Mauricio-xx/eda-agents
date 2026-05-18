"""Wrøngm Ron/gm IBA validation on GF180MCU.

GF180 sibling of ``examples/16_wrongm_iba_ihp.py``. Same deterministic
walkthrough:

  1. RonGmLookup picks NMOS + PMOS device sizes at the characterisation
     bias current that meet the target Ron/gm.
  2. Width-scale to the design bias current (Wrøngm convention).
  3. Sweep ``Vbias`` to find the inverter trip point.
  4. Run the open-loop AC testbench at the trip point.
  5. Report Adc / GBW / PM / Iq and compare against the GF180-equivalent
     spec floors documented on ``InverterBasedAmplifierGF180``.

Wrøngm's IHP reference (``Gm=40 uS``, ``UGB=9.55 MHz``, ``Iq=2.5 uA``)
is the methodology target on a 1.2 V rail. On GF180's 3.3 V rail the
same ``CL = 667 fF`` load asks for the same ``Gm`` but the current
cost is higher because GF180 nfet_03v3 / pfet_03v3 are slower and
have lower gm/Id. The Wrøngm Table II numbers therefore do not
transfer directly; the validation gate uses the GF180 floors set in
``iba_gf180.SPEC_*`` (Adc >= 15 dB, GBW >= 5 MHz, Iq <= 15 uA).

Usage::

    export PDK_ROOT=/path/to/wafer-space-gf180mcu
    python examples/19_wrongm_iba_gf180.py
    python examples/19_wrongm_iba_gf180.py --show-skill
"""

from __future__ import annotations

import argparse
import logging
import tempfile
from pathlib import Path

from eda_agents.core.ron_gm_lookup import RonGmLookup
from eda_agents.core.spice_runner import SpiceRunner
from eda_agents.topologies.iba_gf180 import InverterBasedAmplifierGF180


def _print_skill_preamble() -> None:
    """Render ``analog.ron_gm_sizing`` against the GF180 topology."""
    import eda_agents.skills.analog  # noqa: F401  -- registers skills
    from eda_agents.skills.registry import get_skill

    topo = InverterBasedAmplifierGF180()
    rendered = get_skill("analog.ron_gm_sizing").render(topo)
    print("=" * 72)
    print("Skill content injected into autoresearch prompt:")
    print("-" * 72)
    print(rendered[:1500])
    print("... [truncated; full skill content has",
          len(rendered), "chars]")
    print("=" * 72)


def _size_devices_via_ron_gm(
    ibias_char_uA: float = 10.0,
    ron_gm_target: float = 50e6,
    L_n_um: float = 3.0,
    L_p_um: float = 3.0,
) -> tuple[dict, dict]:
    """Size NMOS + PMOS via the GF180 RonGmLookup.

    Wrøngm's IHP convention reads the on-state Ron at Vds = 0.05 V.
    The GF180 LUT's Vds grid step is 0.1 V (vs IHP's 0.05 V), so
    the nearest-neighbor read at Vds_on = 0.05 V hits the Vds = 0
    grid point where Id is identically zero. Setting Vds_on = 0.1 V
    lands on the smallest non-zero sampled point. The Ron values
    are therefore a slightly different on-state convention than
    IHP, which is documented honestly in the methodology notes.
    """
    lut = RonGmLookup(pdk="gf180mcu")

    print(f"Sizing at Ibias_char = {ibias_char_uA} uA, "
          f"Ron/gm target = {ron_gm_target:.2e} "
          "(Vds_on = 0.1 V to land on the GF180 LUT's Vds grid)")
    print()

    n_out = lut.size_from_ron_gm(
        ron_gm_target=ron_gm_target,
        mos_type="nmos", L_um=L_n_um,
        Ibias_uA=ibias_char_uA,
        Vds_on=0.1,
    )
    p_out = lut.size_from_ron_gm(
        ron_gm_target=ron_gm_target,
        mos_type="pmos", L_um=L_p_um,
        Ibias_uA=ibias_char_uA,
        Vds_on=0.1,
    )

    for tag, out in (("NMOS", n_out), ("PMOS", p_out)):
        print(f"  {tag}: W={out.W_um:.3f} um  L={out.L_um:.2f} um")
        print(f"        Vbias_design={out.vgs_V:.3f} V  "
              f"gm/ID={out.gmid:.2f}  Ron={out.Ron_ohm/1e3:.1f} kΩ")
        print(f"        Ron/gm={out.Ron_gm:.3e}  "
              f"Ipeak={out.Ipeak_uA:.2f} uA  "
              f"deadzone Vbias={out.deadzone_bias_V:.3f} V")

    return (
        {"W_um": n_out.W_um, "L_um": n_out.L_um, "m": 1},
        {"W_um": p_out.W_um, "L_um": p_out.L_um, "m": 1},
    )


def _scale_to_design_bias(
    sizing: dict,
    ibias_char_uA: float,
    ibias_design_uA: float,
    wmin_um: float,
) -> dict:
    """Wrøngm width scaling ``W ∝ Ibias_design / Ibias_char``."""
    scale = ibias_design_uA / ibias_char_uA
    return {
        "W_um": max(wmin_um, sizing["W_um"] * scale),
        "L_um": sizing["L_um"],
        "m": sizing["m"],
    }


def _trip_point_sweep(
    topo: InverterBasedAmplifierGF180,
    runner: SpiceRunner,
    base_params: dict,
    vbias_grid: list[float],
) -> tuple[dict, dict]:
    """Sweep Vbias to find the GF180 inverter trip point."""
    best = None
    best_fom = -float("inf")
    print()
    print(f"Vbias trip-point sweep across {len(vbias_grid)} candidates:")
    print(f"{'Vbias [V]':>10} {'Adc [dB]':>10} {'GBW [MHz]':>10} "
          f"{'PM [deg]':>9} {'Iq [uA]':>9} {'valid':>6}")
    print("-" * 60)
    for vbias in vbias_grid:
        params = dict(base_params)
        params["Vbias_V"] = vbias
        sizing = topo.params_to_sizing(params)
        with tempfile.TemporaryDirectory() as td:
            cir = topo.generate_netlist(sizing, Path(td))
            result = runner.run(cir)
        if not result.success:
            print(f"{vbias:>10.3f}  (sim failed)")
            continue
        iq_a = (result.measurements or {}).get("iq_dc", 0.0)
        iq_uA = iq_a * 1e6 if iq_a else 0.0
        adc = result.Adc_dB or 0.0
        gbw_MHz = (result.GBW_Hz or 0.0) / 1e6
        pm = result.PM_deg or 0.0
        valid, _ = topo.check_validity(result, sizing)
        fom = topo.compute_fom(result, sizing)
        print(f"{vbias:>10.3f} {adc:>10.2f} {gbw_MHz:>10.2f} "
              f"{pm:>9.1f} {iq_uA:>9.2f} {str(valid):>6}")
        if fom > best_fom and result.GBW_Hz:
            best_fom = fom
            best = {"params": params, "result": result, "iq_uA": iq_uA,
                    "valid": valid, "fom": fom}
    return (best["params"], best) if best else ({}, {})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ibias-char-uA", type=float, default=10.0,
                        help="Characterisation current for the LUT search")
    parser.add_argument("--ibias-design-uA", type=float, default=2.5,
                        help="Target design quiescent current per branch. "
                             "Default 2.5 uA matches the Wrøngm IHP "
                             "convention; on GF180 the actual quiescent "
                             "current at the trip point is around 16 uA "
                             "(under SPEC_IQ_UA = 20 uA).")
    parser.add_argument("--ron-gm-target", type=float, default=50e6)
    parser.add_argument("--show-skill", action="store_true",
                        help="Print the analog.ron_gm_sizing skill prompt content")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    if args.show_skill:
        _print_skill_preamble()
        print()

    topo = InverterBasedAmplifierGF180()
    wmin_um = topo.pdk.Wmin_m * 1e6

    # 1+2: Size via RonGmLookup, then width-scale to design bias.
    nmos_char, pmos_char = _size_devices_via_ron_gm(
        ibias_char_uA=args.ibias_char_uA,
        ron_gm_target=args.ron_gm_target,
    )
    nmos = _scale_to_design_bias(
        nmos_char, args.ibias_char_uA, args.ibias_design_uA, wmin_um
    )
    pmos = _scale_to_design_bias(
        pmos_char, args.ibias_char_uA, args.ibias_design_uA, wmin_um
    )

    print()
    print(f"After width-scale to Ibias_design = {args.ibias_design_uA} uA:")
    print(f"  NMOS: W={nmos['W_um']:.3f} um  L={nmos['L_um']:.2f} um")
    print(f"  PMOS: W={pmos['W_um']:.3f} um  L={pmos['L_um']:.2f} um")

    # 3+4: build IBA params, sweep Vbias around the GF180 mid-rail.
    runner = SpiceRunner(pdk="gf180mcu")
    base_params = {
        "W_n_um": nmos["W_um"], "L_n_um": nmos["L_um"], "m_n": nmos["m"],
        "W_p_um": pmos["W_um"], "L_p_um": pmos["L_um"], "m_p": pmos["m"],
        "Vbias_V": 1.65,  # placeholder; replaced in sweep
    }
    # Mid-rail = 1.65 V. The actual trip point depends on PMOS/NMOS
    # mobility imbalance; on GF180 it tends to sit a few hundred mV
    # below mid-rail when W_p/W_n is too small.
    vbias_grid = [1.20, 1.30, 1.40, 1.50, 1.55, 1.60, 1.65, 1.70, 1.80, 1.90]
    _best_params, best = _trip_point_sweep(topo, runner, base_params, vbias_grid)

    # 5: report.
    print()
    print("=" * 72)
    if not best:
        print("WARNING: no valid trip-point design found. Try lowering "
              "Ron/gm target or raising ibias_char to push the sized "
              "widths up.")
        return

    res = best["result"]
    iq_uA = best["iq_uA"]
    adc = res.Adc_dB or 0.0
    gbw_MHz = (res.GBW_Hz or 0.0) / 1e6
    pm = res.PM_deg or 0.0
    gm_uS = 2 * 3.14159265 * (res.GBW_Hz or 0.0) * topo.CL_F * 1e6
    print("Best design at trip point:")
    print(f"  Vbias        = {best['params']['Vbias_V']:.3f} V")
    print(f"  Adc          = {adc:.2f} dB    "
          f"(floor >= {topo.SPEC_ADC_DB:.0f} dB)")
    print(f"  GBW          = {gbw_MHz:.2f} MHz "
          f"(floor >= {topo.SPEC_GBW_HZ/1e6:.2f} MHz)")
    print(f"  PM           = {pm:.1f} deg    "
          f"(floor >= {topo.SPEC_PM_DEG:.0f} deg)")
    print(f"  Iq           = {iq_uA:.2f} uA   "
          f"(ceiling <= {topo.SPEC_IQ_UA:.1f} uA)")
    print(f"  Gm estimate  = {gm_uS:.2f} uS")
    print(f"  FoM          = {best['fom']:.3e}")
    print(f"  Spec valid   = {best['valid']}")

    print()
    print("Notes on the GF180 vs IHP comparison:")
    print("  - The Wrøngm IHP reference (Iq=2.5 uA, UGB=9.55 MHz)")
    print("    does not transfer directly: GF180 is 180 nm at 3.3 V,")
    print("    so the same CL=667 fF load takes more current to swing.")
    print("  - The methodology is unchanged: Ron/gm + width scale + ")
    print("    trip-point sweep, all on the open-loop AC testbench.")
    print("  - Strict reproduction of GF180 silicon would still need ")
    print("    an SS-corner LUT and a closed-loop cap-feedback bench;")
    print("    both are out of scope here, same as the IHP example.")
    print("=" * 72)


if __name__ == "__main__":
    main()
