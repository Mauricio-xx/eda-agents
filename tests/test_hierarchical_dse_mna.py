"""Unit tests for the symbolic MNA solver under
``eda_agents.topologies.sstadex.symbolic_mna``.

All checks are formulaic: each test wires up a small network whose
analytical answer is known and verifies that the solver returns the
same expression (or a numerically equivalent one). Pure sympy --
no LUT, no PDK, runs in milliseconds even on a barebones CI worker.
"""

from __future__ import annotations

import sympy as sym

from eda_agents.topologies.sstadex.symbolic_mna import (
    Capacitor,
    CurrentSource,
    Resistor,
    VCCS,
    VoltageSource,
    build_system,
    solve_system,
    transfer_function,
)


class TestPassiveStamps:
    def test_resistor_divider_dc_gain(self):
        Vin, R1, R2 = sym.symbols("V_in R1 R2", positive=True)
        elements = [
            VoltageSource("V_in", "IN", "GND", Vin),
            Resistor("R1", "IN", "A", R1),
            Resistor("R2", "A", "GND", R2),
        ]
        tf = transfer_function(elements, "A", Vin, ground="GND")
        assert sym.simplify(tf - R2 / (R1 + R2)) == 0

    def test_capacitor_lowpass_at_dc_is_unity(self):
        Vin, R, C = sym.symbols("V_in R C", positive=True)
        s = sym.Symbol("s")
        elements = [
            VoltageSource("V_in", "IN", "GND", Vin),
            Resistor("R", "IN", "A", R),
            Capacitor("C", "A", "GND", C),
        ]
        tf = transfer_function(
            elements, "A", Vin, ground="GND", s_symbol=s
        )
        # At s = 0 the capacitor is open: V[A] = V_in.
        assert sym.simplify(tf.subs(s, 0) - 1) == 0
        # At the corner s = j/(R*C), |TF| should be 1/sqrt(2) (well-
        # known first-order low-pass corner).
        tf_j = tf.subs(s, sym.I / (R * C))
        mag = sym.sqrt(sym.simplify(tf_j * tf_j.conjugate()))
        assert sym.simplify(mag - 1 / sym.sqrt(2)) == 0


class TestVccs:
    def test_inverting_amp_gain(self):
        Vin, gm, RL = sym.symbols("V_in gm R_L", positive=True)
        elements = [
            VoltageSource("V_in", "IN", "GND", Vin),
            Resistor("RL", "OUT", "GND", RL),
            VCCS("Gm", "OUT", "GND", "IN", "GND", gm),
        ]
        tf = transfer_function(elements, "OUT", Vin, ground="GND")
        assert sym.simplify(tf - (-gm * RL)) == 0

    def test_two_vccs_in_series_cancels(self):
        # Two cascaded inverters give a positive gain g1*RL1*g2*RL2.
        Vin, g1, g2, RL1, RL2 = sym.symbols(
            "V_in g_1 g_2 R_L1 R_L2", positive=True
        )
        elements = [
            VoltageSource("V_in", "IN", "GND", Vin),
            Resistor("R_L1", "N1", "GND", RL1),
            VCCS("G_m1", "N1", "GND", "IN", "GND", g1),
            Resistor("R_L2", "OUT", "GND", RL2),
            VCCS("G_m2", "OUT", "GND", "N1", "GND", g2),
        ]
        tf = transfer_function(elements, "OUT", Vin, ground="GND")
        assert sym.simplify(tf - g1 * RL1 * g2 * RL2) == 0


class TestCurrentSource:
    def test_current_source_drives_resistor(self):
        # I_src pulled out of n+ flows back through R to ground.
        # V[n+] = -I_src * R (positive current leaving raises voltage
        # at the other side; with the canonical SPICE polarity
        # ``Isrc n+ n- value`` pulling +value out of n+, V[n+]
        # becomes -I_src * R when n+ is the only node connected.)
        Is, R = sym.symbols("I_s R", positive=True)
        elements = [
            CurrentSource("Is", "A", "GND", Is),
            Resistor("R", "A", "GND", R),
        ]
        sys = build_system(elements, ground="GND")
        voltages = solve_system(sys, simplify=True)
        # KCL at A: V/R = -I_s (current leaving via R = +I_s injected
        # via the source from outside -> negative on the LHS).
        assert sym.simplify(voltages["A"] - (-Is * R)) == 0


class TestGround:
    def test_output_at_ground_is_zero(self):
        Vin = sym.Symbol("V_in", positive=True)
        elements = [
            VoltageSource("V_in", "IN", "GND", Vin),
            Resistor("R", "IN", "GND", 1),
        ]
        tf = transfer_function(elements, "GND", Vin, ground="GND")
        assert tf == 0
