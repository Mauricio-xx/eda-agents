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


@pytest.mark.glayout
class TestGLayoutPdkDispatch:
    """S11 Fase 4: PDK dispatch through generalised glayout_driver.

    The driver now routes spec['pdk'] to either gf180_mapped_pdk or
    sg13g2_mapped_pdk, and accepts diff_pair / current_mirror / FVF as
    composite components. These tests prove the plumbing runs real
    gLayout on both PDKs — they skip gracefully when ``.venv-glayout``
    isn't set up.
    """

    @pytest.fixture(autouse=True)
    def check_prereqs(self):
        # Try the worktree-local venv first, then fall back to the main
        # repo's venv (which is where .venv-glayout actually lives).
        local = Path(".venv-glayout")
        main = Path("/home/montanares/personal_exp/eda-agents/.venv-glayout")
        if not local.is_dir() and not main.is_dir():
            pytest.skip("no .venv-glayout found (tried local + main repo)")

    def _runner(self, pdk: str) -> GLayoutRunner:
        local = Path(".venv-glayout")
        venv = str(local) if local.is_dir() else (
            "/home/montanares/personal_exp/eda-agents/.venv-glayout"
        )
        return GLayoutRunner(glayout_venv=venv, pdk=pdk)

    def test_nmos_gf180(self, tmp_path):
        result = self._runner("gf180mcu").generate_component(
            component="nmos",
            params={"width": 1.0, "length": 0.28, "fingers": 2},
            output_dir=tmp_path,
        )
        assert result.success, f"gf180 nmos failed: {result.error}"
        assert Path(result.gds_path).is_file()

    def test_nmos_sg13g2(self, tmp_path):
        result = self._runner("ihp_sg13g2").generate_component(
            component="nmos",
            params={"width": 1.0, "length": 0.13, "fingers": 2},
            output_dir=tmp_path,
        )
        assert result.success, f"sg13g2 nmos failed: {result.error}"
        assert Path(result.gds_path).is_file()

    def test_diff_pair_sg13g2(self, tmp_path):
        result = self._runner("ihp_sg13g2").generate_component(
            component="diff_pair",
            params={"width": 5.0, "length": 1.0, "fingers": 4},
            output_dir=tmp_path,
        )
        assert result.success, f"sg13g2 diff_pair failed: {result.error}"
        assert Path(result.gds_path).is_file()

    def test_opamp_rejects_sg13g2(self, tmp_path):
        # opamp_twostage is gf180-only until the SG13G2 upstream port
        # lands. The driver must surface a clear error rather than
        # crash mid-composite-build.
        result = self._runner("ihp_sg13g2").generate_component(
            component="opamp_twostage",
            params={},
            output_dir=tmp_path,
        )
        assert not result.success
        assert "gf180mcu-only" in (result.error or "")

    def test_unknown_pdk_errors_cleanly(self, tmp_path):
        runner = GLayoutRunner(
            glayout_venv=(
                "/home/montanares/personal_exp/eda-agents/.venv-glayout"
            ),
            pdk="skywater-alternate",
        )
        result = runner.generate_component(
            component="nmos",
            params={"width": 1.0},
            output_dir=tmp_path,
        )
        assert not result.success
        assert "not importable" in (result.error or "") or "PDK" in (result.error or "")


try:
    import fastmcp  # noqa: F401

    _HAS_FASTMCP = True
except ImportError:  # pragma: no cover
    _HAS_FASTMCP = False


@pytest.mark.glayout
@pytest.mark.mcp
@pytest.mark.skipif(not _HAS_FASTMCP, reason="fastmcp not installed")
class TestGenerateAnalogLayoutMCP:
    """S11 Fase 4: MCP tool `generate_analog_layout` end-to-end."""

    @pytest.fixture(autouse=True)
    def check_prereqs(self):
        local = Path(".venv-glayout")
        main = Path("/home/montanares/personal_exp/eda-agents/.venv-glayout")
        if not local.is_dir() and not main.is_dir():
            pytest.skip("no .venv-glayout found (tried local + main repo)")

    def _venv(self) -> str:
        local = Path(".venv-glayout")
        return (
            str(local)
            if local.is_dir()
            else "/home/montanares/personal_exp/eda-agents/.venv-glayout"
        )

    async def test_unknown_pdk_reports_error(self, tmp_path):
        from eda_agents.mcp.server import mcp

        result = await mcp.call_tool(
            "generate_analog_layout",
            {
                "pdk": "sky130a",
                "component": "nmos",
                "params": {"width": 1.0},
                "output_dir": str(tmp_path),
                "glayout_venv": self._venv(),
            },
        )
        data = result.structured_content
        assert data["success"] is False
        assert "not importable" in data["error"] or "PDK" in data["error"]

    async def test_sg13g2_nmos_via_mcp(self, tmp_path):
        from eda_agents.mcp.server import mcp

        result = await mcp.call_tool(
            "generate_analog_layout",
            {
                "pdk": "ihp_sg13g2",
                "component": "nmos",
                "params": {"width": 1.0, "length": 0.13, "fingers": 2},
                "output_dir": str(tmp_path),
                "glayout_venv": self._venv(),
            },
        )
        data = result.structured_content
        assert data["success"] is True, data["error"]
        assert Path(data["gds_path"]).is_file()
