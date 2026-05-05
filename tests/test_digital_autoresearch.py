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
    _detect_librelane_venv_pythonpath,
    detect_nix_eda_tool_dirs,
)
from eda_agents.core.digital_design import DEFAULT_PPA_COLUMNS
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

    # Domain-agnostic API: design declares its measurement columns and
    # extracts them from the StageResults bag the runner builds. This
    # mock mirrors the default ABC behavior so the runner sees a real
    # dict (not a MagicMock auto-generated value).
    design.measurement_columns.return_value = list(DEFAULT_PPA_COLUMNS)

    def extract_measurements(stage_results):
        m = stage_results.flow_metrics
        if m is None:
            return {col: None for col in DEFAULT_PPA_COLUMNS}
        return {
            "wns_worst_ns": m.wns_worst_ns,
            "cell_count": m.synth_cell_count,
            "die_area_um2": m.die_area_um2,
            "power_mw": m.power_total_mw,
            "wire_length_um": m.wire_length_um,
        }

    def compute_fom(measurements):
        wns = measurements.get("wns_worst_ns")
        if wns is not None and wns >= 0:
            return wns + 1.0
        return 0.0

    def check_validity(measurements):
        violations = []
        wns = measurements.get("wns_worst_ns")
        if wns is not None and wns < 0:
            violations.append("Timing not closed")
        return (len(violations) == 0, violations)

    design.extract_measurements.side_effect = extract_measurements
    design.compute_fom.side_effect = compute_fom
    design.check_validity.side_effect = check_validity
    # Anti-centroid seed: by default the mock has no baseline so the
    # runner takes the LLM-only path (matches every legacy test's
    # implicit expectation). Tests that exercise the seed override
    # this in-place.
    design.baseline_params.return_value = {}
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

    def test_system_prompt_skills_precede_program(self, design, monkeypatch):
        """S10c contract: skill text appears before program.md content."""
        monkeypatch.delenv("EDA_AGENTS_INJECT_SKILLS", raising=False)
        design.relevant_skills.return_value = ["digital.synthesis"]
        runner = DigitalAutoresearchRunner(design=design)
        prompt = runner._system_prompt("## Goal\nTest")
        # digital.synthesis skill body opens with "You are a synthesis engineer".
        skill_marker = "You are a synthesis engineer"
        skill_idx = prompt.find(skill_marker)
        goal_idx = prompt.find("## Goal")
        assert skill_idx >= 0, "digital.synthesis body missing"
        assert goal_idx > skill_idx, "skill must be rendered before program.md"

    def test_system_prompt_escape_hatch(self, design, monkeypatch):
        """EDA_AGENTS_INJECT_SKILLS=0 restores the pre-S10c prompt."""
        monkeypatch.setenv("EDA_AGENTS_INJECT_SKILLS", "0")
        design.relevant_skills.return_value = ["digital.synthesis"]
        runner = DigitalAutoresearchRunner(design=design)
        prompt = runner._system_prompt("## Goal\nTest")
        assert "You are a synthesis engineer" not in prompt
        assert "## Goal" in prompt

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
    async def test_llm_failure_fallback_uses_baseline(self, design, tmp_path):
        """LLM failure should fall back to baseline_params, not the
        centroid-of-design-space midpoint that ``default_config()``
        returns. The baseline anchor is what makes the fallback safe;
        without it the fallback re-introduces the centroid bias the
        seed was specifically designed to remove."""
        # Pin a non-midpoint baseline so we can distinguish from
        # default_config (whose midpoint for the discrete fixture is
        # PL=65, CLOCK=40 — too close to the test value).
        design.baseline_params.return_value = {
            "PL_TARGET_DENSITY_PCT": 75,
            "CLOCK_PERIOD": 50,
        }
        metrics_path = _make_mock_metrics_file(tmp_path)
        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=2,
            use_mock_metrics=metrics_path,
            dedup=False,
        )

        with patch.object(runner, "_propose_params", new_callable=AsyncMock) as mock_propose:
            # Eval 1 will be the seeded baseline (no LLM call). Eval 2's
            # LLM call raises, exercising the fallback path.
            mock_propose.side_effect = [RuntimeError("API timeout")]

            result = await runner.run(tmp_path / "run")
            # 1 seeded eval + 1 LLM-failed-eval (baseline fallback) = 2
            assert result.total_evals == 2

            # The TSV's eval-2 row must carry the baseline (75 / 50),
            # not the design_space midpoint (65 / 40).
            tsv_lines = (tmp_path / "run" / "results.tsv").read_text().strip().splitlines()
            # header + 2 data rows
            assert len(tsv_lines) == 3
            eval2 = tsv_lines[2].split("\t")
            # Header layout: eval, PL_TARGET_DENSITY_PCT, CLOCK_PERIOD,
            # ...measurements..., fom, valid, status
            assert eval2[0] == "2"
            assert float(eval2[1]) == 75.0  # PL_TARGET_DENSITY_PCT
            assert float(eval2[2]) == 50.0  # CLOCK_PERIOD

    @pytest.mark.anyio
    async def test_crash_handled(self, design, tmp_path):
        """Evaluation crash should be caught, not stop the loop.

        ``run_rtl_sim=False`` skips the cocotb gate so the test can
        focus on ``_evaluate``'s crash-handling path. With the gate
        on, the mock design's MagicMock testbench would short-circuit
        before ``_evaluate`` runs.
        """
        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=2,
            run_rtl_sim=False,
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


