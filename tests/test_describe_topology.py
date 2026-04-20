"""Tests for the ``describe_topology`` MCP tool.

Exercises the real topology registry — these tests do not hit SPICE or
any external tool, so they run under the default ``not spice`` marker
gate without any per-test marker.
"""

from __future__ import annotations

from eda_agents.mcp.server import describe_topology
from eda_agents.topologies import list_topology_names


class TestDescribeTopology:
    def test_unknown_name_returns_error(self):
        result = describe_topology("does_not_exist")
        assert "error" in result
        assert "does_not_exist" in result["error"]

    def test_known_topology_has_expected_keys(self):
        result = describe_topology("miller_ota")
        assert "error" not in result
        for key in (
            "name",
            "design_space",
            "default_params",
            "description",
            "design_vars",
            "specs",
            "fom",
            "reference",
            "exploration_hints",
            "relevant_skills",
            "auxiliary_tools",
        ):
            assert key in result, f"missing key {key!r}"

    def test_design_space_entries_have_min_max_default(self):
        result = describe_topology("miller_ota")
        space = result["design_space"]
        assert space, "design_space should be non-empty"
        for var, entry in space.items():
            assert "min" in entry and "max" in entry, f"{var} missing bounds"
            assert entry["min"] <= entry["max"], f"{var} min > max"
            # default should be inside bounds for this topology
            assert "default" in entry, f"{var} missing default"
            assert entry["min"] <= entry["default"] <= entry["max"]

    def test_default_params_match_design_space_keys(self):
        result = describe_topology("miller_ota")
        assert set(result["default_params"].keys()) == set(
            result["design_space"].keys()
        )

    def test_all_registered_topologies_are_describable(self):
        # Smoke test: every topology_name listed by the registry must
        # describe cleanly. Catches ABC contract regressions early.
        for name in list_topology_names():
            result = describe_topology(name)
            assert "error" not in result, (
                f"describe_topology({name!r}) returned error: "
                f"{result.get('error')}"
            )
            assert result["name"] == name
            assert result["design_space"], (
                f"{name} has empty design_space"
            )

    def test_relevant_skills_is_list_of_strings(self):
        result = describe_topology("miller_ota")
        skills = result["relevant_skills"]
        assert isinstance(skills, list)
        for s in skills:
            assert isinstance(s, str)

    def test_prompt_blocks_are_strings(self):
        result = describe_topology("aa_ota")
        for key in (
            "description",
            "design_vars",
            "specs",
            "fom",
            "reference",
            "auxiliary_tools",
        ):
            assert isinstance(result[key], str), (
                f"{key} should be str, got {type(result[key]).__name__}"
            )
