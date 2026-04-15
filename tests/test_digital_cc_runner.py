"""Tests for DigitalClaudeCodeRunner (Phase 5).

Tests prompt generation, dry_run, result parsing, and mocked CLI
execution.  No real Claude CLI invocation (see test_claude_code_harness.py
for the integration test).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from eda_agents.agents.digital_cc_runner import DigitalClaudeCodeRunner
from eda_agents.core.digital_design import DigitalDesign, TestbenchSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_design() -> DigitalDesign:
    """Build a mock DigitalDesign for testing."""

    class _TestDesign(DigitalDesign):
        def project_name(self):
            return "test-cc-design"

        def specification(self):
            return "Test design for CC runner testing."

        def design_space(self):
            return {
                "PL_TARGET_DENSITY_PCT": [45, 55, 65, 75, 85],
                "CLOCK_PERIOD": [35, 40, 45, 50],
            }

        def flow_config_overrides(self):
            return {"PL_TARGET_DENSITY_PCT": 65}

        def project_dir(self):
            return Path("/tmp/test-cc-design")

        def librelane_config(self):
            return Path("/tmp/test-cc-design/librelane/config.yaml")

        def compute_fom(self, metrics):
            return metrics.weighted_fom()

        def check_validity(self, metrics):
            return metrics.validity_check()

        def prompt_description(self):
            return "A test digital design for CC runner testing."

        def design_vars_description(self):
            return (
                "PL_TARGET_DENSITY_PCT: [45, 55, 65, 75, 85] (%)\n"
                "CLOCK_PERIOD: [35, 40, 45, 50] (ns)"
            )

        def specs_description(self):
            return "WNS >= 0 ns, DRC clean, LVS match"

        def fom_description(self):
            return "weighted_fom(timing=1.0, area=0.5, power=0.3)"

        def reference_description(self):
            return "PL_TARGET_DENSITY_PCT=65, CLOCK_PERIOD=40 -> WNS=19.5 ns"

        def testbench(self):
            return TestbenchSpec(driver="cocotb", target="make sim")

        def rtl_sources(self):
            return [Path("/tmp/test-cc-design/src/top.v")]

        def pdk_config(self):
            from eda_agents.core.pdk import GF180MCU_D
            return GF180MCU_D

    return _TestDesign()


# ---------------------------------------------------------------------------
# Prompt generation tests
# ---------------------------------------------------------------------------


class TestPromptGeneration:
    def test_prompt_contains_design_name(self):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(design, work_dir=Path("/tmp"))
        prompt = runner._build_prompt()
        assert "test-cc-design" in prompt

    def test_prompt_contains_workflow(self):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(design, work_dir=Path("/tmp"))
        prompt = runner._build_prompt()
        assert "Phase 1" in prompt
        assert "Phase 2" in prompt
        assert "Phase 3" in prompt
        assert "Phase 4" in prompt
        assert "DONE" in prompt

    def test_prompt_contains_pdk_rule(self):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(design, work_dir=Path("/tmp"))
        prompt = runner._build_prompt()
        assert "PDK_ROOT" in prompt
        assert "PDK=gf180mcuD" in prompt
        assert "CRITICAL ENVIRONMENT RULE" in prompt

    def test_prompt_contains_specs(self):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(design, work_dir=Path("/tmp"))
        prompt = runner._build_prompt()
        assert "WNS >= 0" in prompt
        assert "DRC clean" in prompt

    def test_prompt_contains_tunable_knobs(self):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(design, work_dir=Path("/tmp"))
        prompt = runner._build_prompt()
        assert "PL_TARGET_DENSITY_PCT" in prompt
        assert "CLOCK_PERIOD" in prompt

    def test_prompt_contains_librelane_config(self):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(design, work_dir=Path("/tmp"))
        prompt = runner._build_prompt()
        assert "config.yaml" in prompt

    def test_prompt_contains_testbench_info(self):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(design, work_dir=Path("/tmp"))
        prompt = runner._build_prompt()
        assert "cocotb" in prompt


# ---------------------------------------------------------------------------
# Dry run tests
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_returns_prompt(self):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(design, work_dir=Path("/tmp/dry"))
        result = runner.dry_run()
        assert result["design"] == "test-cc-design"
        assert "prompt" in result
        assert len(result["prompt"]) > 100
        assert result["prompt_length"] == len(result["prompt"])

    def test_dry_run_returns_config(self):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(
            design, work_dir=Path("/tmp/dry"),
            timeout_s=7200, model="opus",
        )
        result = runner.dry_run()
        assert result["timeout_s"] == 7200
        assert result["model"] == "opus"
        assert result["work_dir"] == "/tmp/dry"

    def test_dry_run_via_run_method(self):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(design, work_dir=Path("/tmp/dry"))
        result = asyncio.run(runner.run(dry_run=True))
        assert "prompt" in result
        assert result["design"] == "test-cc-design"


# ---------------------------------------------------------------------------
# Result parsing tests
# ---------------------------------------------------------------------------


class TestResultParsing:
    def test_parse_signoff_clean(self):
        text = """
        Flow completed successfully.
        Cell count: 12,201
        WNS: +19.5 ns (nom_tt corner)
        DRC: 0 violations (clean)
        LVS: match
        Overall: SIGNOFF CLEAN
        DONE
        """
        parsed = DigitalClaudeCodeRunner._parse_result(text)
        assert parsed["done"] is True
        assert parsed["verdict"] == "SIGNOFF_CLEAN"
        assert parsed["wns_ns"] == 19.5
        assert parsed["cell_count"] == 12201
        assert parsed["drc_count"] == 0
        assert parsed["lvs_match"] is True

    def test_parse_blocked(self):
        text = """
        Flow completed with issues.
        DRC: 5 violations found
        LVS: mismatch
        BLOCKED: DRC violations and LVS mismatch
        """
        parsed = DigitalClaudeCodeRunner._parse_result(text)
        assert parsed["verdict"] == "BLOCKED"
        assert parsed["drc_count"] == 5
        assert parsed["lvs_match"] is False

    def test_parse_negative_wns(self):
        text = "Timing: WNS: -2.3 ns at max_ss corner"
        parsed = DigitalClaudeCodeRunner._parse_result(text)
        assert parsed["wns_ns"] == -2.3

    def test_parse_empty(self):
        parsed = DigitalClaudeCodeRunner._parse_result("")
        assert parsed == {}

    def test_parse_tapeout_ready(self):
        text = "All checks passed. TAPEOUT READY. DONE"
        parsed = DigitalClaudeCodeRunner._parse_result(text)
        assert parsed["verdict"] == "SIGNOFF_CLEAN"
        assert parsed["done"] is True

    def test_parse_drc_clean_text(self):
        text = "DRC: clean (zero violations)"
        parsed = DigitalClaudeCodeRunner._parse_result(text)
        assert parsed["drc_count"] == 0


# ---------------------------------------------------------------------------
# Mocked run tests
# ---------------------------------------------------------------------------


class TestMockedRun:
    def test_run_delegates_to_harness(self, tmp_path):
        from unittest.mock import AsyncMock

        from eda_agents.agents.claude_code_harness import HarnessResult

        mock_result = HarnessResult(
            success=True,
            result_text="Cell count: 12000\nWNS: +5.0 ns\nDRC: 0 violations\nLVS: match\nSIGNOFF CLEAN\nDONE",
            duration_ms=5000,
            num_turns=5,
            total_cost_usd=1.5,
            cli_version="2.1.104",
        )

        with patch(
            "eda_agents.agents.claude_code_harness.ClaudeCodeHarness.run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ), patch(
            "eda_agents.agents.claude_code_harness.ClaudeCodeHarness.get_cli_version",
            new_callable=AsyncMock,
            return_value="2.1.104",
        ):
            design = _make_design()
            runner = DigitalClaudeCodeRunner(design, work_dir=tmp_path)
            result = asyncio.run(runner.run())

        assert result["success"] is True
        assert result["design"] == "test-cc-design"
        assert result["cli_version"] == "2.1.104"
        assert result["cost_usd"] == 1.5
        assert result["verdict"] == "SIGNOFF_CLEAN"


# ---------------------------------------------------------------------------
# Helper script tests
# ---------------------------------------------------------------------------


class TestHelperScript:
    def test_write_helper_script(self, tmp_path):
        design = _make_design()
        runner = DigitalClaudeCodeRunner(design, work_dir=tmp_path)
        script_path = runner._write_helper_script()
        assert Path(script_path).exists()
        content = Path(script_path).read_text()
        assert "query_flow.py" in script_path
        assert "def cmd_status" in content
        assert "def cmd_metrics" in content
        assert "def cmd_timing" in content
        assert "def cmd_modify" in content
