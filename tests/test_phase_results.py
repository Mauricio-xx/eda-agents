"""Tests for phase result dataclasses."""

from eda_agents.agents.phase_results import (
    DRCResult,
    ExplorationResult,
    FlowResult,
    LVSResult,
)


class TestExplorationResult:
    def test_empty(self):
        r = ExplorationResult(best_params={}, best_fom=0.0, best_valid=False)
        assert r.n_evals == 0
        assert r.n_valid == 0
        assert r.validity_rate == 0.0

    def test_with_evals(self):
        evals = [
            {"params": {"x": 1}, "valid": True, "fom": 10.0},
            {"params": {"x": 2}, "valid": False, "fom": 5.0},
            {"params": {"x": 3}, "valid": True, "fom": 15.0},
        ]
        r = ExplorationResult(
            best_params={"x": 3},
            best_fom=15.0,
            best_valid=True,
            all_evals=evals,
            agent_summary="found best at x=3",
        )
        assert r.n_evals == 3
        assert r.n_valid == 2
        assert r.validity_rate == 2 / 3

    def test_all_invalid(self):
        evals = [{"valid": False}, {"valid": False}]
        r = ExplorationResult(
            best_params={}, best_fom=0.0, best_valid=False, all_evals=evals
        )
        assert r.n_valid == 0
        assert r.validity_rate == 0.0


class TestFlowResult:
    def test_success(self):
        r = FlowResult(
            success=True,
            gds_path="/tmp/chip.gds",
            timing_met=True,
            drc_clean=True,
            run_time_s=120.5,
        )
        assert "GDS generated" in r.summary
        assert "timing met" in r.summary
        assert "DRC clean" in r.summary

    def test_failure(self):
        r = FlowResult(success=False, error="synthesis failed")
        assert "Flow failed" in r.summary
        assert "synthesis failed" in r.summary

    def test_timing_violated(self):
        r = FlowResult(
            success=True,
            gds_path="/tmp/chip.gds",
            timing_met=False,
            run_time_s=90,
        )
        assert "VIOLATED" in r.summary

    def test_minimal(self):
        r = FlowResult(success=True, run_time_s=60)
        assert "completed" in r.summary


class TestDRCResult:
    def test_clean(self):
        r = DRCResult(total_violations=0, clean=True, iterations=1)
        assert "clean" in r.summary
        assert "1 iteration" in r.summary

    def test_dirty(self):
        r = DRCResult(
            total_violations=42,
            violated_rules={"M1.S.1": 20, "M2.W.1": 15, "COMP.S.1": 7},
            clean=False,
            iterations=3,
        )
        assert "42 violations" in r.summary
        assert "3 rules" in r.summary
        assert "3 iteration" in r.summary

    def test_fixes_tracked(self):
        r = DRCResult(
            total_violations=5,
            violated_rules={"M1.S.1": 5},
            clean=False,
            fixes_applied=[
                {"rule": "M1.S.1", "fix": "reduce density", "iteration": 1}
            ],
            iterations=1,
        )
        assert len(r.fixes_applied) == 1


class TestLVSResult:
    def test_match(self):
        r = LVSResult(match=True)
        assert r.summary == "LVS: match"

    def test_mismatch(self):
        r = LVSResult(match=False, mismatches=3)
        assert "MISMATCH" in r.summary
        assert "3 differences" in r.summary