class TestProposalTemperature:
    """Coverage for ``proposal_temperature``.

    Regression guard for the gpt-5* / o1 / o3 incompatibility with
    ``temperature=0.7`` (LiteLLM raises
    ``UnsupportedParamsError`` and the autoresearch proposal call
    fails, forcing a fallback that breaks the RL-emulated feedback).
    """

    def test_default_temperature(self) -> None:
        from eda_agents.agents._autoresearch_core import proposal_temperature
        assert proposal_temperature(None) == 0.7
        assert proposal_temperature("") == 0.7
        assert proposal_temperature("anthropic/claude-sonnet-4-6") == 0.7
        assert proposal_temperature("openrouter/google/gemini-2.5-flash") == 0.7
        assert proposal_temperature("openai/gpt-4-turbo") == 0.7

    def test_gpt5_models_get_temperature_one(self) -> None:
        from eda_agents.agents._autoresearch_core import proposal_temperature
        # Plain OpenAI namespacing.
        assert proposal_temperature("openai/gpt-5-codex") == 1.0
        assert proposal_temperature("openai/gpt-5.1-codex") == 1.0
        assert proposal_temperature("openai/gpt-5.2") == 1.0
        assert proposal_temperature("openai/gpt-5.3-codex") == 1.0
        assert proposal_temperature("openai/gpt-5.4") == 1.0
        # OpenRouter / opencode-style nested namespacing.
        assert proposal_temperature("openrouter/openai/gpt-5.3-codex") == 1.0
        # Bare model id.
        assert proposal_temperature("gpt-5-codex") == 1.0

    def test_reasoning_o1_o3_get_temperature_one(self) -> None:
        from eda_agents.agents._autoresearch_core import proposal_temperature
        assert proposal_temperature("openai/o1-preview") == 1.0
        assert proposal_temperature("openai/o3-mini") == 1.0
        # Plain "o1" without slash should not match (ambiguous, e.g.
        # ``mistral/mistral-o1-foo`` in some hypothetical future
        # namespace would be a false positive); the marker is "/o1".
        assert proposal_temperature("co1lor-fake") == 0.7


class TestPrependNixTools:
    """Coverage for ``DigitalAutoresearchRunner._prepend_nix_tools``.

    Regression guard for the ``ModuleNotFoundError: No module named
    'click'`` Yosys-from-Nix issue: when LibreLane is invoked from
    autoresearch (no agent prompt path), the env_extra dict must
    carry both PATH (Nix tool dirs) and PYTHONPATH (LibreLane venv
    site-packages).
    """

    def test_pythonpath_helper_returns_list(self) -> None:
        # Non-throwing on any host; returns a list of dirs that exist.
        out = _detect_librelane_venv_pythonpath()
        assert isinstance(out, list)
        for p in out:
            assert isinstance(p, str)

    def test_prepend_nix_tools_sets_pythonpath_when_available(
        self, monkeypatch
    ) -> None:
        # Force both detectors to return non-empty so we can assert
        # on the exact dict shape regardless of host.
        monkeypatch.setattr(
            "eda_agents.agents.digital_autoresearch.detect_nix_eda_tool_dirs",
            lambda: ["/fake/yosys/bin", "/fake/openroad/bin"],
        )
        monkeypatch.setattr(
            "eda_agents.agents.digital_autoresearch._detect_librelane_venv_pythonpath",
            lambda: ["/fake/venv/lib/python3.12/site-packages"],
        )
        env: dict[str, str] = {}
        DigitalAutoresearchRunner._prepend_nix_tools(env)
        assert "PATH" in env
        assert env["PATH"].startswith("/fake/yosys/bin:/fake/openroad/bin:")
        assert "PYTHONPATH" in env
        assert env["PYTHONPATH"].startswith(
            "/fake/venv/lib/python3.12/site-packages"
        )

    def test_prepend_nix_tools_skips_pythonpath_when_no_venv(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "eda_agents.agents.digital_autoresearch.detect_nix_eda_tool_dirs",
            lambda: ["/fake/yosys/bin"],
        )
        monkeypatch.setattr(
            "eda_agents.agents.digital_autoresearch._detect_librelane_venv_pythonpath",
            lambda: [],
        )
        env: dict[str, str] = {}
        DigitalAutoresearchRunner._prepend_nix_tools(env)
        assert "PATH" in env
        assert "PYTHONPATH" not in env


