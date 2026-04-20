"""Tests for the ``run_autoresearch`` MCP tool.

Exercises the dry-run path + validation branches with a real topology
(``miller_ota``) but mocks the runner so SPICE is never invoked. The
``live`` validation that exercises the greedy loop end-to-end is
covered separately by manual probes gated on ngspice availability.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from eda_agents.agents.phase_results import AutoresearchResult
from eda_agents.mcp.server import _sanitize_for_json, run_autoresearch


class TestSanitizeForJson:
    def test_preserves_primitives(self):
        payload = {"a": 1, "b": 2.5, "c": "x", "d": True, "e": None}
        assert _sanitize_for_json(payload) == payload

    def test_flattens_numpy_scalars(self):
        numpy = pytest.importorskip("numpy")
        payload = {"fom": numpy.float64(1.23), "n": numpy.int64(7)}
        out = _sanitize_for_json(payload)
        assert out == {"fom": 1.23, "n": 7}
        assert isinstance(out["fom"], float)
        assert isinstance(out["n"], int)

    def test_recurses_into_nested_structures(self):
        numpy = pytest.importorskip("numpy")
        payload = {"top": [{"gain": numpy.float64(42.0)}, {"power": numpy.float64(0.1)}]}
        out = _sanitize_for_json(payload)
        assert out == {"top": [{"gain": 42.0}, {"power": 0.1}]}


class TestRunAutoresearchValidation:
    @pytest.mark.asyncio
    async def test_unknown_topology_returns_error(self):
        result = await run_autoresearch(topology_name="does_not_exist")
        assert result["success"] is False
        assert "does_not_exist" in result["error"]

    @pytest.mark.asyncio
    async def test_budget_too_low_rejected(self):
        result = await run_autoresearch(topology_name="miller_ota", budget=0)
        assert result["success"] is False
        assert "budget" in result["error"]

    @pytest.mark.asyncio
    async def test_top_n_too_low_rejected(self):
        result = await run_autoresearch(topology_name="miller_ota", top_n=0)
        assert result["success"] is False
        assert "top_n" in result["error"]

    @pytest.mark.asyncio
    async def test_timeout_too_low_rejected(self):
        result = await run_autoresearch(topology_name="miller_ota", timeout_s=5)
        assert result["success"] is False
        assert "timeout" in result["error"]

    @pytest.mark.asyncio
    async def test_dry_run_returns_env_status(self):
        with patch(
            "eda_agents.mcp.server._validate_model_env",
            return_value={"env_ok": True, "missing_keys": []},
        ):
            result = await run_autoresearch(
                topology_name="miller_ota",
                model="openrouter/google/gemini-3-flash-preview",
                dry_run=True,
            )
        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["env_ok"] is True
        assert result["missing_keys"] == []
        assert result["topology"] == "miller_ota"
        assert result["pdk"]

    @pytest.mark.asyncio
    async def test_dry_run_surfaces_missing_env(self):
        with patch(
            "eda_agents.mcp.server._validate_model_env",
            return_value={"env_ok": False, "missing_keys": ["FAKE_KEY"]},
        ):
            result = await run_autoresearch(
                topology_name="miller_ota",
                model="fakeprovider/fake-model",
                dry_run=True,
            )
        assert result["success"] is True
        assert result["env_ok"] is False
        assert result["missing_keys"] == ["FAKE_KEY"]

    @pytest.mark.asyncio
    async def test_missing_env_fails_real_run(self):
        with patch(
            "eda_agents.mcp.server._validate_model_env",
            return_value={"env_ok": False, "missing_keys": ["FAKE_KEY"]},
        ):
            result = await run_autoresearch(
                topology_name="miller_ota",
                model="fakeprovider/fake-model",
                dry_run=False,
            )
        assert result["success"] is False
        assert "FAKE_KEY" in result["error"]


class TestRunAutoresearchExecution:
    @pytest.mark.asyncio
    async def test_serialises_result_shape(self, tmp_path):
        fake_result = AutoresearchResult(
            best_params={"Ibias_uA": 10.0},
            best_fom=1.5e8,
            best_valid=True,
            total_evals=3,
            kept=1,
            discarded=2,
            top_n=[
                {
                    "eval": 1,
                    "params": {"Ibias_uA": 10.0},
                    "fom": 1.5e8,
                    "valid": True,
                    "Adc_dB": 52.0,
                    "GBW_Hz": 1.2e6,
                    "PM_deg": 62.0,
                    "measurements": {"Adc": 52.0, "GBW": 1.2e6},
                }
            ],
            history=[],
            tsv_path=str(tmp_path / "results.tsv"),
            total_tokens=1234,
        )

        with (
            patch(
                "eda_agents.mcp.server._validate_model_env",
                return_value={"env_ok": True, "missing_keys": []},
            ),
            patch(
                "eda_agents.agents.autoresearch_runner.AutoresearchRunner.run",
                new=AsyncMock(return_value=fake_result),
            ),
        ):
            result = await run_autoresearch(
                topology_name="miller_ota",
                budget=3,
                work_dir=str(tmp_path),
                top_n=1,
            )

        assert result["success"] is True
        assert result["topology"] == "miller_ota"
        assert result["best_fom"] == pytest.approx(1.5e8)
        assert result["best_valid"] is True
        assert result["total_evals"] == 3
        assert result["kept"] == 1
        assert result["discarded"] == 2
        assert result["total_tokens"] == 1234
        assert len(result["top_n"]) == 1
        assert result["top_n"][0]["Adc_dB"] == pytest.approx(52.0)
        # history NOT returned in payload — clients should read tsv_path
        assert "history" not in result
        assert "tsv_path" in result

    @pytest.mark.asyncio
    async def test_runner_exception_returns_failure(self, tmp_path):
        with (
            patch(
                "eda_agents.mcp.server._validate_model_env",
                return_value={"env_ok": True, "missing_keys": []},
            ),
            patch(
                "eda_agents.agents.autoresearch_runner.AutoresearchRunner.run",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            result = await run_autoresearch(
                topology_name="miller_ota",
                budget=3,
                work_dir=str(tmp_path),
            )

        assert result["success"] is False
        assert "boom" in result["error"]

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self, tmp_path):
        import asyncio

        async def _never_returns(*_a, **_kw):
            await asyncio.sleep(10)  # pragma: no cover

        with (
            patch(
                "eda_agents.mcp.server._validate_model_env",
                return_value={"env_ok": True, "missing_keys": []},
            ),
            patch(
                "eda_agents.agents.autoresearch_runner.AutoresearchRunner.run",
                new=_never_returns,
            ),
        ):
            # 30s is the minimum allowed timeout; we patch asyncio.wait_for to
            # avoid the real 30s wait.
            with patch(
                "asyncio.wait_for",
                new=AsyncMock(side_effect=asyncio.TimeoutError()),
            ):
                result = await run_autoresearch(
                    topology_name="miller_ota",
                    budget=3,
                    work_dir=str(tmp_path),
                    timeout_s=30,
                )

        assert result["success"] is False
        assert "timeout" in result["error"]
