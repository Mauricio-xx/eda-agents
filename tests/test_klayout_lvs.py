"""Tests for KLayout LVS runner.

Integration tests (needs klayout + GF180 PDK):
    pytest tests/test_klayout_lvs.py -m klayout -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agents.core.klayout_lvs import KLayoutLvsResult, KLayoutLvsRunner


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestKLayoutLvsResult:
    def test_match_summary(self):
        r = KLayoutLvsResult(success=True, match=True)
        assert "match" in r.summary.lower()
        assert "mismatch" not in r.summary.lower()

    def test_mismatch_summary(self):
        r = KLayoutLvsResult(success=True, match=False)
        assert "MISMATCH" in r.summary.upper()

    def test_error_summary(self):
        r = KLayoutLvsResult(success=False, match=False, error="crashed")
        assert "error" in r.summary.lower()


class TestKLayoutLvsRunnerInit:
    def test_gds_not_found(self, tmp_path):
        runner = KLayoutLvsRunner(pdk_root="/nonexistent")
        result = runner.run(
            gds_path=tmp_path / "missing.gds",
            netlist_path=tmp_path / "test.cdl",
            run_dir=tmp_path / "run",
        )
        assert not result.success
        assert "not found" in result.error

    def test_netlist_not_found(self, tmp_path):
        gds = tmp_path / "test.gds"
        gds.write_bytes(b"")
        runner = KLayoutLvsRunner(pdk_root="/nonexistent")
        result = runner.run(
            gds_path=gds,
            netlist_path=tmp_path / "missing.cdl",
            run_dir=tmp_path / "run",
        )
        assert not result.success
        assert "not found" in result.error

    def test_script_not_found(self, tmp_path):
        gds = tmp_path / "test.gds"
        gds.write_bytes(b"")
        cdl = tmp_path / "test.cdl"
        cdl.write_text("* netlist")
        runner = KLayoutLvsRunner(pdk_root="/nonexistent")
        result = runner.run(gds_path=gds, netlist_path=cdl, run_dir=tmp_path / "run")
        assert not result.success
        assert "run_lvs.py" in result.error


# ---------------------------------------------------------------------------
# Integration tests (require klayout + GF180 PDK)
# ---------------------------------------------------------------------------

GF180_PDK_ROOT = Path(
    "/home/montanares/git/wafer-space-gf180mcu"
)

LVS_TEST_DIR = GF180_PDK_ROOT / (
    "gf180mcuD/libs.tech/klayout/tech/lvs/testing/"
    "testcases/unit/mos_devices"
)

NFET_GDS = LVS_TEST_DIR / "layout" / "nfet_03v3.gds"
NFET_CDL = LVS_TEST_DIR / "netlist" / "nfet_03v3.cdl"


@pytest.mark.klayout
class TestKLayoutLvsIntegration:
    """Integration tests that run real KLayout LVS.

    These require:
    - klayout in PATH
    - GF180MCU PDK at the expected location
    - python3 with klayout.db and docopt
    """

    @pytest.fixture(autouse=True)
    def check_prereqs(self):
        import shutil

        if not shutil.which("klayout"):
            pytest.skip("klayout not in PATH")
        if not GF180_PDK_ROOT.is_dir():
            pytest.skip("GF180MCU PDK not found")
        if not NFET_GDS.is_file():
            pytest.skip(f"Test GDS not found: {NFET_GDS}")
        if not NFET_CDL.is_file():
            pytest.skip(f"Test CDL not found: {NFET_CDL}")

    def test_lvs_nfet_match(self, tmp_path):
        """Run LVS on nfet_03v3.gds + .cdl -> expect match=True."""
        runner = KLayoutLvsRunner(
            pdk_root=str(GF180_PDK_ROOT),
            variant="C",
            timeout_s=300,
        )
        result = runner.run(
            gds_path=NFET_GDS,
            netlist_path=NFET_CDL,
            run_dir=tmp_path,
            top_cell="sample_nfet_03v3",
        )
        assert result.success, f"LVS failed: {result.error}"
        assert result.match, f"LVS mismatch: {result.stdout_tail[-500:]}"

    def test_validate_setup(self):
        runner = KLayoutLvsRunner(pdk_root=str(GF180_PDK_ROOT))
        problems = runner.validate_setup()
        assert problems == [], f"Setup problems: {problems}"
