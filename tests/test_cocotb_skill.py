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

    def test_mandates_assert_for_comparisons(self, body):
        # Added after the S12-A Haiku FFT-8 probe: Haiku wrote a TB that
        # used cocotb.log.warning instead of assert for comparison
        # failures. The TB structurally couldn't fail; results.xml
        # reported PASS=1 FAIL=0 even though the FFT was functionally
        # broken (Haiku itself admitted it in the verdict text). The
        # skill must mandate ``assert`` and explicitly call out
        # log.warning / log.error / print as bug-hiding patterns.
        assert "ASSERTIONS ARE MANDATORY" in body
        # Forbid the exact patterns Haiku used.
        for forbidden in (
            "cocotb.log.warning",
            "cocotb.log.error",
        ):
            assert forbidden in body, (
                f"skill must explicitly call out {forbidden!r} as a "
                "bug-hiding pattern that does NOT fail the test"
            )
        # The skill must reference the correct shape using `assert`.
        assert "assert actual ==" in body or "assert err <=" in body
        # And must spell out that log annotations don't fail tests.
        lowered = body.lower()
        assert "do not affect the test verdict" in lowered or (
            "do not fail the test" in lowered
        )

    def test_mandates_minimum_verification_coverage(self, body):
        # Same Haiku probe also produced a TB that ran for only 250 ns
        # of simulated time — a single short stimulus burst that
        # bypassed most of the design. The skill must steer the agent
        # toward a meaningful coverage envelope.
        assert "MINIMUM VERIFICATION COVERAGE" in body
        # Mention sim-time order of magnitude (us / micro-second).
        lowered = body.lower()
        assert "micro-second" in lowered or "microsecond" in lowered or (
            "us" in body and "ns" in body
        )

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

    def test_documents_gl_sim_timescale_requirement(self, body):
        # Added 2026-04-25 after the chipathon ex04 GL sim tripped on
        # iverilog's 1s/1s default precision when no source carried a
        # `timescale directive. The skill must teach the agent to ship
        # tb/timescale.v and prepend it to VERILOG_SOURCES.
        assert "GATE-LEVEL SIM SPECIFICS" in body
        assert "tb/timescale.v" in body
        # The exact failure message the agent will hit if they skip it.
        assert "Bad period" in body
        assert "1e0" in body
        # The fix must show the right `timescale.
        assert "1ns/1ps" in body
        # And must spell out that timescale.v MUST come first.
        lowered = body.lower()
        assert "first" in lowered and "verilog_sources" in lowered

    def test_documents_pipelined_timer_escape_hatch(self, body):
        # Added 2026-04-25 after the chipathon ex04 alu_macro GL sim
        # required Timer(1, unit="ns") after RisingEdge to read post-
        # edge values; ReadOnly() would have blocked the next loop
        # iteration's input writes (back-to-back stimulus pattern).
        # The skill must distinguish this case from the canonical
        # ReadOnly pattern.
        assert "Timer(1, unit=\"ns\")" in body or 'Timer(1, unit="ns")' in body
        # Why ReadOnly is wrong here.
        assert "ReadOnly" in body
        # Symptom call-out so the agent recognises it from a log.
        assert "pre-edge value" in body
        # Mention the iverilog scheduling root cause so the agent
        # doesn't think it's a TB authoring bug.
        lowered = body.lower()
        assert "non-blocking" in lowered

    def test_troubleshooting_covers_gl_sim_pitfalls(self, body):
        # The TROUBLESHOOTING section (zero-budget reference) must list
        # both new GL gotchas so the agent finds them when grep-ing
        # the prompt for an error string.
        # Bad period error -> timescale fix.
        assert "Bad period" in body
        # Pipelined sampling -> Timer(1, unit="ns") escape hatch.
        assert "tight loop" in body or "tight-loop" in body


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
