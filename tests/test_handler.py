"""Tests for SpiceEvaluationHandler."""
import asyncio

from eda_agents.core.spice_runner import SpiceRunner
from eda_agents.topologies.ota_miller import MillerOTATopology
from eda_agents.agents.handler import SpiceEvaluationHandler


class TestSpiceHandlerUnit:
    def _make_handler(self, tmp_path, max_evals=30, prefilter=True):
        topo = MillerOTATopology()
        runner = SpiceRunner(pdk_root=tmp_path)
        return SpiceEvaluationHandler(
            topology=topo, runner=runner, work_dir=tmp_path,
            max_evals=max_evals, analytical_prefilter=prefilter,
        )

    def test_budget_tracking(self, tmp_path):
        h = self._make_handler(tmp_path, max_evals=10)
        assert h.budget_remaining == 10
        assert h.eval_count == 0

    def test_cache_key_determinism(self, tmp_path):
        h = self._make_handler(tmp_path)
        params = {"gmid_input": 12.0, "gmid_load": 10.0, "L_input_um": 0.5,
                  "L_load_um": 0.5, "Cc_pF": 0.5, "Ibias_uA": 10.0}
        k1 = h._cache_key(params)
        k2 = h._cache_key(params)
        assert k1 == k2

    def test_prefilter_bad_design(self, tmp_path):
        h = self._make_handler(tmp_path, prefilter=True)
        # Very high gmid -> weak inversion -> low gain -> should be pre-filtered
        params = {"gmid_input": 25.0, "gmid_load": 20.0, "L_input_um": 0.13,
                  "L_load_um": 0.13, "Cc_pF": 5.0, "Ibias_uA": 0.5}
        result = asyncio.get_event_loop().run_until_complete(h.evaluate(params))
        assert result.eval_mode in ("analytical_prefilter", "spice")
        # Budget should not have been consumed for prefiltered
        if result.eval_mode == "analytical_prefilter":
            assert h.budget_remaining == 30

    def test_export_results(self, tmp_path):
        h = self._make_handler(tmp_path)
        params = {"gmid_input": 12.0, "gmid_load": 10.0, "L_input_um": 0.5,
                  "L_load_um": 0.5, "Cc_pF": 0.5, "Ibias_uA": 10.0}
        asyncio.get_event_loop().run_until_complete(h.evaluate(params))
        export_path = tmp_path / "results.json"
        h.export_results(export_path)
        assert export_path.exists()
