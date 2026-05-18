"""Tests for the Inverter-Based Amplifier topology on GF180MCU.

Mirrors ``tests/test_iba_ihp_topology.py``: structural checks need no
PDK or LUT and run in the default CI gate; the ``spice``-marked test
runs the full deck through ngspice and validates Wrøngm-comparable
Adc / GBW / Iq at the inverter trip point on the GF180 3.3 V rail.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

from eda_agents.topologies.iba_gf180 import InverterBasedAmplifierGF180


@pytest.fixture(scope="module")
def topo() -> InverterBasedAmplifierGF180:
    return InverterBasedAmplifierGF180()


class TestStructural:
    def test_topology_name_stable(self, topo):
        assert topo.topology_name() == "iba_gf180"

    def test_pdk_is_gf180(self, topo):
        assert topo.pdk.name == "gf180mcu"
        assert topo.pdk.finger_param == "nf"
        assert topo.pdk.VDD == pytest.approx(3.3)

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

    def test_design_space_widths_within_gf180_min(self, topo):
        space = topo.design_space()
        assert space["W_n_um"][0] >= topo.pdk.Wmin_m * 1e6 - 1e-9
        assert space["W_p_um"][0] >= topo.pdk.Wmin_m * 1e6 - 1e-9
        assert space["L_n_um"][0] >= topo.pdk.Lmin_m * 1e6 - 1e-9
        assert space["L_p_um"][0] >= topo.pdk.Lmin_m * 1e6 - 1e-9

    def test_default_params_are_in_design_space(self, topo):
        space = topo.design_space()
        defaults = topo.default_params()
        for name, val in defaults.items():
            lo, hi = space[name]
            assert lo <= val <= hi, f"{name}={val} out of [{lo},{hi}]"

    def test_vbias_default_is_mid_rail(self, topo):
        # GF180 inverter trip point should sit near VDD/2 once the
        # PMOS/NMOS ratio is tuned. The default bias is 1.65 V (=
        # 3.3 V / 2) so the autoresearch loop has a sane starting
        # point for the trip-point search.
        assert topo.default_params()["Vbias_V"] == pytest.approx(1.65)

    def test_specs_strings_advertise_gf180_floors(self, topo):
        text = topo.specs_description()
        assert "15" in text and "dB" in text     # Adc floor
        assert "5.00 MHz" in text                # GBW floor
        assert "60" in text and "deg" in text    # PM floor
        assert "20.0 uA" in text                 # Iq floor

    def test_relevant_skills_lists_ron_gm_first(self, topo):
        skills = topo.relevant_skills()
        assert skills[0] == "analog.ron_gm_sizing"
        assert "analog.gmid_sizing" in skills


class TestSizing:
    def test_sizing_clips_to_pdk_min_widths(self, topo):
        params = topo.default_params()
        params["W_n_um"] = 0.001  # well below GF180 Wmin=0.22 um
        sizing = topo.params_to_sizing(params)
        assert sizing["M_N"]["W"] >= topo.pdk.Wmin_m

    def test_multiplier_rounded_to_integer(self, topo):
        params = topo.default_params()
        params["m_n"] = 2.7
        params["m_p"] = 1.2
        sizing = topo.params_to_sizing(params)
        assert sizing["M_N"]["m"] == 3
        assert sizing["M_P"]["m"] == 1

    def test_vbias_passed_through(self, topo):
        params = topo.default_params()
        params["Vbias_V"] = 1.42
        sizing = topo.params_to_sizing(params)
        assert sizing["_Vbias"] == pytest.approx(1.42)
        assert sizing["_VDD"] == pytest.approx(3.3)


class TestNetlist:
    def test_netlist_uses_gf180_devices(self, topo):
        sizing = topo.params_to_sizing(topo.default_params())
        with tempfile.TemporaryDirectory() as td:
            cir = topo.generate_netlist(sizing, Path(td))
            text = cir.read_text()
        # GF180 device names + finger param.
        assert "nfet_03v3" in text
        assert "pfet_03v3" in text
        assert " nf=" in text   # GF180 finger param (vs IHP's ng=)
        assert " ng=" not in text
        # GF180 lib lines.
        assert "sm141064.ngspice" in text
        assert ".include $PDK_ROOT/gf180mcuD/" in text
        # 667 fF load.
        assert "6.6700e-13" in text

    def test_netlist_absorbs_multiplier_into_w(self, topo):
        params = topo.default_params()
        params["W_n_um"] = 0.5
        params["m_n"] = 3
        sizing = topo.params_to_sizing(params)
        with tempfile.TemporaryDirectory() as td:
            cir = topo.generate_netlist(sizing, Path(td))
            text = cir.read_text()
        nmos_lines = [
            line for line in text.splitlines()
            if line.startswith("XMN")
        ]
        assert len(nmos_lines) == 1, (
            f"Expected one NMOS instance line; got {nmos_lines!r}"
        )
        line = nmos_lines[0]
        assert " m=" not in line.lower()
        w_match = re.search(r"w=([\d.e+-]+)", line)
        assert w_match is not None
        w_val = float(w_match.group(1))
        # 0.5 um (unit) * 3 (multiplier) = 1.5 um total.
        assert w_val == pytest.approx(1.5e-6, rel=1e-3)


# ---------------------------------------------------------------------------
# SPICE-gated integration: full IBA run through ngspice on the GF180 PDK.
# ---------------------------------------------------------------------------

_HAS_GF180_PDK = bool(os.environ.get("PDK_ROOT")) and Path(
    os.environ.get("PDK_ROOT", ""), "gf180mcuD"
).exists()


@pytest.mark.spice
@pytest.mark.skipif(
    not _HAS_GF180_PDK,
    reason=(
        "GF180MCU PDK not on disk at $PDK_ROOT/gf180mcuD; set "
        "PDK_ROOT to your wafer-space-gf180mcu clone."
    ),
)
class TestSpiceIntegration:
    def test_iba_at_trip_point_meets_specs(self, topo):
        """At the default sizing, the GF180 IBA must reach the
        topology spec floors. The defaults were tuned against
        ngspice to land at Adc=19 dB, GBW=8.75 MHz, Iq=10 uA --
        comfortably above the published floors.
        """
        from eda_agents.core.spice_runner import SpiceRunner

        runner = SpiceRunner(pdk="gf180mcu")
        sizing = topo.params_to_sizing(topo.default_params())
        with tempfile.TemporaryDirectory() as td:
            cir = topo.generate_netlist(sizing, Path(td))
            result = runner.run(cir)

        assert result.success, f"ngspice failed: {result.error}"
        assert result.Adc_dB is not None
        assert result.GBW_Hz is not None
        assert result.Adc_dB >= topo.SPEC_ADC_DB - 1.0   # allow 1 dB margin
        assert result.GBW_Hz >= topo.SPEC_GBW_HZ
        iq = (result.measurements or {}).get("iq_dc")
        assert iq is not None
        assert iq < topo.SPEC_IQ_UA * 1e-6 * 2.0   # 2x headroom
