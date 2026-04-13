"""Tests for digital ADK agents (Phase 4).

Tests structural correctness of ProjectManager, sub-agent factories,
prompt builders, and tool factories.  All tests run without LLM
invocation or subprocess execution.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from eda_agents.agents.digital_adk_prompts import (
    physical_designer_prompt,
    project_manager_prompt,
    signoff_checker_prompt,
    synthesis_engineer_prompt,
    verification_engineer_prompt,
)
from eda_agents.core.digital_design import DigitalDesign, TestbenchSpec
from eda_agents.core.tool_environment import ToolEnvironment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_design() -> DigitalDesign:
    """Build a mock DigitalDesign for testing."""

    class _TestDesign(DigitalDesign):
        def project_name(self):
            return "test-design"

        def specification(self):
            return "Test design for unit testing."

        def design_space(self):
            return {
                "PL_TARGET_DENSITY_PCT": [45, 55, 65, 75, 85],
                "CLOCK_PERIOD": [35, 40, 45, 50],
            }

        def flow_config_overrides(self):
            return {"PL_TARGET_DENSITY_PCT": 65}

        def project_dir(self):
            return Path("/tmp/test-design")

        def librelane_config(self):
            return Path("/tmp/test-design/librelane/config.yaml")

        def compute_fom(self, metrics):
            return metrics.weighted_fom()

        def check_validity(self, metrics):
            return metrics.validity_check()

        def prompt_description(self):
            return "A test digital design for unit testing."

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
            return [Path("/tmp/test-design/src/top.v")]

    return _TestDesign()


def _make_env() -> ToolEnvironment:
    """Build a mock ToolEnvironment."""
    env = MagicMock(spec=ToolEnvironment)
    env.which.return_value = "/usr/bin/verilator"
    return env


# ---------------------------------------------------------------------------
# Prompt builder tests
# ---------------------------------------------------------------------------


class TestPromptBuilders:
    """Verify prompt builders produce non-empty strings with design metadata."""

    def setup_method(self):
        self.design = _make_design()

    def test_project_manager_prompt_has_design_name(self):
        prompt = project_manager_prompt(self.design)
        assert "test-design" in prompt

    def test_project_manager_prompt_has_phases(self):
        prompt = project_manager_prompt(self.design)
        assert "Phase 1" in prompt
        assert "Phase 4" in prompt
        assert "SIGNOFF" in prompt

    def test_verification_engineer_prompt_has_testbench(self):
        prompt = verification_engineer_prompt(self.design)
        assert "cocotb" in prompt
        assert "make sim" in prompt

    def test_verification_engineer_prompt_no_testbench(self):
        design = _make_design()
        design.testbench = lambda: None
        prompt = verification_engineer_prompt(design)
        assert "No testbench configured" in prompt

    def test_synthesis_engineer_prompt_has_specs(self):
        prompt = synthesis_engineer_prompt(self.design)
        assert "WNS >= 0" in prompt
        assert "CLOCK_PERIOD" in prompt

    def test_physical_designer_prompt_has_tuning_strategies(self):
        prompt = physical_designer_prompt(self.design)
        assert "PL_TARGET_DENSITY_PCT" in prompt
        assert "PDN_VPITCH" in prompt
        assert "CONGESTION" in prompt

    def test_signoff_checker_prompt_has_drc_categories(self):
        prompt = signoff_checker_prompt(self.design)
        assert "SHORT" in prompt
        assert "ANTENNA" in prompt
        assert "TAPEOUT READY" in prompt

    def test_all_prompts_nonempty(self):
        for fn in (project_manager_prompt, verification_engineer_prompt,
                   synthesis_engineer_prompt, physical_designer_prompt,
                   signoff_checker_prompt):
            prompt = fn(self.design)
            assert len(prompt) > 100, f"{fn.__name__} prompt too short"


# ---------------------------------------------------------------------------
# Tool factory tests
# ---------------------------------------------------------------------------


class TestToolFactories:
    """Verify tool factories produce callable FunctionTool objects."""

    def test_rtl_lint_tool_creates(self):
        from eda_agents.agents.digital_adk_agents import _make_rtl_lint_tool

        tool = _make_rtl_lint_tool(_make_design(), _make_env())
        assert tool is not None
        # FunctionTool wraps a callable
        assert hasattr(tool, "_func") or hasattr(tool, "func")

    def test_rtl_sim_tool_creates(self):
        from eda_agents.agents.digital_adk_agents import _make_rtl_sim_tool

        tool = _make_rtl_sim_tool(_make_design(), _make_env())
        assert tool is not None

    def test_physical_slice_tool_creates(self):
        from eda_agents.agents.digital_adk_agents import (
            _make_physical_slice_tool,
        )

        runner = MagicMock()
        tool = _make_physical_slice_tool(runner)
        assert tool is not None

    def test_precheck_tool_creates(self):
        from eda_agents.agents.digital_adk_agents import _make_precheck_tool

        tool = _make_precheck_tool(
            _make_design(), _make_env(), Path("/tmp/precheck")
        )
        assert tool is not None


# ---------------------------------------------------------------------------
# Sub-agent factory tests
# ---------------------------------------------------------------------------


class TestSubAgentFactories:
    """Verify sub-agent factories build LlmAgent instances."""

    def test_verification_engineer(self):
        from eda_agents.agents.digital_adk_agents import (
            _make_verification_engineer,
        )

        agent = _make_verification_engineer(
            _make_design(), _make_env(), "fake-model"
        )
        assert agent.name == "verification_engineer"
        assert len(agent.tools) == 2  # lint + sim

    def test_synthesis_engineer(self):
        from eda_agents.agents.digital_adk_agents import (
            _make_synthesis_engineer,
        )

        runner = MagicMock()
        agent = _make_synthesis_engineer(_make_design(), runner, "fake-model")
        assert agent.name == "synthesis_engineer"
        assert len(agent.tools) == 4  # flow + timing + status + modify

    def test_physical_designer(self):
        from eda_agents.agents.digital_adk_agents import (
            _make_physical_designer,
        )

        runner = MagicMock()
        agent = _make_physical_designer(_make_design(), runner, "fake-model")
        assert agent.name == "physical_designer"
        assert len(agent.tools) == 5  # flow + slice + modify + timing + status

    def test_signoff_checker(self):
        from eda_agents.agents.digital_adk_agents import (
            _make_signoff_checker,
        )

        runner = MagicMock()
        agent = _make_signoff_checker(
            runner, _make_design(), _make_env(),
            Path("/tmp/precheck"), "fake-model",
        )
        assert agent.name == "signoff_checker"
        assert len(agent.tools) == 7  # drc + summary + lvs + modify + rerun + status + precheck


# ---------------------------------------------------------------------------
# ProjectManager tests
# ---------------------------------------------------------------------------


class TestProjectManager:
    """Verify ProjectManager construction and dry_run()."""

    def test_construction_defaults(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager

        design = _make_design()
        pm = ProjectManager(design=design)
        assert pm.design is design
        assert pm.worker_model == pm.model
        assert pm.precheck_dir.name == "gf180mcu-precheck"

    def test_construction_custom_params(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager

        design = _make_design()
        pm = ProjectManager(
            design=design,
            model="openrouter/anthropic/claude-haiku-4.5",
            worker_model="openrouter/google/gemini-2.0-flash-001",
            precheck_dir=Path("/custom/precheck"),
            env=_make_env(),
        )
        assert pm.model == "openrouter/anthropic/claude-haiku-4.5"
        assert pm.worker_model == "openrouter/google/gemini-2.0-flash-001"
        assert pm.precheck_dir == Path("/custom/precheck")
        assert pm.env is not None

    def test_dry_run_returns_agent_graph(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager

        design = _make_design()
        pm = ProjectManager(design=design, env=_make_env())
        result = pm.dry_run()

        assert result["design"] == "test-design"
        assert result["master_agent"] == "project_manager"
        assert len(result["sub_agents"]) == 4
        assert result["sub_agent_names"] == [
            "verification_engineer",
            "synthesis_engineer",
            "physical_designer",
            "signoff_checker",
        ]

    def test_dry_run_tool_counts(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager

        design = _make_design()
        pm = ProjectManager(design=design, env=_make_env())
        result = pm.dry_run()

        tool_counts = {
            sa["name"]: sa["tool_count"] for sa in result["sub_agents"]
        }
        assert tool_counts["verification_engineer"] == 2
        assert tool_counts["synthesis_engineer"] == 4
        assert tool_counts["physical_designer"] == 5
        assert tool_counts["signoff_checker"] == 7

    def test_dry_run_via_run_method(self):
        """run(dry_run=True) delegates to dry_run()."""
        import asyncio

        from eda_agents.agents.digital_adk_agents import ProjectManager

        design = _make_design()
        pm = ProjectManager(design=design, env=_make_env())
        result = asyncio.run(
            pm.run(work_dir=Path("/tmp/dry"), dry_run=True)
        )
        # Should return the same structure as dry_run()
        assert "master_agent" in result
        assert result["design"] == "test-design"

    def test_build_initial_prompt_has_workflow(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager

        design = _make_design()
        pm = ProjectManager(design=design)
        prompt = pm._build_initial_prompt()
        assert "RTL verification" in prompt
        assert "synthesis" in prompt
        assert "signoff" in prompt

    def test_get_env_default(self):
        """Without explicit env, creates LocalToolEnvironment."""
        from eda_agents.agents.digital_adk_agents import ProjectManager

        design = _make_design()
        pm = ProjectManager(design=design)
        env = pm._get_env()
        from eda_agents.core.tool_environment import LocalToolEnvironment
        assert isinstance(env, LocalToolEnvironment)

    def test_get_env_explicit(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager

        design = _make_design()
        mock_env = _make_env()
        pm = ProjectManager(design=design, env=mock_env)
        assert pm._get_env() is mock_env

    def test_backend_default_is_adk(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager

        pm = ProjectManager(design=_make_design())
        assert pm.backend == "adk"

    def test_backend_cc_cli(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager

        pm = ProjectManager(design=_make_design(), backend="cc_cli")
        assert pm.backend == "cc_cli"

    def test_backend_invalid_raises(self):
        import pytest
        from eda_agents.agents.digital_adk_agents import ProjectManager

        with pytest.raises(ValueError, match="Unknown backend"):
            ProjectManager(design=_make_design(), backend="invalid")

    def test_cc_cli_dry_run(self):
        import asyncio
        from eda_agents.agents.digital_adk_agents import ProjectManager

        design = _make_design()
        pm = ProjectManager(design=design, backend="cc_cli")
        result = asyncio.run(
            pm.run(work_dir=Path("/tmp/cc_dry"), dry_run=True)
        )
        assert result["design"] == "test-design"
        assert "prompt" in result
        assert len(result["prompt"]) > 100

    def test_cc_cli_params_stored(self):
        from eda_agents.agents.digital_adk_agents import ProjectManager

        pm = ProjectManager(
            design=_make_design(),
            backend="cc_cli",
            allow_dangerous=True,
            cli_path="/usr/bin/claude",
            max_budget_usd=10.0,
        )
        assert pm.allow_dangerous is True
        assert pm.cli_path == "/usr/bin/claude"
        assert pm.max_budget_usd == 10.0
