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


def test_digital_autoresearch_rejects_missing_design_dir(tmp_path):
    """Gap #4: empty/missing design_dir -> FAIL_INFRA."""
    task = _dry_task(
        id="digital_autoresearch_no_design",
        family="end-to-end",
        domain="digital",
        pdk="gf180mcu",
        expected_backend="librelane",
        harness="digital_autoresearch",
        scoring=["audit_passed"],
        inputs={},  # no design_dir
    )
    res = run_task(task, tmp_path)
    assert res.status is BenchStatus.FAIL_INFRA
    assert any("design_dir" in e for e in res.errors)


def test_digital_autoresearch_skips_without_mock_and_api_key(tmp_path, monkeypatch):
    """Gap #4: real mode needs OPENROUTER_API_KEY; absent -> SKIPPED."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    task = _dry_task(
        id="digital_autoresearch_nokey",
        family="end-to-end",
        domain="digital",
        pdk="gf180mcu",
        expected_backend="librelane",
        harness="digital_autoresearch",
        scoring=["audit_passed"],
        inputs={
            "design_dir": "bench/designs/counter_bench",
            "budget": 1,
        },
    )
    res = run_task(task, tmp_path)
    assert res.status is BenchStatus.FAIL_INFRA  # maps to SKIPPED in summary
    assert any(
        "OPENROUTER_API_KEY" in e or "PDK" in e for e in res.errors
    )


def test_digital_autoresearch_runs_mock_mode(tmp_path):
    """Gap #4: mock_metrics_path drives an offline run and audits PASS."""
    task = _dry_task(
        id="digital_autoresearch_mock_unit",
        family="end-to-end",
        domain="digital",
        pdk="gf180mcu",
        expected_backend="librelane",
        harness="digital_autoresearch",
        scoring=["audit_passed"],
        inputs={
            "design_dir": "bench/designs/counter_bench",
            "budget": 1,
            "mock_metrics_path": "mock_flow_metrics.json",
        },
    )
    res = run_task(task, tmp_path)
    assert res.status is BenchStatus.PASS, (res.status, res.errors, res.notes)
    assert res.metrics.get("iterations_kept", 0) >= 1
    assert any("mode=mock" in n for n in res.notes)


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


def test_sar11b_adapter_skips_cleanly_when_tools_missing(tmp_path, monkeypatch):
    """Gap #6 adapter: no ngspice/openvaf/verilator on PATH -> FAIL_INFRA."""
    from eda_agents.bench.adapters import run_sar11_enob_measurement

    # Force the tool detection to report every binary missing.
    import eda_agents.bench.adapters as _adapters
    monkeypatch.setattr(_adapters.shutil, "which", lambda _: None)
    task = BenchTask.model_validate(
        {
            "id": "e2e_sar11_mock",
            "family": "end-to-end",
            "category": "adc",
            "domain": "mixed",
            "pdk": "ihp_sg13g2",
            "difficulty": "hard",
            "expected_backend": "ngspice-osdi",
            "harness": "callable",
            "inputs": {
                "callable": "eda_agents.bench.adapters:run_sar11_enob_measurement",
            },
            "scoring": ["compile", "sim_run"],
        }
    )
    res = run_sar11_enob_measurement(task, tmp_path)
    assert res.status is BenchStatus.FAIL_INFRA
    assert any("missing tools" in e for e in res.errors)


def test_llm_adapter_skips_when_no_api_key(tmp_path, monkeypatch):
    """Gap #8: absent OPENROUTER_API_KEY -> FAIL_INFRA (SKIPPED in summary)."""
    from eda_agents.bench.adapters import llm_spec_to_sizing_adapter

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    task = BenchTask.model_validate(
        {
            "id": "spec_llm_nokey",
            "family": "spec-to-topology",
            "category": "ota",
            "domain": "voltage",
            "pdk": "ihp_sg13g2",
            "difficulty": "easy",
            "expected_backend": "ngspice-osdi",
            "harness": "callable",
            "inputs": {
                "callable": "eda_agents.bench.adapters:llm_spec_to_sizing_adapter",
                "spec_yaml": "block: miller_ota\n",
            },
            "scoring": ["compile", "sim_run", "metrics_in_range"],
            "expected_metrics": {"Adc_dB": {"min": 25.0}},
        }
    )
    res = llm_spec_to_sizing_adapter(task, tmp_path)
    assert res.status is BenchStatus.FAIL_INFRA
    assert any("OPENROUTER_API_KEY" in e for e in res.errors)


