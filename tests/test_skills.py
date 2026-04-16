"""Tests for the Skill registry and migration of existing prompts."""

from __future__ import annotations

import pytest

from eda_agents.agents import adk_prompts, digital_adk_prompts
from eda_agents.skills import Skill, get_skill, list_skills, register_skill


class _FakeTopology:
    """Minimal CircuitTopology stub for prompt-rendering tests.

    Returns deterministic strings; does not inherit the ABC so it
    sidesteps abstract-method enforcement.
    """

    def topology_name(self) -> str:
        return "fake_topo"

    def prompt_description(self) -> str:
        return "A fake topology for tests"

    def design_vars_description(self) -> str:
        return "- x: [0, 1]"

    def specs_description(self) -> str:
        return "x >= 0.5"

    def fom_description(self) -> str:
        return "FoM = x"

    def reference_description(self) -> str:
        return "x = 0.7"

    def auxiliary_tools_description(self) -> str:
        return ""

    def tool_spec(self) -> dict:
        return {"type": "function", "function": {"name": "fake"}}


class _FakeDesign:
    """Minimal DigitalDesign stub for prompt-rendering tests."""

    def project_name(self) -> str:
        return "fake_design"

    def prompt_description(self) -> str:
        return "A fake digital design"

    def specs_description(self) -> str:
        return "timing met"

    def design_vars_description(self) -> str:
        return "- density: 0.5"

    def fom_description(self) -> str:
        return "FoM = 1/cells"

    def testbench(self):
        return None


# --------------------------------------------------------------------- #
# Registry core
# --------------------------------------------------------------------- #


class TestRegistryCore:
    def test_expected_skills_registered(self):
        names = {s.name for s in list_skills()}
        expected = {
            "analog.explorer",
            "analog.corner_validator",
            "analog.orchestrator",
            "analog.adc_metrics",
            "analog.gmid_sizing",
            "analog.behavioral_primitives",
            "analog.roles.librarian",
            "analog.roles.architect",
            "analog.roles.designer",
            "analog.roles.verifier",
            "analog.sar_adc_design",
            "digital.project_manager",
            "digital.verification",
            "digital.synthesis",
            "digital.physical",
            "digital.signoff",
            "flow.runner",
            "flow.drc_checker",
            "flow.drc_fixer",
            "flow.lvs_checker",
            "tools.simulate_miller_ota",
            "tools.gmid_lookup",
            "tools.evaluate_miller_ota",
        }
        missing = expected - names
        assert not missing, f"Missing skills: {missing}"

    def test_prefix_filter(self):
        digital_skills = list_skills(prefix="digital.")
        assert len(digital_skills) >= 5
        assert all(s.name.startswith("digital.") for s in digital_skills)

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError, match="not found"):
            get_skill("does.not.exist")

    def test_duplicate_register_raises(self):
        skill = Skill(name="analog.explorer", description="dup")
        with pytest.raises(ValueError, match="already registered"):
            register_skill(skill)

    def test_duplicate_overwrite_ok(self):
        original = get_skill("analog.explorer")
        try:
            replacement = Skill(name="analog.explorer", description="temp")
            register_skill(replacement, overwrite=True)
            assert get_skill("analog.explorer") is replacement
        finally:
            register_skill(original, overwrite=True)


# --------------------------------------------------------------------- #
# Skill base contracts
# --------------------------------------------------------------------- #


class TestSkillBase:
    def test_render_without_prompt_fn_raises(self):
        skill = Skill(name="only.spec", description="test", tool_spec={"k": 1})
        with pytest.raises(RuntimeError, match="no prompt_fn"):
            skill.render()

    def test_spec_without_tool_spec_raises(self):
        skill = Skill(name="only.prompt", description="test", prompt_fn=lambda: "x")
        with pytest.raises(RuntimeError, match="no tool_spec"):
            skill.spec()

    def test_validate_without_validator_raises(self):
        skill = Skill(name="bare", description="test")
        with pytest.raises(RuntimeError, match="no validator"):
            skill.validate()


