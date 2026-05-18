"""Tests for the ``analog.ron_gm_sizing`` skill registration and
rendering. No SPICE or LUT required.
"""

from __future__ import annotations

# Importing the analog skill module triggers registration as a side
# effect; pytest gives each test its own process so we re-import here
# to be explicit about dependency.
import eda_agents.skills.analog  # noqa: F401 -- registers skills
from eda_agents.skills.registry import get_skill, list_skills


class TestRegistration:
    def test_skill_is_registered(self):
        skill = get_skill("analog.ron_gm_sizing")
        assert skill.name == "analog.ron_gm_sizing"
        assert "Ron/gm" in skill.description or "Ron_on/g_m" in skill.description.replace("Ron/gm", "Ron_on/g_m")

    def test_listed_with_analog_prefix(self):
        names = [s.name for s in list_skills(prefix="analog.")]
        assert "analog.ron_gm_sizing" in names


class TestRendering:
    def test_renders_without_topology(self):
        rendered = get_skill("analog.ron_gm_sizing").render(None)
        assert rendered  # non-empty
        # All three bundle parts contribute their headings.
        assert "# Ron/gm Methodology for Inverter-Based Dynamic Amplifiers" in rendered
        assert "# Ron/gm Sizing API -- RonGmLookup" in rendered
        assert "# Ron/gm Corner Analysis" in rendered

    def test_topology_context_prepended(self):
        # The skill should inject the topology-specific header before
        # the markdown body when given a topology.
        from eda_agents.topologies.iba_ihp import InverterBasedAmplifier
        topo = InverterBasedAmplifier(pdk="ihp_sg13g2")
        rendered = get_skill("analog.ron_gm_sizing").render(topo)
        assert "Active topology: iba_ihp" in rendered
        # The body still ships.
        assert "# Ron/gm Methodology" in rendered

    def test_token_budget_reasonable(self):
        # The skill is large by design (three markdown sections); cap
        # at ~5000 tokens (20k chars) so authors do not blow the prompt
        # without noticing.
        rendered = get_skill("analog.ron_gm_sizing").render(None)
        assert len(rendered) < 20_000, (
            f"Skill prompt grew to {len(rendered)} chars; "
            "split into narrower skills or trim content."
        )
