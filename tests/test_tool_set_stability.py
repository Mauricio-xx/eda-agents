"""Snapshot test for digital agent tool sets.

Catches silent tool-set drift that historically caused prompt
regressions in ADK sub-agents.  If a tool is added/removed from a
sub-agent, update the snapshot here intentionally.
"""

from eda_agents.agents.digital_adk_agents import (
    PHYSICAL_TOOLS,
    SIGNOFF_TOOLS,
    SYNTH_TOOLS,
    VERIF_TOOLS,
)


class TestToolSetStability:
    """Assert tool set constants match known snapshots."""

    def test_verif_tools(self):
        assert VERIF_TOOLS == frozenset({
            "run_rtl_lint",
            "run_rtl_sim",
        })

    def test_synth_tools(self):
        assert SYNTH_TOOLS == frozenset({
            "run_librelane_flow",
            "read_timing_report",
            "check_flow_status",
            "modify_flow_config",
        })

    def test_physical_tools(self):
        assert PHYSICAL_TOOLS == frozenset({
            "run_librelane_flow",
            "run_physical_slice",
            "modify_flow_config",
            "read_timing_report",
            "check_flow_status",
        })

    def test_signoff_tools(self):
        assert SIGNOFF_TOOLS == frozenset({
            "run_klayout_drc",
            "read_drc_summary",
            "run_klayout_lvs",
            "modify_flow_config",
            "rerun_flow",
            "check_flow_status",
            "run_precheck",
        })

    def test_no_overlap_verif_signoff(self):
        """Verification and signoff should have distinct tool sets."""
        assert VERIF_TOOLS.isdisjoint(SIGNOFF_TOOLS)

    def test_all_sets_are_frozenset(self):
        """Tool sets must be immutable."""
        for name, tools in [
            ("VERIF_TOOLS", VERIF_TOOLS),
            ("SYNTH_TOOLS", SYNTH_TOOLS),
            ("PHYSICAL_TOOLS", PHYSICAL_TOOLS),
            ("SIGNOFF_TOOLS", SIGNOFF_TOOLS),
        ]:
            assert isinstance(tools, frozenset), f"{name} should be frozenset"
