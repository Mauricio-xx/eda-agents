"""Bench runner: dispatches tasks, aggregates :class:`BenchResult`, writes report.

The runner is intentionally self-contained:

* No network calls (``scripts/run_bench.py --dry-run`` works on a clean
  CI host with no ngspice / no PDKs).
* Reuses :class:`eda_agents.bridge.JobRegistry` (S8) when the caller asks
  for parallelism via ``--workers > 1``. Each task runs in a registry
  job; the runner waits on all of them and collates the typed results.
* Audits *every* result before declaring PASS — see
  :func:`audit_adapter_result` for the gating rules. We never emit
  ``status=PASS`` for a task whose required scoring criterion didn't run.

Reporting:

* ``bench/results/<run_id>/`` — one ``BenchResult`` JSON per task plus
  ``summary.json`` and ``report.md``.
* ``bench/results/latest.md`` — symlink-style copy of the most recent
  ``report.md`` so docs and PR comments can hot-link a stable path.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from eda_agents.bench.adapters import AdapterResult, run_task
from eda_agents.bench.models import (
    BenchResult,
    BenchScores,
    BenchStatus,
    BenchTask,
    MetricBound,
    TaskScoring,
)


_LATEST_FILENAME = "latest.md"


@dataclass
class RunSummary:
    """Aggregate verdict across one batch."""

    run_id: str
    total: int
    passed: int
    failed: int
    skipped: int
    errored: int
    by_family: dict[str, dict[str, int]]
    results: list[BenchResult]

    def pass_rate(self) -> float:
        denom = self.total - self.skipped
        return 0.0 if denom <= 0 else self.passed / denom


# ---------------------------------------------------------------------------
# Audit (per-task scoring)
# ---------------------------------------------------------------------------


def _check_regex_set(patterns: Iterable[str], text: str, *, want_match: bool) -> tuple[bool, list[str]]:
    """Return ``(ok, notes)`` for ``must_include`` / ``must_not_include`` checks."""
    notes: list[str] = []
    ok = True
    for pat in patterns:
        try:
            found = bool(re.search(pat, text, re.MULTILINE))
        except re.error as exc:
            notes.append(f"regex {pat!r} invalid: {exc}")
            ok = False
            continue
        if want_match and not found:
            ok = False
            notes.append(f"missing required pattern: {pat!r}")
        elif (not want_match) and found:
            ok = False
            notes.append(f"forbidden pattern present: {pat!r}")
    return ok, notes


def _check_metric_bounds(
    metrics: dict, expected: dict[str, MetricBound]
) -> tuple[bool, list[str]]:
    notes: list[str] = []
    ok = True
    for name, bound in expected.items():
        if name not in metrics:
            ok = False
            notes.append(f"metric {name!r} missing from result")
            continue
        try:
            value = float(metrics[name])
        except (TypeError, ValueError):
            ok = False
            notes.append(f"metric {name!r} not numeric: {metrics[name]!r}")
            continue
        passed, margin = bound.check(value)
        if passed:
            notes.append(f"{name}={value:.4g} in range (margin={margin:.3g})")
        else:
            ok = False
            notes.append(f"{name}={value:.4g} OUT of range (margin={margin:.3g})")
    return ok, notes


def _gather_text_for_regex(adapter_res: AdapterResult) -> str:
    """Concatenate adapter raw_text + readable artifacts for regex match."""
    chunks: list[str] = []
    if adapter_res.raw_text:
        chunks.append(adapter_res.raw_text)
    for artifact in adapter_res.artifacts:
        try:
            p = Path(artifact)
            if p.is_file() and p.stat().st_size < 1_000_000:
                chunks.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def audit_adapter_result(
    task: BenchTask, adapter_res: AdapterResult
) -> tuple[BenchStatus, BenchScores, list[str]]:
    """Translate an :class:`AdapterResult` into a graded :class:`BenchResult`.

    Maps adapter status + scoring criteria to the final BenchStatus
    enum. Critically, a task whose adapter returned PASS but failed any
    requested ``scoring`` criterion is **downgraded** to FAIL_AUDIT —
    we do not let an over-eager adapter paint over a failing audit.
    """
    notes: list[str] = []
    scores: dict[str, float] = {}

    # 1. Mechanical pass-throughs from the adapter.
    if adapter_res.compile_ok is not None:
        scores["compile"] = 1.0 if adapter_res.compile_ok else 0.0
    if adapter_res.sim_ok is not None:
        scores["sim_run"] = 1.0 if adapter_res.sim_ok else 0.0

    # 2. Adapter-level failure modes short-circuit the audit but still
    #    populate scores so the report shows what worked.
    if adapter_res.status in {
        BenchStatus.ERROR,
        BenchStatus.FAIL_INFRA,
    }:
        weighted = sum(scores.values()) / max(len(scores), 1)
        return adapter_res.status, BenchScores(
            **scores, weighted_total=weighted
        ), notes

    if adapter_res.status is BenchStatus.FAIL_SIM:
        weighted = sum(scores.values()) / max(len(scores), 1)
        return BenchStatus.FAIL_SIM, BenchScores(
            **scores, weighted_total=weighted
        ), notes

    if adapter_res.status is BenchStatus.FAIL_COMPILE:
        weighted = sum(scores.values()) / max(len(scores), 1)
        return BenchStatus.FAIL_COMPILE, BenchScores(
            **scores, weighted_total=weighted
        ), notes

    # 3. Scoring criteria.
    audit_results: list[bool] = []

    if TaskScoring.AUDIT_PASSED in task.scoring:
        ok = adapter_res.status is BenchStatus.PASS
        scores["audit_passed"] = 1.0 if ok else 0.0
        audit_results.append(ok)

    if TaskScoring.REGEX_MATCH in task.scoring or task.must_include or task.must_not_include:
        text = _gather_text_for_regex(adapter_res)
        inc_ok, inc_notes = _check_regex_set(
            task.must_include, text, want_match=True
        )
        exc_ok, exc_notes = _check_regex_set(
            task.must_not_include, text, want_match=False
        )
        notes.extend(inc_notes + exc_notes)
        regex_ok = inc_ok and exc_ok
        if TaskScoring.REGEX_MATCH in task.scoring:
            scores["regex_match"] = 1.0 if regex_ok else 0.0
            audit_results.append(regex_ok)

    if TaskScoring.METRICS_IN_RANGE in task.scoring:
        if not task.expected_metrics:
            notes.append(
                "scoring asks for metrics_in_range but task has no expected_metrics"
            )
            scores["metrics_in_range"] = 0.0
            audit_results.append(False)
        else:
            metrics_ok, metric_notes = _check_metric_bounds(
                adapter_res.metrics, task.expected_metrics
            )
            notes.extend(metric_notes)
            scores["metrics_in_range"] = 1.0 if metrics_ok else 0.0
            audit_results.append(metrics_ok)

    # If any score column dropped to 0, the task fails the audit.
    weighted = (
        sum(scores.values()) / len(scores) if scores else 0.0
    )
    final_status = BenchStatus.PASS
    if any(v == 0.0 for v in scores.values()):
        # If the only zero is sim_run because adapter didn't simulate,
        # we still PASS *if* every scoring criterion the task asked for
        # was satisfied. Otherwise downgrade.
        wanted = {s.value for s in task.scoring}
        unsatisfied = {
            name for name, v in scores.items()
            if v == 0.0 and name in wanted
        }
        if unsatisfied:
            final_status = BenchStatus.FAIL_AUDIT
            notes.append(
                f"audit downgraded: {sorted(unsatisfied)} did not pass"
            )

    return final_status, BenchScores(**scores, weighted_total=weighted), notes


# ---------------------------------------------------------------------------
# Per-task execution
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_task(task: BenchTask, run_root: Path) -> BenchResult:
    """Run a single task and return its graded :class:`BenchResult`."""
    work_dir = run_root / task.id
    started = _now_iso()
    t0 = time.monotonic()
    try:
        adapter_res = run_task(task, work_dir)
    except Exception as exc:  # noqa: BLE001 — the runner must never crash
        finished = _now_iso()
        return BenchResult(
            task_id=task.id,
            status=BenchStatus.ERROR,
            scores=BenchScores(weighted_total=0.0),
            harness_used=task.harness.value,
            backend_used=task.expected_backend.value,
            pdk_used=task.pdk,
            duration_s=time.monotonic() - t0,
            artifacts=[],
            metrics={},
            errors=[f"adapter raised {type(exc).__name__}: {exc}"],
            started=started,
            finished=finished,
        )
    duration = time.monotonic() - t0
    finished = _now_iso()

    final_status, scores, audit_notes = audit_adapter_result(task, adapter_res)
    metrics = {
        k: (v if isinstance(v, (int, float, str, bool)) or v is None else str(v))
        for k, v in adapter_res.metrics.items()
    }
    return BenchResult(
        task_id=task.id,
        status=final_status,
        scores=scores,
        harness_used=task.harness.value,
        backend_used=adapter_res.backend_used or task.expected_backend.value,
        pdk_used=task.pdk,
        duration_s=duration,
        artifacts=list(adapter_res.artifacts),
        metrics=metrics,
        errors=list(adapter_res.errors),
        notes=list(adapter_res.notes) + audit_notes,
        started=started,
        finished=finished,
    )


# ---------------------------------------------------------------------------
# Batch execution
# ---------------------------------------------------------------------------


def run_batch(
    tasks: list[BenchTask],
    *,
    output_root: Path,
    run_id: str | None = None,
    workers: int = 1,
) -> RunSummary:
    """Execute ``tasks`` and write artifacts under ``output_root/<run_id>/``.

    ``workers > 1`` opts into the bridge :class:`JobRegistry` for
    parallel execution. With ``workers == 1`` we stay single-threaded
    so stack traces stay attributable.
    """
    rid = run_id or _new_run_id()
    run_root = output_root / rid
    run_root.mkdir(parents=True, exist_ok=True)

    results: list[BenchResult] = []
    if workers <= 1 or len(tasks) <= 1:
        for task in tasks:
            res = execute_task(task, run_root)
            res.save_json(run_root / f"{task.id}.json")
            results.append(res)
    else:
        from eda_agents.bridge.jobs import JobRegistry

        registry = JobRegistry(jobs_dir=run_root / "_jobs", max_workers=workers)
        try:
            ids: dict[str, str] = {}
            for task in tasks:
                jid = registry.submit(
                    execute_task,
                    task,
                    run_root,
                    kind="bench-task",
                    metadata={"task_id": task.id},
                )
                ids[task.id] = jid
            for task in tasks:
                rec = registry.wait(ids[task.id], timeout=task.timeout_s + 60)
                if rec is None or "result" not in rec or rec.get("result") is None:
                    res = BenchResult(
                        task_id=task.id,
                        status=BenchStatus.ERROR,
                        scores=BenchScores(weighted_total=0.0),
                        harness_used=task.harness.value,
                        backend_used=task.expected_backend.value,
                        pdk_used=task.pdk,
                        duration_s=0.0,
                        errors=[
                            "registry returned no result; see "
                            f"{(run_root / '_jobs').as_posix()}"
                        ],
                        started=_now_iso(),
                        finished=_now_iso(),
                    )
                else:
                    res = BenchResult.model_validate(rec["result"])
                res.save_json(run_root / f"{task.id}.json")
                results.append(res)
        finally:
            registry.shutdown()

    summary = _build_summary(rid, results)
    (run_root / "summary.json").write_text(
        json.dumps(_summary_to_dict(summary), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report_path = run_root / "report.md"
    report_path.write_text(render_markdown_report(summary), encoding="utf-8")
    # Update the stable latest.md pointer.
    latest = output_root / _LATEST_FILENAME
    latest.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    return summary


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def _build_summary(run_id: str, results: list[BenchResult]) -> RunSummary:
    by_family = _count_by_family(results)
    return RunSummary(
        run_id=run_id,
        total=len(results),
        passed=sum(1 for r in results if r.status is BenchStatus.PASS),
        failed=sum(
            1 for r in results
            if r.status in {
                BenchStatus.FAIL_AUDIT,
                BenchStatus.FAIL_COMPILE,
                BenchStatus.FAIL_SIM,
            }
        ),
        skipped=sum(
            1 for r in results
            if r.status in {BenchStatus.SKIPPED, BenchStatus.FAIL_INFRA}
        ),
        errored=sum(1 for r in results if r.status is BenchStatus.ERROR),
        by_family=by_family,
        results=results,
    )


def _count_by_family(results: list[BenchResult]) -> dict[str, dict[str, int]]:
    """Group counts by the result file location prefix.

    Result objects do not carry the task family, so we infer it from
    the task id prefix. Prefixes follow ``{family-token}_*``:
    ``spec`` / ``bugfix`` / ``tb`` / ``e2e``. Unknown prefixes go into
    ``other``.
    """
    prefix_map = {
        "spec": "spec-to-topology",
        "bugfix": "bugfix",
        "tb": "tb-generation",
        "e2e": "end-to-end",
    }
    out: dict[str, dict[str, int]] = {}
    for r in results:
        prefix = r.task_id.split("_", 1)[0]
        family = prefix_map.get(prefix, "other")
        bucket = out.setdefault(
            family, {"total": 0, "pass": 0, "fail": 0, "skipped": 0, "error": 0}
        )
        bucket["total"] += 1
        if r.status is BenchStatus.PASS:
            bucket["pass"] += 1
        elif r.status in {BenchStatus.SKIPPED, BenchStatus.FAIL_INFRA}:
            bucket["skipped"] += 1
        elif r.status is BenchStatus.ERROR:
            bucket["error"] += 1
        else:
            bucket["fail"] += 1
    return out


def _summary_to_dict(s: RunSummary) -> dict:
    return {
        "run_id": s.run_id,
        "total": s.total,
        "passed": s.passed,
        "failed": s.failed,
        "skipped": s.skipped,
        "errored": s.errored,
        "pass_rate": s.pass_rate(),
        "by_family": s.by_family,
        "results": [r.model_dump(mode="json") for r in s.results],
    }


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def render_markdown_report(summary: RunSummary) -> str:
    """Return the contents of ``report.md`` for one run."""
    lines: list[str] = []
    lines.append(f"# eda-agents bench — run `{summary.run_id}`")
    lines.append("")
    lines.append(
        f"Total: **{summary.total}** — "
        f"PASS: **{summary.passed}**, FAIL: **{summary.failed}**, "
        f"SKIPPED: **{summary.skipped}**, ERROR: **{summary.errored}**, "
        f"Pass@1 (excluding skipped): **{summary.pass_rate():.0%}**"
    )
    lines.append("")
    lines.append("## By family")
    lines.append("")
    lines.append("| family | total | pass | fail | skipped | error |")
    lines.append("|---|---|---|---|---|---|")
    for fam, bucket in sorted(summary.by_family.items()):
        lines.append(
            f"| {fam} | {bucket['total']} | {bucket['pass']} | "
            f"{bucket['fail']} | {bucket['skipped']} | {bucket['error']} |"
        )
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append(
        "| task | status | harness | backend | pdk | duration_s | weighted | notes |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in summary.results:
        note_summary = "; ".join(r.notes[-2:]) if r.notes else ""
        # Trim very long notes so the table stays readable.
        note_summary = note_summary[:140]
        lines.append(
            f"| `{r.task_id}` | **{r.status.value}** | {r.harness_used} | "
            f"{r.backend_used or ''} | {r.pdk_used or ''} | "
            f"{r.duration_s:.2f} | {r.scores.weighted_total:.2f} | "
            f"{note_summary.replace('|', '/')} |"
        )
    lines.append("")
    if summary.errored:
        lines.append("## Errors")
        lines.append("")
        for r in summary.results:
            if r.status is BenchStatus.ERROR and r.errors:
                lines.append(f"### `{r.task_id}`")
                for e in r.errors:
                    lines.append(f"- {e}")
                lines.append("")
    return "\n".join(lines) + "\n"


__all__ = [
    "RunSummary",
    "audit_adapter_result",
    "execute_task",
    "render_markdown_report",
    "run_batch",
]