class TestNixEdaToolDetection:
    """Coverage for ``detect_nix_eda_tool_dirs``.

    The detector is a thin glob over /nix/store. The behaviour we
    care about per-host is:

    * On a host that has a Nix-built LibreLane install (yosys +
      openroad + opensta + magic + netgen + klayout), every tool is
      represented exactly once.
    * On a host without Nix, the function returns an empty list
      without raising.

    We also enforce the full tool ordering via a tmp-fixture host so
    the ``opensta`` slot is wired (regression guard for the
    ``FileNotFoundError: 'sta'`` LibreLane regression).
    """

    def test_returns_list(self) -> None:
        # Non-throwing on any host.
        assert isinstance(detect_nix_eda_tool_dirs(), list)

    def test_simulated_full_install(self, tmp_path, monkeypatch) -> None:
        # Build a fake Nix store with one of each tool. opensta also
        # needs a working ``sta`` binary so the version-probe picker
        # ranks it.
        store = tmp_path / "store"
        names = [
            "abc-yosys-with-plugins-0.60",
            "def-openroad-2025-06-12-python3",
            "ghi-opensta",
            "jkl-magic-vlsi-8.3.515",
            "mno-netgen-1.5.295",
            "pqr-klayout-0.30.4",
        ]
        for name in names:
            (store / name / "bin").mkdir(parents=True)
        # Stub ``sta`` to print a known version string when probed.
        import os as _os

        sta = store / "ghi-opensta" / "bin" / "sta"
        sta.write_text("#!/bin/sh\necho '2.7.0'\n")
        _os.chmod(sta, 0o755)

        # Patch glob's lookup root: easier than vendoring the function,
        # we monkeypatch the function-local glob.glob to anchor at our
        # fake store.
        import glob as _glob

        real_glob = _glob.glob

        def fake_glob(pattern, *args, **kwargs):
            if pattern.startswith("/nix/store/"):
                rewritten = pattern.replace("/nix/store/", str(store) + "/")
                return real_glob(rewritten, *args, **kwargs)
            return real_glob(pattern, *args, **kwargs)

        monkeypatch.setattr(_glob, "glob", fake_glob)
        dirs = detect_nix_eda_tool_dirs()
        # Six entries, ordered yosys, openroad, opensta, magic, netgen,
        # klayout. The exact paths mirror the fake store.
        assert len(dirs) == 6
        assert all(d.startswith(str(store)) for d in dirs)
        assert any("opensta" in d for d in dirs), (
            "opensta must appear in the detected tool dirs (LibreLane "
            "STAPrePNR / STAPostPNR shell out to a standalone 'sta' "
            "binary; without this glob the flow fails with "
            "FileNotFoundError: 'sta')."
        )
        assert dirs[2].endswith("ghi-opensta/bin")  # ordering check

    def test_higher_opensta_version_preferred(
        self, tmp_path, monkeypatch
    ) -> None:
        # Two opensta candidates with stub ``sta`` binaries: one
        # reports 2.6.0, the other 2.7.0. The picker probes
        # ``sta -version`` and must pick the 2.7.0 one because 2.6.0
        # lacks ``report_checks -group_path_count`` (real-host
        # regression that blocked phase 2 STAPrePNR).
        store = tmp_path / "store"
        old = store / "abc-opensta-old"
        new = store / "def-opensta-new"
        for d, ver in [(old, "2.6.0"), (new, "2.7.0")]:
            (d / "bin").mkdir(parents=True)
            sta = d / "bin" / "sta"
            sta.write_text(f"#!/bin/sh\necho '{ver}'\n")
            import os as _os

            _os.chmod(sta, 0o755)

        import glob as _glob

        real_glob = _glob.glob

        def fake_glob(pattern, *args, **kwargs):
            if pattern.startswith("/nix/store/"):
                rewritten = pattern.replace("/nix/store/", str(store) + "/")
                return real_glob(rewritten, *args, **kwargs)
            return real_glob(pattern, *args, **kwargs)

        monkeypatch.setattr(_glob, "glob", fake_glob)
        dirs = detect_nix_eda_tool_dirs()
        opensta_hits = [d for d in dirs if "opensta" in d]
        assert len(opensta_hits) == 1
        assert opensta_hits[0].endswith("def-opensta-new/bin")

    def test_higher_openroad_date_preferred(
        self, tmp_path, monkeypatch
    ) -> None:
        # Two openroad candidates with date-stamped store paths;
        # picker must select the later YYYY-MM-DD. Hash-reverse-sort
        # would otherwise land on a stale 2025-06 build that lacks
        # the ``est::`` Tcl namespace LibreLane STAMidPnR uses.
        store = tmp_path / "store"
        for name in ["abc-openroad-2026-02-17", "def-openroad-2025-06-12"]:
            (store / name / "bin").mkdir(parents=True)

        import glob as _glob

        real_glob = _glob.glob

        def fake_glob(pattern, *args, **kwargs):
            if pattern.startswith("/nix/store/"):
                rewritten = pattern.replace("/nix/store/", str(store) + "/")
                return real_glob(rewritten, *args, **kwargs)
            return real_glob(pattern, *args, **kwargs)

        monkeypatch.setattr(_glob, "glob", fake_glob)
        dirs = detect_nix_eda_tool_dirs()
        openroad_hits = [d for d in dirs if "openroad" in d]
        assert len(openroad_hits) == 1
        assert openroad_hits[0].endswith("abc-openroad-2026-02-17/bin")

    def test_openroad_non_env_preferred_over_env_at_same_date(
        self, tmp_path, monkeypatch
    ) -> None:
        # Two openroad builds at the same date, one bare and one with
        # a python env suffix. The bare flavour is preferred because
        # the python-env flavour drags a different python version
        # into PATH that conflicts with the LibreLane venv.
        store = tmp_path / "store"
        for name in [
            "aaa-openroad-2026-02-17",
            "zzz-openroad-2026-02-17-python3-3.13.9-env",
        ]:
            (store / name / "bin").mkdir(parents=True)

        import glob as _glob

        real_glob = _glob.glob

        def fake_glob(pattern, *args, **kwargs):
            if pattern.startswith("/nix/store/"):
                rewritten = pattern.replace("/nix/store/", str(store) + "/")
                return real_glob(rewritten, *args, **kwargs)
            return real_glob(pattern, *args, **kwargs)

        monkeypatch.setattr(_glob, "glob", fake_glob)
        dirs = detect_nix_eda_tool_dirs()
        openroad_hits = [d for d in dirs if "openroad" in d]
        assert len(openroad_hits) == 1
        assert openroad_hits[0].endswith("aaa-openroad-2026-02-17/bin")


class TestTsvColumns:
    def test_default_ppa_columns_defined(self):
        # Domain-agnostic API: the canonical PPA tuple lives on
        # ``digital_design`` (single source of truth) and any design
        # that doesn't override ``measurement_columns()`` ships these
        # five names to the TSV.
        assert "wns_worst_ns" in DEFAULT_PPA_COLUMNS
        assert "cell_count" in DEFAULT_PPA_COLUMNS
        assert "die_area_um2" in DEFAULT_PPA_COLUMNS
        assert "power_mw" in DEFAULT_PPA_COLUMNS
        assert "wire_length_um" in DEFAULT_PPA_COLUMNS

    def test_header_uses_design_measurement_columns(self, design, tmp_path):
        runner = DigitalAutoresearchRunner(design=design)
        tsv_logger = runner._make_tsv_logger(tmp_path / "test.tsv")
        tsv_logger.write_header()
        header = (tmp_path / "test.tsv").read_text().strip()
        for col in design.measurement_columns():
            assert col in header

    def test_runner_reads_columns_from_design(self, design):
        # Domain-agnostic invariant: the runner copies whatever the
        # design declares in ``measurement_columns()`` into its own
        # ``measurement_cols`` list. Future designs that add columns
        # therefore propagate without a runner edit.
        runner = DigitalAutoresearchRunner(design=design)
        assert runner.measurement_cols == list(design.measurement_columns())


