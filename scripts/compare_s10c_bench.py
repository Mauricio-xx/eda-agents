#!/usr/bin/env python3
"""Compare S10c skill-injection ON vs OFF bench runs.

Discovers all ``bench/results/s10c_{on,off}_<TS>_seed*/`` directories
for a given timestamp, loads each per-task JSON, and writes a single
markdown report with Pass@1 / best_fom / total_tokens / wall-time
deltas and a green/red gate verdict.

Pass@1 is computed as the fraction of runs with ``status == "PASS"``
across the seeds of a given condition × task pair. best_fom and
total_tokens are aggregated as mean and sum respectively over the
same seeds.

Gate rules (see ~/.claude/plans/wiggly-sleeping-lighthouse.md L130):
  * Pass@1: ``pass_on - pass_off`` must be ``>= -0.05`` (absolute).
  * best_fom: ``(fom_on - fom_off) / max(|fom_off|, eps)`` must be
    ``>= -0.05`` (relative). Falsy fom_off collapses to an
    inconclusive verdict instead of a divide-by-zero.
  * Tokens and wall-time are reported only; no gate.

Usage:
    python scripts/compare_s10c_bench.py --ts 20260416T200000Z
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO_ROOT / "bench" / "results"

LLM_TASKS = ["e2e_digital_autoresearch_counter_live"]
SANITY_TASKS = ["e2e_miller_ota_audit_ihp", "e2e_sar11b_enob_ihp"]


def _discover_runs(ts: str, state: str) -> dict[str, list[dict]]:
    """Return ``{task_id: [run_dict, ...]}`` for every seed on ``state``."""
    pattern = f"s10c_{state}_{ts}_seed*"
    per_task: dict[str, list[dict]] = {}
    for run_dir in sorted(RESULTS_ROOT.glob(pattern)):
        for task_json in sorted(run_dir.glob("*.json")):
            if task_json.name in {"summary.json"}:
                continue
            try:
                payload = json.loads(task_json.read_text())
            except json.JSONDecodeError:
                continue
            task_id = payload.get("task_id") or task_json.stem
            per_task.setdefault(task_id, []).append(payload)
    return per_task


def _pass_rate(runs: list[dict]) -> float:
    if not runs:
        return float("nan")
    passes = sum(1 for r in runs if r.get("status") == "PASS")
    return passes / len(runs)


def _mean_metric(runs: list[dict], key: str) -> float:
    values = [r.get("metrics", {}).get(key) for r in runs]
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return float("nan")
    return statistics.fmean(nums)


def _sum_metric(runs: list[dict], key: str) -> float:
    values = [r.get("metrics", {}).get(key) for r in runs]
    nums = [v for v in values if isinstance(v, (int, float))]
    return float(sum(nums))


def _mean_duration(runs: list[dict]) -> float:
    nums = [r.get("duration_s") for r in runs if isinstance(r.get("duration_s"), (int, float))]
    if not nums:
        return float("nan")
    return statistics.fmean(nums)


def _render_task_table(task_id: str, on: list[dict], off: list[dict]) -> tuple[str, bool]:
    """Render the task row and return (markdown, gate_green)."""
    n_on, n_off = len(on), len(off)
    pass_on, pass_off = _pass_rate(on), _pass_rate(off)
    fom_on = _mean_metric(on, "best_fom")
    fom_off = _mean_metric(off, "best_fom")
    tokens_on = _sum_metric(on, "total_tokens")
    tokens_off = _sum_metric(off, "total_tokens")
    dur_on = _mean_duration(on)
    dur_off = _mean_duration(off)

    pass_delta_abs = pass_on - pass_off
    if fom_off and fom_off == fom_off:  # non-zero and non-NaN
        fom_delta_rel = (fom_on - fom_off) / abs(fom_off)
    else:
        fom_delta_rel = float("nan")

    pass_gate_green = pass_delta_abs >= -0.05
    fom_gate_green = fom_delta_rel >= -0.05 if fom_delta_rel == fom_delta_rel else True

    lines = [
        f"### {task_id}",
        "",
        f"| metric | skills ON (N={n_on}) | skills OFF (N={n_off}) | delta |",
        "|--------|----------------------|------------------------|-------|",
        f"| Pass@1 | {pass_on:.2f} | {pass_off:.2f} | {pass_delta_abs:+.2f} abs |",
        f"| mean best_fom | {fom_on:.3e} | {fom_off:.3e} | "
        f"{fom_delta_rel:+.1%} rel" + (" |" if fom_delta_rel == fom_delta_rel else " (n/a) |"),
        f"| total tokens (sum) | {tokens_on:.0f} | {tokens_off:.0f} | "
        f"{tokens_on - tokens_off:+.0f} |",
        f"| mean wall-time (s) | {dur_on:.1f} | {dur_off:.1f} | "
        f"{dur_on - dur_off:+.1f} |",
        "",
        f"- Pass@1 gate ({-5:d}% abs): **{'PASS' if pass_gate_green else 'FAIL'}**",
        f"- best_fom gate ({-5:d}% rel): **{'PASS' if fom_gate_green else 'FAIL'}**",
        "",
    ]
    return "\n".join(lines), pass_gate_green and fom_gate_green


def _render_verdict(per_task_green: dict[str, bool]) -> str:
    llm_results = {k: v for k, v in per_task_green.items() if k in LLM_TASKS}
    if not llm_results:
        return "**Overall verdict:** INCONCLUSIVE — no LLM-backed task results found."
    if all(llm_results.values()):
        return (
            "**Overall verdict:** GATE GREEN — skill injection does not regress "
            "Pass@1 or best_fom within the ±5 % tolerance on the LLM-backed task."
        )
    return (
        "**Overall verdict:** GATE RED — at least one LLM-backed task regresses "
        "beyond the ±5 % tolerance. Consider flipping the default of "
        "``EDA_AGENTS_INJECT_SKILLS`` to ``\"0\"`` until skill content is revised."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ts", required=True, help="TS stamp used by the bench driver")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional override for the output markdown path.",
    )
    args = parser.parse_args()

    ts = args.ts
    on_runs = _discover_runs(ts, "on")
    off_runs = _discover_runs(ts, "off")

    task_ids = sorted(set(on_runs) | set(off_runs))
    if not task_ids:
        print(f"No runs found under {RESULTS_ROOT} for TS={ts!r}")
        return 2

    body_parts = [
        "# S10c skill-injection A/B validation",
        "",
        f"TS: `{ts}`",
        "",
        "## LLM-backed A/B (gate-relevant)",
        "",
    ]
    per_task_green: dict[str, bool] = {}

    for task_id in task_ids:
        if task_id not in LLM_TASKS:
            continue
        block, gate_green = _render_task_table(
            task_id, on_runs.get(task_id, []), off_runs.get(task_id, [])
        )
        body_parts.append(block)
        per_task_green[task_id] = gate_green

    body_parts.append("## Callable sanity (no LLM, delta expected ≈ 0)")
    body_parts.append("")
    for task_id in task_ids:
        if task_id not in SANITY_TASKS:
            continue
        block, gate_green = _render_task_table(
            task_id, on_runs.get(task_id, []), off_runs.get(task_id, [])
        )
        body_parts.append(block)
        per_task_green[task_id] = gate_green

    body_parts.append(_render_verdict(per_task_green))
    body_parts.append("")

    out_path = args.output or RESULTS_ROOT / f"s10c_{ts}_comparison.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(body_parts))
    print(f"Wrote {out_path}")

    overall_green = all(
        per_task_green[t] for t in LLM_TASKS if t in per_task_green
    )
    return 0 if overall_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