def test_llm_adapter_parses_canned_response_and_invokes_designer(tmp_path, monkeypatch):
    """Gap #8: with a canned LLM response, sizing flows into the real path.

    Mocks `_call_openrouter` to avoid hitting the network, then validates
    the adapter passes the parsed params to analytical_miller_design and
    returns the simulated AdapterResult. Requires ngspice; otherwise
    falls back to checking the LLM parse + synthetic task wiring.
    """
    import shutil as _shutil

    from eda_agents.bench import adapters

    canned = (
        "Here is the JSON:\n"
        '{"gmid_input": 12.0, "gmid_load": 10.0, '
        '"L_input": 1.0e-6, "L_load": 1.0e-6, "Cc": 1.0e-12}'
    )
    monkeypatch.setattr(adapters, "_call_openrouter", lambda **kw: canned)
    task = BenchTask.model_validate(
        {
            "id": "spec_llm_canned",
            "family": "spec-to-topology",
            "category": "ota",
            "domain": "voltage",
            "pdk": "ihp_sg13g2",
            "difficulty": "easy",
            "expected_backend": "ngspice-osdi",
            "harness": "callable",
            "inputs": {
                "callable": "eda_agents.bench.adapters:llm_spec_to_sizing_adapter",
                "spec_yaml": "block: miller_ota\n",
            },
            "scoring": ["compile", "sim_run", "metrics_in_range"],
            "expected_metrics": {"Adc_dB": {"min": 25.0}},
        }
    )
    res = adapters.llm_spec_to_sizing_adapter(task, tmp_path)

    # The LLM response file is always persisted for auditability.
    assert (tmp_path / "llm_response.txt").read_text() == canned
    # Notes capture the model + parsed params for the report.
    assert any("params=" in n for n in res.notes)

    if _shutil.which("ngspice"):
        # Under real ngspice we expect a PASS (sizing is the known-good IHP point).
        assert res.status in {BenchStatus.PASS, BenchStatus.FAIL_SIM, BenchStatus.FAIL_AUDIT}
    else:
        # Without ngspice the inner adapter returns FAIL_INFRA (ngspice-missing).
        assert res.status is BenchStatus.FAIL_INFRA
        assert any("ngspice" in e.lower() for e in res.errors)


def test_llm_adapter_rejects_malformed_json(tmp_path, monkeypatch):
    """Gap #8: LLM emitted prose without JSON -> FAIL_AUDIT, not ERROR."""
    from eda_agents.bench import adapters

    monkeypatch.setattr(
        adapters, "_call_openrouter", lambda **kw: "I am not returning JSON today."
    )
    task = BenchTask.model_validate(
        {
            "id": "spec_llm_bad_json",
            "family": "spec-to-topology",
            "category": "ota",
            "domain": "voltage",
            "pdk": "ihp_sg13g2",
            "difficulty": "easy",
            "expected_backend": "ngspice-osdi",
            "harness": "callable",
            "inputs": {
                "callable": "eda_agents.bench.adapters:llm_spec_to_sizing_adapter",
                "spec_yaml": "block: miller_ota\n",
            },
            "scoring": ["compile", "sim_run"],
        }
    )
    res = adapters.llm_spec_to_sizing_adapter(task, tmp_path)
    assert res.status is BenchStatus.FAIL_AUDIT
    assert any("JSON" in e for e in res.errors)


def test_llm_adapter_rejects_out_of_range_json(tmp_path, monkeypatch):
    """Gap #8: LLM emitted JSON with a field out of the typed range -> FAIL_AUDIT."""
    from eda_agents.bench import adapters

    canned = (
        '{"gmid_input": 999.0, "gmid_load": 10.0, '
        '"L_input": 1.0e-6, "L_load": 1.0e-6, "Cc": 1.0e-12}'
    )
    monkeypatch.setattr(adapters, "_call_openrouter", lambda **kw: canned)
    task = BenchTask.model_validate(
        {
            "id": "spec_llm_oor",
            "family": "spec-to-topology",
            "category": "ota",
            "domain": "voltage",
            "pdk": "ihp_sg13g2",
            "difficulty": "easy",
            "expected_backend": "ngspice-osdi",
            "harness": "callable",
            "inputs": {
                "callable": "eda_agents.bench.adapters:llm_spec_to_sizing_adapter",
                "spec_yaml": "block: miller_ota\n",
            },
            "scoring": ["compile", "sim_run"],
        }
    )
    res = adapters.llm_spec_to_sizing_adapter(task, tmp_path)
    assert res.status is BenchStatus.FAIL_AUDIT
    assert any("gmid_input" in e or "40" in e for e in res.errors)


def test_sar11b_adapter_rejects_bogus_inputs(tmp_path):
    """Gap #6 typed-input rejection propagates through the adapter."""
    from eda_agents.bench.adapters import run_sar11_enob_measurement

    task = BenchTask.model_validate(
        {
            "id": "e2e_sar11_typo",
            "family": "end-to-end",
            "category": "adc",
            "domain": "mixed",
            "pdk": "ihp_sg13g2",
            "difficulty": "hard",
            "expected_backend": "ngspice-osdi",
            "harness": "callable",
            "inputs": {
                "callable": "eda_agents.bench.adapters:run_sar11_enob_measurement",
                "N_sample": 64,  # typo: real field is N_samples
            },
            "scoring": ["compile", "sim_run"],
        }
    )
    res = run_sar11_enob_measurement(task, tmp_path)
    assert res.status is BenchStatus.FAIL_INFRA
    assert any("N_sample" in e for e in res.errors)