# ---------------------------------------------------------------------------
# Backend dispatch tests for ``_propose_params`` (strategy=flow)
# ---------------------------------------------------------------------------


def _fake_harness_result(json_payload: dict):
    """Build a HarnessResult-shaped object whose result_text is JSON.

    Both ClaudeCodeHarness and OpenCodeHarness return objects that
    expose ``success``/``result_text``/``error``; we only need those
    three to exercise the dispatch paths.
    """
    from eda_agents.agents.claude_code_harness import HarnessResult

    return HarnessResult(
        success=True,
        result_text=json.dumps(json_payload),
        error=None,
    )


class TestProposalDispatch:
    """Coverage for ``_propose_params`` backend dispatch (strategy=flow).

    Regression guard for the bug where ``backend="cc_cli"`` and
    ``backend="opencode"`` silently fell through to ``litellm.acompletion``
    when ``strategy="flow"``. Concrete user-facing breakage:

    * ``--backend opencode --model openai/gpt-5.3-codex`` shipped the
      proposal call straight to OpenAI's API via LiteLLM, bypassing the
      opencode CLI and the OAuth-managed subscription the user pays
      for. The user explicitly said "codex va mediante opencode, no
      openrouter".
    * ``--backend cc_cli`` routed proposals through whatever default
      LiteLLM model was configured (e.g. gemini-2.5-flash), not Opus
      4.7 under the Claude subscription.
    """

    @pytest.mark.anyio
    async def test_cc_cli_backend_uses_claude_harness(
        self, design, monkeypatch
    ):
        """backend=cc_cli must instantiate ClaudeCodeHarness."""
        instances: list[object] = []
        payload = {"PL_TARGET_DENSITY_PCT": 75, "CLOCK_PERIOD": 45}

        class FakeClaudeCodeHarness:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                instances.append(self)

            async def run(self):
                return _fake_harness_result(payload)

        monkeypatch.setattr(
            "eda_agents.agents.claude_code_harness.ClaudeCodeHarness",
            FakeClaudeCodeHarness,
        )

        runner = DigitalAutoresearchRunner(
            design=design, backend="cc_cli", strategy="flow"
        )
        params = await runner._propose_params(
            "## Goal\nTest\n", history=[], best=None, eval_num=1
        )

        assert len(instances) == 1, "exactly one ClaudeCodeHarness instance"
        # _clamp_params must snap to the discrete grid.
        assert params == {"PL_TARGET_DENSITY_PCT": 75, "CLOCK_PERIOD": 45}
        # The harness was constructed with the design's project_dir
        # and the runner's cli_path / allow_dangerous flags. These
        # are the user-tunable knobs we cannot regress on.
        kw = instances[0].kwargs
        assert kw["work_dir"] == design.project_dir.return_value
        assert kw["allow_dangerous"] is False
        assert kw["cli_path"] == "claude"

    @pytest.mark.anyio
    async def test_opencode_backend_uses_opencode_harness(
        self, design, monkeypatch
    ):
        """backend=opencode must instantiate OpenCodeHarness with the
        configured model so codex-class models route through opencode
        OAuth, NOT through LiteLLM acompletion."""
        instances: list[object] = []
        payload = {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}

        class FakeOpenCodeHarness:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                instances.append(self)

            async def run(self):
                return _fake_harness_result(payload)

        monkeypatch.setattr(
            "eda_agents.agents.opencode_harness.OpenCodeHarness",
            FakeOpenCodeHarness,
        )

        runner = DigitalAutoresearchRunner(
            design=design,
            backend="opencode",
            strategy="flow",
            opencode_model="openai/gpt-5.3-codex",
            opencode_cli_path="/usr/local/bin/opencode",
        )
        params = await runner._propose_params(
            "## Goal\nTest\n", history=[], best=None, eval_num=1
        )

        assert len(instances) == 1
        assert params == {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}
        kw = instances[0].kwargs
        assert kw["model"] == "openai/gpt-5.3-codex"
        assert kw["cli_path"] == "/usr/local/bin/opencode"

    @pytest.mark.anyio
    async def test_litellm_backend_uses_acompletion(
        self, design, monkeypatch
    ):
        """backend=litellm must keep using litellm.acompletion (legacy
        path). Same goes for backend=adk (no autoresearch-side
        difference)."""
        calls: list[dict] = []
        payload = {"PL_TARGET_DENSITY_PCT": 55, "CLOCK_PERIOD": 35}

        class FakeMessage:
            def __init__(self, content):
                self.content = content

        class FakeChoice:
            def __init__(self, content):
                self.message = FakeMessage(content)

        class FakeResponse:
            def __init__(self, content):
                self.choices = [FakeChoice(content)]
                self.usage = {"total_tokens": 42}

        async def fake_acompletion(**kwargs):
            calls.append(kwargs)
            return FakeResponse(json.dumps(payload))

        import litellm

        monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

        runner = DigitalAutoresearchRunner(
            design=design, backend="litellm", strategy="flow",
            model="openrouter/google/gemini-2.5-flash",
        )
        params = await runner._propose_params(
            "## Goal\nTest\n", history=[], best=None, eval_num=1
        )

        assert calls, "litellm.acompletion must be called for backend=litellm"
        assert calls[0]["model"] == "openrouter/google/gemini-2.5-flash"
        assert params == {"PL_TARGET_DENSITY_PCT": 55, "CLOCK_PERIOD": 35}

    @pytest.mark.anyio
    async def test_cc_cli_does_not_call_litellm(
        self, design, monkeypatch
    ):
        """Hard guard: backend=cc_cli must NEVER reach litellm.acompletion.
        Regression for the bug where the user paid for the Claude
        subscription but proposals silently routed through gemini.
        """
        payload = {"PL_TARGET_DENSITY_PCT": 75, "CLOCK_PERIOD": 45}

        class FakeClaudeCodeHarness:
            def __init__(self, **kwargs):
                pass

            async def run(self):
                return _fake_harness_result(payload)

        monkeypatch.setattr(
            "eda_agents.agents.claude_code_harness.ClaudeCodeHarness",
            FakeClaudeCodeHarness,
        )

        async def fail_acompletion(**kwargs):
            raise AssertionError(
                "litellm.acompletion called for backend=cc_cli "
                "(this is the bug we are trying to prevent)"
            )

        import litellm

        monkeypatch.setattr(litellm, "acompletion", fail_acompletion)

        runner = DigitalAutoresearchRunner(
            design=design, backend="cc_cli", strategy="flow"
        )
        await runner._propose_params(
            "## Goal\nTest\n", history=[], best=None, eval_num=1
        )

    @pytest.mark.anyio
    async def test_opencode_does_not_call_litellm(
        self, design, monkeypatch
    ):
        """Hard guard: backend=opencode must NEVER reach litellm.acompletion.
        User explicitly required: codex goes through opencode, not
        OpenAI direct.
        """
        payload = {"PL_TARGET_DENSITY_PCT": 65, "CLOCK_PERIOD": 40}

        class FakeOpenCodeHarness:
            def __init__(self, **kwargs):
                pass

            async def run(self):
                return _fake_harness_result(payload)

        monkeypatch.setattr(
            "eda_agents.agents.opencode_harness.OpenCodeHarness",
            FakeOpenCodeHarness,
        )

        async def fail_acompletion(**kwargs):
            raise AssertionError(
                "litellm.acompletion called for backend=opencode "
                "(codex must route through opencode OAuth, not OpenAI direct)"
            )

        import litellm

        monkeypatch.setattr(litellm, "acompletion", fail_acompletion)

        runner = DigitalAutoresearchRunner(
            design=design,
            backend="opencode",
            strategy="flow",
            opencode_model="openai/gpt-5.3-codex",
        )
        await runner._propose_params(
            "## Goal\nTest\n", history=[], best=None, eval_num=1
        )

    @pytest.mark.anyio
    async def test_cc_cli_failed_run_raises(self, design, monkeypatch):
        """A failed harness call must raise so the upper-loop fallback
        can trigger (it logs the proposal failure and falls back to
        defaults via _clamp_params(default_config()))."""
        from eda_agents.agents.claude_code_harness import HarnessResult

        class FakeClaudeCodeHarness:
            def __init__(self, **kwargs):
                pass

            async def run(self):
                return HarnessResult(
                    success=False,
                    result_text="",
                    error="claude exited with code 1",
                )

        monkeypatch.setattr(
            "eda_agents.agents.claude_code_harness.ClaudeCodeHarness",
            FakeClaudeCodeHarness,
        )

        runner = DigitalAutoresearchRunner(
            design=design, backend="cc_cli", strategy="flow"
        )
        with pytest.raises(RuntimeError, match="CC CLI flow proposal failed"):
            await runner._propose_params(
                "## Goal\nTest\n", history=[], best=None, eval_num=1
            )

    def test_build_flow_proposal_prompt_contains_system_and_user(
        self, design
    ):
        """The CLI prompt builder must concatenate system + user + the
        JSON-only guard suffix. Regression for the case where the
        builder loses one of the three sections."""
        runner = DigitalAutoresearchRunner(
            design=design, backend="cc_cli", strategy="flow"
        )
        prompt = runner._build_flow_proposal_prompt(
            "## Goal\nMaximize FoM\n",
            history=[],
            best=None,
            eval_num=2,
        )
        # System prompt body markers
        assert "PL_TARGET_DENSITY_PCT" in prompt
        # User prompt body markers
        assert "Evaluation 2/" in prompt or "2/" in prompt
        # JSON-only guard suffix
        assert "json.loads" in prompt
        assert "ONLY the raw JSON" in prompt


