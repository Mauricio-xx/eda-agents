"""Tests for DigitalAutoresearchRunner.

Unit tests (no LLM or LibreLane needed):
    pytest tests/test_digital_autoresearch.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from eda_agents.agents.digital_autoresearch import (
    DigitalAutoresearchRunner,
    _DIGITAL_MEASUREMENT_COLS,
)
from eda_agents.core.flow_stage import FlowStage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_design():
    """Create a mock DigitalDesign with fazyrv-like design space."""
    design = MagicMock()
    design.project_name.return_value = "test-digital"
    design.design_space.return_value = {
        "PL_TARGET_DENSITY_PCT": [45, 55, 65, 75, 85],
        "CLOCK_PERIOD": [35, 40, 45, 50],
    }
    design.default_config.return_value = {
        "PL_TARGET_DENSITY_PCT": 65,
        "CLOCK_PERIOD": 40,
    }
    design.flow_config_overrides.return_value = {}
    design.fom_description.return_value = "WNS + area + power weighted"
    design.specs_description.return_value = "WNS >= 0, DRC clean"
    design.design_vars_description.return_value = (
        "- PL_TARGET_DENSITY_PCT: placement density\n"
        "- CLOCK_PERIOD: clock period in ns"
    )
    design.reference_description.return_value = "Reference: d=65, clk=40"
    design.prompt_description.return_value = "Test digital design."
    design.project_dir.return_value = Path("/project")
    design.librelane_config.return_value = Path("/project/config.yaml")
    design.pdk_root.return_value = Path("/pdk")

    from eda_agents.core.pdk import GF180MCU_D
    design.pdk_config.return_value = GF180MCU_D

    # FoM: valid designs get positive FoM
    def compute_fom(metrics):
        if metrics.wns_worst_ns is not None and metrics.wns_worst_ns >= 0:
            return metrics.wns_worst_ns + 1.0
        return 0.0

    def check_validity(metrics):
        violations = []
        if metrics.wns_worst_ns is not None and metrics.wns_worst_ns < 0:
            violations.append("Timing not closed")
        return (len(violations) == 0, violations)

    design.compute_fom.side_effect = compute_fom
    design.check_validity.side_effect = check_validity
    return design


def _make_mock_metrics_file(tmp_path: Path, metrics_list=None) -> Path:
    """Create a mock metrics JSON file."""
    if metrics_list is None:
        metrics_list = {
            "wns_worst_ns": 1.407,
            "synth_cell_count": 12201,
            "die_area_um2": 256175.0,
            "power_total_w": 0.05185,
            "wire_length_um": 155900.0,
        }
    path = tmp_path / "mock_metrics.json"
    path.write_text(json.dumps(metrics_list))
    return path


@pytest.fixture
def design():
    return _make_design()


@pytest.fixture
def mock_metrics_path(tmp_path):
    return _make_mock_metrics_file(tmp_path)


# ---------------------------------------------------------------------------
# Constructor / config tests
# ---------------------------------------------------------------------------


class TestDigitalAutoresearchConfig:
    def test_default_params(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        assert runner.budget == 5
        assert runner.stop_after is None  # full flow by default
        assert runner.dedup is True

    def test_custom_params(self, design):
        runner = DigitalAutoresearchRunner(
            design=design, budget=10, stop_after=FlowStage.SIGNOFF_DRC, dedup=False
        )
        assert runner.budget == 10
        assert runner.stop_after == FlowStage.SIGNOFF_DRC
        assert runner.dedup is False


# ---------------------------------------------------------------------------
# Param clamping tests (discrete design space)
# ---------------------------------------------------------------------------


class TestParamClamping:
    def test_exact_value_passes(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        result = runner._clamp_params({"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40})
        assert result["PL_TARGET_DENSITY_PCT"] == 65
        assert result["CLOCK_PERIOD"] == 40

    def test_nearest_snap(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        # 60 is between 55 and 65, should snap to nearest
        result = runner._clamp_params({"PL_TARGET_DENSITY_PCT": 60, "CLOCK_PERIOD": 42})
        assert result["PL_TARGET_DENSITY_PCT"] in [55, 65]
        assert result["CLOCK_PERIOD"] in [40, 45]

    def test_out_of_range_snaps_to_boundary(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        result = runner._clamp_params({"PL_TARGET_DENSITY_PCT": 100, "CLOCK_PERIOD": 10})
        assert result["PL_TARGET_DENSITY_PCT"] == 85
        assert result["CLOCK_PERIOD"] == 35

    def test_missing_param_uses_default(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        result = runner._clamp_params({"PL_TARGET_DENSITY_PCT": 75})
        assert result["PL_TARGET_DENSITY_PCT"] == 75
        assert result["CLOCK_PERIOD"] == 40  # default


# ---------------------------------------------------------------------------
# Dedup tests
# ---------------------------------------------------------------------------


class TestDedup:
    def test_no_history_not_dup(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        assert not runner._is_duplicate({"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}, [])

    def test_exact_match_is_dup(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        history = [{"params": {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}}]
        assert runner._is_duplicate({"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}, history)

    def test_different_params_not_dup(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        history = [{"params": {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}}]
        assert not runner._is_duplicate({"PL_TARGET_DENSITY_PCT": 75, "CLOCK_PERIOD": 40}, history)

    def test_dedup_disabled(self, design):
        runner = DigitalAutoresearchRunner(design=design, dedup=False)
        history = [{"params": {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}}]
        assert not runner._is_duplicate({"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}, history)


# ---------------------------------------------------------------------------
# program.md generation tests
# ---------------------------------------------------------------------------


class TestProgramGeneration:
    def test_program_has_design_info(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        content = runner._generate_program()
        assert "test-digital" in content
        assert "GF180MCU" in content
        assert "## Goal" in content
        assert "NEVER STOP" in content

    def test_program_has_design_space(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        content = runner._generate_program()
        assert "PL_TARGET_DENSITY_PCT" in content
        assert "CLOCK_PERIOD" in content

    def test_program_store_creates_file(self, design, tmp_path):
        runner = DigitalAutoresearchRunner(design=design)
        store = runner._make_program_store(tmp_path)
        store.init()
        assert (tmp_path / "program.md").is_file()


# ---------------------------------------------------------------------------
# Prompt generation tests
# ---------------------------------------------------------------------------


class TestPromptGeneration:
    def test_system_prompt(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        prompt = runner._system_prompt("## Goal\nTest")
        assert "autonomous" in prompt.lower()
        assert "JSON" in prompt
        assert "PL_TARGET_DENSITY_PCT" in prompt

    def test_proposal_prompt_no_history(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        prompt = runner._build_proposal_prompt([], None, 1)
        assert "1/" in prompt
        assert "No valid design" in prompt

    def test_proposal_prompt_with_best(self, design):
        runner = DigitalAutoresearchRunner(design=design)
        best = {
            "eval": 2,
            "fom": 9.14,
            "valid": True,
            "params": {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40},
            "wns_worst_ns": 1.407,
            "cell_count": 12201,
            "die_area_um2": 256175.0,
            "power_mw": 51.85,
        }
        prompt = runner._build_proposal_prompt([], best, 3)
        assert "WNS=" in prompt
        assert "#2" in prompt


# ---------------------------------------------------------------------------
# Mock evaluation tests
# ---------------------------------------------------------------------------


class TestMockEvaluation:
    @pytest.mark.anyio
    async def test_mock_metrics_flat_dict(self, design, tmp_path):
        metrics_path = _make_mock_metrics_file(tmp_path)
        runner = DigitalAutoresearchRunner(
            design=design, use_mock_metrics=metrics_path
        )
        entry = await runner._evaluate(
            {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}, tmp_path, 1
        )
        assert entry["success"]
        assert entry["wns_worst_ns"] == 1.407
        assert entry["cell_count"] == 12201
        assert entry["fom"] > 0

    @pytest.mark.anyio
    async def test_mock_metrics_list(self, design, tmp_path):
        metrics_list = [
            {"wns_worst_ns": 1.0, "synth_cell_count": 10000,
             "die_area_um2": 200000, "power_total_w": 0.05, "wire_length_um": 100000},
            {"wns_worst_ns": -0.5, "synth_cell_count": 11000,
             "die_area_um2": 220000, "power_total_w": 0.06, "wire_length_um": 120000},
        ]
        path = tmp_path / "list_metrics.json"
        path.write_text(json.dumps(metrics_list))

        runner = DigitalAutoresearchRunner(design=design, use_mock_metrics=path)

        # First eval gets index 0 (valid)
        entry1 = await runner._evaluate({}, tmp_path, 1)
        assert entry1["valid"]

        # Second eval gets index 1 (invalid, negative WNS)
        entry2 = await runner._evaluate({}, tmp_path, 2)
        assert not entry2["valid"]


# ---------------------------------------------------------------------------
# Format helpers tests
# ---------------------------------------------------------------------------


class TestFormatHelpers:
    def test_format_digital_best(self):
        entry = {
            "eval": 3,
            "fom": 9.14,
            "params": {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40},
            "wns_worst_ns": 1.407,
            "cell_count": 12201,
            "die_area_um2": 256175.0,
            "power_mw": 51.85,
            "wire_length_um": 155900.0,
        }
        text = DigitalAutoresearchRunner._format_digital_best(entry)
        assert "Eval #3" in text
        assert "WNS=" in text
        assert "cells=" in text


# ---------------------------------------------------------------------------
# Full loop tests (mock mode, no LLM)
# ---------------------------------------------------------------------------


class TestFullLoop:
    @pytest.mark.anyio
    async def test_mock_mode_loop(self, design, tmp_path):
        """Full loop with mock metrics and mocked LLM."""
        metrics_path = _make_mock_metrics_file(tmp_path)
        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=3,
            use_mock_metrics=metrics_path,
        )

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose:
            mock_propose.return_value = {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}

            result = await runner.run(tmp_path / "run")

            assert result.total_evals == 3
            assert result.best_valid
            assert result.best_fom > 0

            # TSV should exist
            tsv = (tmp_path / "run" / "results.tsv")
            assert tsv.is_file()
            lines = tsv.read_text().strip().splitlines()
            assert len(lines) == 4  # header + 3 rows

            # program.md should exist
            assert (tmp_path / "run" / "program.md").is_file()

    @pytest.mark.anyio
    async def test_dedup_in_loop(self, design, tmp_path):
        """Duplicate params should be skipped."""
        metrics_path = _make_mock_metrics_file(tmp_path)
        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=3,
            use_mock_metrics=metrics_path,
            dedup=True,
        )

        # All proposals return the same params -> first is kept, rest are dedup
        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose:
            mock_propose.return_value = {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}

            result = await runner.run(tmp_path / "run")

            assert result.total_evals == 3
            assert result.kept == 1  # first eval kept

            # Check TSV has dedup status
            tsv_content = (tmp_path / "run" / "results.tsv").read_text()
            assert "dedup" in tsv_content

    @pytest.mark.anyio
    async def test_invalid_designs_not_kept(self, design, tmp_path):
        """Invalid designs (negative WNS) should not become best."""
        # Mock metrics with negative WNS -> invalid
        path = tmp_path / "bad_metrics.json"
        path.write_text(json.dumps({
            "wns_worst_ns": -2.0,
            "synth_cell_count": 12000,
            "die_area_um2": 250000,
            "power_total_w": 0.05,
            "wire_length_um": 150000,
        }))

        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=2,
            use_mock_metrics=path,
        )

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose:
            mock_propose.return_value = {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}

            result = await runner.run(tmp_path / "run")

            assert not result.best_valid
            assert result.kept == 0

    @pytest.mark.anyio
    async def test_llm_failure_fallback(self, design, tmp_path):
        """LLM failure should fall back to default params."""
        metrics_path = _make_mock_metrics_file(tmp_path)
        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=2,
            use_mock_metrics=metrics_path,
            dedup=False,
        )

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose:
            mock_propose.side_effect = [
                RuntimeError("API timeout"),
                {"PL_TARGET_DENSITY_PCT": 75, "CLOCK_PERIOD": 45},
            ]

            result = await runner.run(tmp_path / "run")
            assert result.total_evals == 2

    @pytest.mark.anyio
    async def test_crash_handled(self, design, tmp_path):
        """Evaluation crash should be caught, not stop the loop."""
        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=2,
        )

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose, \
             patch.object(runner, "_evaluate", new_callable=AsyncMock) as mock_eval:

            mock_propose.return_value = {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}
            mock_eval.side_effect = RuntimeError("LibreLane segfault")

            result = await runner.run(tmp_path / "run")

            assert result.total_evals == 2
            assert result.kept == 0

            tsv_content = (tmp_path / "run" / "results.tsv").read_text()
            assert "crash" in tsv_content


# ---------------------------------------------------------------------------
# Resume tests
# ---------------------------------------------------------------------------


class TestResume:
    @pytest.mark.anyio
    async def test_resume_from_prior_run(self, design, tmp_path):
        """If work_dir has prior results, resume from next eval number."""
        metrics_path = _make_mock_metrics_file(tmp_path)
        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=2,
            use_mock_metrics=metrics_path,
            dedup=False,
        )

        # Seed with 2 prior evals
        work_dir = tmp_path / "run"
        work_dir.mkdir()
        store = runner._make_program_store(work_dir)
        store.init()

        tsv_logger = runner._make_tsv_logger(work_dir / "results.tsv")
        tsv_logger.write_header()
        tsv_logger.append_row({
            "eval": 1,
            "params": {"PL_TARGET_DENSITY_PCT": 65.0, "CLOCK_PERIOD": 40.0},
            "wns_worst_ns": 1.407,
            "cell_count": 12201,
            "die_area_um2": 256175.0,
            "power_mw": 51.85,
            "wire_length_um": 155900.0,
            "fom": 2.407,
            "valid": True,
            "status": "kept",
        })

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose:
            mock_propose.return_value = {"PL_TARGET_DENSITY_PCT": 75, "CLOCK_PERIOD": 45}

            result = await runner.run(work_dir)

            # 1 prior + 2 new = 3 total
            assert result.total_evals == 3

            tsv_lines = (work_dir / "results.tsv").read_text().strip().splitlines()
            assert len(tsv_lines) == 4  # header + 3 data rows


# ---------------------------------------------------------------------------
# TSV column tests
# ---------------------------------------------------------------------------


class TestTsvColumns:
    def test_measurement_cols_defined(self):
        assert "wns_worst_ns" in _DIGITAL_MEASUREMENT_COLS
        assert "cell_count" in _DIGITAL_MEASUREMENT_COLS
        assert "die_area_um2" in _DIGITAL_MEASUREMENT_COLS
        assert "power_mw" in _DIGITAL_MEASUREMENT_COLS
        assert "wire_length_um" in _DIGITAL_MEASUREMENT_COLS

    def test_header_has_digital_cols(self, design, tmp_path):
        runner = DigitalAutoresearchRunner(design=design)
        tsv_logger = runner._make_tsv_logger(tmp_path / "test.tsv")
        tsv_logger.write_header()
        header = (tmp_path / "test.tsv").read_text().strip()
        for col in _DIGITAL_MEASUREMENT_COLS:
            assert col in header
