"""Tests for Magic PEX runner.

Unit tests (no magic needed):
    pytest tests/test_magic_pex.py -m "not magic" -v

Integration tests (needs magic + GF180MCU PDK):
    pytest tests/test_magic_pex.py -m magic -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from eda_agents.core.magic_pex import (
    ExtFileParser,
    MagicPexResult,
    MagicPexRunner,
    ParasiticCap,
    _detect_degenerate_netlist,
)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestMagicPexResult:
    def test_success_summary(self):
        r = MagicPexResult(
            success=True,
            extracted_netlist_path="/tmp/design.rcx.spice",
            corner="ngspice()",
        )
        assert "design.rcx.spice" in r.summary
        assert "ngspice()" in r.summary

    def test_error_summary(self):
        r = MagicPexResult(success=False, error="magic not found")
        assert "error" in r.summary.lower()
        assert "magic not found" in r.summary


class TestMagicPexRunnerInit:
    def test_validate_setup_missing_magic(self, tmp_path):
        with patch("shutil.which", return_value=None):
            runner = MagicPexRunner(pdk_root=str(tmp_path))
            problems = runner.validate_setup()
            assert any("magic" in p.lower() for p in problems)

    def test_validate_setup_missing_pdk(self, tmp_path):
        runner = MagicPexRunner(pdk_root=str(tmp_path / "nonexistent"))
        problems = runner.validate_setup()
        assert any("pdk" in p.lower() or "not found" in p.lower() for p in problems)

    def test_run_missing_gds(self, tmp_path):
        runner = MagicPexRunner(pdk_root=str(tmp_path))
        result = runner.run(
            gds_path=tmp_path / "nonexistent.gds",
            design_name="test",
            work_dir=tmp_path / "work",
        )
        assert not result.success
        assert "not found" in result.error

    def test_run_missing_magic(self, tmp_path):
        # Create a dummy GDS file
        gds = tmp_path / "test.gds"
        gds.write_bytes(b"\x00")

        with patch("shutil.which", return_value=None):
            runner = MagicPexRunner(pdk_root=str(tmp_path))
            result = runner.run(
                gds_path=gds,
                design_name="test",
                work_dir=tmp_path / "work",
            )
            assert not result.success
            assert "magic" in result.error.lower()


# ---------------------------------------------------------------------------
# Integration tests (require magic + GF180MCU PDK + .venv-glayout)
# ---------------------------------------------------------------------------


class TestExtFileParser:
    """Tests for .ext file parasitic cap parsing."""

    SAMPLE_EXT = """\
timestamp 1711891234
version 1.0
tech gf180mcuD
style ngspice()
scale 100 1 200
port "VDD" 1 2000 0 0 0
port "GND" 2 0 0 0 0
port "VP" 3 1000 0 0 0
port "VN" 4 1000 0 0 0
port "VOUT" 5 1500 0 0 0
port "DIFFPAIR_BIAS" 6 500 0 0 0
port "CS_BIAS" 7 500 0 0 0
node "VDD" 5000.0 100 200 metalc
node "GND" 3000.0 0 0 metalc
node "VP" 193000.0 100 200 metal1
node "VN" 192000.0 100 200 metal1
node "DIFFPAIR_BIAS" 774000.0 100 200 metal2
node "VOUT" 500000.0 200 300 metal2
node "a_n726_2275#" 150000.0 50 60 metal1
cap "VP" "VN" 12880.6
cap "VP" "a_n726_2275#" 5400.0
cap "VN" "a_n726_2275#" 5200.0
cap "DIFFPAIR_BIAS" "a_n726_2275#" 2340000.0
cap "VOUT" "GND" 45000.0
"""

    def test_parse_caps_from_sample(self, tmp_path):
        ext_file = tmp_path / "design.ext"
        ext_file.write_text(self.SAMPLE_EXT)
        parser = ExtFileParser(ext_file)
        caps = parser.parse_caps()

        assert len(caps) == 5
        # VP-VN coupling: 12880.6 aF = 12.8806 fF
        vp_vn = [c for c in caps if {c.net1, c.net2} == {"VP", "VN"}]
        assert len(vp_vn) == 1
        assert abs(vp_vn[0].value_fF - 12.8806) < 0.001

    def test_parse_caps_nonexistent_file(self, tmp_path):
        parser = ExtFileParser(tmp_path / "nonexistent.ext")
        assert parser.parse_caps() == []

    def test_port_caps_filter(self, tmp_path):
        ext_file = tmp_path / "design.ext"
        ext_file.write_text(self.SAMPLE_EXT)
        parser = ExtFileParser(ext_file)

        port_names = ["VDD", "GND", "VP", "VN", "VOUT", "DIFFPAIR_BIAS", "CS_BIAS"]
        port_caps = parser.parse_port_caps(port_names)
        # All 5 caps involve at least one port (the internal node is caught
        # by the other cap end being a port)
        assert len(port_caps) == 5

        # Filter with only VP and VN
        vp_vn_caps = parser.parse_port_caps(["VP", "VN"])
        # VP-VN, VP-internal, VN-internal = 3 caps
        assert len(vp_vn_caps) == 3

    def test_labeled_node_total_cap(self, tmp_path):
        ext_file = tmp_path / "design.ext"
        ext_file.write_text(self.SAMPLE_EXT)
        parser = ExtFileParser(ext_file)

        totals = parser.labeled_node_total_cap(["VP", "VN", "DIFFPAIR_BIAS"])
        assert "VP" in totals
        # VP: 193000 aF = 193 fF
        assert abs(totals["VP"] - 193.0) < 0.1
        assert abs(totals["VN"] - 192.0) < 0.1
        assert abs(totals["DIFFPAIR_BIAS"] - 774.0) < 0.1

    def test_labeled_node_ignores_unlabeled(self, tmp_path):
        ext_file = tmp_path / "design.ext"
        ext_file.write_text(self.SAMPLE_EXT)
        parser = ExtFileParser(ext_file)

        totals = parser.labeled_node_total_cap(["VP"])
        assert list(totals.keys()) == ["VP"]


class TestDegenerateDetection:
    """Tests for degenerate netlist detection."""

    def test_healthy_netlist_not_degenerate(self, tmp_path):
        """A normal OTA netlist should not be flagged."""
        netlist = tmp_path / "good.spice"
        netlist.write_text("""\