# ---------------------------------------------------------------------------
# Free-form proposal tests (design space is a hint, not a cage)
# ---------------------------------------------------------------------------


class TestFreeFormProposals:
    """Coverage for the "design space is a starting hint" policy.

    User direction: the LLM must have maximum freedom to experiment.
    The only immutables are the spec (``check_validity``) and the FoM
    (``compute_fom``). Anything else, including LibreLane safe-listed
    config keys not declared in ``design_space()``, must pass through
    ``_clamp_params`` untouched and reach
    ``LibreLaneRunner.modify_config`` for safety validation.
    """

    def test_clamp_params_passes_through_undeclared_keys(self, design):
        """A LibreLane safe-listed key the LLM proposes that is NOT in
        ``design_space()`` must survive ``_clamp_params``. Without
        this, the LLM cannot ever explore beyond the declared
        starting hint."""
        runner = DigitalAutoresearchRunner(design=design)
        params = {
            "PL_TARGET_DENSITY_PCT": 65,  # declared
            "CLOCK_PERIOD": 40,           # declared
            "GPL_CELL_PADDING": 4,        # safe-listed but undeclared
            "SYNTH_STRATEGY": "AREA 0",   # not in safe-list (will be
                                          # rejected downstream by
                                          # modify_config; pass-through
                                          # at this layer)
        }
        clean = runner._clamp_params(params)
        assert clean["GPL_CELL_PADDING"] == 4
        assert clean["SYNTH_STRATEGY"] == "AREA 0"
        # Declared keys still go through the snap/clamp path
        assert clean["PL_TARGET_DENSITY_PCT"] == 65
        assert clean["CLOCK_PERIOD"] == 40

    def test_clamp_params_clamps_declared_tuple_keys(self, design):
        """Tuple-bounded keys still get clamped to (lo, hi) so a
        wildly out-of-range proposal does not crash LibreLane on the
        very first config write."""
        # Override design_space to a continuous tuple so the test
        # exercises clamp behaviour rather than discrete snap.
        design.design_space.return_value = {
            "PL_TARGET_DENSITY_PCT": (1.0, 99.0),
            "CLOCK_PERIOD": (0.1, 10000.0),
        }
        design.default_config.return_value = {
            "PL_TARGET_DENSITY_PCT": 50,
            "CLOCK_PERIOD": 50.0,
        }
        runner = DigitalAutoresearchRunner(design=design)

        clean = runner._clamp_params({
            "PL_TARGET_DENSITY_PCT": 200,   # above hi
            "CLOCK_PERIOD": -5.0,           # below lo
        })
        assert clean["PL_TARGET_DENSITY_PCT"] == 99.0
        assert clean["CLOCK_PERIOD"] == 0.1

    def test_system_prompt_announces_full_safe_list(self, design):
        """The system prompt must surface the LibreLane safe-list so
        the LLM knows it can step outside the declared design space.
        Regression for the prior wording 'Keys must match the design
        space variables' which actively discouraged exploration."""
        runner = DigitalAutoresearchRunner(design=design)
        prompt = runner._system_prompt("## Goal\nTest")
        # Surface a few representative safe-list keys
        assert "GPL_CELL_PADDING" in prompt or "PDN_VPITCH" in prompt
        assert "DESIGN-SPACE POLICY" in prompt
        assert "starting hint" in prompt
        assert "not a cage" in prompt
        # Old hard-restriction wording is gone
        assert "Keys must match the design space" not in prompt


