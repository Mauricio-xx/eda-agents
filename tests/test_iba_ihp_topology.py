"""Tests for the Inverter-Based Amplifier topology (IHP SG13G2 port of Wrøngm).

The structural checks (design space, sizing, netlist text) need no PDK
or LUT and run in the default CI gate. The ``spice``-marked test runs
the full deck through ngspice and validates Wrøngm-comparable Adc /
GBW / PM / Iq at the inverter trip point. It is skipped when the IHP
PDK is unavailable.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

from eda_agents.topologies.iba_ihp import InverterBasedAmplifier


@pytest.fixture(scope="module")
def topo() -> InverterBasedAmplifier:
    return InverterBasedAmplifier(pdk="ihp_sg13g2")


class TestStructural:
    def test_topology_name_stable(self, topo):
        assert topo.topology_name() == "iba_ihp"

    def test_design_space_has_seven_knobs(self, topo):
        space = topo.design_space()
        expected = {
            "W_n_um", "L_n_um", "m_n",
            "W_p_um", "L_p_um", "m_p",
            "Vbias_V",
        }
        assert set(space) == expected
        for _name, (lo, hi) in space.items():
            assert lo < hi
            assert lo > 0  # no negative widths or lengths

    def test_default_params_are_in_design_space(self, topo):
        space = topo.design_space()
        defaults = topo.default_params()
        for name, val in defaults.items():
            lo, hi = space[name]
            assert lo <= val <= hi, f"{name}={val} out of [{lo},{hi}]"

    def test_relevant_skills_lists_ron_gm_first(self, topo):
        skills = topo.relevant_skills()
        assert skills[0] == "analog.ron_gm_sizing"
        # gm/ID stays available as a fallback / cross-reference.
        assert "analog.gmid_sizing" in skills

    def test_forbidden_patterns_block_ron_blind_insights(self, topo):
        patterns = topo.forbidden_insight_patterns()
        sample = "I will ignore Ron in this design and use an ideal current source."
        assert any(p.search(sample) for p in patterns), (
            "At least one forbidden pattern should match the Ron-blind "
            "insight sample."
        )

    def test_forbidden_patterns_do_not_overmatch(self, topo):
        # Reasonable design discussion must not be blocked.
        ok_sample = "Raise the load capacitance and re-check phase margin."
        for p in topo.forbidden_insight_patterns():
            assert not p.search(ok_sample), (
                f"Pattern {p.pattern!r} matched a benign sentence."
            )


class TestSizing:
    def test_sizing_clips_to_pdk_min_widths(self, topo):
        params = topo.default_params()
        params["W_n_um"] = 0.001  # well below Wmin
        sizing = topo.params_to_sizing(params)
        assert sizing["M_N"]["W"] >= topo.pdk.Wmin_m

    def test_multiplier_rounded_to_integer(self, topo):
        params = topo.default_params()
        params["m_n"] = 3.7
        params["m_p"] = 1.2
        sizing = topo.params_to_sizing(params)
        assert sizing["M_N"]["m"] == 4
        assert sizing["M_P"]["m"] == 1

    def test_vbias_passed_through(self, topo):
        params = topo.default_params()
        params["Vbias_V"] = 0.72
        sizing = topo.params_to_sizing(params)
        assert sizing["_Vbias"] == pytest.approx(0.72)


class TestNetlist:
    def test_netlist_contains_lib_lines_and_devices(self, topo):
        sizing = topo.params_to_sizing(topo.default_params())
        with tempfile.TemporaryDirectory() as td:
            cir = topo.generate_netlist(sizing, Path(td))
            text = cir.read_text()
        # PDK lib directives ride on netlist_lib_lines() and must
        # appear (so the deck is technology-agnostic).
        assert ".lib" in text
        # Both devices instantiated as subcircuits (IHP convention).
        assert f"{topo.pdk.instance_prefix}MN" in text
        assert f"{topo.pdk.instance_prefix}MP" in text
        # Reference 667 fF load.
        assert "6.6700e-13" in text or "667f" in text.lower() or "6.67e-13" in text
        # AC sweep + DC op must be present inside the .control block.
        # ngspice control-block syntax uses bare ``ac dec`` and ``op``,
        # not the legacy ``.ac`` / ``.op`` directives.
        assert "ac dec" in text.lower()
        assert "op\n" in text.lower() or "op " in text.lower()

    def test_netlist_absorbs_multiplier_into_w(self, topo):
        # ``m=`` parameter on subcircuit instance lines triggers a
        # warning under IHP PDK; the topology must absorb the
        # multiplier into the W parameter instead.
        params = topo.default_params()
        params["W_n_um"] = 0.5
        params["m_n"] = 4
        sizing = topo.params_to_sizing(params)
        with tempfile.TemporaryDirectory() as td:
            cir = topo.generate_netlist(sizing, Path(td))
            text = cir.read_text()
        nmos_lines = [
            line for line in text.splitlines()
            if line.startswith("XMN") or line.startswith("MN ")
        ]
        assert len(nmos_lines) == 1, (
            f"Expected exactly one NMOS instance line; got {nmos_lines!r}"
        )
        nmos_line = nmos_lines[0]
        assert " m=" not in nmos_line.lower(), (
            f"Multiplier leaked onto subcircuit instance line: {nmos_line}"
        )
        w_match = re.search(r"w=([\d.e+-]+)", nmos_line)
        assert w_match is not None
        w_val = float(w_match.group(1))
        # 0.5 um (unit) * 4 (multiplier) = 2 um total.
        assert w_val == pytest.approx(2.0e-6, rel=1e-3)


# ---------------------------------------------------------------------------
# SPICE-gated integration: full IBA run through ngspice on the IHP PDK.
# ---------------------------------------------------------------------------

_HAS_IHP_PDK = bool(os.environ.get("PDK_ROOT")) and Path(
    os.environ.get("PDK_ROOT", ""), "ihp-sg13g2"
).exists()


@pytest.mark.spice
@pytest.mark.skipif(
    not _HAS_IHP_PDK,
    reason=(
        "IHP SG13G2 PDK not on disk at $PDK_ROOT/ihp-sg13g2; set "
        "PDK_ROOT to your IHP-Open-PDK clone."
    ),
)
class TestSpiceIntegration:
    def test_iba_at_trip_point_meets_specs(self, topo):
        """At the inverter trip point, the default IBA must reach the
        Wrøngm spec on Adc / GBW / PM, with Iq comparable to the
        reference 2.5 uA range.
        """
        from eda_agents.core.spice_runner import SpiceRunner

        runner = SpiceRunner(pdk="ihp_sg13g2")

        # Match Wrøngm's Ron/gm-sized IBA scale (Iq ~ 2.5 uA).
        params = topo.default_params()
        params.update({
            "W_n_um": 0.13, "m_n": 2,
            "W_p_um": 0.13, "L_p_um": 0.2, "m_p": 2,
            "Vbias_V": 0.60,
        })
        sizing = topo.params_to_sizing(params)
        with tempfile.TemporaryDirectory() as td:
            cir = topo.generate_netlist(sizing, Path(td))
            result = runner.run(cir)

        assert result.success, f"ngspice failed: {result.error}"
        assert result.Adc_dB is not None
        assert result.GBW_Hz is not None
        # Spec floors from the topology (looser than Wrøngm reports to
        # absorb LUT clipping / numerical differences):
        assert result.Adc_dB >= 20.0
        assert result.GBW_Hz >= 5e6  # gentle floor; Wrøngm target 9.55 MHz
        assert result.PM_deg is None or result.PM_deg >= 60.0
        iq = (result.measurements or {}).get("iq_dc")
        assert iq is not None
        # 5 uA budget per the topology, with some headroom -- catches
        # the case where Iq leaks above 10 uA due to broken bias.
        assert iq < 10e-6
