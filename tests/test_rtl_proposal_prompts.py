"""Tests for RTL proposal prompt templates."""

from pathlib import Path

from eda_agents.agents.rtl_proposal_prompts import (
    cc_cli_hybrid_prompt,
    cc_cli_rtl_prompt,
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

    def test_includes_pdk_name_when_provided(self):
        prompt = cc_cli_hybrid_prompt(
            design_name="counter",
            design_spec="A counter",
            optimization_goal="Minimize area",
            rtl_file_paths=[Path("/tmp/x.v")],
            config_path=Path("/tmp/config.yaml"),
            pdk_root="/pdk/ihp",
            pdk_name="ihp-sg13g2",
        )
        assert "PDK_ROOT=/pdk/ihp" in prompt
        assert "PDK=ihp-sg13g2" in prompt

    def test_omits_pdk_name_line_when_not_provided(self):
        prompt = cc_cli_hybrid_prompt(
            design_name="counter",
            design_spec="A counter",
            optimization_goal="Minimize area",
            rtl_file_paths=[Path("/tmp/x.v")],
            config_path=Path("/tmp/config.yaml"),
            pdk_root="/pdk/x",
        )
        assert "PDK_ROOT=/pdk/x" in prompt
        assert "PDK=gf180mcuD" not in prompt
        assert "\nPDK=" not in prompt

    def test_forbids_librelane_run(self):
        prompt = cc_cli_hybrid_prompt(
            design_name="x", design_spec="x", optimization_goal="x",
            rtl_file_paths=[], config_path=Path("/tmp/c.yaml"),
        )
        assert "Do NOT run LibreLane" in prompt

    def test_renders_history_section_when_provided(self):
        history = [
            {"eval": 1, "params": {"density": 50, "clk": 5000.0},
             "fom": 3.5, "valid": True, "status": "kept",
             "rtl_rationale": "shift-add"},
            {"eval": 2, "params": {"density": 50, "clk": 5000.0},
             "fom": 0.0, "valid": False, "status": "dedup"},
            {"eval": 3, "params": {"density": 60, "clk": 4000.0},
             "fom": None, "valid": False, "status": "lint_fail",
             "rtl_rationale": "bad change"},
        ]
        prompt = cc_cli_hybrid_prompt(
            design_name="counter", design_spec="4-bit", optimization_goal="x",
            rtl_file_paths=[Path("/tmp/x.v")], config_path=Path("/tmp/c.yaml"),
            history=history, eval_num=4, budget=6,
        )
        assert "## Prior Evaluations" in prompt
        assert "eval 4 of 6" in prompt
        # Each entry rendered (eval number visible).
        assert "| 1 |" in prompt
        assert "| 2 |" in prompt
        assert "| 3 |" in prompt
        # Statuses surfaced.
        assert "kept" in prompt
        assert "dedup" in prompt
        assert "lint_fail" in prompt
        # Already-tried list and anti-duplicate warning.
        # Keys are sorted alphabetically so duplicates share the same signature.
        assert "Already-tried params combinations" in prompt
        assert "clk=5000,density=50" in prompt
        assert "Avoid duplicates" in prompt

    def test_history_section_omitted_when_history_none(self):
        prompt = cc_cli_hybrid_prompt(
            design_name="counter", design_spec="4-bit", optimization_goal="x",
            rtl_file_paths=[Path("/tmp/x.v")], config_path=Path("/tmp/c.yaml"),
        )
        assert "Prior Evaluations" not in prompt
        assert "Avoid duplicates" not in prompt
        assert "Already-tried params" not in prompt

    def test_history_section_truncates_to_last_15(self):
        history = [
            {"eval": i, "params": {"density": 50 + i, "clk": 4000.0},
             "fom": float(i), "valid": True, "status": "kept",
             "rtl_rationale": f"try {i}"}
            for i in range(1, 21)
        ]
        prompt = cc_cli_hybrid_prompt(
            design_name="counter", design_spec="4-bit", optimization_goal="x",
            rtl_file_paths=[Path("/tmp/x.v")], config_path=Path("/tmp/c.yaml"),
            history=history, eval_num=21, budget=25,
        )
        # Last 15 (#6..#20) appear; #1..#5 do not.
        assert "| 20 |" in prompt
        assert "| 6 |" in prompt
        assert "| 5 |" not in prompt
        assert "| 1 |" not in prompt

    def test_history_section_lists_dedup_entries(self):
        history = [
            {"eval": 1, "params": {"density": 50, "clk": 5000.0},
             "fom": 3.5, "valid": True, "status": "kept"},
            {"eval": 2, "params": {"density": 50, "clk": 5000.0},
             "fom": 0.0, "valid": False, "status": "dedup"},
        ]
        prompt = cc_cli_hybrid_prompt(
            design_name="counter", design_spec="4-bit", optimization_goal="x",
            rtl_file_paths=[Path("/tmp/x.v")], config_path=Path("/tmp/c.yaml"),
            history=history, eval_num=3, budget=6,
        )
        assert "dedup" in prompt
        # Already-tried list collapses duplicates.
        tried_line = next(
            line for line in prompt.split("\n")
            if line.startswith("Already-tried params combinations")
        )
        assert tried_line.count("clk=5000,density=50") == 1


class TestCcCliRtlPrompt:
    def test_includes_file_paths(self):
        prompt = cc_cli_rtl_prompt(
            design_name="counter", design_spec="4-bit", optimization_goal="x",
            rtl_file_paths=[Path("/tmp/src/counter.v")],
        )
        assert "/tmp/src/counter.v" in prompt

    def test_renders_history_section_when_provided(self):
        history = [
            {"eval": 1, "params": {"density": 50, "clk": 5000.0},
             "fom": 3.5, "valid": True, "status": "kept",
             "rtl_rationale": "shift-add"},
            {"eval": 2, "params": {"density": 50, "clk": 5000.0},
             "fom": 0.0, "valid": False, "status": "dedup"},
        ]
        prompt = cc_cli_rtl_prompt(
            design_name="counter", design_spec="4-bit", optimization_goal="x",
            rtl_file_paths=[Path("/tmp/x.v")],
            history=history, eval_num=3, budget=6,
        )
        assert "## Prior Evaluations" in prompt
        assert "eval 3 of 6" in prompt
        assert "| 1 |" in prompt
        assert "kept" in prompt
        assert "dedup" in prompt
        assert "Already-tried params combinations" in prompt
        assert "Avoid duplicates" in prompt

    def test_history_section_omitted_when_history_none(self):
        prompt = cc_cli_rtl_prompt(
            design_name="counter", design_spec="4-bit", optimization_goal="x",
            rtl_file_paths=[Path("/tmp/x.v")],
        )
        assert "Prior Evaluations" not in prompt
        assert "Avoid duplicates" not in prompt
