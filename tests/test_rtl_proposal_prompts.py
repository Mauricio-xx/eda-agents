"""Tests for RTL proposal prompt templates."""

from pathlib import Path

from eda_agents.agents.rtl_proposal_prompts import (
    cc_cli_hybrid_prompt,
    hybrid_system_prompt,
    rtl_proposal_prompt,
    rtl_system_prompt,
)

SAMPLE_RTL = {
    "src/counter.v": "module counter(input clk, rst_n, en, output reg [3:0] count);\n"
    "  always @(posedge clk or negedge rst_n)\n"
    "    if (!rst_n) count <= 0; else if (en) count <= count + 1;\n"
    "endmodule\n"
}

SAMPLE_PROGRAM = "# Test Program\n## Goal\nMaximize FoM\n## Design Space\n- X: [1, 10]"

SAMPLE_SPACE = {
    "PL_TARGET_DENSITY_PCT": [50, 60, 70],
    "CLOCK_PERIOD": [40.0, 50.0],
}


class TestRtlSystemPrompt:
    def test_includes_rtl_content(self):
        prompt = rtl_system_prompt(SAMPLE_PROGRAM, SAMPLE_RTL, "A counter")
        assert "counter" in prompt
        assert "endmodule" in prompt

    def test_includes_module_preservation_rule(self):
        prompt = rtl_system_prompt(SAMPLE_PROGRAM, SAMPLE_RTL, "A counter")
        assert "preserve" in prompt.lower()
        assert "module name" in prompt.lower()

    def test_includes_response_format(self):
        prompt = rtl_system_prompt(SAMPLE_PROGRAM, SAMPLE_RTL, "A counter")
        assert "rtl_changes" in prompt
        assert "rationale" in prompt


class TestHybridSystemPrompt:
    def test_includes_both_rtl_and_config(self):
        prompt = hybrid_system_prompt(SAMPLE_PROGRAM, SAMPLE_RTL, SAMPLE_SPACE, "A counter")
        assert "endmodule" in prompt
        assert "PL_TARGET_DENSITY_PCT" in prompt
        assert "CLOCK_PERIOD" in prompt

    def test_response_format_has_config_and_rtl(self):
        prompt = hybrid_system_prompt(SAMPLE_PROGRAM, SAMPLE_RTL, SAMPLE_SPACE, "A counter")
        assert '"config"' in prompt
        assert '"rtl_changes"' in prompt


class TestRtlProposalPrompt:
    def test_shows_eval_number(self):
        prompt = rtl_proposal_prompt([], None, 3, 10)
        assert "3/10" in prompt

    def test_shows_best_with_rationale(self):
        best = {
            "eval": 1, "fom": 1.5, "valid": True,
            "rtl_rationale": "shift-add optimization",
            "wns_worst_ns": 0.5, "cell_count": 100,
        }
        prompt = rtl_proposal_prompt([], best, 2, 5)
        assert "shift-add" in prompt
        assert "1.50e+00" in prompt

    def test_shows_history(self):
        history = [
            {"eval": 1, "fom": 1.0, "valid": True, "kept": True,
             "status": "kept", "rtl_rationale": "initial"},
            {"eval": 2, "fom": 0.5, "valid": False, "kept": False,
             "status": "lint_fail", "rtl_rationale": "bad change"},
        ]
        prompt = rtl_proposal_prompt(history, history[0], 3, 5)
        assert "lint_fail" in prompt
        assert "bad change" in prompt


class TestCcCliHybridPrompt:
    def test_includes_file_paths(self):
        prompt = cc_cli_hybrid_prompt(
            design_name="counter",
            design_spec="A 4-bit counter",
            optimization_goal="Minimize area",
            rtl_file_paths=[Path("/tmp/src/counter.v")],
            config_path=Path("/tmp/config.yaml"),
        )
        assert "/tmp/src/counter.v" in prompt
        assert "/tmp/config.yaml" in prompt

    def test_includes_pdk_root(self):
        prompt = cc_cli_hybrid_prompt(
            design_name="counter",
            design_spec="A counter",
            optimization_goal="Minimize area",
            rtl_file_paths=[Path("/tmp/x.v")],
            config_path=Path("/tmp/config.yaml"),
            pdk_root="/pdk/gf180",
        )
        assert "PDK_ROOT=/pdk/gf180" in prompt

    def test_forbids_librelane_run(self):
        prompt = cc_cli_hybrid_prompt(
            design_name="x", design_spec="x", optimization_goal="x",
            rtl_file_paths=[], config_path=Path("/tmp/c.yaml"),
        )
        assert "Do NOT run LibreLane" in prompt
