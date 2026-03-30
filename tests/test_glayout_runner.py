"""Tests for gLayout runner.

Unit tests (no glayout needed):
    pytest tests/test_glayout_runner.py -m "not glayout" -v

Integration tests (needs .venv-glayout):
    pytest tests/test_glayout_runner.py -m glayout -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agents.core.glayout_runner import GLayoutResult, GLayoutRunner


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestGLayoutResult:
    def test_success_summary(self):
        r = GLayoutResult(
            success=True,
            gds_path="/tmp/nmos.gds",
            component="nmos",
        )
        assert "nmos" in r.summary
        assert "/tmp/nmos.gds" in r.summary

    def test_error_summary(self):
        r = GLayoutResult(
            success=False,
            component="nmos",
            error="venv not found",
        )
        assert "error" in r.summary.lower()


class TestGLayoutRunnerInit:
    def test_missing_venv(self, tmp_path):
        runner = GLayoutRunner(
            glayout_venv=str(tmp_path / "nonexistent_venv"),
        )
        result = runner.generate_component(
            component="nmos",
            params={"width": 1.0, "length": 0.28},
            output_dir=tmp_path / "out",
        )
        assert not result.success
        assert "not found" in result.error

    def test_validate_setup_missing_venv(self, tmp_path):
        runner = GLayoutRunner(
            glayout_venv=str(tmp_path / "nonexistent"),
        )
        problems = runner.validate_setup()
        assert len(problems) > 0
        assert "venv" in problems[0].lower()

    def test_missing_driver_script(self, tmp_path):
        # Create a fake venv with python
        venv = tmp_path / "fake_venv"
        venv.mkdir()
        (venv / "bin").mkdir()
        fake_python = venv / "bin" / "python"
        fake_python.write_text("#!/bin/bash\nexit 1\n")
        fake_python.chmod(0o755)

        runner = GLayoutRunner(
            glayout_venv=str(venv),
            driver_script=str(tmp_path / "nonexistent_driver.py"),
        )
        result = runner.generate_component(
            component="nmos",
            params={"width": 1.0},
            output_dir=tmp_path / "out",
        )
        assert not result.success
        assert "driver" in result.error.lower() or "not found" in result.error.lower()


# ---------------------------------------------------------------------------
# Integration tests (require .venv-glayout with gLayout installed)
# ---------------------------------------------------------------------------


@pytest.mark.glayout
class TestGLayoutIntegration:
    """Integration tests that generate real layouts.

    These require:
    - .venv-glayout with gLayout + gdstk + numpy installed
    - scripts/glayout_driver.py present
    """

    @pytest.fixture(autouse=True)
    def check_prereqs(self):
        venv = Path(".venv-glayout")
        if not venv.is_dir():
            pytest.skip(".venv-glayout not found")
        if not (venv / "bin" / "python").is_file():
            pytest.skip("Python not found in .venv-glayout")

    def test_generate_nmos(self, tmp_path):
        runner = GLayoutRunner()
        result = runner.generate_component(
            component="nmos",
            params={
                "width": 1.0,
                "length": 0.28,
                "fingers": 2,
            },
            output_dir=tmp_path,
        )
        assert result.success, f"gLayout failed: {result.error}"
        assert result.gds_path
        assert Path(result.gds_path).is_file()

    def test_validate_setup(self):
        runner = GLayoutRunner()
        problems = runner.validate_setup()
        assert problems == [], f"Setup problems: {problems}"

    def test_generate_ota(self, tmp_path):
        from eda_agents.topologies.ota_gf180 import GF180OTATopology

        topo = GF180OTATopology()
        sizing = topo.params_to_sizing(topo.default_params())
        runner = GLayoutRunner()
        result = runner.generate_ota(sizing, tmp_path)
        assert result.success, f"OTA layout failed: {result.error}"
        assert result.gds_path
        assert Path(result.gds_path).is_file()
        assert result.netlist_path
        assert Path(result.netlist_path).is_file()