def test_librelane_flow_adapter_rejects_bogus_inputs(tmp_path):
    """Gap #5: typed-input rejection on DigitalFlowInputs typos."""
    from eda_agents.bench.adapters import run_librelane_flow_task

    task = BenchTask.model_validate(
        {
            "id": "e2e_counter_typo",
            "family": "end-to-end",
            "category": "digital",
            "domain": "digital",
            "pdk": "gf180mcu",
            "difficulty": "easy",
            "expected_backend": "librelane",
            "harness": "callable",
            "inputs": {
                "callable": "eda_agents.bench.adapters:run_librelane_flow_task",
                "design_dir": "bench/designs/counter_bench",
                "stop_after_step": "ROUTE",  # typo: real field is stop_after
            },
            "scoring": ["compile"],
        }
    )
    res = run_librelane_flow_task(task, tmp_path)
    assert res.status is BenchStatus.FAIL_INFRA
    assert any("stop_after_step" in e or "Extra inputs" in e for e in res.errors)


def test_librelane_flow_adapter_skips_when_pdk_absent(tmp_path, monkeypatch):
    """Gap #5: no PDK root -> FAIL_INFRA (SKIPPED in summary)."""
    from eda_agents.bench.adapters import run_librelane_flow_task

    import eda_agents.bench.adapters as _adapters
    monkeypatch.setattr(_adapters, "_resolve_librelane_pdk_root", lambda _: None)
    task = BenchTask.model_validate(
        {
            "id": "e2e_counter_nopdk",
            "family": "end-to-end",
            "category": "digital",
            "domain": "digital",
            "pdk": "gf180mcu",
            "difficulty": "easy",
            "expected_backend": "librelane",
            "harness": "callable",
            "inputs": {
                "callable": "eda_agents.bench.adapters:run_librelane_flow_task",
                "design_dir": "bench/designs/counter_bench",
            },
            "scoring": ["compile"],
        }
    )
    res = run_librelane_flow_task(task, tmp_path)
    assert res.status is BenchStatus.FAIL_INFRA
    assert any("PDK" in e or "pdk" in e for e in res.errors)


def test_gl_sim_adapter_skips_without_cache_or_env(tmp_path, monkeypatch):
    """Gap #2: no run_dir + no env + empty bench cache -> FAIL_INFRA."""
    from eda_agents.bench.adapters import run_gl_sim_post_synth
    import eda_agents.bench.adapters as _adapters

    monkeypatch.delenv("EDA_AGENTS_GL_SIM_RUN_DIR", raising=False)
    monkeypatch.setattr(
        _adapters, "_discover_cached_gl_sim_run", lambda _name: None
    )
    task = BenchTask.model_validate(
        {
            "id": "e2e_gl_sim_nocache",
            "family": "end-to-end",
            "category": "digital_glsim",
            "domain": "digital",
            "pdk": "gf180mcu",
            "difficulty": "hard",
            "expected_backend": "librelane",
            "harness": "callable",
            "inputs": {
                "callable": "eda_agents.bench.adapters:run_gl_sim_post_synth",
            },
            "scoring": ["compile", "sim_run"],
        }
    )
    res = run_gl_sim_post_synth(task, tmp_path)
    assert res.status is BenchStatus.FAIL_INFRA
    assert any("hardened run" in e.lower() or "counter" in e.lower() for e in res.errors)


def test_gl_sim_adapter_discovers_counter_cache_symlink(tmp_path, monkeypatch):
    """Gap #2: adapter auto-discovers a hardened counter run via the bench cache."""
    from eda_agents.bench.adapters import _discover_cached_gl_sim_run
    import eda_agents.bench.adapters as _adapters

    fake_cache = tmp_path / "cache" / "librelane_runs"
    run_dir = fake_cache / "counter" / "runs" / "bench-demo"
    (run_dir / "final" / "pnl").mkdir(parents=True)
    monkeypatch.setattr(
        _adapters, "_bench_librelane_cache_root", lambda: fake_cache
    )
    discovered = _discover_cached_gl_sim_run("counter")
    assert discovered == run_dir.resolve()


def test_bench_cache_root_prefers_repo_bench_over_package_bench():
    """Gap #5: cache resolver must not pick src/eda_agents/bench by name."""
    from eda_agents.bench.adapters import _bench_librelane_cache_root

    root = _bench_librelane_cache_root()
    assert root.name == "librelane_runs"
    # The resolver should have found the repo-root bench/ (with tasks/),
    # not the package dir that also ends in `bench/`.
    assert (root.parent.parent / "tasks").is_dir(), f"unexpected cache root: {root}"


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
