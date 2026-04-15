"""Tests for digital prompt builders and script writers in tool_defs.py (Phase 5).

Verifies ``build_digital_rtl2gds_prompt`` and ``write_librelane_flow_script``
produce correct outputs from DigitalDesign metadata.
"""

from __future__ import annotations

from pathlib import Path

from eda_agents.agents.tool_defs import (
    build_digital_rtl2gds_prompt,
    write_librelane_flow_script,
)
from eda_agents.core.digital_design import DigitalDesign, TestbenchSpec


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _make_design() -> DigitalDesign:
    class _D(DigitalDesign):
        def project_name(self):
            return "prompt-test-design"

        def specification(self):
            return "Prompt test."

        def design_space(self):
            return {"PL_TARGET_DENSITY_PCT": [50, 70], "CLOCK_PERIOD": [40, 50]}

        def flow_config_overrides(self):
            return {}

        def project_dir(self):
            return Path("/work/designs/prompt-test")

        def librelane_config(self):
            return Path("/work/designs/prompt-test/config.yaml")

        def compute_fom(self, m):
            return 0.0

        def check_validity(self, m):
            return True, []

        def prompt_description(self):
            return "A test design for prompt generation."

        def design_vars_description(self):
            return "PL_TARGET_DENSITY_PCT, CLOCK_PERIOD"

        def specs_description(self):
            return "WNS >= 0, DRC clean"

        def fom_description(self):
            return "weighted_fom"

        def reference_description(self):
            return "density=70 -> WNS=5ns"

        def testbench(self):
            return TestbenchSpec(driver="cocotb", target="make sim")

        def rtl_sources(self):
            return [Path("/work/designs/prompt-test/src/top.v")]

        def pdk_config(self):
            from eda_agents.core.pdk import GF180MCU_D
            return GF180MCU_D

    return _D()


# ---------------------------------------------------------------------------
# build_digital_rtl2gds_prompt tests
# ---------------------------------------------------------------------------


class TestBuildDigitalRtl2gdsPrompt:
    def test_contains_design_name(self):
        prompt = build_digital_rtl2gds_prompt(_make_design())
        assert "prompt-test-design" in prompt

    def test_contains_f5_pdk_rule(self):
        prompt = build_digital_rtl2gds_prompt(_make_design())
        assert "CRITICAL ENVIRONMENT RULE" in prompt
        assert "PDK=gf180mcuD" in prompt

    def test_ihp_override_via_pdk_config_arg(self):
        """Explicit pdk_config arg overrides the design's own PDK."""
        prompt = build_digital_rtl2gds_prompt(
            _make_design(), pdk_config="ihp_sg13g2"
        )
        assert "PDK=ihp-sg13g2" in prompt
        assert "PDK=gf180mcuD" not in prompt

    def test_contains_workflow_phases(self):
        prompt = build_digital_rtl2gds_prompt(_make_design())
        assert "Phase 1" in prompt
        assert "Phase 2" in prompt
        assert "Phase 3" in prompt
        assert "Phase 4" in prompt

    def test_contains_done_sentinel(self):
        prompt = build_digital_rtl2gds_prompt(_make_design())
        assert '"DONE"' in prompt

    def test_contains_safe_config_keys(self):
        prompt = build_digital_rtl2gds_prompt(_make_design())
        assert "PL_TARGET_DENSITY_PCT" in prompt
        assert "GRT_OVERFLOW_ITERS" in prompt

    def test_contains_rtl_sources(self):
        prompt = build_digital_rtl2gds_prompt(_make_design())
        assert "top.v" in prompt

    def test_contains_testbench(self):
        prompt = build_digital_rtl2gds_prompt(_make_design())
        assert "cocotb" in prompt
        assert "make sim" in prompt

    def test_no_testbench(self):
        design = _make_design()
        design.testbench = lambda: None
        prompt = build_digital_rtl2gds_prompt(design)
        assert "No testbench" in prompt
        assert "Skip simulation" in prompt

    def test_contains_librelane_invocation(self):
        prompt = build_digital_rtl2gds_prompt(_make_design())
        assert "python3 -m librelane" in prompt
        assert "config.yaml" in prompt

    def test_prompt_is_substantial(self):
        prompt = build_digital_rtl2gds_prompt(_make_design())
        assert len(prompt) > 500


# ---------------------------------------------------------------------------
# write_librelane_flow_script tests
# ---------------------------------------------------------------------------


class TestWriteLibrelaneFlowScript:
    def test_creates_script(self, tmp_path):
        path = write_librelane_flow_script(str(tmp_path), _make_design())
        assert Path(path).exists()
        assert path.endswith("query_flow.py")

    def test_script_is_executable(self, tmp_path):
        import os
        import stat

        path = write_librelane_flow_script(str(tmp_path), _make_design())
        mode = os.stat(path).st_mode
        assert mode & stat.S_IXUSR

    def test_script_contains_commands(self, tmp_path):
        path = write_librelane_flow_script(str(tmp_path), _make_design())
        content = Path(path).read_text()
        assert "def cmd_status" in content
        assert "def cmd_metrics" in content
        assert "def cmd_timing" in content
        assert "def cmd_modify" in content
        assert "def cmd_list_runs" in content

    def test_script_contains_design_paths(self, tmp_path):
        path = write_librelane_flow_script(str(tmp_path), _make_design())
        content = Path(path).read_text()
        assert "/work/designs/prompt-test" in content
        assert "config.yaml" in content

    def test_script_contains_sys_path(self, tmp_path):
        path = write_librelane_flow_script(str(tmp_path), _make_design())
        content = Path(path).read_text()
        assert "sys.path.insert" in content
