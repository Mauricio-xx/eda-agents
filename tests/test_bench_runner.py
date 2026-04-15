"""Tests for the bench runner + adapter dispatch + audit logic.

These are pure-Python (no ngspice / no klayout / no LLM). The runner
gets exercised end-to-end via the dry-run harness so that
``scripts/run_bench.py --dry-run`` is fully covered without any
external dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agents.bench.adapters import (
    AdapterResult,
    HARNESS_DISPATCH,
    callable_adapter,
    dry_run_adapter,
    resolve_callable,
    run_task,
)
from eda_agents.bench.models import (
    BenchScores,
    BenchStatus,
    BenchTask,
)
from eda_agents.bench.runner import (
    audit_adapter_result,
    execute_task,
    render_markdown_report,
    run_batch,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _dry_task(**overrides):
    base = {
        "id": "e2e_dry_smoke",
        "family": "end-to-end",
        "category": "pipeline",
        "domain": "voltage",
        "pdk": "ihp_sg13g2",
        "difficulty": "easy",
        "expected_backend": "dry-run",
        "harness": "dry_run",
        "scoring": ["compile", "sim_run"],
    }
    base.update(overrides)
    return BenchTask.model_validate(base)


def _bugfix_task(**overrides):
    base = {
        "id": "bugfix_floating_node_detected",
        "family": "bugfix",
        "category": "structural",
        "domain": "voltage",
        "pdk": "ihp_sg13g2",
        "difficulty": "easy",
        "expected_backend": "dry-run",
        "harness": "callable",
        "inputs": {
            "callable": "eda_agents.bench.adapters:run_pre_sim_gate_on_inline_netlist",
            "gate": "floating_nodes",
            "subckt": "broken",
            "expect_violation": True,
            "netlist": (
                ".subckt broken in out vdd vss\n"
                "M1 dangling in vss vss nfet W=1u L=180n\n"
                "M2 out in vss vss nfet W=1u L=180n\n"
                ".ends\n"
            ),
        },
        "scoring": ["audit_passed", "regex_match"],
        "must_include": ["dangling"],
    }
    base.update(overrides)
    return BenchTask.model_validate(base)


# ---------------------------------------------------------------------------
# Adapter dispatch
# ---------------------------------------------------------------------------


def test_dry_run_adapter_emits_metrics(tmp_path):
    task = _dry_task()
    res = dry_run_adapter(task, tmp_path)
    assert res.status is BenchStatus.PASS
    assert res.metrics["Adc_dB"] == 60.0
    assert res.compile_ok and res.sim_ok
    assert (tmp_path / "dry_run.txt").is_file()


def test_run_task_routes_dry_run(tmp_path):
    task = _dry_task()
    res = run_task(task, tmp_path)
    assert res.status is BenchStatus.PASS
    # adapter_runtime_s is appended as a note by run_task itself.
    assert any(n.startswith("adapter_runtime_s=") for n in res.notes)


def test_run_task_unknown_harness_returns_fail_infra(tmp_path):
    task = _dry_task()
    # Sneak past frozen dispatch by yanking the entry temporarily.
    orig = HARNESS_DISPATCH.pop("dry_run")
    try:
        res = run_task(task, tmp_path)
    finally:
        HARNESS_DISPATCH["dry_run"] = orig
    assert res.status is BenchStatus.FAIL_INFRA
    assert "no adapter registered" in (res.errors[0] if res.errors else "")


def test_resolve_callable_rejects_outside_bench_namespace():
    with pytest.raises(ValueError):
        resolve_callable("eda_agents.core.spice_runner:SpiceRunner")
    fn = resolve_callable("eda_agents.bench.adapters:dry_run_adapter")
    assert callable(fn)


def test_callable_adapter_missing_callable(tmp_path):
    task = _dry_task(harness="callable", inputs={})
    res = callable_adapter(task, tmp_path)
    assert res.status is BenchStatus.FAIL_INFRA


def test_pre_sim_gate_detects_violation_via_callable(tmp_path):
    task = _bugfix_task()
    res = run_task(task, tmp_path)
    assert res.status is BenchStatus.PASS
    assert res.metrics["passed_gate"] is False
    assert res.metrics["violations"] >= 1


def test_pre_sim_gate_clean_subckt(tmp_path):
    task = _bugfix_task(
        id="bugfix_floating_clean",
        inputs={
            "callable": "eda_agents.bench.adapters:run_pre_sim_gate_on_inline_netlist",
            "gate": "floating_nodes",
            "subckt": "clean_inv",
            "expect_violation": False,
            "netlist": (
                ".subckt clean_inv in out vdd vss\n"
                "M1 out in vdd vdd pfet W=2u L=180n\n"
                "M2 out in vss vss nfet W=1u L=180n\n"
                ".ends\n"
            ),
        },
        scoring=["audit_passed"],
        must_include=[],
    )
    res = run_task(task, tmp_path)
    assert res.status is BenchStatus.PASS
    assert res.metrics["passed_gate"] is True


def test_pre_sim_gate_unexpected_outcome_fails_audit(tmp_path):
    task = _bugfix_task(
        inputs={
            "callable": "eda_agents.bench.adapters:run_pre_sim_gate_on_inline_netlist",
            "gate": "floating_nodes",
            "subckt": "clean_inv",
            "expect_violation": True,  # we *expect* a violation but won't get one
            "netlist": (
                ".subckt clean_inv in out vdd vss\n"
                "M1 out in vdd vdd pfet W=2u L=180n\n"
                "M2 out in vss vss nfet W=1u L=180n\n"
                ".ends\n"
            ),
        },
        scoring=["audit_passed"],
        must_include=[],
    )
    res = run_task(task, tmp_path)
    assert res.status is BenchStatus.FAIL_AUDIT


# ---------------------------------------------------------------------------
# Audit logic
# ---------------------------------------------------------------------------


def test_audit_passes_when_metrics_in_range():
    task = _dry_task(
        scoring=["compile", "sim_run", "metrics_in_range"],
        expected_metrics={
            "Adc_dB": {"min": 30.0},
            "GBW_Hz": {"min": 1.0e6},
        },
    )
    adapter_res = AdapterResult(
        status=BenchStatus.PASS,
        backend_used="dry-run",
        metrics={"Adc_dB": 60.0, "GBW_Hz": 5.0e6},
        compile_ok=True,
        sim_ok=True,
    )
    final, scores, notes = audit_adapter_result(task, adapter_res)
    assert final is BenchStatus.PASS
    assert scores.metrics_in_range == 1.0
    assert any("in range" in n for n in notes)


def test_audit_downgrades_to_fail_audit_when_metric_out_of_range():
    task = _dry_task(
        scoring=["compile", "sim_run", "metrics_in_range"],
        expected_metrics={"Adc_dB": {"min": 100.0}},
    )
    adapter_res = AdapterResult(
        status=BenchStatus.PASS,
        backend_used="dry-run",
        metrics={"Adc_dB": 60.0},
        compile_ok=True,
        sim_ok=True,
    )
    final, scores, notes = audit_adapter_result(task, adapter_res)
    assert final is BenchStatus.FAIL_AUDIT
    assert scores.metrics_in_range == 0.0
    assert any("OUT of range" in n for n in notes)


def test_audit_propagates_fail_sim_unchanged():
    task = _dry_task(
        scoring=["compile", "sim_run", "metrics_in_range"],
        expected_metrics={"Adc_dB": {"min": 0.0}},
    )
    adapter_res = AdapterResult(
        status=BenchStatus.FAIL_SIM,
        backend_used="ngspice-osdi",
        metrics={},
        compile_ok=True,
        sim_ok=False,
        errors=["ngspice failed"],
    )
    final, scores, _ = audit_adapter_result(task, adapter_res)
    assert final is BenchStatus.FAIL_SIM
    assert scores.compile == 1.0
    assert scores.sim_run == 0.0


def test_audit_must_include_failure_downgrades():
    task = _dry_task(
        scoring=["regex_match"],
        must_include=["UNFINDABLE_PATTERN"],
    )
    adapter_res = AdapterResult(
        status=BenchStatus.PASS,
        backend_used="dry-run",
        raw_text="hello world",
        compile_ok=True,
        sim_ok=True,
    )
    final, scores, notes = audit_adapter_result(task, adapter_res)
    assert final is BenchStatus.FAIL_AUDIT
    assert scores.regex_match == 0.0
    assert any("missing required pattern" in n for n in notes)


def test_audit_must_not_include_present_downgrades():
    task = _dry_task(
        scoring=["regex_match"],
        must_not_include=["FORBIDDEN"],
    )
    adapter_res = AdapterResult(
        status=BenchStatus.PASS,
        backend_used="dry-run",
        raw_text="this contains FORBIDDEN content",
        compile_ok=True,
        sim_ok=True,
    )
    final, scores, _ = audit_adapter_result(task, adapter_res)
    assert final is BenchStatus.FAIL_AUDIT
    assert scores.regex_match == 0.0


def test_audit_metrics_in_range_without_expected_metrics_marks_fail():
    task = _dry_task(
        scoring=["metrics_in_range"],
        expected_metrics={},
    )
    adapter_res = AdapterResult(
        status=BenchStatus.PASS,
        backend_used="dry-run",
        compile_ok=True,
        sim_ok=True,
    )
    final, scores, notes = audit_adapter_result(task, adapter_res)
    assert final is BenchStatus.FAIL_AUDIT
    assert scores.metrics_in_range == 0.0
    assert any("no expected_metrics" in n for n in notes)


def test_audit_fail_infra_short_circuits_scoring():
    task = _dry_task(scoring=["compile", "sim_run", "metrics_in_range"])
    adapter_res = AdapterResult(
        status=BenchStatus.FAIL_INFRA,
        backend_used="ngspice-missing",
        errors=["ngspice not on PATH"],
    )
    final, scores, _ = audit_adapter_result(task, adapter_res)
    assert final is BenchStatus.FAIL_INFRA
    assert isinstance(scores, BenchScores)


# ---------------------------------------------------------------------------
# execute_task / run_batch
# ---------------------------------------------------------------------------


def test_execute_task_returns_bench_result_for_dry_run(tmp_path):
    task = _dry_task(
        scoring=["compile", "sim_run", "metrics_in_range"],
        expected_metrics={"Adc_dB": {"min": 30.0}},
    )
    res = execute_task(task, tmp_path)
    assert res.task_id == task.id
    assert res.status is BenchStatus.PASS
    assert res.duration_s >= 0.0
    assert res.scores.weighted_total > 0.0


def test_execute_task_records_error_on_adapter_crash(tmp_path):
    # A callable harness pointing at a non-existent function triggers an
    # ImportError inside the callable adapter — that path is *handled*
    # (returns FAIL_INFRA). To force the runner to wrap an exception we
    # patch HARNESS_DISPATCH with something that raises raw.
    def boom(task, work_dir):
        raise RuntimeError("synthetic crash")

    HARNESS_DISPATCH["dry_run"], saved = boom, HARNESS_DISPATCH["dry_run"]
    try:
        res = execute_task(_dry_task(), tmp_path)
    finally:
        HARNESS_DISPATCH["dry_run"] = saved
    assert res.status is BenchStatus.ERROR
    assert any("synthetic crash" in e for e in res.errors)


def test_run_batch_writes_per_task_json_and_summary(tmp_path):
    tasks = [
        _dry_task(),
        _dry_task(
            id="e2e_dry_high_bar",
            scoring=["metrics_in_range"],
            expected_metrics={"Adc_dB": {"min": 9999.0}},
        ),
    ]
    summary = run_batch(tasks, output_root=tmp_path, workers=1)
    assert summary.total == 2
    assert summary.passed == 1
    assert summary.failed == 1
    run_dir = tmp_path / summary.run_id
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "report.md").is_file()
    assert (tmp_path / "latest.md").is_file()
    for t in tasks:
        assert (run_dir / f"{t.id}.json").is_file()


def test_render_markdown_report_includes_pass_rate():
    task = _dry_task()
    summary = run_batch([task], output_root=Path("/tmp/_bench_render_test"), workers=1)
    md = render_markdown_report(summary)
    assert "Pass@1" in md
    assert "PASS" in md.upper()
    assert task.id in md


# ---------------------------------------------------------------------------
# Loader/seed sanity
# ---------------------------------------------------------------------------


def test_seed_tasks_have_known_harnesses():
    """Every seed task's harness must be in HARNESS_DISPATCH."""
    from eda_agents.bench import load_tasks_from_dir

    repo_root = Path(__file__).resolve().parents[1]
    seed_root = repo_root / "bench" / "tasks"
    if not seed_root.exists():
        pytest.skip("no seed tasks shipped yet")
    tasks = load_tasks_from_dir(seed_root)
    assert tasks, "seed tasks expected"
    unknown = [t for t in tasks if t.harness.value not in HARNESS_DISPATCH]
    assert not unknown, (
        f"seed tasks reference harnesses with no adapter: "
        f"{[(t.id, t.harness.value) for t in unknown]}"
    )
    for t in tasks:
        if t.harness.value == "callable":
            dotted = t.inputs.get("callable")
            assert dotted, f"{t.id}: callable harness needs inputs.callable"
            # Resolves without raising.
            resolve_callable(dotted)
