"""SSTADEX 1-stage OTA Pareto reproduction on IHP SG13G2.

End-to-end demo of the eda-agents port of SSTADEX (#16 CAC2026,
MIT license). The script:

  1. Assembles a 1-stage OTA macromodel from the four canonical
     primitives (simplediffpair, simplecurrentmirror,
     simplecurrentsource), wired identically to the upstream
     notebook (cells 26-46).
  2. Runs the deterministic ``HierarchicalDseRunner`` to harvest a
     Pareto frontier on (area, gain_1stage) -- one ``dfs()`` call,
     persists ``program.md`` + ``results.tsv`` + ``pareto.csv``.
  3. Optionally cross-validates three Pareto corners (smallest area,
     mid-gain, highest gain) against ngspice using
     ``eda_agents.core.spice_runner.SpiceRunner`` -- the validation
     gate the upstream notebook documents in cell 47 ("gain error
     remains minimal").

Usage::

    python examples/17_sstadex_pareto_ihp.py
    python examples/17_sstadex_pareto_ihp.py --validate-spice
    python examples/17_sstadex_pareto_ihp.py --i-amp-uA 50 \
        --workdir ./run_50uA

Environment::

    EDA_AGENTS_IHP_LUT_DIR=/path/to/ihp-gmid-kit/data
    PDK_ROOT=/path/to/IHP-Open-PDK   # only when --validate-spice
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


# Notebook reference electrical parameters (cell 26).
_VOUT = 1.0
_VREF = 0.9
_VDD = 1.5
_LENGTHS_UM = [0.4, 0.8, 1.6, 3.2, 6.4]


def build_1stage_ota(lut: GmIdLookup, **knobs) -> Macromodel:
    """Reproduce the upstream notebook's 1-stage OTA macromodel.

    ``knobs`` accepts ``I_amp`` (per-branch tail current, A) and
    ``N_points`` (tail voltage / current-source sweep grid size).
    """
    I_amp: float = knobs.get("I_amp", 20e-6)
    N_points: int = int(knobs.get("N_points", 10))
    vs = np.linspace(0.2, _VOUT - 0.1, N_points)

    lib = Library(name="ihp_sg13g2", lut=lut)

    diffpair = lib.get("simplediffpair", il=I_amp)
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

    currentmirror = lib.get("simplecurrentmirror", il=I_amp)
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

    currentsource = lib.get("simplecurrentsource", il=I_amp)
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
# Optional ngspice cross-validation
# ---------------------------------------------------------------------------


def pick_three_corners(pareto_df: pd.DataFrame) -> pd.DataFrame:
    """Return three Pareto points: smallest area, middle, highest gain."""
    if "gain_1stage" not in pareto_df.columns or "area" not in pareto_df.columns:
        return pareto_df.head(min(3, len(pareto_df.index)))
    sorted_by_gain = pareto_df.sort_values("gain_1stage", ascending=False)
    smallest_area = pareto_df.sort_values("area").iloc[[0]]
    highest_gain = sorted_by_gain.iloc[[0]]
    mid_idx = len(sorted_by_gain) // 2
    middle = sorted_by_gain.iloc[[mid_idx]]
    out = pd.concat([smallest_area, middle, highest_gain]).drop_duplicates(
        subset=["W_diff", "L_diff", "W_al", "L_al",
                "W_cs_m1", "L_cs"],
    )
    return out


def _ngspice_deck(row: pd.Series, pdk_root: Path) -> str:
    """Build an open-loop AC deck for one Pareto row -- matches the
    upstream cell-47 feedback wrapper minus the discrete R/C bias
    network. The DUT is a hand-rolled 1-stage OTA subcircuit so we
    do not depend on an ngspice library that ships only with the
    SSTADEx fork."""
    W_diff = row["W_diff"]
    L_diff = row["L_diff"]
    W_al = row["W_al"]
    L_al = row["L_al"]
    W_cs_m1 = row["W_cs_m1"]
    W_cs_m2 = row["W_cs_m2"]
    L_cs = row["L_cs"]

    ng_diff = max(1, int(np.ceil(W_diff / 5e-6)))
    ng_al = max(1, int(np.ceil(W_al / 5e-6)))
    ng_cs_m1 = max(1, int(np.ceil(W_cs_m1 / 5e-6)))
    ng_cs_m2 = max(1, int(np.ceil(W_cs_m2 / 5e-6)))

    osdi = pdk_root / "ihp-sg13g2/libs.tech/ngspice/osdi/psp103_nqs.osdi"
    lib = pdk_root / "ihp-sg13g2/libs.tech/ngspice/models/cornerMOSlv.lib"

    return f"""* 1-stage OTA open-loop AC validation
