"""Tests for gLayout OTA integration in GF180OTATopology.

Unit tests (no external tools):
    pytest tests/test_ota_glayout.py -m "not magic and not spice and not glayout" -v

SPICE validation (needs ngspice + GF180MCU PDK):
    pytest tests/test_ota_glayout.py -m spice -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agents.topologies.ota_gf180 import GF180OTATopology


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def topo():
    return GF180OTATopology()


@pytest.fixture
def default_sizing(topo):
    return topo.params_to_sizing(topo.default_params())


@pytest.fixture
def glayout_defaults():
    return GF180OTATopology.glayout_default_params()


# Minimal gLayout netlist (same structure as opamp_twostage output).
# Uses diffpair_bias values for both CMIRROR instances (the bug).
SAMPLE_GLAYOUT_NETLIST = """\
.subckt NMOS D G S B l=2.0 w=10.0 m=1 dm=1
XMAIN D G S B nfet_03v3 l={l} w={w} m={m}
XDUMMY1 B B B B nfet_03v3 l={l} w={w} m={dm}
.ends NMOS

.subckt DIFF_PAIR VP VN VDD1 VDD2 VTAIL B
X0 VDD1 VP VTAIL B NMOS l=2.0 w=10.0 m=1 dm=1
X1 VDD2 VN VTAIL B NMOS l=2.0 w=10.0 m=1 dm=1
.ends DIFF_PAIR

.subckt CMIRROR VREF VCOPY VSS VB l=2.0 w=6.0 m=4
XA VREF VREF VSS VB nfet_03v3 l={l} w={w} m={m}
XB VCOPY VREF VSS VB nfet_03v3 l={l} w={w} m={m}
XDUMMY VB VB VB VB nfet_03v3 l={l} w={w} m={2}
.ends CMIRROR

.subckt INPUT_STAGE VP VN VDD1 VDD2 IBIAS VSS B
X0 VP VN VDD1 VDD2 wire0 B DIFF_PAIR
X1 IBIAS wire0 VSS VSS CMIRROR l=2.0 w=6.0 m=4
.ends INPUT_STAGE

.subckt DIFF_TO_SINGLE VIN VOUT VSS VSS2 l=5.0 w=4.0 mt=8 mb=2
XTOP1 V1 VIN VSS VSS pfet_03v3 l={l} w={w} m={mt}
XTOP2 VSS2 VIN VSS VSS pfet_03v3 l={l} w={w} m={mt}
XBOT1 VIN VIN V1 VSS pfet_03v3 l={l} w={w} m={mb}
XBOT2 VOUT VIN VSS2 VSS pfet_03v3 l={l} w={w} m={mb}
.ends DIFF_TO_SINGLE

.subckt PMOS D G S B l=2.0 w=7.0 m=30 dm=6
XMAIN D G S B pfet_03v3 l={l} w={w} m={m}
XDUMMY1 B B B B pfet_03v3 l={l} w={w} m={dm}
.ends PMOS

.subckt DIFF_TO_SINGLE_CS VIN1 VIN2 VOUT VSS VSS2
X0 VIN1 VIN2 VSS VSS2 DIFF_TO_SINGLE l=5.0 w=4.0 mt=8 mb=2
X1 VOUT VIN2 VSS VSS PMOS l=2.0 w=7.0 m=30 dm=6
X2 VOUT VIN2 VSS VSS PMOS l=2.0 w=7.0 m=30 dm=6
.ends DIFF_TO_SINGLE_CS

.subckt MIMCap V1 V2 l=1 w=1
X1 V1 V2 mimcap_1p0fF l={l} w={w}
.ends MIMCap

.subckt MIMCAP_ARR V1 V2
X0 V1 V2 MIMCap l=12.0 w=12.0
X1 V1 V2 MIMCap l=12.0 w=12.0
X2 V1 V2 MIMCap l=12.0 w=12.0
X3 V1 V2 MIMCap l=12.0 w=12.0
X4 V1 V2 MIMCap l=12.0 w=12.0
X5 V1 V2 MIMCap l=12.0 w=12.0
.ends MIMCAP_ARR

.subckt GAIN_STAGE VIN1 VIN2 VOUT VDD IBIAS GND
X0 VIN1 VIN2 VOUT VDD wire0 DIFF_TO_SINGLE_CS
X1 IBIAS VOUT GND GND CMIRROR l=2.0 w=6.0 m=4
X2 VOUT wire0 MIMCAP_ARR
.ends GAIN_STAGE