# --------------------------------------------------------------------- #
# Compatibility shim: old functions must route through the registry
# --------------------------------------------------------------------- #


class TestAnalogShimParity:
    def test_explorer_prompt(self):
        topo = _FakeTopology()
        expected = get_skill("analog.explorer").render(topo, 30)
        assert adk_prompts.explorer_prompt(topo, 30) == expected
        assert "fake_topo" in expected

    def test_corner_validator_prompt(self):
        topo = _FakeTopology()
        expected = get_skill("analog.corner_validator").render(topo)
        assert adk_prompts.corner_validator_prompt(topo) == expected

    def test_orchestrator_prompt_no_topology(self):
        expected = get_skill("analog.orchestrator").render(None, None, 3)
        assert adk_prompts.orchestrator_prompt() == expected

    def test_orchestrator_prompt_with_topology(self):
        topo = _FakeTopology()
        expected = get_skill("analog.orchestrator").render(topo, None, 5)
        assert (
            adk_prompts.orchestrator_prompt(
                topology=topo, max_drc_iterations=5
            )
            == expected
        )


class TestFlowShimParity:
    def test_flow_runner_prompt(self):
        assert adk_prompts.flow_runner_prompt("/tmp/x") == get_skill(
            "flow.runner"
        ).render("/tmp/x")

    def test_drc_checker_prompt(self):
        assert adk_prompts.drc_checker_prompt() == get_skill(
            "flow.drc_checker"
        ).render()

    def test_drc_fixer_prompt_default(self):
        assert adk_prompts.drc_fixer_prompt() == get_skill(
            "flow.drc_fixer"
        ).render(3)

    def test_drc_fixer_prompt_custom(self):
        assert adk_prompts.drc_fixer_prompt(7) == get_skill(
            "flow.drc_fixer"
        ).render(7)

    def test_lvs_checker_prompt(self):
        assert adk_prompts.lvs_checker_prompt() == get_skill(
            "flow.lvs_checker"
        ).render()


class TestDigitalShimParity:
    def test_project_manager_prompt(self):
        d = _FakeDesign()
        assert digital_adk_prompts.project_manager_prompt(d) == get_skill(
            "digital.project_manager"
        ).render(d)

    def test_verification_engineer_prompt(self):
        d = _FakeDesign()
        assert digital_adk_prompts.verification_engineer_prompt(d) == get_skill(
            "digital.verification"
        ).render(d)

    def test_synthesis_engineer_prompt(self):
        d = _FakeDesign()
        assert digital_adk_prompts.synthesis_engineer_prompt(d) == get_skill(
            "digital.synthesis"
        ).render(d)

    def test_physical_designer_prompt(self):
        d = _FakeDesign()
        assert digital_adk_prompts.physical_designer_prompt(d) == get_skill(
            "digital.physical"
        ).render(d)

    def test_signoff_checker_prompt(self):
        d = _FakeDesign()
        assert digital_adk_prompts.signoff_checker_prompt(d) == get_skill(
            "digital.signoff"
        ).render(d)


# --------------------------------------------------------------------- #
# Tool spec skills
# --------------------------------------------------------------------- #


class TestToolSpecSkills:
    def test_gmid_lookup_matches_constant(self):
        from eda_agents.agents.tool_defs import GMID_LOOKUP_TOOL_SPEC

        assert get_skill("tools.gmid_lookup").spec() is GMID_LOOKUP_TOOL_SPEC

    def test_simulate_matches_constant(self):
        from eda_agents.agents.tool_defs import SIMULATE_TOOL_SPEC

        assert get_skill("tools.simulate_miller_ota").spec() is SIMULATE_TOOL_SPEC

    def test_evaluate_matches_constant(self):
        from eda_agents.agents.tool_defs import EVALUATE_TOOL_SPEC

        assert get_skill("tools.evaluate_miller_ota").spec() is EVALUATE_TOOL_SPEC
