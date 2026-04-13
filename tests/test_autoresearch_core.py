"""Tests for the shared autoresearch core helpers.

Tests ProgramStore and TsvLogger independently of any runner.
"""

from __future__ import annotations

from pathlib import Path

from eda_agents.agents._autoresearch_core import (
    ProgramStore,
    TsvLogger,
    extract_json_from_response,
    generate_program_content,
)


# ---------------------------------------------------------------------------
# ProgramStore tests
# ---------------------------------------------------------------------------


class TestProgramStore:
    def _make_store(self, tmp_path: Path) -> ProgramStore:
        def gen():
            return (
                "# Test Program\n\n"
                "## Goal\nMaximize FoM.\n\n"
                "## Metrics\nPrimary: FoM\n\n"
                "## Design Space\n- x: [0, 10]\n\n"
                "## Specs\nAll specs met.\n\n"
                "## Current Best\nNo valid design found yet.\n\n"
                "## Strategy\nStart exploring.\n\n"
                "## Learned So Far\n"
                "(empty -- will be populated as exploration progresses)\n\n"
                "## Rules\nNEVER STOP.\n"
            )
        return ProgramStore(tmp_path, gen)

    def test_init_creates_file(self, tmp_path):
        store = self._make_store(tmp_path)
        path = store.init()
        assert path.is_file()
        assert "## Goal" in path.read_text()

    def test_init_does_not_overwrite(self, tmp_path):
        (tmp_path / "program.md").write_text("custom content")
        store = self._make_store(tmp_path)
        store.init()
        assert "custom content" in store.read()

    def test_read(self, tmp_path):
        store = self._make_store(tmp_path)
        store.init()
        content = store.read()
        assert "## Goal" in content

    def test_update_best(self, tmp_path):
        store = self._make_store(tmp_path)
        store.init()

        entry = {"eval": 3, "fom": 1.5e6, "params": {"x": 5}}
        store.update_best(entry, lambda e: f"Eval #{e['eval']}: FoM={e['fom']:.2e}")

        content = store.read()
        assert "Eval #3" in content
        assert "1.50e+06" in content
        assert "No valid design" not in content

    def test_update_learning_first(self, tmp_path):
        store = self._make_store(tmp_path)
        store.init()

        store.update_learning("First insight")
        content = store.read()
        assert "First insight" in content
        assert "(empty" not in content

    def test_update_learning_append(self, tmp_path):
        store = self._make_store(tmp_path)
        store.init()

        store.update_learning("Insight one")
        store.update_learning("Insight two")
        content = store.read()
        assert "Insight one" in content
        assert "Insight two" in content

    def test_update_strategy(self, tmp_path):
        store = self._make_store(tmp_path)
        store.init()

        store.update_strategy("Focus on parameter x next.")
        content = store.read()
        assert "Focus on parameter x next" in content

    def test_path_property(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.path == tmp_path / "program.md"


# ---------------------------------------------------------------------------
# TsvLogger tests
# ---------------------------------------------------------------------------


class TestTsvLogger:
    def _make_logger(self, tmp_path: Path, measurement_cols=None) -> TsvLogger:
        return TsvLogger(
            tsv_path=tmp_path / "results.tsv",
            param_cols=["x", "y"],
            measurement_cols=measurement_cols or ["metric_a", "metric_b"],
        )

    def test_write_header(self, tmp_path):
        tsv = self._make_logger(tmp_path)
        tsv.write_header()
        header = tsv.tsv_path.read_text().strip()
        assert header == "eval\tx\ty\tmetric_a\tmetric_b\tfom\tvalid\tstatus"

    def test_append_row(self, tmp_path):
        tsv = self._make_logger(tmp_path)
        tsv.write_header()

        entry = {
            "eval": 1,
            "params": {"x": 3.0, "y": 7.0},
            "metric_a": 42.5,
            "metric_b": 100.0,
            "fom": 1.23e6,
            "valid": True,
            "status": "kept",
        }
        tsv.append_row(entry)

        lines = tsv.tsv_path.read_text().strip().splitlines()
        assert len(lines) == 2
        data = lines[1].split("\t")
        assert data[0] == "1"
        assert data[-1] == "kept"

    def test_append_row_missing_measurement(self, tmp_path):
        tsv = self._make_logger(tmp_path)
        tsv.write_header()

        entry = {
            "eval": 1,
            "params": {"x": 1.0, "y": 2.0},
            "metric_a": 10.0,
            # metric_b is missing
            "fom": 0.0,
            "valid": False,
            "status": "crash",
        }
        tsv.append_row(entry)

        lines = tsv.tsv_path.read_text().strip().splitlines()
        assert len(lines) == 2  # header + 1 row

    def test_load_history_empty(self, tmp_path):
        tsv = self._make_logger(tmp_path)
        history, best, start = tsv.load_history()
        assert history == []
        assert best is None
        assert start == 1

    def test_load_history_header_only(self, tmp_path):
        tsv = self._make_logger(tmp_path)
        tsv.write_header()
        history, best, start = tsv.load_history()
        assert history == []
        assert best is None
        assert start == 1

    def test_load_history_with_data(self, tmp_path):
        tsv = self._make_logger(tmp_path)
        tsv.write_header()

        entries = [
            {"eval": 1, "params": {"x": 1.0, "y": 2.0},
             "metric_a": 10.0, "metric_b": 20.0,
             "fom": 1e5, "valid": True, "status": "kept"},
            {"eval": 2, "params": {"x": 3.0, "y": 4.0},
             "metric_a": 5.0, "metric_b": 15.0,
             "fom": 5e4, "valid": True, "status": "discarded"},
            {"eval": 3, "params": {"x": 0.0, "y": 0.0},
             "fom": 0.0, "valid": False, "status": "crash"},
        ]
        for e in entries:
            tsv.append_row(e)

        history, best, start = tsv.load_history()
        assert len(history) == 3
        assert best is not None
        assert best["eval"] == 1
        assert best["fom"] == 1e5
        assert start == 4

    def test_load_history_measurements_parsed(self, tmp_path):
        tsv = self._make_logger(tmp_path)
        tsv.write_header()
        tsv.append_row({
            "eval": 1, "params": {"x": 1.0, "y": 2.0},
            "metric_a": 42.5, "metric_b": 100.0,
            "fom": 1e5, "valid": True, "status": "kept",
        })

        history, _, _ = tsv.load_history()
        assert history[0]["metric_a"] == 42.5
        assert history[0]["metric_b"] == 100.0

    def test_load_history_best_from_valid_only(self, tmp_path):
        tsv = self._make_logger(tmp_path)
        tsv.write_header()

        # Invalid entry with higher FoM should not be best
        tsv.append_row({
            "eval": 1, "params": {"x": 1.0, "y": 2.0},
            "metric_a": 10.0, "metric_b": 20.0,
            "fom": 1e10, "valid": False, "status": "discarded",
        })
        tsv.append_row({
            "eval": 2, "params": {"x": 3.0, "y": 4.0},
            "metric_a": 5.0, "metric_b": 15.0,
            "fom": 1e5, "valid": True, "status": "kept",
        })

        _, best, _ = tsv.load_history()
        assert best["eval"] == 2

    def test_different_measurement_cols(self, tmp_path):
        """Digital-style columns should work."""
        tsv = TsvLogger(
            tsv_path=tmp_path / "digital.tsv",
            param_cols=["CLOCK_PERIOD", "PL_TARGET_DENSITY_PCT"],
            measurement_cols=["wns_worst_ns", "cell_count", "die_area_um2"],
        )
        tsv.write_header()
        header = tsv.tsv_path.read_text().strip()
        assert "CLOCK_PERIOD" in header
        assert "wns_worst_ns" in header
        assert "die_area_um2" in header

        tsv.append_row({
            "eval": 1,
            "params": {"CLOCK_PERIOD": 40.0, "PL_TARGET_DENSITY_PCT": 65.0},
            "wns_worst_ns": 1.407,
            "cell_count": 12201,
            "die_area_um2": 256175.0,
            "fom": 9.14,
            "valid": True,
            "status": "kept",
        })

        history, best, start = tsv.load_history()
        assert len(history) == 1
        assert history[0]["wns_worst_ns"] == 1.407
        assert history[0]["cell_count"] == 12201.0
        assert best is not None
        assert start == 2


# ---------------------------------------------------------------------------
# generate_program_content tests
# ---------------------------------------------------------------------------


class TestGenerateProgramContent:
    def test_basic(self):
        content = generate_program_content(
            domain_name="test-circuit",
            pdk_display_name="TestPDK",
            fom_description="Higher is better.",
            specs_description="All specs met.",
            design_vars_description="- x: range [0, 10]",
            design_space_lines="- x: [0, 10]",
            reference_description="Reference: x=5, FoM=1.0",
        )
        assert "## Goal" in content
        assert "test-circuit" in content
        assert "TestPDK" in content
        assert "NEVER STOP" in content
        assert "## Rules" in content

    def test_contains_all_sections(self):
        content = generate_program_content(
            domain_name="d",
            pdk_display_name="p",
            fom_description="f",
            specs_description="s",
            design_vars_description="v",
            design_space_lines="l",
            reference_description="r",
        )
        for section in ["Goal", "Metrics", "Design Space", "Specs",
                        "Current Best", "Strategy", "Learned So Far", "Rules"]:
            assert f"## {section}" in content


# ---------------------------------------------------------------------------
# extract_json_from_response tests
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_raw_json(self):
        result = extract_json_from_response('{"x": 1, "y": 2}')
        assert result.strip() == '{"x": 1, "y": 2}'

    def test_markdown_fenced(self):
        text = 'Here is the JSON:\n```json\n{"x": 1}\n```\nDone.'
        result = extract_json_from_response(text)
        assert '{"x": 1}' in result

    def test_embedded_in_text(self):
        text = "I suggest we try these params: {\"x\": 5} and see."
        result = extract_json_from_response(text)
        assert '{"x": 5}' in result

    def test_code_block_no_language(self):
        text = "```\n{\"x\": 1}\n```"
        result = extract_json_from_response(text)
        assert '{"x": 1}' in result