.subckt OPAMP_TWO_STAGE VDD GND DIFFPAIR_BIAS VP VN CS_BIAS VOUT
X0 VP VN wire0 wire1 DIFFPAIR_BIAS GND GND INPUT_STAGE
X1 wire0 wire1 VOUT VDD CS_BIAS GND GAIN_STAGE
.ends OPAMP_TWO_STAGE
"""


# ---------------------------------------------------------------------------
# Step 1: glayout_default_params
# ---------------------------------------------------------------------------


class TestGlayoutDefaultParams:
    def test_returns_expected_keys(self):
        params = GF180OTATopology.glayout_default_params()
        expected_keys = {
            "half_diffpair_params",
            "diffpair_bias",
            "half_common_source_params",
            "half_common_source_bias",
            "half_pload",
            "mim_cap_size",
            "mim_cap_rows",
        }
        assert set(params.keys()) == expected_keys

    def test_tuples_have_correct_lengths(self):
        params = GF180OTATopology.glayout_default_params()
        # 3-element tuples: (w, l, fingers)
        assert len(params["half_diffpair_params"]) == 3
        assert len(params["diffpair_bias"]) == 3
        assert len(params["half_pload"]) == 3
        # 4-element tuples: (w, l, fingers, mults)
        assert len(params["half_common_source_params"]) == 4
        assert len(params["half_common_source_bias"]) == 4
        # 2-element tuple: (size_x, size_y)
        assert len(params["mim_cap_size"]) == 2


# ---------------------------------------------------------------------------
# Step 2: CS_BIAS preprocessing fix
# ---------------------------------------------------------------------------


class TestCsBiasFix:
    def test_patches_gain_stage_cmirror(self, topo):
        """GAIN_STAGE CMIRROR should get half_common_source_bias values."""
        glayout_params = {
            "half_common_source_bias": (10.0, 3.0, 6, 2),
            "diffpair_bias": (6, 2, 4),
        }

        fixed = GF180OTATopology._fix_cs_bias_netlist(
            SAMPLE_GLAYOUT_NETLIST, glayout_params,
        )

        # Find the GAIN_STAGE CMIRROR instance
        in_gain_stage = False
        gain_cmirror_line = None
        for line in fixed.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith(".subckt") and "gain_stage" in stripped:
                in_gain_stage = True
            elif in_gain_stage and stripped.startswith(".ends"):
                in_gain_stage = False
            elif in_gain_stage and "cmirror" in stripped:
                gain_cmirror_line = line
                break

        assert gain_cmirror_line is not None, "GAIN_STAGE CMIRROR line not found"
        assert "w=10.0" in gain_cmirror_line
        assert "l=3.0" in gain_cmirror_line
        assert "m=6" in gain_cmirror_line

    def test_does_not_modify_input_stage(self, topo):
        """INPUT_STAGE CMIRROR should keep original diffpair_bias values."""
        glayout_params = {
            "half_common_source_bias": (10.0, 3.0, 6, 2),
            "diffpair_bias": (6, 2, 4),
        }

        fixed = GF180OTATopology._fix_cs_bias_netlist(
            SAMPLE_GLAYOUT_NETLIST, glayout_params,
        )

        in_input_stage = False
        input_cmirror_line = None
        for line in fixed.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith(".subckt") and "input_stage" in stripped:
                in_input_stage = True
            elif in_input_stage and stripped.startswith(".ends"):
                in_input_stage = False
            elif in_input_stage and "cmirror" in stripped:
                input_cmirror_line = line
                break

        assert input_cmirror_line is not None
        # Should still have original diffpair_bias values
        assert "w=6.0" in input_cmirror_line
        assert "l=2.0" in input_cmirror_line
        assert "m=4" in input_cmirror_line

    def test_does_not_modify_subckt_definition(self):
        """The CMIRROR .subckt definition should not be changed."""
        glayout_params = {
            "half_common_source_bias": (10.0, 3.0, 6, 2),
        }

        fixed = GF180OTATopology._fix_cs_bias_netlist(
            SAMPLE_GLAYOUT_NETLIST, glayout_params,
        )

        for line in fixed.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith(".subckt cmirror"):
                # Subckt definition should keep defaults from diffpair_bias
                assert "w=6.0" in line
                assert "l=2.0" in line
                break


# ---------------------------------------------------------------------------
# Step 3: Overlay testbench structure
# ---------------------------------------------------------------------------


class TestOverlayTestbenchStructure:
    def test_separate_bias_sources(self, topo, default_sizing, tmp_path):
        """Overlay testbench should have separate nb_dp and nb_cs nodes."""
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        cir_path = topo.generate_postlayout_testbench_overlay(
            glayout_netlist_path=netlist_path,
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "sim",
        )

        text = cir_path.read_text()
        assert "nb_dp" in text, "Missing nb_dp bias node"
        assert "nb_cs" in text, "Missing nb_cs bias node"
        assert "Ibias_dp" in text, "Missing Ibias_dp source"
        assert "Ibias_cs" in text, "Missing Ibias_cs source"

    def test_port_mapping_vp_vn(self, topo, default_sizing, tmp_path):
        """VP (inverting) should map to inn, VN (non-inverting) to inp."""
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        cir_path = topo.generate_postlayout_testbench_overlay(
            glayout_netlist_path=netlist_path,
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "sim",
        )

        text = cir_path.read_text()
        # Instance line: X1 VDD 0 nb_dp inn inp nb_cs vout <subckt>
        # Port order: VDD GND DIFFPAIR_BIAS VP VN CS_BIAS VOUT
        # So VP=inn (4th port), VN=inp (5th port)
        for line in text.splitlines():
            if line.startswith("X1 "):
                parts = line.split()
                # parts: X1 VDD 0 nb_dp inn inp nb_cs vout <subckt_name>
                assert parts[4] == "inn", f"VP should map to inn, got {parts[4]}"
                assert parts[5] == "inp", f"VN should map to inp, got {parts[5]}"
                break
        else:
            pytest.fail("X1 instance line not found in testbench")

    def test_dc_feedback_present(self, topo, default_sizing, tmp_path):
        """Inductor feedback Lfb should connect vout to inn."""
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        cir_path = topo.generate_postlayout_testbench_overlay(
            glayout_netlist_path=netlist_path,
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "sim",
        )

        text = cir_path.read_text()
        assert "Lfb vout inn 1T" in text

    def test_ac_drive_on_inp(self, topo, default_sizing, tmp_path):
        """AC stimulus should be on the non-inverting input (inp)."""
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        cir_path = topo.generate_postlayout_testbench_overlay(
            glayout_netlist_path=netlist_path,
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "sim",
        )

        text = cir_path.read_text()
        assert "Vinp inp 0" in text
        assert "AC=1" in text


# ---------------------------------------------------------------------------
# Step 2+3 combined: preprocessing in overlay
# ---------------------------------------------------------------------------


class TestOverlayPreprocessing:
    def test_mim_cap_model_fixed(self, topo, default_sizing, tmp_path):
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        cir_path = topo.generate_postlayout_testbench_overlay(
            glayout_netlist_path=netlist_path,
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "sim",
        )

        # The fixed netlist is included; check it was written
        sim_dir = tmp_path / "sim"
        fixed = (sim_dir / "opamp.spice").read_text()
        assert "mimcap_1p0fF" not in fixed
        assert "cap_mim_1f0_m2m3_noshield" in fixed

    def test_units_converted_to_meters(self, topo, default_sizing, tmp_path):
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        topo.generate_postlayout_testbench_overlay(
            glayout_netlist_path=netlist_path,
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "sim",
        )

        fixed = (tmp_path / "sim" / "opamp.spice").read_text()
        # l=2.0 (um) should become l=2.000000e-06 (meters)
        assert "2.000000e-06" in fixed
        # Original um values should not appear for transistor params
        # (MIM cap uses c_length/c_width which are NOT converted)

    def test_cs_bias_fixed_with_defaults(self, topo, default_sizing, tmp_path):
        """CS_BIAS fix should apply when using glayout_default_params."""
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        defaults = GF180OTATopology.glayout_default_params()

        topo.generate_postlayout_testbench_overlay(
            glayout_netlist_path=netlist_path,
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "sim",
            glayout_params=defaults,
        )

        fixed = (tmp_path / "sim" / "opamp.spice").read_text()

        # Find GAIN_STAGE CMIRROR in the preprocessed netlist.
        # After CS_BIAS fix: w=6 l=2 m=8 (from half_common_source_bias=(6,2,8,2))
        # After um->m: w=6e-6, l=2e-6, m=8 (m is not converted)
        in_gain_stage = False
        for line in fixed.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith(".subckt") and "gain_stage" in stripped:
                in_gain_stage = True
            elif in_gain_stage and stripped.startswith(".ends"):
                in_gain_stage = False
            elif in_gain_stage and "cmirror" in stripped:
                assert "m=8" in line, f"Expected m=8, got: {line}"
                break


# ---------------------------------------------------------------------------
# Step 4: Baseline testbench
# ---------------------------------------------------------------------------


class TestBaselineTestbench:
    def test_generates_baseline_file(self, topo, default_sizing, tmp_path):
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        cir_path = topo.generate_glayout_baseline_testbench(
            glayout_netlist_path=netlist_path,
            sizing=default_sizing,
            work_dir=tmp_path / "baseline",
        )

        assert cir_path.exists()
        assert "baseline" in cir_path.name

    def test_baseline_has_no_parasitic_caps(self, topo, default_sizing, tmp_path):
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        cir_path = topo.generate_glayout_baseline_testbench(
            glayout_netlist_path=netlist_path,
            sizing=default_sizing,
            work_dir=tmp_path / "baseline",
        )

        text = cir_path.read_text()
        assert "Parasitic" not in text
        assert "Cp" not in text  # no Cpxx parasitic cap elements

    def test_baseline_has_separate_bias(self, topo, default_sizing, tmp_path):
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        cir_path = topo.generate_glayout_baseline_testbench(
            glayout_netlist_path=netlist_path,
            sizing=default_sizing,
            work_dir=tmp_path / "baseline",
        )

        text = cir_path.read_text()
        assert "nb_dp" in text
        assert "nb_cs" in text
        assert "Ibias_dp" in text
        assert "Ibias_cs" in text

    def test_baseline_same_structure_as_overlay(self, topo, default_sizing, tmp_path):
        """Baseline and overlay should share circuit structure (sans parasitics)."""
        netlist_path = tmp_path / "opamp.spice"
        netlist_path.write_text(SAMPLE_GLAYOUT_NETLIST)

        baseline_cir = topo.generate_glayout_baseline_testbench(
            glayout_netlist_path=netlist_path,
            sizing=default_sizing,
            work_dir=tmp_path / "baseline",
        )
        overlay_cir = topo.generate_postlayout_testbench_overlay(
            glayout_netlist_path=netlist_path,
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "overlay",
        )

        base_text = baseline_cir.read_text()
        over_text = overlay_cir.read_text()

        # Both should have the same instance line
        for text in [base_text, over_text]:
            found = False
            for line in text.splitlines():
                if line.startswith("X1 "):
                    assert "nb_dp" in line
                    assert "nb_cs" in line
                    found = True
                    break
            assert found, "X1 instance line not found"


# ---------------------------------------------------------------------------
# Step 5: PostLayoutResult baseline fields
# ---------------------------------------------------------------------------


class TestPostLayoutResultBaseline:
    def test_baseline_fields_default(self):
        from eda_agents.agents.phase_results import PostLayoutResult

        r = PostLayoutResult()
        assert r.baseline_Adc_dB is None
        assert r.baseline_GBW_Hz is None
        assert r.baseline_PM_deg is None
        assert r.baseline_fom == 0.0
        assert r.baseline_valid is False

    def test_summary_with_baseline(self):
        from eda_agents.agents.phase_results import PostLayoutResult

        r = PostLayoutResult(
            gds_path="/tmp/test.gds",
            post_Adc_dB=45.0,
            post_GBW_Hz=2e6,
            post_PM_deg=60.0,
            baseline_Adc_dB=48.0,
            baseline_GBW_Hz=2.5e6,
            baseline_PM_deg=65.0,
        )
        summary = r.summary
        assert "base=48.0" in summary
        assert "45.0dB" in summary


# ---------------------------------------------------------------------------
# Hybrid testbench tests
# ---------------------------------------------------------------------------


class TestHybridTestbench:
    """Tests for the hybrid post-layout approach (pre-layout OTA + gLayout parasitics)."""

    def test_hybrid_port_map_bias_nodes(self):
        """Both DIFFPAIR_BIAS and CS_BIAS should map to 'nb'."""
        pm = GF180OTATopology._HYBRID_PORT_MAP
        assert pm["DIFFPAIR_BIAS"] == "nb"
        assert pm["CS_BIAS"] == "nb"
        assert pm["VDD"] == "VDD"
        assert pm["GND"] == "0"
        assert pm["VP"] == "inn"
        assert pm["VN"] == "inp"
        assert pm["VOUT"] == "vout"

    def test_hybrid_generates_file(self, topo, default_sizing, tmp_path):
        """Hybrid testbench file should be created with correct name."""
        cir_path = topo.generate_hybrid_postlayout_testbench(
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "hybrid",
        )

        assert cir_path.exists()
        assert cir_path.name == "gf180_ota_hybrid_postlayout.ac.cir"

    def test_hybrid_contains_parasitic_caps(self, topo, default_sizing, tmp_path):
        """Synthetic parasitic caps should appear in hybrid testbench output."""
        from dataclasses import dataclass

        @dataclass
        class FakeCap:
            net1: str
            net2: str
            value_fF: float

        caps = [
            FakeCap(net1="VOUT", net2="VDD", value_fF=15.0),
            FakeCap(net1="VP", net2="GND", value_fF=5.0),
        ]

        cir_path = topo.generate_hybrid_postlayout_testbench(
            parasitic_caps=caps,
            sizing=default_sizing,
            work_dir=tmp_path / "hybrid",
        )

        text = cir_path.read_text()
        assert "Parasitic caps from gLayout PEX" in text
        assert "Hybrid:" in text
        # Port-to-port cap: VOUT->vout, VDD->VDD
        assert "vout" in text
        assert "15.0000f" in text

    def test_hybrid_contains_prelayout_netlist(self, topo, default_sizing, tmp_path):
        """Hybrid testbench should include the pre-layout netlist."""
        cir_path = topo.generate_hybrid_postlayout_testbench(
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "hybrid",
        )

        text = cir_path.read_text()
        assert ".include gf180_ota.net" in text

    def test_hybrid_no_glayout_subcircuits(self, topo, default_sizing, tmp_path):
        """Hybrid testbench should NOT contain gLayout subcircuit names."""
        cir_path = topo.generate_hybrid_postlayout_testbench(
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "hybrid",
        )

        text = cir_path.read_text()
        assert "OPAMP_TWO_STAGE" not in text
        assert "DIFF_PAIR" not in text
        assert "GAIN_STAGE" not in text
        assert "DIFF_TO_SINGLE" not in text

    def test_hybrid_title_updated(self, topo, default_sizing, tmp_path):
        """First line should indicate hybrid post-layout mode."""
        cir_path = topo.generate_hybrid_postlayout_testbench(
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "hybrid",
        )

        text = cir_path.read_text()
        first_line = text.splitlines()[0]
        assert "Hybrid" in first_line

    def test_hybrid_dat_filename(self, topo, default_sizing, tmp_path):
        """Output .dat file should use hybrid naming."""
        cir_path = topo.generate_hybrid_postlayout_testbench(
            parasitic_caps=[],
            sizing=default_sizing,
            work_dir=tmp_path / "hybrid",
        )

        text = cir_path.read_text()
        assert "gf180_ota_hybrid_postlayout.ac.dat" in text
        assert "gf180_ota.ac.dat" not in text


# ---------------------------------------------------------------------------
# SPICE integration tests
# ---------------------------------------------------------------------------


@pytest.mark.spice
class TestGlayoutBaselineSpice:
    """Integration test: baseline testbench should produce reasonable gain.

    Requires ngspice and GF180MCU PDK (PDK_ROOT set).
    """

    @pytest.fixture(autouse=True)
    def check_prereqs(self):
        import shutil

        if not shutil.which("ngspice"):
            pytest.skip("ngspice not found in PATH")

        import os
        if not os.environ.get("PDK_ROOT"):
            pytest.skip("PDK_ROOT not set")

    def test_baseline_functional_amplifier(self, topo, default_sizing, tmp_path):
        """gLayout baseline with default params should produce Adc > 20dB."""
        # Use the real gLayout netlist if available, else skip
        real_netlist = Path("/tmp/postlayout_overlay2/postlayout/layout/opamp_twostage.spice")
        if not real_netlist.exists():
            pytest.skip("Real gLayout netlist not available")

        from eda_agents.core.spice_runner import SpiceRunner

        cir_path = topo.generate_glayout_baseline_testbench(
            glayout_netlist_path=real_netlist,
            sizing=default_sizing,
            work_dir=tmp_path / "baseline",
        )

        runner = SpiceRunner()
        result = runner.run(cir_path, work_dir=tmp_path / "baseline")

        assert result.success, f"SPICE failed: {result.error}"
        assert result.Adc_dB is not None, "No Adc measurement"
        assert result.Adc_dB > 20.0, f"Adc={result.Adc_dB:.1f}dB < 20dB (non-functional)"
