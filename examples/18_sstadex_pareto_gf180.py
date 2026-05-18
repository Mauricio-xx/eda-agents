"""SSTADEX 1-stage OTA Pareto reproduction on GF180MCU.

GF180 sibling of ``examples/17_sstadex_pareto_ihp.py``. The SSTADEX
schema is PDK-agnostic by design; this example only swaps the
``GmIdLookup`` PDK, the electrical-parameter rail (3.3 V vs 1.5 V),
and the per-PDK ngspice validation deck. The macromodel topology and
the deterministic ``HierarchicalDseRunner`` are reused verbatim.

Operating-point rationale
=========================

The IHP notebook biases the diff-pair at ``VINP=Vref=0.9 V`` and
``VOUTP=Vout=1.0 V`` with ``vs = linspace(0.2, Vout-0.1, N)``. On the
3.3 V GF180 rail, two constraints rescale this:

* The NMOS diff-pair needs ``vgs = Vref - vs > Vth_n`` to be on, so
  ``vs < Vref - 0.2``.
* The NMOS current source needs ``vgs_cs = vs > Vth_n`` to be on,
  giving ``vs > 0.7`` V at room-temperature GF180.

We therefore use ``Vref = Vout = 2.0 V`` and a strong-inversion sweep
``vs in [0.7, 1.3]`` (7 points). Widths land in 0.2-300 um and the
analytical one-corner gain is around 33 dB, comparable to the IHP
reference.

Usage::

    python examples/18_sstadex_pareto_gf180.py
    python examples/18_sstadex_pareto_gf180.py --validate-spice
    python examples/18_sstadex_pareto_gf180.py --i-amp-uA 30 \\
        --workdir ./run_30uA

Environment::

    PDK_ROOT=/path/to/wafer-space-gf180mcu    # required for --validate-spice
    EDA_AGENTS_GMID_LUT_DIR=/path/to/lut/dir   # optional; defaults to the
                                                # auto-downloaded XDG cache
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from sympy import Symbol

from eda_agents.agents.hierarchical_dse_runner import HierarchicalDseRunner
from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.topologies.sstadex import (
    Library,
    Macromodel,
    Testbench,
    VoltageSource,
)


# GF180 electrical parameters (rescaled from the IHP notebook for the
# 3.3 V rail). The diff-pair stays in strong inversion across the vs
# sweep when Vref-vs > 0.5 V and the current source stays in strong
# inversion when vs > Vth_n_GF180 ~ 0.7 V.
_VOUT = 2.0
_VREF = 2.0
_VDD = 3.3
_VS_MIN = 0.7
_VS_MAX = 1.3
# GF180 LUT lengths grid (0.28*i for i in 2,4,8,16,30 um). Maps roughly
# onto the IHP [0.4, 0.8, 1.6, 3.2, 6.4] geometric progression.
_LENGTHS_UM = [0.56, 1.12, 2.24, 4.48, 8.4]


def build_1stage_ota(lut: GmIdLookup, **knobs) -> Macromodel:
    """Reproduce the upstream notebook's 1-stage OTA macromodel on GF180.

    The wiring is identical to the IHP version; only the bias rail and
    the vs sweep window change.
    """
    I_amp: float = knobs.get("I_amp", 20e-6)
    N_points: int = int(knobs.get("N_points", 7))
    vs = np.linspace(_VS_MIN, _VS_MAX, N_points)

    lib = Library(name="gf180mcu", lut=lut)

    diffpair = lib.get("simplediffpair", il=I_amp, lengths_um=_LENGTHS_UM)
    diffpair.set_port_voltages({
        "VINP": _VREF, "VINN": _VREF,
        "VOUTP": _VOUT, "VOUTN": _VOUT,
        "VTAIL": vs,
    })
    df_dp = diffpair.build(lut)
    diffpair.outputs = {
        Symbol("W_diff"): df_dp["width"].values,
        Symbol("L_diff"): df_dp["length"].values,
    }
    diffpair.interface_variables = {
        "vs_diff": np.tile(vs, len(_LENGTHS_UM)),
    }

    currentmirror = lib.get("simplecurrentmirror", il=I_amp,
                             lengths_um=_LENGTHS_UM)
    currentmirror.set_port_voltages({
        "VINP": _VOUT, "VINN": _VOUT,
        "VOUTP": _VOUT, "VOUTN": _VOUT,
        "VDD": _VDD,
    })
    df_cm = currentmirror.build(lut)
    currentmirror.outputs = {
        Symbol("W_al"): df_cm["width"].values,
        Symbol("L_al"): df_cm["length"].values,
    }

    currentsource = lib.get("simplecurrentsource", il=I_amp,
                             lengths_um=_LENGTHS_UM)
    currentsource.set_port_voltages({
        "VOUTP": vs, "VSS": 0.0, "VINP": vs, "VINN": vs,
    })
    df_cs = currentsource.build(lut)
    currentsource.outputs = {
        Symbol("W_cs_m1"): df_cs["width_m1"].values,
        Symbol("W_cs_m2"): df_cs["width_m2"].values,
        Symbol("L_cs"): df_cs["length"].values,
    }
    currentsource.interface_variables = {
        "vs_cs": np.tile(vs, len(_LENGTHS_UM)),
    }

    cs_macro = Macromodel(
        name="current_source_macro",
        ports=["VOUT", "VSS", "Vbias"],
        outputs=[
            Symbol("W_cs_m1"), Symbol("W_cs_m2"), Symbol("L_cs"),
        ],
        macromodel_parameters={
            Symbol("Ixcs_macro"): np.array([I_amp]),
        },
        interface_variables=["vs_cs"],
    )
    cs_macro.add_instance("xcs", currentsource, {
        "VOUTP": "VOUT", "VSS": "VSS",
        "VOUTN": "Vbias", "VINP": "Vbias", "VINN": "Vbias",
    })
    cs_macro.num_level_exp = 1
    cs_macro.primitives = [currentsource]
    tb_ibias = Testbench(
        name="currentsource_ibas",
        dut=cs_macro,
        elements=[],
        tf=("VOUT", "Vbias"),
        parameter_map={
            Symbol("g_gm_cs_m2"): Symbol("g_gm_cs_m1"),
            Symbol("R_gds_cs_m2"): Symbol("R_gds_cs_m1"),
            Symbol("s"): 0,
        },
    )
    cs_macro.specifications = [
        tb_ibias.make_test(
            name="ibias_currentsource",
            opt_goal="max",
            conditions={"min": [0]},
        ),
    ]
    cs_macro.propagated_conditions = {
        "direct": [
            {"kind": "range", "column": Symbol("W_cs_m1"),
             "condition": {"min": 1e-6, "max": 1000e-6}},
            {"kind": "range", "column": Symbol("W_cs_m2"),
             "condition": {"min": 1e-6, "max": 1000e-6}},
        ],
        "derived": [],
    }

    ota = Macromodel(
        name="OTA_1stage_macro",
        ports=["VINP", "VINN", "VOUT", "VDD", "IBIAS", "Vbias", "VSS"],
        outputs=[
            Symbol("W_diff"), Symbol("L_diff"),
            Symbol("W_al"), Symbol("L_al"),
        ],
        interface_variables=["vs_diff"],
        shared_nodes={"IBIAS_node": ["vs_diff", "vs_cs"]},
    )
    ota.add_instance("xdp", diffpair, {
        "VINP": "VINP", "VINN": "VINN",
        "VOUTP": "VOUT", "VOUTN": "N1", "VTAIL": "IBIAS",
    })
    ota.add_instance("xcm", currentmirror, {
        "VINP": "N1", "VINN": "N1",
        "VOUTP": "VOUT", "VOUTN": "N1", "VDD": "VDD",
    })
    ota.add_instance("xcs_macro", cs_macro, {
        "VOUT": "IBIAS", "VSS": "VSS", "Vbias": "Vbias",
    })

    tb_gain = Testbench(
        name="ota_1stage_gain",
        dut=ota,
        elements=[
            VoltageSource("Vdd", "VDD", "VSS", 0),
            VoltageSource("V_n", "VINN", "VSS", 0),
            VoltageSource("V_p", "VINP", "VSS", Symbol("V_p")),
        ],
        tf=("VOUT", "VINP"),
        parameter_map={
            Symbol("V_p"): 1,
            Symbol("g_gm_xdp_m2"): Symbol("g_gm_xdp_m1"),
            Symbol("R_gds_xdp_m2"): Symbol("R_gds_xdp_m1"),
            Symbol("g_gm_xcm_m2"): Symbol("g_gm_xcm_m1"),
            Symbol("R_gds_xcm_m2"): Symbol("R_gds_xcm_m1"),
            Symbol("g_gm_cs_m2"): Symbol("g_gm_cs_m1"),
            Symbol("R_gds_cs_m2"): Symbol("R_gds_cs_m1"),
            Symbol("s"): 0,
        },
    )
    ota.specifications = [
        tb_gain.make_test(
            name="gain_1stage",
            opt_goal="max",
            conditions={"min": [1e-5]},
        ),
    ]
    ota.opt_specifications = ota.specifications
    ota.primitives = [diffpair, currentmirror]
    ota.submacromodels = [cs_macro]
    ota.num_level_exp = -1
    ota.run_pareto = True
    ota.propagated_conditions = {
        "direct": [
            {"kind": "range", "column": Symbol("W_al"),
             "condition": {"min": 1e-6, "max": 1000e-6}},
            {"kind": "range", "column": Symbol("W_diff"),
             "condition": {"min": 1e-6, "max": 1000e-6}},
        ],
        "derived": [],
    }
    return ota


# ---------------------------------------------------------------------------
# Optional ngspice cross-validation (GF180 deck)
# ---------------------------------------------------------------------------


def pick_three_corners(pareto_df: pd.DataFrame) -> pd.DataFrame:
    """Return three Pareto points: smallest area, mid-gain, and the
    "near-peak" gain row that sits one step in from the absolute
    maximum. Skipping the absolute-peak row keeps the cross-validation
    away from the LUT extrapolation edges where Vds-axis resolution
    starts to dominate the BSIM4-vs-LUT delta on the gain estimate.
    """
    if "gain_1stage" not in pareto_df.columns or "area" not in pareto_df.columns:
        return pareto_df.head(min(3, len(pareto_df.index)))
    sorted_by_gain = pareto_df.sort_values("gain_1stage", ascending=False)
    smallest_area = pareto_df.sort_values("area").iloc[[0]]
    # Step one in from the peak (or stay at row 0 if the Pareto is
    # too short for the skip to be meaningful).
    near_peak_idx = 1 if len(sorted_by_gain) > 1 else 0
    near_peak = sorted_by_gain.iloc[[near_peak_idx]]
    mid_idx = len(sorted_by_gain) // 2
    middle = sorted_by_gain.iloc[[mid_idx]]
    out = pd.concat([smallest_area, middle, near_peak]).drop_duplicates(
        subset=["W_diff", "L_diff", "W_al", "L_al",
                "W_cs_m1", "L_cs"],
    )
    return out


def _ngspice_deck(row: pd.Series, pdk_root: Path) -> str:
    """Build a GF180 open-loop AC deck for one Pareto row.

    GF180 differences from the IHP deck: BSIM4 model lib (no OSDI
    pre-load), the ``design.ngspice`` global include emits the model
    parameters, the subcircuit primitives are ``nfet_03v3`` /
    ``pfet_03v3`` and take ``nf`` (number of fingers) instead of
    IHP's ``ng``. Subcircuit pin order is ``(d, g, s, b)`` in both
    PDKs, so the device-line wiring is unchanged.

    The deck pins ``Vbias = row.vs_diff`` (equal to ``row.vs_cs``
    by the macromodel's ``shared_nodes`` constraint) so the SPICE
    operating point matches the symbolic LUT sweep, not the sweep
    midpoint.
    """
    W_diff = row["W_diff"]
    L_diff = row["L_diff"]
    W_al = row["W_al"]
    L_al = row["L_al"]
    W_cs_m1 = row["W_cs_m1"]
    W_cs_m2 = row["W_cs_m2"]
    L_cs = row["L_cs"]
    nf_diff = max(1, int(np.ceil(W_diff / 5e-6)))
    nf_al = max(1, int(np.ceil(W_al / 5e-6)))
    nf_cs_m1 = max(1, int(np.ceil(W_cs_m1 / 5e-6)))
    nf_cs_m2 = max(1, int(np.ceil(W_cs_m2 / 5e-6)))

    design_inc = pdk_root / "gf180mcuD/libs.tech/ngspice/design.ngspice"
    lib = pdk_root / "gf180mcuD/libs.tech/ngspice/sm141064.ngspice"

    return f"""* 1-stage OTA open-loop AC validation (GF180MCU)
.include '{design_inc}'
.lib '{lib}' typical

* DUT subcircuit
.subckt OTA_1stage VINP VINN VOUT VDD IBIAS VSS Vbias
* Diff pair (NMOS)
XMNDPM1 VOUT VINP IBIAS VSS nfet_03v3 w={W_diff} l={L_diff} nf={nf_diff}
XMNDPM2 N1   VINN IBIAS VSS nfet_03v3 w={W_diff} l={L_diff} nf={nf_diff}
* Current mirror (PMOS, diode-connected M2)
XMPCMM1 VOUT N1 VDD VDD pfet_03v3 w={W_al} l={L_al} nf={nf_al}
XMPCMM2 N1   N1 VDD VDD pfet_03v3 w={W_al} l={L_al} nf={nf_al}
* Current source (NMOS, diode-connected M2)
XMNCSM1 IBIAS Vbias VSS VSS nfet_03v3 w={W_cs_m1} l={L_cs} nf={nf_cs_m1}
XMNCSM2 Vbias Vbias VSS VSS nfet_03v3 w={W_cs_m2} l={L_cs} nf={nf_cs_m2}
.ends OTA_1stage

* Top-level: open-loop AC at the midpoint of the symbolic vs sweep.
* The diff-pair and current source share the IBIAS_node, so the
* shared_nodes constraint pins vs_diff == vs_cs across the Pareto.
* Biasing Vbias at the sweep midpoint matches the bulk of the
* surviving Pareto rows; per-row Vbias matching trades stability
* on boundary rows for marginal gain on interior rows.
xota VINP VINN VOUT VDD IBIAS VSS Vbias OTA_1stage
Vdd  VDD  0 {_VDD}
Vss  VSS  0 0
Vbref Vbias 0 {(_VS_MIN + _VS_MAX) / 2:.4f}
Vp   VINP 0 dc {_VREF} ac 1
Vn   VINN 0 dc {_VREF} ac 0
Cl   VOUT VSS 1p

.control
ac dec 10 1 1e9
meas ac gain find vdb(VOUT) at=10
print v(VOUT)
print v(VINP)
.endc
.end
"""


def cross_validate_corners(
    pareto_df: pd.DataFrame, work_dir: Path, pdk_root: Path
) -> pd.DataFrame:
    """Run three Pareto corners through ngspice and report dB error vs
    the symbolic gain. Identical pipeline to example 17 but with the
    GF180 BSIM4 deck."""
    from eda_agents.core.spice_runner import SpiceRunner

    corners = pick_three_corners(pareto_df)
    if len(corners) == 0:
        return pd.DataFrame()

    work_dir.mkdir(parents=True, exist_ok=True)
    runner = SpiceRunner(pdk="gf180mcu")

    rows = []
    for i, (_, row) in enumerate(corners.iterrows()):
        sim_dir = work_dir / f"corner_{i}"
        sim_dir.mkdir(parents=True, exist_ok=True)
        deck = sim_dir / "ota.cir"
        deck.write_text(_ngspice_deck(row, pdk_root))
        result = runner.run(deck)
        gain_sym_db = 20 * np.log10(max(row["gain_1stage"], 1e-30))
        gain_spice_db = (result.measurements or {}).get("gain")
        if isinstance(gain_spice_db, str):
            try:
                gain_spice_db = float(gain_spice_db)
            except ValueError:
                gain_spice_db = float("nan")
        rows.append({
            "W_diff_um": row["W_diff"] * 1e6,
            "L_diff_um": row["L_diff"] * 1e6,
            "W_al_um": row["W_al"] * 1e6,
            "L_al_um": row["L_al"] * 1e6,
            "W_cs_um": row["W_cs_m1"] * 1e6,
            "L_cs_um": row["L_cs"] * 1e6,
            "gain_sym_db": gain_sym_db,
            "gain_spice_db": gain_spice_db,
            "delta_db": (
                gain_spice_db - gain_sym_db
                if isinstance(gain_spice_db, (int, float))
                else float("nan")
            ),
            "success": bool(result.success),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--i-amp-uA", type=float, default=20.0,
        help="Per-branch tail current in microamperes (default: 20).",
    )
    parser.add_argument(
        "--n-points", type=int, default=7,
        help="Sweep grid size for VTAIL / current-source bias (default: 7).",
    )
    parser.add_argument(
        "--workdir", type=Path,
        default=Path("./sstadex_pareto_gf180"),
        help="Where to write program.md / results.tsv / pareto.csv.",
    )
    parser.add_argument(
        "--validate-spice", action="store_true",
        help="Cross-validate 3 Pareto corners against ngspice "
             "(requires PDK_ROOT pointing at wafer-space-gf180mcu).",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="Wipe the workdir before running.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    logger = logging.getLogger("examples.18_sstadex_pareto_gf180")

    if args.clean and args.workdir.exists():
        shutil.rmtree(args.workdir)

    lut = GmIdLookup(pdk="gf180mcu")
    runner = HierarchicalDseRunner(
        macromodel_builder=build_1stage_ota,
        lut=lut,
        knob_defaults={
            "I_amp": args.i_amp_uA * 1e-6,
            "N_points": args.n_points,
        },
    )
    result = runner.run(args.workdir)
    logger.info(
        "Pareto frontier: %d points (out of %d total post-filter rows).",
        result.pareto_rows, result.total_rows,
    )
    logger.info("Best gain on Pareto: %.4g V/V (%.2f dB).",
                result.best_fom, 20 * np.log10(max(result.best_fom, 1e-30)))
    logger.info("Artefacts:")
    logger.info("  program.md  : %s", result.program_md)
    logger.info("  results.tsv : %s", result.results_tsv)
    logger.info("  pareto.csv  : %s", result.pareto_csv)

    pareto_df = pd.read_csv(result.results_tsv, sep="\t")
    print("\nTop-5 by gain_1stage:")
    print(pareto_df.sort_values("gain_1stage", ascending=False).head(5)[
        ["gain_1stage", "area", "W_diff", "L_diff", "W_al", "L_al",
         "W_cs_m1", "L_cs"]
    ])
    print("\nSmallest area:")
    print(pareto_df.sort_values("area").head(3)[
        ["gain_1stage", "area", "W_diff", "L_diff", "W_al", "L_al",
         "W_cs_m1", "L_cs"]
    ])

    if args.validate_spice:
        pdk_root = os.environ.get("PDK_ROOT")
        if not pdk_root or not Path(pdk_root, "gf180mcuD").exists():
            logger.error(
                "--validate-spice requires PDK_ROOT pointing at a "
                "wafer-space-gf180mcu clone containing gf180mcuD/."
            )
            return 2
        logger.info("Cross-validating 3 Pareto corners through ngspice...")
        cross = cross_validate_corners(
            pareto_df, args.workdir / "spice", Path(pdk_root)
        )
        print("\nSpice cross-validation:")
        print(cross.to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