# ---------------------------------------------------------------------------
# _apply_rtl_and_lint contract tests
# ---------------------------------------------------------------------------


class TestApplyRtlAndLint:
    """Coverage for ``_apply_rtl_and_lint``.

    Regression guard for the bug where the helper read
    ``lint_result.metrics`` on a ``StageResult`` (which only exposes
    ``metrics_delta``). The crash hit every non-cc_cli backend in the
    hybrid strategy, killing the first eval before any LibreLane work
    happened. opencode + hybrid surfaced it on demo_goertzel_fp32.
    """

    def test_metrics_delta_is_read_not_metrics(self, design, monkeypatch):
        """The helper must consume ``lint_result.metrics_delta``, not
        the non-existent ``.metrics`` attribute. Provides a real
        ``StageResult`` (no monkey-attributed dunders) so the test
        catches drift in the dataclass shape too."""
        from eda_agents.core.flow_stage import FlowStage, StageResult

        success_result = StageResult(
            stage=FlowStage.RTL_LINT,
            success=True,
            metrics_delta={"lint_warnings": 7, "lint_errors": 0},
            log_tail="ok",
            run_time_s=0.1,
        )

        class FakeLinter:
            def __init__(self, **kwargs):
                pass

            def run(self):
                return success_result

        monkeypatch.setattr(
            "eda_agents.core.stages.rtl_lint_runner.RtlLintRunner",
            FakeLinter,
        )

        class FakeSnapshot:
            def restore_best(self, *args, **kwargs):
                pass

            def apply_rtl_changes(self, changes):
                pass

        runner = DigitalAutoresearchRunner(
            design=design, backend="litellm", strategy="hybrid"
        )
        ok, err, warns = runner._apply_rtl_and_lint(
            proposal={"rtl_changes": {"top.v": "module top; endmodule"}},
            snapshot_mgr=FakeSnapshot(),
            eval_num=1,
        )
        assert ok is True
        assert err is None
        assert warns == 7

    def test_lint_failure_short_circuits(self, design, monkeypatch):
        """Helper must surface lint failures without touching the
        metrics_delta path (the StageResult on failure has empty
        metrics_delta and a non-empty error)."""
        from eda_agents.core.flow_stage import FlowStage, StageResult

        fail_result = StageResult(
            stage=FlowStage.RTL_LINT,
            success=False,
            metrics_delta={},
            log_tail="syntax error at line 12",
            run_time_s=0.1,
            error="3 lint errors",
        )

        class FakeLinter:
            def __init__(self, **kwargs):
                pass

            def run(self):
                return fail_result

        monkeypatch.setattr(
            "eda_agents.core.stages.rtl_lint_runner.RtlLintRunner",
            FakeLinter,
        )

        class FakeSnapshot:
            def restore_best(self, *args, **kwargs):
                pass

            def apply_rtl_changes(self, changes):
                pass

        runner = DigitalAutoresearchRunner(
            design=design, backend="litellm", strategy="hybrid"
        )
        ok, err, warns = runner._apply_rtl_and_lint(
            proposal={"rtl_changes": {"top.v": "broken"}},
            snapshot_mgr=FakeSnapshot(),
            eval_num=1,
        )
        assert ok is False
        assert err is not None
        assert "3 lint errors" in err
        assert warns == 0


# ---------------------------------------------------------------------------
# Anti-centroid seed + drop-tuples tests
# ---------------------------------------------------------------------------


class TestProgramMdNoLiteralTuples:
    """The Goertzel reward-hacking incident traced the LLM's centroid
    pick to the literal ``[lo, hi]`` lines under ``## Design Space``.
    The harness now passes only the design's own
    ``design_vars_description`` text — no auto-generated tuple
    serialisation, no ``Ranges:`` sub-block."""

    def test_program_md_uses_design_vars_description_only(self, design):
        # Sentinel string lets us assert the description is plumbed
        # through verbatim instead of being supplemented with
        # auto-generated tuple text.
        sentinel = "SENTINEL_DESIGN_DESCRIPTION_42"
        design.design_vars_description.return_value = sentinel
        runner = DigitalAutoresearchRunner(design=design)
        content = runner._generate_program()
        assert sentinel in content
        assert "Ranges:" not in content
        # No tuple-style auto-generated line should appear either.
        assert "- PL_TARGET_DENSITY_PCT: [" not in content
        assert "- CLOCK_PERIOD: [" not in content

    def test_program_md_no_literal_range_tuples_with_generic(
        self, tmp_path,
    ):
        """End-to-end check via GenericDesign: the centroid-anchoring
        ``(0.1, 10000.0)`` parens form must NOT appear anywhere in
        program.md. The descriptive ``range [0.1, 10000.0]`` form
        lives inside free-form prose, which is acceptable — the LLM
        sees that as bounds, not as a midpoint hint."""
        import yaml as _yaml

        from eda_agents.core.designs.generic import GenericDesign

        cfg = tmp_path / "config.yaml"
        cfg.write_text(_yaml.safe_dump({
            "DESIGN_NAME": "demo_block",
            "CLOCK_PERIOD": 40,
            "PL_TARGET_DENSITY_PCT": 65,
            "VERILOG_FILES": ["dir::../src/top.v"],
        }))
        d = GenericDesign(cfg)
        runner = DigitalAutoresearchRunner(design=d)
        content = runner._generate_program()
        # Continuous-range tuple form must be gone.
        assert "(0.1, 10000.0)" not in content
        assert "(1.0, 99.0)" not in content


