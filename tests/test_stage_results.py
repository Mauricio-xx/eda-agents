"""Contract tests for the per-eval :class:`StageResults` bag."""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.flow_stage import FlowStage, StageResult
from eda_agents.core.stage_results import StageResults


def test_required_fields_only():
    """The minimal bag carries eval metadata; every other field defaults to ``None``."""
    sr = StageResults(eval_idx=1, params={"a": 1}, work_dir=Path("/tmp"))
    assert sr.eval_idx == 1
    assert sr.params == {"a": 1}
    assert sr.work_dir == Path("/tmp")
    assert sr.flow_metrics is None
    assert sr.run_dir is None
    assert sr.rtl_lint is None
    assert sr.rtl_sim is None
    assert sr.gl_sim_post_synth is None
    assert sr.gl_sim_post_pnr is None
    assert sr.rtl_changes is None
    assert sr.extras == {}


def test_carries_flow_metrics():
    metrics = FlowMetrics(wns_worst_ns=2.5, synth_cell_count=10000)
    sr = StageResults(
        eval_idx=2,
        params={},
        work_dir=Path("/tmp"),
        flow_metrics=metrics,
        run_dir=Path("/runs/RUN_2026"),
    )
    assert sr.flow_metrics is metrics
    assert sr.run_dir == Path("/runs/RUN_2026")


def test_carries_stage_result():
    rtl_sim = StageResult(stage=FlowStage.RTL_SIM, success=True)
    sr = StageResults(
        eval_idx=3, params={}, work_dir=Path("/tmp"),
        rtl_sim=rtl_sim,
    )
    assert sr.rtl_sim is rtl_sim


def test_frozen_dataclass():
    """Designs treat the bag as read-only — direct field assignment raises."""
    sr = StageResults(eval_idx=1, params={}, work_dir=Path("/tmp"))
    with pytest.raises((AttributeError, Exception)):
        sr.eval_idx = 99


def test_extras_default_per_instance():
    """``extras`` must not be a shared mutable default."""
    sr1 = StageResults(eval_idx=1, params={}, work_dir=Path("/tmp"))
    sr2 = StageResults(eval_idx=2, params={}, work_dir=Path("/tmp"))
    sr1.extras["domain_key"] = "a"
    assert sr2.extras == {}
