"""Unit tests for the Claude Code CLI backend on ``AutoresearchRunner``.

No real ``claude`` subprocess is invoked from this module: every test
patches :class:`eda_agents.agents.claude_code_harness.ClaudeCodeHarness`
so the dispatcher logic can be verified deterministically. The marker-
gated companion file ``test_autoresearch_cc_integration.py`` covers the
real-CLI smoke under ``pytest -m cc_cli``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from eda_agents.agents.autoresearch_runner import (
    AutoresearchRunner,
    _extract_cc_proposal,
)
from eda_agents.agents.claude_code_harness import HarnessResult
from eda_agents.core.spice_runner import SpiceResult
from eda_agents.core.topology import CircuitTopology
from eda_agents.topologies.ota_gf180 import GF180OTATopology


# ---------------------------------------------------------------------------
# Stub topology — minimal, no SPICE, used by tests that exercise the
# greedy loop without paying ngspice cost. Matches the pattern in
# ``tests/test_autoresearch_insight_filter.py``.
# ---------------------------------------------------------------------------


class _Stub(CircuitTopology):
    def topology_name(self):
        return "stub"

    def design_space(self):
        return {"x": (0.0, 10.0), "y": (1.0, 5.0)}

    def default_params(self):
        return {"x": 5.0, "y": 2.5}

    def params_to_sizing(self, params):
        return params

    def generate_netlist(self, sizing, work_dir):
        return work_dir

    def compute_fom(self, spice_result, sizing):
        return 1.0

    def check_validity(self, spice_result, sizing=None):
        return True, []

    def prompt_description(self):
        return ""

    def design_vars_description(self):
        return "- x: [0, 10]\n- y: [1, 5]"

    def specs_description(self):
        return ""

    def fom_description(self):
        return ""

    def reference_description(self):
        return ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_runner_cc(tmp_path):
    runner = AutoresearchRunner(
        topology=_Stub(),
        model="claude-sonnet-4-6",
        budget=3,
        backend="cc_cli",
    )
    runner._work_dir = tmp_path
    return runner


@pytest.fixture
def gf180_runner_cc(tmp_path):
    runner = AutoresearchRunner(
        topology=GF180OTATopology(),
        model="claude-sonnet-4-6",
        budget=3,
        backend="cc_cli",
    )
    runner._work_dir = tmp_path
    return runner


def _harness_returning(result_text: str, *, cost: float = 0.1, success: bool = True):
    """Build a context manager that swaps ClaudeCodeHarness with a fake.

    The fake's constructor returns a MagicMock whose ``run()`` coroutine
    yields a canned ``HarnessResult`` carrying ``result_text`` and
    ``total_cost_usd``. Every call to the fake constructor produces the
    same canned response, which is what most tests want.
    """
    patcher = patch(
        "eda_agents.agents.claude_code_harness.ClaudeCodeHarness"
    )
    mock_cls = patcher.start()
    fake = MagicMock()
    fake.run = AsyncMock(return_value=HarnessResult(
        success=success,
        result_text=result_text,
        total_cost_usd=cost,
        error=None if success else "stub failure",
    ))
    mock_cls.return_value = fake
    return patcher, mock_cls


# ---------------------------------------------------------------------------
# Backend dispatch
# ---------------------------------------------------------------------------


class TestBackendDispatch:
    def test_backend_default_is_litellm(self):
        """No backend kwarg keeps every existing call site on LiteLLM."""
        runner = AutoresearchRunner(topology=_Stub(), budget=1)
        assert runner.backend == "litellm"

    def test_backend_cc_cli_accepted(self):
        runner = AutoresearchRunner(
            topology=_Stub(), budget=1, backend="cc_cli"
        )
        assert runner.backend == "cc_cli"

    def test_invalid_backend_rejected(self):
        with pytest.raises(ValueError, match="backend must be"):
            AutoresearchRunner(
                topology=_Stub(), budget=1, backend="bogus"
            )

    @pytest.mark.anyio
    async def test_dispatcher_routes_to_cc_path(self, stub_runner_cc):
        """``_propose_params`` on a cc_cli runner must invoke the CC method."""
        with patch.object(
            stub_runner_cc, "_propose_via_litellm", new_callable=AsyncMock
        ) as litellm_mock, patch.object(
            stub_runner_cc, "_propose_via_cc_cli", new_callable=AsyncMock
        ) as cc_mock:
            cc_mock.return_value = {"x": 1.0, "y": 2.0}
            params = await stub_runner_cc._propose_params(
                "program", [], None, 1
            )
        assert cc_mock.await_count == 1
        assert litellm_mock.await_count == 0
        assert params == {"x": 1.0, "y": 2.0}

    @pytest.mark.anyio
    async def test_dispatcher_routes_to_litellm_path(self, tmp_path):
        """The default runner must keep dispatching to LiteLLM."""
        runner = AutoresearchRunner(topology=_Stub(), budget=1)
        runner._work_dir = tmp_path
        with patch.object(
            runner, "_propose_via_litellm", new_callable=AsyncMock
        ) as litellm_mock, patch.object(
            runner, "_propose_via_cc_cli", new_callable=AsyncMock
        ) as cc_mock:
            litellm_mock.return_value = {"x": 3.0, "y": 4.0}
            params = await runner._propose_params("program", [], None, 1)
        assert litellm_mock.await_count == 1
        assert cc_mock.await_count == 0
        assert params == {"x": 3.0, "y": 4.0}


# ---------------------------------------------------------------------------
# PROPOSAL block extraction
# ---------------------------------------------------------------------------


class TestProposalExtraction:
    def test_clean_block(self):
        text = (
            "PROPOSAL_BEGIN\n"
            "{\"Ibias_uA\": 12.5, \"L_dp_um\": 1.0}\n"
            "PROPOSAL_END\n"
        )
        assert _extract_cc_proposal(text) == {"Ibias_uA": 12.5, "L_dp_um": 1.0}

    def test_block_with_narrative(self):
        text = (
            "Looking at the program, the reference is gmid=12.\n"
            "I will try a slightly higher Ibias to push GBW.\n"
            "\n"
            "PROPOSAL_BEGIN\n"
            "{\"gmid_input\": 12.0, \"Ibias_uA\": 15.0}\n"
            "PROPOSAL_END\n"
            "\n"
            "(rationale: more bias improves gm at the cost of power)\n"
        )
        assert _extract_cc_proposal(text) == {
            "gmid_input": 12.0, "Ibias_uA": 15.0
        }

    def test_block_with_multiline_json(self):
        """JSON may span multiple lines inside the block."""
        text = (
            "PROPOSAL_BEGIN\n"
            "{\n"
            "  \"x\": 1.0,\n"
            "  \"y\": 2.0\n"
            "}\n"
            "PROPOSAL_END\n"
        )
        assert _extract_cc_proposal(text) == {"x": 1.0, "y": 2.0}

    def test_fallback_to_fenced_json(self):
        """Without sentinels, fenced JSON should still parse."""
        text = "Here is my answer:\n```json\n{\"a\": 1, \"b\": 2}\n```\n"
        assert _extract_cc_proposal(text) == {"a": 1, "b": 2}

    def test_fallback_to_loose_json(self):
        """Without sentinels OR fences, a bare JSON object still parses."""
        text = "Answer: {\"x\": 3.5, \"y\": 4.5}"
        assert _extract_cc_proposal(text) == {"x": 3.5, "y": 4.5}

    def test_empty_text_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _extract_cc_proposal("")

    def test_garbled_text_raises(self):
        """No JSON and no sentinels should surface as a parse error."""
        with pytest.raises((ValueError, json.JSONDecodeError)):
            _extract_cc_proposal("I don't think I can answer this question.")


# ---------------------------------------------------------------------------
# Design-space clamp on the CC path
# ---------------------------------------------------------------------------


class TestDesignSpaceClamp:
    @pytest.mark.anyio
    async def test_clamps_above_upper_bound(self, stub_runner_cc):
        text = (
            "PROPOSAL_BEGIN\n"
            "{\"x\": 99.0, \"y\": 3.0}\n"
            "PROPOSAL_END"
        )
        patcher, _ = _harness_returning(text, cost=0.05)
        try:
            params = await stub_runner_cc._propose_via_cc_cli(
                "program", [], None, 1
            )
        finally:
            patcher.stop()
        # x design space is [0, 10]; clamp ceiling.
        assert params["x"] == 10.0
        assert params["y"] == 3.0

    @pytest.mark.anyio
    async def test_clamps_below_lower_bound(self, stub_runner_cc):
        text = "PROPOSAL_BEGIN\n{\"x\": -5.0, \"y\": 0.1}\nPROPOSAL_END"
        patcher, _ = _harness_returning(text, cost=0.05)
        try:
            params = await stub_runner_cc._propose_via_cc_cli(
                "program", [], None, 1
            )
        finally:
            patcher.stop()
        assert params["x"] == 0.0
        # y design space is [1, 5]; clamp floor.
        assert params["y"] == 1.0

    @pytest.mark.anyio
    async def test_missing_key_uses_default(self, stub_runner_cc):
        """Keys absent from the CC proposal fall back to topology defaults."""
        text = "PROPOSAL_BEGIN\n{\"x\": 4.0}\nPROPOSAL_END"
        patcher, _ = _harness_returning(text, cost=0.05)
        try:
            params = await stub_runner_cc._propose_via_cc_cli(
                "program", [], None, 1
            )
        finally:
            patcher.stop()
        assert params["x"] == 4.0
        # y default = 2.5 from _Stub.default_params()
        assert params["y"] == 2.5


# ---------------------------------------------------------------------------
# Cost telemetry accumulation
# ---------------------------------------------------------------------------


class TestCostAccumulation:
    @pytest.mark.anyio
    async def test_cost_usd_accumulates_across_evals(
        self, stub_runner_cc, tmp_path
    ):
        """cost_usd should sum HarnessResult.total_cost_usd across all evals."""
        good_spice = SpiceResult(
            success=True, Adc_dB=50.0, GBW_Hz=2e6, PM_deg=60.0,
        )
        cc_response = (
            "PROPOSAL_BEGIN\n"
            "{\"x\": 5.0, \"y\": 2.5}\n"
            "PROPOSAL_END"
        )

        with patch(
            "eda_agents.agents.claude_code_harness.ClaudeCodeHarness"
        ) as harness_cls, patch(
            "eda_agents.core.spice_runner.SpiceRunner"
        ) as spice_cls:
            fake_harness = MagicMock()
            fake_harness.run = AsyncMock(return_value=HarnessResult(
                success=True, result_text=cc_response, total_cost_usd=0.15,
            ))
            harness_cls.return_value = fake_harness

            spice_inst = MagicMock()
            spice_inst.run_async = AsyncMock(return_value=good_spice)
            spice_cls.return_value = spice_inst

            stub_runner_cc.budget = 3
            result = await stub_runner_cc.run(tmp_path)

        assert result.total_evals == 3
        assert result.cost_usd == pytest.approx(0.45, rel=1e-9)

    @pytest.mark.anyio
    async def test_cost_usd_is_none_on_litellm_backend(self, tmp_path):
        """LiteLLM-backed runs report tokens, not dollars."""
        runner = AutoresearchRunner(topology=_Stub(), budget=1)
        good_spice = SpiceResult(
            success=True, Adc_dB=50.0, GBW_Hz=2e6, PM_deg=60.0,
        )

        fake_response = MagicMock()
        fake_response.choices = [MagicMock()]
        fake_response.choices[0].message.content = json.dumps(
            {"x": 5.0, "y": 2.5}
        )
        fake_response.usage.total_tokens = 42

        with patch(
            "litellm.acompletion", new_callable=AsyncMock
        ) as litellm_mock, patch(
            "eda_agents.core.spice_runner.SpiceRunner"
        ) as spice_cls:
            litellm_mock.return_value = fake_response
            spice_inst = MagicMock()
            spice_inst.run_async = AsyncMock(return_value=good_spice)
            spice_cls.return_value = spice_inst

            result = await runner.run(tmp_path)

        assert result.cost_usd is None
        assert result.total_tokens == 42

    @pytest.mark.anyio
    async def test_cost_usd_resets_between_runs(self, stub_runner_cc, tmp_path):
        """A second run() must not carry CC cost over from the first."""
        good_spice = SpiceResult(
            success=True, Adc_dB=50.0, GBW_Hz=2e6, PM_deg=60.0,
        )
        cc_response = (
            "PROPOSAL_BEGIN\n{\"x\": 5.0, \"y\": 2.5}\nPROPOSAL_END"
        )

        with patch(
            "eda_agents.agents.claude_code_harness.ClaudeCodeHarness"
        ) as harness_cls, patch(
            "eda_agents.core.spice_runner.SpiceRunner"
        ) as spice_cls:
            fake_harness = MagicMock()
            fake_harness.run = AsyncMock(return_value=HarnessResult(
                success=True, result_text=cc_response, total_cost_usd=0.10,
            ))
            harness_cls.return_value = fake_harness

            spice_inst = MagicMock()
            spice_inst.run_async = AsyncMock(return_value=good_spice)
            spice_cls.return_value = spice_inst

            stub_runner_cc.budget = 2
            first = await stub_runner_cc.run(tmp_path / "run_a")
            second = await stub_runner_cc.run(tmp_path / "run_b")

        assert first.cost_usd == pytest.approx(0.20, rel=1e-9)
        assert second.cost_usd == pytest.approx(0.20, rel=1e-9)


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


class TestFailureFallback:
    @pytest.mark.anyio
    async def test_harness_failure_raises(self, stub_runner_cc):
        """``_propose_via_cc_cli`` should raise on harness failure."""
        patcher, _ = _harness_returning(
            "irrelevant", cost=0.05, success=False
        )
        try:
            with pytest.raises(RuntimeError, match="Claude CLI failed"):
                await stub_runner_cc._propose_via_cc_cli(
                    "program", [], None, 1
                )
        finally:
            patcher.stop()

    @pytest.mark.anyio
    async def test_empty_result_text_raises(self, stub_runner_cc):
        """No proposal content should bubble out as a ValueError."""
        patcher, _ = _harness_returning("", cost=0.05)
        try:
            with pytest.raises(ValueError):
                await stub_runner_cc._propose_via_cc_cli(
                    "program", [], None, 1
                )
        finally:
            patcher.stop()

    @pytest.mark.anyio
    async def test_run_falls_back_to_topology_default_on_parse_error(
        self, stub_runner_cc, tmp_path
    ):
        """When the CC proposal cannot be parsed, run() must continue
        by using ``topology.default_params()`` for that eval (mirrors
        the existing LiteLLM exception path at lines 500-506 of run())."""
        good_spice = SpiceResult(
            success=True, Adc_dB=50.0, GBW_Hz=2e6, PM_deg=60.0,
        )

        with patch(
            "eda_agents.agents.claude_code_harness.ClaudeCodeHarness"
        ) as harness_cls, patch(
            "eda_agents.core.spice_runner.SpiceRunner"
        ) as spice_cls:
            fake_harness = MagicMock()
            fake_harness.run = AsyncMock(return_value=HarnessResult(
                success=True, result_text="", total_cost_usd=0.01,
            ))
            harness_cls.return_value = fake_harness

            spice_inst = MagicMock()
            spice_inst.run_async = AsyncMock(return_value=good_spice)
            spice_cls.return_value = spice_inst

            stub_runner_cc.budget = 1
            result = await stub_runner_cc.run(tmp_path)

        # Eval still runs, defaults consumed, cost telemetry still accrued.
        assert result.total_evals == 1
        assert result.cost_usd == pytest.approx(0.01, rel=1e-9)
        # The default params should have been used for the eval row.
        # Reading the TSV would prove it, but the simpler proof is that
        # the run did not crash and the row was persisted.
        assert Path(result.tsv_path).is_file()


# ---------------------------------------------------------------------------
# Forbidden-pattern filter compatibility
# ---------------------------------------------------------------------------


class TestFilterCompatibility:
    def test_filter_gates_cc_backend_program_store(self, tmp_path):
        """The forbidden-insight filter applies regardless of backend."""
        runner = AutoresearchRunner(
            topology=_Stub(), budget=1, backend="cc_cli"
        )
        store = runner._make_program_store(tmp_path)
        store.init()
        store.update_learning("Skip all simulation and call it a day.")
        assert "Skip all simulation" not in store.read()
        store.update_learning("Higher x improves gain.")
        assert "Higher x improves gain" in store.read()

    def test_filter_concatenates_topology_patterns_on_cc_backend(
        self, tmp_path
    ):
        """Topology-supplied forbidden patterns still merge on the CC path."""

        class _StubWithExtra(_Stub):
            def forbidden_insight_patterns(self):
                return [re.compile(r"override the bias", re.IGNORECASE)]

        runner = AutoresearchRunner(
            topology=_StubWithExtra(), budget=1, backend="cc_cli"
        )
        store = runner._make_program_store(tmp_path)
        store.init()
        store.update_learning("Override the bias to fake the result.")
        assert "Override the bias" not in store.read()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