class TestSeedEval:
    """Coverage for the eval-1 baseline seed + downstream effects."""

    @pytest.mark.anyio
    async def test_run_seeds_eval1_with_baseline(self, design, tmp_path):
        """First eval comes from baseline_params, not from the LLM.

        We set baseline=(75, 50) and the LLM mock to (45, 35). The
        eval-1 row must carry 75/50; the LLM mock must NOT have been
        called at all (budget=1 → only the seed runs)."""
        design.baseline_params.return_value = {
            "PL_TARGET_DENSITY_PCT": 75,
            "CLOCK_PERIOD": 50,
        }
        metrics_path = _make_mock_metrics_file(tmp_path)
        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=1,
            use_mock_metrics=metrics_path,
            dedup=False,
        )

        with patch.object(
            runner, "_propose_params", new_callable=AsyncMock,
        ) as mock_propose:
            mock_propose.return_value = {
                "PL_TARGET_DENSITY_PCT": 45,
                "CLOCK_PERIOD": 35,
            }
            await runner.run(tmp_path / "run")
            # The LLM proposal must NOT have been called for eval 1.
            assert mock_propose.await_count == 0

        tsv_lines = (
            (tmp_path / "run" / "results.tsv").read_text().strip().splitlines()
        )
        assert len(tsv_lines) == 2  # header + 1 row
        eval1 = tsv_lines[1].split("\t")
        # Header order: eval, PL_TARGET_DENSITY_PCT, CLOCK_PERIOD, ...
        assert eval1[0] == "1"
        assert float(eval1[1]) == 75.0
        assert float(eval1[2]) == 50.0

    @pytest.mark.anyio
    async def test_run_skips_seed_when_baseline_empty(self, design, tmp_path):
        """When baseline_params returns {}, the runner falls back to
        the LLM-only path for eval 1. Mock the proposal so the test
        is deterministic."""
        design.baseline_params.return_value = {}
        metrics_path = _make_mock_metrics_file(tmp_path)
        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=1,
            use_mock_metrics=metrics_path,
            dedup=False,
        )

        with patch.object(
            runner, "_propose_params", new_callable=AsyncMock,
        ) as mock_propose:
            mock_propose.return_value = {
                "PL_TARGET_DENSITY_PCT": 65,
                "CLOCK_PERIOD": 40,
            }
            await runner.run(tmp_path / "run")
            # The LLM was the source of eval 1.
            assert mock_propose.await_count == 1

    @pytest.mark.anyio
    async def test_run_seed_only_for_flow_strategy(self, design, tmp_path):
        """The seed concept does not translate cleanly to RTL/hybrid
        strategies (no analogous baseline RTL diff). Even with a
        non-empty baseline the runner must call the LLM for eval 1
        when ``strategy="rtl"``."""
        design.baseline_params.return_value = {
            "PL_TARGET_DENSITY_PCT": 75,
            "CLOCK_PERIOD": 50,
        }
        # Strategy="rtl" requires non-empty rtl_sources.
        rtl_file = tmp_path / "top.v"
        rtl_file.write_text("module top; endmodule\n")
        design.rtl_sources.return_value = [rtl_file]

        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=1,
            strategy="rtl",
            backend="litellm",
        )

        # Patch the rtl proposal call AND _evaluate so we don't hit
        # any real LibreLane / RTL-snapshot infra.
        with patch.object(
            runner, "_propose_litellm", new_callable=AsyncMock,
        ) as mock_propose, \
            patch.object(
                runner, "_evaluate", new_callable=AsyncMock,
            ) as mock_eval, \
            patch(
                "eda_agents.agents.rtl_snapshot_manager.RtlSnapshotManager.init_from_originals",
                lambda self, *a, **kw: None,
            ), \
            patch(
                "eda_agents.agents.rtl_snapshot_manager.RtlSnapshotManager.content_hash",
                lambda self, *a, **kw: "hash",
            ):
            mock_propose.return_value = {
                "rtl_changes": {}, "rationale": "noop",
            }
            mock_eval.return_value = {
                "eval": 1,
                "params": {},
                "success": True,
                "valid": True,
                "fom": 1.0,
                "violations": [],
                "wns_worst_ns": 1.0,
            }
            await runner.run(tmp_path / "run")
            # Even though baseline_params is non-empty, the runner
            # must have hit the LLM path because strategy != "flow".
            assert mock_propose.await_count == 1

    @pytest.mark.anyio
    async def test_resume_after_baseline_seed(self, design, tmp_path):
        """A pre-existing TSV with eval 1 = baseline (status=kept)
        must resume cleanly: start_eval=2, baseline as the current
        best, and the runner does NOT re-seed."""
        design.baseline_params.return_value = {
            "PL_TARGET_DENSITY_PCT": 75,
            "CLOCK_PERIOD": 50,
        }
        metrics_path = _make_mock_metrics_file(tmp_path)
        work_dir = tmp_path / "run"
        work_dir.mkdir()

        runner = DigitalAutoresearchRunner(
            design=design,
            model="test-model",
            budget=1,
            use_mock_metrics=metrics_path,
            dedup=False,
        )
        runner._make_program_store(work_dir).init()
        tsv_logger = runner._make_tsv_logger(work_dir / "results.tsv")
        tsv_logger.write_header()
        tsv_logger.append_row({
            "eval": 1,
            "params": {
                "PL_TARGET_DENSITY_PCT": 75.0,
                "CLOCK_PERIOD": 50.0,
            },
            "wns_worst_ns": 1.4,
            "cell_count": 12000,
            "die_area_um2": 250000.0,
            "power_mw": 50.0,
            "wire_length_um": 150000.0,
            "fom": 2.4,
            "valid": True,
            "status": "kept",
        })

        with patch.object(
            runner, "_propose_params", new_callable=AsyncMock,
        ) as mock_propose:
            mock_propose.return_value = {
                "PL_TARGET_DENSITY_PCT": 65,
                "CLOCK_PERIOD": 40,
            }
            result = await runner.run(work_dir)
            # 1 prior + 1 new (LLM-proposed, no re-seed) = 2 total.
            assert result.total_evals == 2
            # The new eval must have come from the LLM, not a re-seed.
            assert mock_propose.await_count == 1

    def test_eval2_prompt_mentions_baseline(self, design):
        """When history[0]['seed'] is True, the eval 2 prompt must
        explicitly tell the LLM that eval 1 is the project baseline
        so it proposes a delta instead of guessing the same point."""
        runner = DigitalAutoresearchRunner(design=design)
        seed_entry = {
            "eval": 1,
            "fom": 2.0,
            "valid": True,
            "params": {
                "PL_TARGET_DENSITY_PCT": 75,
                "CLOCK_PERIOD": 50,
            },
            "wns_worst_ns": 1.4,
            "cell_count": 12000,
            "die_area_um2": 250000.0,
            "power_mw": 50.0,
            "kept": True,
            "status": "kept",
            "seed": True,
        }
        prompt = runner._build_proposal_prompt([seed_entry], seed_entry, 2)
        assert "baseline" in prompt.lower()
        assert "delta" in prompt.lower()

    def test_eval2_prompt_quiet_when_no_seed(self, design):
        """The anchor cue is only emitted on the post-seed eval 2.
        Without a seeded history[0], the prompt stays unchanged."""
        runner = DigitalAutoresearchRunner(design=design)
        non_seed_entry = {
            "eval": 1,
            "fom": 0.0,
            "valid": False,
            "params": {
                "PL_TARGET_DENSITY_PCT": 65,
                "CLOCK_PERIOD": 40,
            },
            "kept": False,
            "status": "discarded",
            "seed": False,
        }
        prompt = runner._build_proposal_prompt(
            [non_seed_entry], None, 2,
        )
        assert "project baseline" not in prompt.lower()


