"""Tests for the ``digital.cocotb_testbench`` skill.

The skill is a zero-arg prompt body. Tests guard:

* Registration under the expected dotted name.
* Presence of the key-import / key-API anchors so the prompt can't
  silently drift to deprecated cocotb patterns.
* Gate-level-safe rules are retained (the single-source constraint
  that lets the same TB run against RTL + post-synth + post-PnR).
"""

from __future__ import annotations

import pytest

from eda_agents.skills.registry import get_skill, list_skills


class TestCocotbSkillRegistration:
    def test_registered(self):
        skill = get_skill("digital.cocotb_testbench")
        assert skill.prompt_fn is not None

    def test_listed_under_digital_prefix(self):
        names = [s.name for s in list_skills(prefix="digital.")]
        assert "digital.cocotb_testbench" in names


class TestCocotbSkillBody:
    @pytest.fixture(scope="class")
    def body(self) -> str:
        return get_skill("digital.cocotb_testbench").render()

    @pytest.mark.parametrize(
        "anchor",
        [
            # Core cocotb imports the agent MUST emit.
            "import cocotb",
            "from cocotb.clock import Clock",
            "from cocotb.triggers import RisingEdge",
            # Current cocotb API.
            "cocotb.start_soon(",
            "@cocotb.test()",
            'Clock(dut.clk, 10, units="ns")',
            # Makefile essentials.
            "SIM ?= icarus",
            "TOPLEVEL_LANG ?= verilog",
            "cocotb-config --makefiles",
            "Makefile.sim",
        ],
    )
    def test_contains_essential_anchor(self, body, anchor):
        assert anchor in body, f"missing essential anchor: {anchor!r}"

    def test_warns_against_deprecated_api(self, body):
        # cocotb.fork was removed; the skill must warn.
        assert "cocotb.fork" in body
        assert "deprecated" in body.lower()

    def test_retains_gate_level_safe_rules(self, body):
        # These rules are the reason the skill exists — any drop is a
        # regression.
        must_have = [
            "RisingEdge",
            "reset",
            "`x`",
            "one full",
        ]
        for m in must_have:
            assert m in body, f"gate-level-safe rule drift: {m!r} missing"
        # Explicitly forbids bare Timer stimulus patterns.
        assert "NEVER drive DUT inputs with a bare Timer" in body

    def test_warns_about_readonly_write_footgun(self, body):
        # Added after a live cocotb probe failed with mystery off-by-one
        # because Claude wrote `dut.en.value = 1` after `await ReadOnly()`
        # — cocotb silently drops those writes. The skill must flag
        # this footgun prominently and show the correct cycle shape.
        assert "READONLY IS READ-ONLY" in body
        assert "silently drops" in body or "silently drop" in body
        assert "off-by-one" in body

    def test_mentions_cocotb_summary_line(self, body):
        # CocotbDriver parses this specific regex; the skill must point
        # the agent at it so they don't invent their own PASS/FAIL
        # format.
        assert "TESTS=" in body
        assert "PASS=" in body
        assert "FAIL=" in body

    def test_references_glsim_runner_behaviour(self, body):
        # The skill explains that GlSimRunner substitutes VERILOG_SOURCES
        # at gate-level run time so the agent doesn't hand-wire stdcell
        # verilog paths in the Makefile. Words may be split across a
        # line break so we check each lexeme separately.
        lowered = body.lower()
        assert "glsimrunner" in lowered
        assert "stdcell" in lowered
        assert "verilog" in lowered


class TestCocotbSkillThroughMcp:
    """The skill must be renderable via the MCP render_skill tool."""

    def test_render_skill_returns_body(self):
        try:
            import fastmcp  # noqa: F401
        except ImportError:
            pytest.skip("fastmcp not installed")

        from eda_agents.mcp.server import render_skill

        body = render_skill(name="digital.cocotb_testbench")
        assert isinstance(body, str)
        assert len(body) > 1000
        assert not body.startswith("ERROR")
        assert "@cocotb.test()" in body
