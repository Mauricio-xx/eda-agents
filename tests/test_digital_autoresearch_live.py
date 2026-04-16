"""Live-mode integration test for the digital_autoresearch adapter.

Written in S9-residual-closure (gap #4 residual). The mock-mode path
is covered in ``tests/test_bench_runner.py`` and runs on every CI
pull. This file flips the switch and exercises the *real*
:class:`DigitalAutoresearchRunner` path:

  1. GenericDesign wraps the 4-bit counter LibreLane project.
  2. The runner calls OpenRouter (Gemini Flash by default) to propose
     2 sets of flow-config overrides.
  3. For each proposal LibreLane runs to signoff
     (``Checker.KLayoutDRC``) on GF180MCU-D.
  4. ``FlowMetrics.from_librelane_run_dir`` extracts WNS, cell count,
     area, power.
  5. Greedy keep/discard against
     :meth:`GenericDesign.check_validity`.
  6. Audit: ``iterations_kept >= 1``.

Prereqs — the test skips cleanly when any is missing:
  * ``OPENROUTER_API_KEY`` in env.
  * LibreLane venv reachable via ``_find_librelane_python``.
  * GF180MCU-D PDK root (either ``$PDK_ROOT`` or the wafer-space
    fork at ``/home/montanares/git/wafer-space-gf180mcu``).

One eval on the counter is ~55 s LibreLane + a few seconds of LLM
roundtrip. Budget=2 therefore lands around 2 minutes wall-clock,
plus a few seconds of process startup.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eda_agents.bench.adapters import digital_autoresearch_adapter
from eda_agents.bench.models import BenchStatus, BenchTask


def _bench_design_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "bench" / "designs" / "counter_bench"
        if candidate.is_dir():
            return candidate
    raise RuntimeError("counter_bench design dir not found in repo tree")


def _librelane_available() -> bool:
    try:
        from eda_agents.core.librelane_runner import _find_librelane_python
    except ImportError:
        return False
    return _find_librelane_python() is not None


def _gf180_root() -> Path | None:
    explicit = os.environ.get("PDK_ROOT")
    if explicit and (Path(explicit) / "gf180mcuD").is_dir():
        return Path(explicit)
    fork = Path("/home/montanares/git/wafer-space-gf180mcu")
    if (fork / "gf180mcuD").is_dir():
        return fork
    return None


@pytest.mark.librelane
def test_digital_autoresearch_real_mode_produces_real_metrics(tmp_path):
    """Budget=2 LibreLane run + Gemini Flash proposals + audit PASS.

    The mock-mode counterpart is
    ``tests/test_bench_runner.py::test_digital_autoresearch_runs_mock_mode``;
    this test is the real-tool mirror.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set; cannot call LLM")
    if not _librelane_available():
        pytest.skip("librelane venv not reachable")
    if _gf180_root() is None:
        pytest.skip("GF180MCU-D PDK not reachable")

    design_dir = _bench_design_dir()

    task = BenchTask(
        id="digital_autoresearch_live_integration",
        family="end-to-end",
        category="digital",
        domain="digital",
        pdk="gf180mcu",
        difficulty="hard",
        expected_backend="librelane",
        harness="digital_autoresearch",
        topology="counter_4bit",
        inputs={
            "design_dir": str(design_dir),
            "budget": 2,
            # No mock_metrics_path: live-mode.
        },
        expected_metrics={
            "iterations_kept": {"min": 1},
        },
        scoring=["audit_passed"],
        weight=1.0,
        timeout_s=1800,
    )

    work_dir = tmp_path / "autoresearch_work"
    result = digital_autoresearch_adapter(task, work_dir)

    # Surface any skip-style infra failure clearly so the test log
    # explains *why* the runner bailed (missing PDK path, LibreLane
    # step rename, etc.) rather than hiding behind a generic assert.
    assert result.status is BenchStatus.PASS, (
        f"live autoresearch did not PASS: status={result.status.name} "
        f"errors={result.errors} notes={result.notes}"
    )

    # We are in real mode; the adapter's notes must flag that.
    assert any(
        "mode=live" in n for n in result.notes
    ), f"expected mode=live in notes, got {result.notes}"
    assert not any(
        "mode=mock" in n for n in result.notes
    ), f"unexpected mode=mock in notes: {result.notes}"

    # Budget was 2 -> 2 evaluations attempted.
    assert result.metrics.get("iterations_total", 0.0) == pytest.approx(2.0), (
        f"expected 2 total evals, got {result.metrics.get('iterations_total')}"
    )

    # Audit contract: the run must keep at least one valid eval.
    assert result.metrics.get("iterations_kept", 0.0) >= 1.0, (
        f"live run kept 0 evals (audit would FAIL). notes={result.notes}"
    )

    # best_fom > 0 means a kept eval produced a non-degenerate metric.
    assert result.metrics.get("best_fom", 0.0) > 0.0, (
        f"best_fom == 0 means no eval survived validity_check. "
        f"metrics={result.metrics}"
    )

    # TSV artefact should exist and have at least 2 data rows (one
    # per eval).
    tsv_artifacts = [a for a in result.artifacts if a.endswith(".tsv")]
    assert tsv_artifacts, f"no TSV artefact in {result.artifacts}"
    tsv_path = Path(tsv_artifacts[0])
    assert tsv_path.is_file(), f"results.tsv missing: {tsv_path}"
    tsv_lines = tsv_path.read_text().strip().splitlines()
    assert len(tsv_lines) >= 3, (
        f"results.tsv has only {len(tsv_lines)} lines (header + rows); "
        f"expected at least 3 for budget=2."
    )