.lib '{lib}' mos_tt

* DUT subcircuit
.subckt OTA_1stage VINP VINN VOUT VDD IBIAS VSS Vbias
* Diff pair (NMOS)
XMNDPM1 VOUT VINP IBIAS VSS sg13_lv_nmos w={W_diff} l={L_diff} ng={ng_diff}
XMNDPM2 N1   VINN IBIAS VSS sg13_lv_nmos w={W_diff} l={L_diff} ng={ng_diff}
* Current mirror (PMOS, diode-connected M2)
XMPCMM1 VOUT N1 VDD VDD sg13_lv_pmos w={W_al} l={L_al} ng={ng_al}
XMPCMM2 N1   N1 VDD VDD sg13_lv_pmos w={W_al} l={L_al} ng={ng_al}
* Current source (NMOS, diode-connected M2)
XMNCSM1 IBIAS Vbias VSS VSS sg13_lv_nmos w={W_cs_m1} l={L_cs} ng={ng_cs_m1}
XMNCSM2 Vbias Vbias VSS VSS sg13_lv_nmos w={W_cs_m2} l={L_cs} ng={ng_cs_m2}
.ends OTA_1stage

* Top-level: open-loop AC
xota VINP VINN VOUT VDD IBIAS VSS Vbias OTA_1stage
Vdd  VDD  0 1.5
Vss  VSS  0 0
Vbref Vbias 0 0.5
I0   VDD  Vbias 20e-6
Vp   VINP 0 dc 0.9 ac 1
Vn   VINN 0 dc 0.9 ac 0
Cl   VOUT VSS 1p

.control
pre_osdi '{osdi}'
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
    the symbolic gain."""
    from eda_agents.core.spice_runner import SpiceRunner

    corners = pick_three_corners(pareto_df)
    if len(corners) == 0:
        return pd.DataFrame()

    work_dir.mkdir(parents=True, exist_ok=True)
    runner = SpiceRunner(pdk="ihp_sg13g2")

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
        "--n-points", type=int, default=10,
        help="Sweep grid size for VTAIL / current-source bias (default: 10).",
    )
    parser.add_argument(
        "--workdir", type=Path,
        default=Path("./sstadex_pareto_ihp"),
        help="Where to write program.md / results.tsv / pareto.csv.",
    )
    parser.add_argument(
        "--validate-spice", action="store_true",
        help="Cross-validate 3 Pareto corners against ngspice (requires PDK_ROOT).",
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
    logger = logging.getLogger("examples.17_sstadex_pareto_ihp")

    if not os.environ.get("EDA_AGENTS_IHP_LUT_DIR"):
        logger.error(
            "EDA_AGENTS_IHP_LUT_DIR is unset. Set it to a clone of "
            "ihp-gmid-kit (e.g. /home/<you>/ihp-gmid-kit/data) and retry."
        )
        return 2

    if args.clean and args.workdir.exists():
        shutil.rmtree(args.workdir)

    lut = GmIdLookup(pdk="ihp_sg13g2")
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
        if not pdk_root or not Path(pdk_root, "ihp-sg13g2").exists():
            logger.error(
                "--validate-spice requires PDK_ROOT pointing at an "
                "IHP-Open-PDK clone containing ihp-sg13g2/."
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
