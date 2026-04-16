"""S10c skill-injection tests for the analog CC CLI prompt builder.

Covers ``build_cc_spice_system_prompt`` in ``eda_agents.agents.tool_defs``,
which builds the system prompt consumed by ``ClaudeCodeHarness`` for
topology-aware analog SPICE exploration runs.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _fake_topology(skills: list | None = None):
    topo = MagicMock()
    topo.topology_name.return_value = "fake_topo"
    topo.prompt_description.return_value = "a fake topology for tests"
    topo.fom_description.return_value = "FoM = x"
    topo.specs_description.return_value = "x >= 0.5"
    topo.design_vars_description.return_value = "- x: [0, 1]"
    topo.reference_description.return_value = "x = 0.7"
    topo.auxiliary_tools_description.return_value = ""
    topo.design_space.return_value = {"x": (0.0, 1.0)}
    topo.relevant_skills.return_value = skills if skills is not None else []
    return topo


class TestCCSpicePromptSkillInjection:
    def _build(self, topology):
        from eda_agents.agents.tool_defs import build_cc_spice_system_prompt

        return build_cc_spice_system_prompt(
            topology=topology,
            agent_id="a1",
            eval_script="/tmp/eval.py",
            gmid_script="/tmp/gmid.py",
            strategy="none",
            budget=10,
        )

    def test_default_no_skills_no_change(self, monkeypatch):
        monkeypatch.delenv("EDA_AGENTS_INJECT_SKILLS", raising=False)
        prompt = self._build(_fake_topology([]))
        assert "gm/ID methodology" not in prompt
        assert "You are an analog circuit design agent" in prompt

    def test_declared_skill_precedes_preamble(self, monkeypatch):
        monkeypatch.delenv("EDA_AGENTS_INJECT_SKILLS", raising=False)
        prompt = self._build(_fake_topology(["analog.gmid_sizing"]))
        skill_idx = prompt.find("gm/ID methodology")
        agent_idx = prompt.find("You are an analog circuit design agent")
        assert skill_idx >= 0, "gmid_sizing body missing from CC SPICE prompt"
        assert agent_idx > skill_idx

    def test_escape_hatch_disables_injection(self, monkeypatch):
        monkeypatch.setenv("EDA_AGENTS_INJECT_SKILLS", "0")
        prompt = self._build(_fake_topology(["analog.gmid_sizing"]))
        assert "gm/ID methodology" not in prompt
