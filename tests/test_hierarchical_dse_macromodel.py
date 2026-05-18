"""Tests for the Macromodel + Testbench schema and the small-signal
element fan-out. No LUT or SPICE required -- everything works on
hand-injected primitive parameters."""

from __future__ import annotations

import pandas as pd
import pytest
import sympy as sym
from sympy import Symbol

from eda_agents.topologies.sstadex.macromodel import (
    Macromodel,
    NetlistInstance,
)
from eda_agents.topologies.sstadex.primitives import (
    Port,
    Primitive,
)
from eda_agents.topologies.sstadex.symbolic_mna import Resistor as MnaResistor
from eda_agents.topologies.sstadex.symbolic_mna import VCCS
from eda_agents.topologies.sstadex.testbench import (
    Testbench,
    VoltageSource as TbVoltageSource,
)


def _toy_primitive() -> Primitive:
    """A single-branch primitive that needs no LUT for unit tests."""
    return Primitive(
        name="toy_amp",
        transistor_type="nmos",
        pin_order=["VIN", "VOUT", "VSS"],
        subckt_name="toy_amp",
        ports={
            "VIN":  Port("VIN",  "input"),
            "VOUT": Port("VOUT", "output"),
            "VSS":  Port("VSS",  "supply"),
        },
        small_signal_branches=[
            {"name": "m1", "vd": "VOUT", "vg": "VIN", "vs": "VSS"},
        ],
        lengths_um=[0.4],
    )


class TestMacromodelInstances:
    def test_add_instance_appends(self):
        prim = _toy_primitive()
        m = Macromodel(name="top", ports=["VIN", "VOUT", "VSS"])
        m.add_instance("xtop", prim, {"VIN": "VIN", "VOUT": "VOUT", "VSS": "VSS"})
        assert len(m.instances) == 1
        assert isinstance(m.instances[0], NetlistInstance)
        assert m.instances[0].name == "xtop"
        assert m.instances[0].block is prim

    def test_small_signal_elements_emits_vccs_and_resistor(self):
        prim = _toy_primitive()
        m = Macromodel(name="top", ports=["VIN", "VOUT", "VSS"])
        m.add_instance("xa", prim, {"VIN": "VIN", "VOUT": "VOUT", "VSS": "VSS"})
        els = m.small_signal_elements()
        # one VCCS + one Resistor per branch.
        assert any(isinstance(e, VCCS) for e in els)
        assert any(isinstance(e, MnaResistor) for e in els)
        vccs = next(e for e in els if isinstance(e, VCCS))
        assert vccs.n_d == "VOUT"
        assert vccs.ctrl_p == "VIN"
        assert vccs.n_s == "VSS"
        # Symbol-naming convention: g_gm_<instance>_<branch>.
        assert vccs.gm == Symbol("g_gm_xa_m1")

    def test_nested_macromodel_rewrites_nets(self):
        # Build child macromodel exposing port VC; parent maps VC -> VOUT.
        prim = _toy_primitive()
        child = Macromodel(name="child", ports=["VIN_C", "VOUT_C", "VSS_C"])
        child.add_instance(
            "xc", prim,
            {"VIN": "VIN_C", "VOUT": "VOUT_C", "VSS": "VSS_C"},
        )
        parent = Macromodel(name="parent", ports=["VIN", "VOUT", "VSS"])
        parent.add_instance(
            "xchild", child,
            {"VIN_C": "VIN", "VOUT_C": "VOUT", "VSS_C": "VSS"},
        )
        els = parent.small_signal_elements()
        vccs = next(e for e in els if isinstance(e, VCCS))
        # Net rewriting maps child's VOUT_C -> parent's VOUT.
        assert vccs.n_d == "VOUT"
        assert vccs.ctrl_p == "VIN"
        assert vccs.n_s == "VSS"


class TestPropagatedConditions:
    def test_range_min_max_filter(self):
        m = Macromodel(name="m")
        m.propagated_conditions = {
            "direct": [
                {"kind": "range", "column": "W",
                 "condition": {"min": 1e-6, "max": 10e-6}},
            ],
            "derived": [],
        }
        df = pd.DataFrame({"W": [0.5e-6, 2e-6, 5e-6, 50e-6]})
        out = m.apply_propagated_conditions(df)
        assert list(out["W"]) == [2e-6, 5e-6]

    def test_symbol_column_resolves_to_string_column(self):
        m = Macromodel(name="m")
        m.propagated_conditions = {
            "direct": [
                {"kind": "range", "column": Symbol("W_diff"),
                 "condition": {"min": 1e-6}},
            ],
            "derived": [],
        }
        df = pd.DataFrame({"W_diff": [0.5e-6, 2e-6, 5e-6]})
        out = m.apply_propagated_conditions(df)
        assert list(out["W_diff"]) == [2e-6, 5e-6]

    def test_derived_metric_filter(self):
        m = Macromodel(name="m")
        m.derived_metrics = {
            "double_w": lambda df: df["W"] * 2,
        }
        m.propagated_conditions = {
            "direct": [],
            "derived": [
                {"kind": "metric", "metric": "double_w",
                 "condition": {"min": 5e-6}},
            ],
        }
        df = pd.DataFrame({"W": [1e-6, 2e-6, 3e-6, 4e-6]})
        out = m.apply_propagated_conditions(df)
        # double_w >= 5e-6 => W >= 2.5e-6 => keep 3e-6, 4e-6.
        assert list(out["W"]) == [3e-6, 4e-6]


class TestTestbenchEval:
    def test_inverting_amp_at_dc(self):
        # Toy macromodel: single inverting gain stage.
        prim = _toy_primitive()
        m = Macromodel(name="amp", ports=["VIN", "VOUT", "VSS"])
        m.add_instance("xa", prim, {"VIN": "VIN", "VOUT": "VOUT", "VSS": "VSS"})

        tb = Testbench(
            name="amp_gain",
            dut=m,
            elements=[
                TbVoltageSource("V_in", "VIN", "VSS", Symbol("V_in")),
            ],
            tf=("VOUT", "VIN"),
            parameter_map={Symbol("V_in"): 1, Symbol("s"): 0},
        )
        expr = tb.eval(simplify=False)
        # Substitute the parameter map.
        expr = expr.xreplace(tb.parameter_map)
        # Sized to gm = 100uS, R_gds = 100k -> gain = -gm * R = -10.
        numeric = expr.subs({
            Symbol("g_gm_xa_m1"): 100e-6,
            Symbol("R_gds_xa_m1"): 100e3,
        })
        assert float(sym.re(numeric)) == pytest.approx(-10.0, rel=1e-6)