.subckt OTA VDD GND VP VN VOUT
M1 net1 inn net2 VDD pfet W=10u L=2u
M2 net3 inp net2 VDD pfet W=10u L=2u
M3 net1 net1 GND GND nfet W=2u L=5u
M4 net3 net1 GND GND nfet W=2u L=5u
M5 net2 nb VDD VDD pfet W=20u L=2u
M6 VOUT net3 GND GND nfet W=20u L=5u
M7 VOUT nb VDD VDD pfet W=40u L=2u
.ends
""")
        assert not _detect_degenerate_netlist(netlist)

    def test_degenerate_netlist_detected(self, tmp_path):
        """When >60% of terminals are on one net, flag as degenerate."""
        netlist = tmp_path / "bad.spice"
        # All transistors have VOUT on most terminals
        lines = [".subckt OTA VDD GND VP VN VOUT"]
        for i in range(20):
            lines.append(f"M{i} VOUT VOUT VOUT VOUT nfet W=1u L=1u")
        lines.append(".ends")
        netlist.write_text("\n".join(lines))
        assert _detect_degenerate_netlist(netlist)

    def test_empty_netlist(self, tmp_path):
        netlist = tmp_path / "empty.spice"
        netlist.write_text("* empty\n.end\n")
        assert not _detect_degenerate_netlist(netlist)

    def test_nonexistent_file(self, tmp_path):
        assert not _detect_degenerate_netlist(tmp_path / "nope.spice")

    def test_threshold_boundary(self, tmp_path):
        """Test near the 60% boundary."""
        netlist = tmp_path / "borderline.spice"
        # 10 transistors, 40 terminals total
        # Put "VOUT" on 23/40 = 57.5% of terminals (below threshold)
        lines = [".subckt OTA VDD GND VOUT"]
        # 5 transistors with VOUT on all 4 terminals = 20
        for i in range(5):
            lines.append(f"M{i} VOUT VOUT VOUT VOUT nfet W=1u L=1u")
        # 5 transistors with VOUT on ~0-1 terminals = 3
        for i in range(5, 10):
            lines.append(f"M{i} net{i} gate{i} src{i} bulk{i} nfet W=1u L=1u")
        lines.append(".ends")
        netlist.write_text("\n".join(lines))
        # 20 VOUT out of 40 = 50% -- below threshold
        assert not _detect_degenerate_netlist(netlist)


class TestMagicPexResultExtended:
    def test_degenerate_summary(self):
        r = MagicPexResult(
            success=True,
            extracted_netlist_path="/tmp/design.rcx.spice",
            degenerate=True,
        )
        assert "DEGENERATE" in r.summary

    def test_ext_file_path(self):
        r = MagicPexResult(
            success=True,
            extracted_netlist_path="/tmp/design.rcx.spice",
            ext_file_path="/tmp/design.ext",
        )
        assert r.ext_file_path == "/tmp/design.ext"


@pytest.mark.magic
class TestMagicPexIntegration:
    """Integration tests that run real parasitic extraction.

    These require:
    - Magic installed and in PATH
    - GF180MCU PDK at PDK_ROOT or default location
    """

    @pytest.fixture(autouse=True)
    def check_prereqs(self):
        import shutil

        if not shutil.which("magic"):
            pytest.skip("magic not found in PATH")

        runner = MagicPexRunner()
        problems = runner.validate_setup()
        if problems:
            pytest.skip(f"Magic PEX prerequisites not met: {problems}")

    @pytest.mark.glayout
    def test_pex_on_nmos_gds(self, tmp_path):
        """Generate nmos GDS via gLayout, extract with Magic."""
        from eda_agents.core.glayout_runner import GLayoutRunner

        venv = Path(".venv-glayout")
        if not venv.is_dir():
            pytest.skip(".venv-glayout not found")

        # Generate a simple nmos GDS
        glayout = GLayoutRunner()
        gen_result = glayout.generate_component(
            component="nmos",
            params={"width": 1.0, "length": 0.28, "fingers": 2},
            output_dir=tmp_path / "layout",
        )
        assert gen_result.success, f"gLayout failed: {gen_result.error}"

        # Run PEX
        runner = MagicPexRunner()
        result = runner.run(
            gds_path=gen_result.gds_path,
            design_name="nmos",
            work_dir=tmp_path / "pex",
        )
        assert result.success, f"Magic PEX failed: {result.error}"
        assert result.extracted_netlist_path
        assert Path(result.extracted_netlist_path).is_file()
        assert ".rcx.spice" in result.extracted_netlist_path
