"""Tests for the autoresearch runner.

Unit tests (no LLM or SPICE needed):
    pytest tests/test_autoresearch_runner.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from eda_agents.agents.autoresearch_runner import AutoresearchRunner
from eda_agents.agents.phase_results import AutoresearchResult
from eda_agents.core.spice_runner import SpiceResult
from eda_agents.topologies.ota_gf180 import GF180OTATopology


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def topology():
    return GF180OTATopology()


@pytest.fixture
def runner(topology):
    return AutoresearchRunner(
        topology=topology,
        model="test-model",
        budget=5,
        top_n=2,
    )


# ---------------------------------------------------------------------------
# AutoresearchResult dataclass tests
# ---------------------------------------------------------------------------


class TestAutoresearchResult:
    def test_basic_result(self):
        r = AutoresearchResult(
            best_params={"Ibias_uA": 200},
            best_fom=1e20,
            best_valid=True,
            total_evals=10,
            kept=3,
            discarded=7,
        )
        assert r.improvement_rate == 0.3
        assert "10 evals" in r.summary
        assert "3 kept" in r.summary

    def test_empty_result(self):
        r = AutoresearchResult(
            best_params={},
            best_fom=0.0,
            best_valid=False,
            total_evals=0,
            kept=0,
            discarded=0,
        )
        assert r.improvement_rate == 0.0
        assert r.validity_rate == 0.0

    def test_validity_rate(self):
        r = AutoresearchResult(
            best_params={},
            best_fom=1.0,
            best_valid=True,
            total_evals=4,
            kept=1,
            discarded=3,
            history=[
                {"valid": True},
                {"valid": False},
                {"valid": True},
                {"valid": False},
            ],
        )
        assert r.validity_rate == 0.5

    def test_top_n_stored(self):
        r = AutoresearchResult(
            best_params={"x": 1},
            best_fom=10.0,
            best_valid=True,
            total_evals=5,
            kept=2,
            discarded=3,
            top_n=[
                {"params": {"x": 1}, "fom": 10.0},
                {"params": {"x": 2}, "fom": 8.0},
            ],
        )
        assert len(r.top_n) == 2
        assert r.top_n[0]["fom"] > r.top_n[1]["fom"]


# ---------------------------------------------------------------------------
# program.md tests
# ---------------------------------------------------------------------------


class TestProgramMd:
    def test_initial_program_created(self, runner, tmp_path):
        """program.md should be created with topology metadata."""
        program_path = runner._init_program(tmp_path)
        assert program_path.is_file()

        content = program_path.read_text()
        assert "## Goal" in content
        assert "## Metrics" in content
        assert "## Design Space" in content
        assert "## Current Best" in content
        assert "## Strategy" in content
        assert "## Learned So Far" in content
        assert "## Rules" in content

        # Autoresearch philosophy baked in
        assert "NEVER STOP" in content
        assert "Crashes" in content
        assert "autonomous" in content.lower()

    def test_program_contains_topology_info(self, runner, tmp_path):
        """program.md should contain topology-specific information."""
        program_path = runner._init_program(tmp_path)
        content = program_path.read_text()

        assert runner.topology.topology_name() in content
        for name in runner.topology.design_space():
            assert name in content
        assert "dB" in content

    def test_program_not_overwritten_on_resume(self, runner, tmp_path):
        """Existing program.md should not be replaced."""
        program_path = tmp_path / "program.md"
        program_path.write_text("# Custom program\nDo not overwrite me.")

        loaded = runner._init_program(tmp_path)
        assert "Do not overwrite me" in loaded.read_text()

    def test_update_best(self, runner, tmp_path):
        """Updating best should change the Current Best section."""
        program_path = runner._init_program(tmp_path)

        entry = {
            "eval": 4,
            "fom": 6.02e20,
            "params": {"Ibias_uA": 200, "L_dp_um": 2},
            "Adc_dB": 52.0,
            "GBW_Hz": 3.87e6,
            "PM_deg": 73.7,
        }
        runner._update_program_best(program_path, entry)

        content = program_path.read_text()
        assert "Eval #4" in content
        assert "6.02e+20" in content
        assert "52.0" in content
        assert "No valid design" not in content

    def test_update_learning(self, runner, tmp_path):
        """Adding a learning should append to Learned So Far section."""
        program_path = runner._init_program(tmp_path)

        runner._update_program_learning(program_path, "Higher Ibias improves gain")
        runner._update_program_learning(program_path, "L_load=5um is a sweet spot")

        content = program_path.read_text()
        assert "Higher Ibias improves gain" in content
        assert "L_load=5um is a sweet spot" in content
        assert "(empty" not in content

    def test_update_strategy(self, runner, tmp_path):
        """Strategy section should be replaceable."""
        program_path = runner._init_program(tmp_path)

        runner._update_program_strategy(program_path, "Focus on L_dp sweep next.")
        content = program_path.read_text()
        assert "Focus on L_dp sweep next" in content


# ---------------------------------------------------------------------------
# Prompt generation tests (no LLM needed)
# ---------------------------------------------------------------------------


class TestPromptGeneration:
    def test_system_prompt_contains_program(self, runner, tmp_path):
        program_path = runner._init_program(tmp_path)
        program_content = runner._read_program(program_path)
        prompt = runner._system_prompt(program_content)
        assert "autonomous" in prompt.lower()
        assert "JSON" in prompt
        assert "## Goal" in prompt

    def test_system_prompt_skills_precede_program(
        self, runner, tmp_path, monkeypatch
    ):
        """S10c contract: skill text appears before program.md content."""
        monkeypatch.delenv("EDA_AGENTS_INJECT_SKILLS", raising=False)
        program_path = runner._init_program(tmp_path)
        program_content = runner._read_program(program_path)
        prompt = runner._system_prompt(program_content)
        # GF180 topology declares ["analog.gmid_sizing"] in S10b. The
        # rendered skill body contains the phrase "gm/ID methodology".
        skill_idx = prompt.find("gm/ID methodology")
        goal_idx = prompt.find("## Goal")
        assert skill_idx >= 0, "skill body missing from injected prompt"
        assert goal_idx > skill_idx, "skill must be rendered before program.md"

    def test_system_prompt_escape_hatch(self, runner, tmp_path, monkeypatch):
        """EDA_AGENTS_INJECT_SKILLS=0 restores the pre-S10c prompt."""
        monkeypatch.setenv("EDA_AGENTS_INJECT_SKILLS", "0")
        program_path = runner._init_program(tmp_path)
        program_content = runner._read_program(program_path)
        prompt = runner._system_prompt(program_content)
        assert "gm/ID methodology" not in prompt
        # Program content must still be present.
        assert "## Goal" in prompt
        assert "JSON" in prompt

    def test_proposal_prompt_no_history(self, runner):
        prompt = runner._build_proposal_prompt([], None, 1)
        assert "1/" in prompt
        assert "No valid design" in prompt
        assert "Budget remaining" in prompt

    def test_proposal_prompt_with_best(self, runner):
        best = {
            "eval": 3,
            "fom": 5e20,
            "valid": True,
            "params": {"Ibias_uA": 200, "L_dp_um": 2},
            "Adc_dB": 52.0,
            "GBW_Hz": 3.87e6,
            "PM_deg": 73.7,
        }
        prompt = runner._build_proposal_prompt([], best, 5)
        assert "Current best" in prompt
        assert "#3" in prompt
        assert "52.0" in prompt

    def test_proposal_prompt_with_history(self, runner):
        history = [
            {
                "eval": 1,
                "fom": 1e20,
                "valid": True,
                "violations": [],
                "kept": True,
                "status": "kept",
                "params": {"Ibias_uA": 100},
            },
            {
                "eval": 2,
                "fom": 5e19,
                "valid": False,
                "violations": ["Adc=30dB < 40dB"],
                "kept": False,
                "status": "discarded",
                "params": {"Ibias_uA": 50},
            },
        ]
        prompt = runner._build_proposal_prompt(history, history[0], 3)
        assert "kept" in prompt
        assert "discarded" in prompt
        assert "INVALID" in prompt
        assert "Adc=30dB" in prompt

    def test_history_limited_to_last_20(self, runner):
        history = [
            {
                "eval": i + 1,
                "fom": float(i),
                "valid": True,
                "violations": [],
                "kept": i % 5 == 0,
                "status": "kept" if i % 5 == 0 else "discarded",
                "params": {"Ibias_uA": 100 + i},
            }
            for i in range(30)
        ]
        prompt = runner._build_proposal_prompt(history, history[-1], 31)
        assert "#5:" not in prompt
        assert "#30:" in prompt


# ---------------------------------------------------------------------------
# Keep/discard logic tests
# ---------------------------------------------------------------------------


class TestKeepDiscardLogic:
    @pytest.mark.anyio
    async def test_keeps_better_valid_design(self, runner, tmp_path):
        """Verify that a valid design with higher FoM replaces the best."""
        good_result = SpiceResult(
            success=True, Adc_dB=55.0, GBW_Hz=5e6, PM_deg=70.0,
        )

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose, \
             patch("eda_agents.core.spice_runner.SpiceRunner") as mock_runner_cls:

            mock_propose.return_value = runner.topology.default_params()

            mock_instance = MagicMock()
            mock_instance.run_async = AsyncMock(return_value=good_result)
            mock_runner_cls.return_value = mock_instance

            runner.budget = 2
            result = await runner.run(tmp_path / "test")

            assert result.best_valid
            assert result.best_fom > 0
            assert result.kept >= 1

            # program.md should have been updated
            program = (tmp_path / "test" / "program.md").read_text()
            assert "No valid design" not in program

    @pytest.mark.anyio
    async def test_discards_invalid_design(self, runner, tmp_path):
        """Verify invalid designs don't become best."""
        bad_result = SpiceResult(
            success=True, Adc_dB=20.0, GBW_Hz=100e3, PM_deg=30.0,
        )

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose, \
             patch("eda_agents.core.spice_runner.SpiceRunner") as mock_runner_cls:

            mock_propose.return_value = runner.topology.default_params()

            mock_instance = MagicMock()
            mock_instance.run_async = AsyncMock(return_value=bad_result)
            mock_runner_cls.return_value = mock_instance

            runner.budget = 3
            result = await runner.run(tmp_path / "test")

            assert not result.best_valid
            assert result.kept == 0
            assert result.discarded == 3

    @pytest.mark.anyio
    async def test_keeps_only_improvements(self, runner, tmp_path):
        """Second valid design with lower FoM should be discarded."""
        results_sequence = [
            SpiceResult(success=True, Adc_dB=55.0, GBW_Hz=5e6, PM_deg=70.0),
            SpiceResult(success=True, Adc_dB=45.0, GBW_Hz=1e6, PM_deg=60.0),
            SpiceResult(success=True, Adc_dB=60.0, GBW_Hz=8e6, PM_deg=65.0),
        ]
        call_count = {"n": 0}

        async def side_effect(*args, **kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            return results_sequence[idx]

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose, \
             patch("eda_agents.core.spice_runner.SpiceRunner") as mock_runner_cls:

            mock_propose.return_value = runner.topology.default_params()

            mock_instance = MagicMock()
            mock_instance.run_async = AsyncMock(side_effect=side_effect)
            mock_runner_cls.return_value = mock_instance

            runner.budget = 3
            result = await runner.run(tmp_path / "test")

            assert result.kept == 2
            assert result.discarded == 1
            assert result.total_evals == 3

    @pytest.mark.anyio
    async def test_handles_simulation_failure(self, runner, tmp_path):
        """Simulation failure should be logged as crash and skipped."""
        fail_result = SpiceResult(success=False, error="ngspice crashed")

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose, \
             patch("eda_agents.core.spice_runner.SpiceRunner") as mock_runner_cls:

            mock_propose.return_value = runner.topology.default_params()

            mock_instance = MagicMock()
            mock_instance.run_async = AsyncMock(return_value=fail_result)
            mock_runner_cls.return_value = mock_instance

            runner.budget = 2
            result = await runner.run(tmp_path / "test")

            assert not result.best_valid
            assert result.kept == 0

            # Check TSV has crash status
            tsv = (tmp_path / "test" / "results.tsv").read_text()
            assert "crash" in tsv

    @pytest.mark.anyio
    async def test_crash_exception_handled(self, runner, tmp_path):
        """Exception during evaluation should be caught and logged as crash."""
        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose, \
             patch("eda_agents.core.spice_runner.SpiceRunner") as mock_runner_cls:

            mock_propose.return_value = runner.topology.default_params()

            mock_instance = MagicMock()
            mock_instance.run_async = AsyncMock(side_effect=RuntimeError("segfault"))
            mock_runner_cls.return_value = mock_instance

            runner.budget = 2
            result = await runner.run(tmp_path / "test")

            # Should not raise, should complete all evals
            assert result.total_evals == 2
            assert result.kept == 0


# ---------------------------------------------------------------------------
# TSV logging tests
# ---------------------------------------------------------------------------


class TestTSVLogging:
    @pytest.mark.anyio
    async def test_tsv_created(self, runner, tmp_path):
        """TSV file should be created with header and data rows."""
        good_result = SpiceResult(
            success=True, Adc_dB=50.0, GBW_Hz=2e6, PM_deg=60.0,
        )

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose, \
             patch("eda_agents.core.spice_runner.SpiceRunner") as mock_runner_cls:

            mock_propose.return_value = runner.topology.default_params()

            mock_instance = MagicMock()
            mock_instance.run_async = AsyncMock(return_value=good_result)
            mock_runner_cls.return_value = mock_instance

            runner.budget = 3
            result = await runner.run(tmp_path / "test")

            tsv_path = Path(result.tsv_path)
            assert tsv_path.is_file()

            lines = tsv_path.read_text().strip().splitlines()
            assert len(lines) == 4  # header + 3 data rows

            header = lines[0]
            for name in runner.topology.design_space():
                assert name in header
            assert "status" in header

    def test_tsv_header_format(self, runner, tmp_path):
        """Verify TSV header matches design space."""
        tsv_path = tmp_path / "test.tsv"
        runner._write_tsv_header(tsv_path)

        header = tsv_path.read_text().strip()
        assert header.startswith("eval\t")
        assert header.endswith("status")

    def test_tsv_row_append(self, runner, tmp_path):
        """Verify TSV row is appended correctly."""
        tsv_path = tmp_path / "test.tsv"
        runner._write_tsv_header(tsv_path)

        entry = {
            "eval": 1,
            "params": runner.topology.default_params(),
            "Adc_dB": 52.0,
            "GBW_Hz": 3.87e6,
            "PM_deg": 73.7,
            "fom": 6.02e20,
            "valid": True,
            "kept": True,
            "status": "kept",
        }
        runner._append_tsv_row(tsv_path, entry)

        lines = tsv_path.read_text().strip().splitlines()
        assert len(lines) == 2
        data = lines[1].split("\t")
        assert data[0] == "1"
        assert data[-1] == "kept"


# ---------------------------------------------------------------------------
# Resume tests
# ---------------------------------------------------------------------------


class TestResume:
    def test_load_empty_history(self, runner, tmp_path):
        """No TSV file -> empty history."""
        history, best, start = runner._load_history(tmp_path / "nope.tsv")
        assert history == []
        assert best is None
        assert start == 1

    def test_load_existing_history(self, runner, tmp_path):
        """Load history from a previous run's TSV."""
        tsv_path = tmp_path / "results.tsv"
        runner._write_tsv_header(tsv_path)

        entries = [
            {"eval": 1, "params": runner.topology.default_params(),
             "Adc_dB": 50.0, "GBW_Hz": 2e6, "PM_deg": 60.0,
             "fom": 1e20, "valid": True, "status": "kept"},
            {"eval": 2, "params": runner.topology.default_params(),
             "Adc_dB": 30.0, "GBW_Hz": 500e3, "PM_deg": 40.0,
             "fom": 5e19, "valid": False, "status": "discarded"},
        ]
        for e in entries:
            runner._append_tsv_row(tsv_path, e)

        history, best, start = runner._load_history(tsv_path)
        assert len(history) == 2
        assert best is not None
        assert best["eval"] == 1
        assert start == 3  # resume from eval 3

    @pytest.mark.anyio
    async def test_resume_continues_from_last_eval(self, runner, tmp_path):
        """If work_dir has prior results, resume from next eval number."""
        work_dir = tmp_path / "test"
        work_dir.mkdir()

        # Create program.md and TSV with 2 prior evals
        runner._init_program(work_dir)
        tsv_path = work_dir / "results.tsv"
        runner._write_tsv_header(tsv_path)
        runner._append_tsv_row(tsv_path, {
            "eval": 1, "params": runner.topology.default_params(),
            "Adc_dB": 50.0, "GBW_Hz": 2e6, "PM_deg": 60.0,
            "fom": 1e20, "valid": True, "status": "kept",
        })
        runner._append_tsv_row(tsv_path, {
            "eval": 2, "params": runner.topology.default_params(),
            "Adc_dB": 45.0, "GBW_Hz": 1e6, "PM_deg": 55.0,
            "fom": 5e19, "valid": True, "status": "discarded",
        })

        good_result = SpiceResult(
            success=True, Adc_dB=55.0, GBW_Hz=5e6, PM_deg=70.0,
        )

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose, \
             patch("eda_agents.core.spice_runner.SpiceRunner") as mock_runner_cls:

            mock_propose.return_value = runner.topology.default_params()
            mock_instance = MagicMock()
            mock_instance.run_async = AsyncMock(return_value=good_result)
            mock_runner_cls.return_value = mock_instance

            runner.budget = 3  # 3 more evals (3, 4, 5)
            result = await runner.run(work_dir)

            # Total should be 2 prior + 3 new = 5
            assert result.total_evals == 5

            # TSV should have 5 data rows + header
            lines = tsv_path.read_text().strip().splitlines()
            assert len(lines) == 6


# ---------------------------------------------------------------------------
# Top-N extraction tests
# ---------------------------------------------------------------------------


class TestTopNExtraction:
    @pytest.mark.anyio
    async def test_top_n_sorted_by_fom(self, runner, tmp_path):
        """Top-N should be sorted by FoM descending."""
        results_sequence = [
            SpiceResult(success=True, Adc_dB=50.0, GBW_Hz=2e6, PM_deg=60.0),
            SpiceResult(success=True, Adc_dB=55.0, GBW_Hz=5e6, PM_deg=70.0),
            SpiceResult(success=True, Adc_dB=52.0, GBW_Hz=3e6, PM_deg=65.0),
        ]
        call_count = {"n": 0}

        async def side_effect(*args, **kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            return results_sequence[idx]

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose, \
             patch("eda_agents.core.spice_runner.SpiceRunner") as mock_runner_cls:

            mock_propose.return_value = runner.topology.default_params()

            mock_instance = MagicMock()
            mock_instance.run_async = AsyncMock(side_effect=side_effect)
            mock_runner_cls.return_value = mock_instance

            runner.budget = 3
            runner.top_n = 2
            result = await runner.run(tmp_path / "test")

            assert len(result.top_n) == 2
            assert result.top_n[0]["fom"] >= result.top_n[1]["fom"]


# ---------------------------------------------------------------------------
# LLM fallback tests
# ---------------------------------------------------------------------------


class TestLLMFallback:
    @pytest.mark.anyio
    async def test_fallback_on_llm_error(self, runner, tmp_path):
        """If LLM fails, should fall back to default params."""
        good_result = SpiceResult(
            success=True, Adc_dB=50.0, GBW_Hz=2e6, PM_deg=60.0,
        )

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose, \
             patch("eda_agents.core.spice_runner.SpiceRunner") as mock_runner_cls:

            mock_propose.side_effect = [
                RuntimeError("API timeout"),
                runner.topology.default_params(),
            ]

            mock_instance = MagicMock()
            mock_instance.run_async = AsyncMock(return_value=good_result)
            mock_runner_cls.return_value = mock_instance

            runner.budget = 2
            result = await runner.run(tmp_path / "test")

            assert result.total_evals == 2


# ---------------------------------------------------------------------------
# TrackDOrchestrator integration (mode validation)
# ---------------------------------------------------------------------------


class TestOrchestratorModes:
    def test_invalid_mode_rejected(self, tmp_path):
        from eda_agents.agents.adk_agents import TrackDOrchestrator
        with pytest.raises(ValueError, match="exploration_mode"):
            TrackDOrchestrator(
                project_dir=tmp_path,
                topology=GF180OTATopology(),
                exploration_mode="invalid",
            )

    def test_valid_modes_accepted(self, tmp_path):
        from eda_agents.agents.adk_agents import TrackDOrchestrator
        for mode in ("adk", "autoresearch", "hybrid"):
            orch = TrackDOrchestrator(
                project_dir=tmp_path,
                topology=GF180OTATopology(),
                exploration_mode=mode,
            )
            assert orch.exploration_mode == mode