class TestGlSimGating:
    """``_run_gl_sim`` no longer filters out cocotb tbs.

    Pre-Commit-5 the gate returned ``None`` whenever the testbench
    spec was anything other than iverilog, which silently skipped GL
    sim for designs like Goertzel. ``GlSimRunner`` already supports
    cocotb internally, so the filter is dropped and ``_run_gl_sim``
    instantiates the runner regardless of driver.
    """

    def _make_pdk_with_glob(self):
        from eda_agents.core.pdk import GF180MCU_D
        return GF180MCU_D

    def test_run_gl_sim_invokes_runner_for_cocotb_tb(
        self, design, tmp_path, monkeypatch,
    ):
        from eda_agents.core.digital_design import TestbenchSpec
        from eda_agents.core.flow_stage import FlowStage
        from eda_agents.core.flow_stage import StageResult

        cocotb_tb = TestbenchSpec(
            driver="cocotb", target="sim", work_dir_relative="tb",
        )
        design.testbench.return_value = cocotb_tb
        design.gl_sim_cells_glob.return_value = "cells/*.v"

        invoked = {"count": 0}

        class _FakeGlSimRunner:
            def __init__(self, **kwargs):  # noqa: ARG002
                pass

            def run_post_synth(self):
                invoked["count"] += 1
                return StageResult(
                    stage=FlowStage.POST_SYNTH_SIM,
                    success=True,
                    metrics_delta={},
                    log_tail="ok",
                    run_time_s=0.1,
                )

            def run_post_pnr(self):  # pragma: no cover - not used here
                raise AssertionError("post_pnr not expected in this test")

        monkeypatch.setattr(
            "eda_agents.core.stages.gl_sim_runner.GlSimRunner",
            _FakeGlSimRunner,
        )
        monkeypatch.setattr(
            "eda_agents.core.pdk.resolve_pdk_root",
            lambda _cfg, explicit_root=None: Path("/fake/pdk"),
        )

        runner = DigitalAutoresearchRunner(design=design)
        out = runner._run_gl_sim(
            tmp_path / "run", eval_num=1,
            pdk_cfg=self._make_pdk_with_glob(),
            mode="post_synth",
        )

        assert out is not None
        assert out["success"] is True
        assert invoked["count"] == 1

    def test_run_gl_sim_skips_when_no_stdcell_glob(
        self, design, tmp_path,
    ):
        """The PDK-glob skip is preserved post-Commit-5; the iverilog
        filter is dropped, but stdcell glob requirements still hold."""
        from eda_agents.core.digital_design import TestbenchSpec

        empty_pdk = MagicMock()
        empty_pdk.stdcell_verilog_models_glob = ""
        empty_pdk.name = "empty"
        design.testbench.return_value = TestbenchSpec(
            driver="cocotb", target="sim", work_dir_relative="tb",
        )
        design.gl_sim_cells_glob.return_value = None

        runner = DigitalAutoresearchRunner(design=design)
        out = runner._run_gl_sim(
            tmp_path / "run", eval_num=1, pdk_cfg=empty_pdk,
            mode="post_synth",
        )
        assert out is None
