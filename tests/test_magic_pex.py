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

from eda_agents.core.magic_pex import MagicPexResult, MagicPexRunner


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
