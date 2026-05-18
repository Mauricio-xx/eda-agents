"""Skill registration and rendering checks for analog.hierarchical_dse.
No SPICE / LUT required."""

from __future__ import annotations

import eda_agents.skills.analog  # noqa: F401 -- registers skills as a side effect
from eda_agents.skills.registry import get_skill, list_skills


class TestRegistration:
    def test_skill_is_registered(self):
        sk = get_skill("analog.hierarchical_dse")
        assert sk.name == "analog.hierarchical_dse"
        assert "Hierarchical" in sk.description or "hierarchical" in sk.description

    def test_listed_with_analog_prefix(self):
        names = [s.name for s in list_skills(prefix="analog.")]
        assert "analog.hierarchical_dse" in names


class TestRendering:
    def test_renders_without_topology(self):
        rendered = get_skill("analog.hierarchical_dse").render(None)
        assert rendered
        assert "# Hierarchical Design-Space Exploration" in rendered
        assert "# Hierarchical DSE API" in rendered
        assert "# Hierarchical DSE -- limits" in rendered

    def test_token_budget_reasonable(self):
        # Three markdown sections; cap below 5000 tokens (20k chars).
        rendered = get_skill("analog.hierarchical_dse").render(None)
        assert len(rendered) < 20_000, (
            f"Skill prompt grew to {len(rendered)} chars; trim content."
        )
