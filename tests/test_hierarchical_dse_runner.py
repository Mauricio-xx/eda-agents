"""End-to-end smoke test for ``HierarchicalDseRunner.run`` on a
1-stage OTA. LUT-gated."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sympy import Symbol

from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.core.pdk import get_pdk


def _resolve_ihp_lut_path() -> Path:
    env_val = os.environ.get("EDA_AGENTS_IHP_LUT_DIR")
    if env_val:
        return Path(env_val)
    return Path(get_pdk("ihp_sg13g2").lut_dir_default or "")


_LUT_DIR = _resolve_ihp_lut_path()
_NMOS_NPZ = _LUT_DIR / "sg13_lv_nmos.npz"

pytestmark = pytest.mark.skipif(
    not _NMOS_NPZ.exists(),
    reason=(
        f"IHP NFET LUT not found at {_NMOS_NPZ}. Set "
        "EDA_AGENTS_IHP_LUT_DIR to a clone of ihp-gmid-kit."
    ),
)


def _build_1stage_ota(lut, **knobs):
    from eda_agents.topologies.sstadex import (
        Library, Macromodel, Testbench,
        VoltageSource,
    )

    lib = Library(name="ihp_sg13g2", lut=lut)
    Vout, Vref, Vdd = 1.0, 0.9, 1.5
    I_amp = knobs.get("I_amp", 20e-6)
    N_points = int(knobs.get("N_points", 10))
    lengths = [0.4, 0.8, 1.6, 3.2, 6.4]
    vs = np.linspace(0.2, Vout - 0.1, N_points)

    diffpair = lib.get("simplediffpair", il=I_amp)
    diffpair.set_port_voltages({
        "VINP": Vref, "VINN": Vref,
        "VOUTP": Vout, "VOUTN": Vout, "VTAIL": vs,
    })
    df_dp = diffpair.build(lut)
    diffpair.outputs = {
        Symbol("W_diff"): df_dp["width"].values,
        Symbol("L_diff"): df_dp["length"].values,
    }
    diffpair.interface_variables = {"vs_diff": np.tile(vs, len(lengths))}

    cm = lib.get("simplecurrentmirror", il=I_amp)
    cm.set_port_voltages({
        "VINP": Vout, "VINN": Vout,
        "VOUTP": Vout, "VOUTN": Vout, "VDD": Vdd,
    })
    df_cm = cm.build(lut)
    cm.outputs = {
        Symbol("W_al"): df_cm["width"].values,
        Symbol("L_al"): df_cm["length"].values,
    }

    cs = lib.get("simplecurrentsource", il=I_amp)
    cs.set_port_voltages({"VOUTP": vs, "VSS": 0.0, "VINP": vs, "VINN": vs})
    df_cs = cs.build(lut)
    cs.outputs = {
        Symbol("W_cs_m1"): df_cs["width_m1"].values,
        Symbol("W_cs_m2"): df_cs["width_m2"].values,
        Symbol("L_cs"): df_cs["length"].values,
    }
    cs.interface_variables = {"vs_cs": np.tile(vs, len(lengths))}

    cs_macro = Macromodel(
        name="current_source_macro", ports=["VOUT", "VSS", "Vbias"],
        outputs=[Symbol("W_cs_m1"), Symbol("W_cs_m2"), Symbol("L_cs")],
        macromodel_parameters={Symbol("Ixcs_macro"): np.array([I_amp])},
        interface_variables=["vs_cs"],
    )
    cs_macro.add_instance("xcs", cs, {
        "VOUTP": "VOUT", "VSS": "VSS",
        "VOUTN": "Vbias", "VINP": "Vbias", "VINN": "Vbias",
    })
    cs_macro.num_level_exp = 1
    cs_macro.primitives = [cs]
    tb_ibias = Testbench(
        name="currentsource_ibas", dut=cs_macro,
        elements=[], tf=("VOUT", "Vbias"),
        parameter_map={
            Symbol("g_gm_cs_m2"): Symbol("g_gm_cs_m1"),
            Symbol("R_gds_cs_m2"): Symbol("R_gds_cs_m1"),
            Symbol("s"): 0,
        },
    )
    cs_macro.specifications = [
        tb_ibias.make_test(name="ibias_currentsource",
                            opt_goal="max", conditions={"min": [0]}),
    ]
    cs_macro.propagated_conditions = {
        "direct": [
            {"kind": "range", "column": Symbol("W_cs_m1"),
             "condition": {"min": 1e-6, "max": 1000e-6}},
            {"kind": "range", "column": Symbol("W_cs_m2"),
             "condition": {"min": 1e-6, "max": 1000e-6}},
        ], "derived": [],
    }

    ota = Macromodel(
        name="OTA_1stage_macro",
        ports=["VINP", "VINN", "VOUT", "VDD", "IBIAS", "Vbias", "VSS"],
        outputs=[Symbol("W_diff"), Symbol("L_diff"),
                 Symbol("W_al"), Symbol("L_al")],
        interface_variables=["vs_diff"],
        shared_nodes={"IBIAS_node": ["vs_diff", "vs_cs"]},
    )
    ota.add_instance("xdp", diffpair,
        {"VINP": "VINP", "VINN": "VINN", "VOUTP": "VOUT",
         "VOUTN": "N1", "VTAIL": "IBIAS"})
    ota.add_instance("xcm", cm,
        {"VINP": "N1", "VINN": "N1", "VOUTP": "VOUT",
         "VOUTN": "N1", "VDD": "VDD"})
    ota.add_instance("xcs_macro", cs_macro,
        {"VOUT": "IBIAS", "VSS": "VSS", "Vbias": "Vbias"})
    tb_gain = Testbench(
        name="ota_1stage_gain", dut=ota,
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
        tb_gain.make_test(name="gain_1stage", opt_goal="max",
                           conditions={"min": [1e-5]}),
    ]
    ota.opt_specifications = ota.specifications
    ota.primitives = [diffpair, cm]
    ota.submacromodels = [cs_macro]
    ota.num_level_exp = -1
    ota.run_pareto = True
    ota.propagated_conditions = {
        "direct": [
            {"kind": "range", "column": Symbol("W_al"),
             "condition": {"min": 1e-6, "max": 1000e-6}},
            {"kind": "range", "column": Symbol("W_diff"),
             "condition": {"min": 1e-6, "max": 1000e-6}},
        ], "derived": [],
    }
    return ota


class TestHierarchicalDseRunner:
    def test_single_shot_run_produces_pareto(self, tmp_path):
        from eda_agents.agents.hierarchical_dse_runner import (
            HierarchicalDseRunner,
        )

        lut = GmIdLookup(pdk="ihp_sg13g2")
        runner = HierarchicalDseRunner(
            macromodel_builder=_build_1stage_ota,
            lut=lut,
            knob_defaults={"I_amp": 20e-6, "N_points": 10},
        )
        result = runner.run(tmp_path)
        assert result.pareto_rows >= 3
        # Best FoM is the max gain along the Pareto. On IHP SG13G2 at
        # I_amp=20uA the analytical gain ceiling sits around 32-35 dB
        # (~40 V/V) -- assert at least 20x with margin.
        assert result.best_fom >= 20.0
        # Persistence artefacts exist and are non-empty.
        assert Path(result.results_tsv).stat().st_size > 0
        assert Path(result.program_md).stat().st_size > 0
        assert result.pareto_csv is not None
        assert Path(result.pareto_csv).stat().st_size > 0

    def test_tsv_columns_include_spec_and_widths(self, tmp_path):
        from eda_agents.agents.hierarchical_dse_runner import (
            HierarchicalDseRunner,
        )

        lut = GmIdLookup(pdk="ihp_sg13g2")
        runner = HierarchicalDseRunner(
            macromodel_builder=_build_1stage_ota,
            lut=lut,
            knob_defaults={"I_amp": 20e-6, "N_points": 5},
        )
        result = runner.run(tmp_path)
        df = pd.read_csv(result.results_tsv, sep="\t")
        for col in ("configuration_id", "row_id", "gain_1stage",
                     "area", "W_diff", "L_diff", "W_al", "L_al",
                     "W_cs_m1", "L_cs"):
            assert col in df.columns, (
                f"Expected column {col!r} missing from results.tsv "
                f"({list(df.columns)})"
            )
