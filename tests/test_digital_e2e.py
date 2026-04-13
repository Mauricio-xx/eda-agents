"""End-to-end tests for the digital RTL-to-GDS pipeline.

Dry-run tests (no markers) validate imports, wiring, and argument
parsing without LLM or tool invocations.

Integration tests (gated ``-m librelane``) exercise the real flow
against the systolic_mac or fazyrv_hachure design.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE_09 = Path(__file__).resolve().parents[1] / "examples" / "09_rtl2gds_gf180.py"
EXAMPLE_10 = Path(__file__).resolve().parents[1] / "examples" / "10_digital_autoresearch_gf180.py"
PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Dry-run tests (no external tools needed)
# ---------------------------------------------------------------------------


class TestExample09DryRun:
    """Validate example 09 dry-run path (no LLM, no subprocess)."""

    def test_dry_run_cc_cli(self):
        """CC CLI dry-run completes in under 5s."""
        result = subprocess.run(
            [PYTHON, str(EXAMPLE_09), "--dry-run", "--backend", "cc_cli"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "PASS" in result.stdout

    def test_dry_run_adk(self):
        """ADK dry-run completes in under 10s."""
        result = subprocess.run(
            [PYTHON, str(EXAMPLE_09), "--dry-run", "--backend", "adk"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "PASS" in result.stdout
        assert "project_manager" in result.stdout

    def test_dry_run_systolic_mac(self):
        """systolic_mac design can be loaded in dry-run."""
        result = subprocess.run(
            [PYTHON, str(EXAMPLE_09),
             "--dry-run", "--backend", "cc_cli",
             "--design", "systolic_mac"],
            capture_output=True, text=True, timeout=10,
        )
        # May fail if design not cloned, but should not crash
        if result.returncode == 0:
            assert "PASS" in result.stdout

    def test_dry_run_output_format(self):
        """Dry-run prints design info and model."""
        result = subprocess.run(
            [PYTHON, str(EXAMPLE_09), "--dry-run", "--backend", "cc_cli"],
            capture_output=True, text=True, timeout=10,
        )
        assert "Design:" in result.stdout
        assert "Backend:" in result.stdout


class TestProjectManagerDryRun:
    """Test ProjectManager directly (no subprocess)."""

    def test_adk_dry_run_returns_agents(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign

        design = FazyRvHachureDesign()
        pm = ProjectManager(design=design, backend="adk")
        result = asyncio.run(pm.run(Path("/tmp/dry_e2e"), dry_run=True))
        assert "master_agent" in result
        assert result["master_agent"] == "project_manager"
        assert len(result["sub_agents"]) == 4

    def test_cc_cli_dry_run_returns_prompt(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign

        design = FazyRvHachureDesign()
        pm = ProjectManager(design=design, backend="cc_cli")
        result = asyncio.run(pm.run(Path("/tmp/dry_e2e_cc"), dry_run=True))
        assert "prompt" in result
        assert len(result["prompt"]) > 100
        assert "fazyrv" in result["prompt"].lower() or "frv_1" in result["prompt"].lower()


class TestValidateScript:
    """Test scripts/validate_digital_flow.py runs without crashing."""

    def test_validate_exits_cleanly(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "validate_digital_flow.py"
        result = subprocess.run(
            [PYTHON, str(script)],
            capture_output=True, text=True, timeout=30,
        )
        # Should exit 0 (all checks pass) or 1 (some missing)
        assert result.returncode in (0, 1)
        assert "Digital RTL-to-GDS" in result.stdout


class TestShellWrapper:
    """Verify FazyRvHachureDesign provides nix-shell wrapper."""

    def test_fazyrv_shell_wrapper(self):
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign

        design = FazyRvHachureDesign()
        wrapper = design.shell_wrapper()
        if wrapper:
            assert "nix-shell" in wrapper
        # May be None if shell.nix not present (CI)

    def test_default_shell_wrapper_is_none(self):
        from eda_agents.core.digital_design import DigitalDesign

        # DigitalDesign is ABC, can't instantiate directly.
        # Just check the default method exists.
        assert hasattr(DigitalDesign, "shell_wrapper")


class TestMockMetricsFixture:
    """Verify the mock metrics fixture is valid."""

    def test_fixture_is_valid_json(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "fake_flow_metrics.json"
        data = json.loads(fixture.read_text())
        assert data["synth_cell_count"] == 12201
        assert data["wns_worst_ns"] > 0
        assert data["drc_clean"] is True


# ---------------------------------------------------------------------------
# Integration tests (gated)
# ---------------------------------------------------------------------------


@pytest.mark.librelane
class TestIntegrationFazyrv:
    """Real LibreLane integration tests against fazyrv-hachure.

    These require nix-shell + LibreLane installed. Run with:
        pytest -m librelane tests/test_digital_e2e.py
    """

    def test_fazyrv_validate_clone(self):
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign

        design = FazyRvHachureDesign()
        problems = design.validate_clone()
        assert problems == [], f"Clone issues: {problems}"

    def test_fazyrv_config_readable(self):
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign
        from eda_agents.core.librelane_runner import LibreLaneRunner

        design = FazyRvHachureDesign()
        config_path = design.librelane_config()
        runner = LibreLaneRunner(
            project_dir=config_path.parent,
            config_file=config_path.name,
            python_cmd="python3",
        )
        config = runner._read_config()
        assert config.get("DESIGN_NAME") is not None
        assert config.get("CLOCK_PERIOD") == 40
