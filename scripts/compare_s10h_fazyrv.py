#!/usr/bin/env python3
"""Compare S10h fazyrv skill-injection A/B runs.

Reads every ``bench/results/s10h_{on,off}_<TS>_seed*/`` directory for
a given TS, aggregates Pass@1 / best_fom / total_tokens / wall-time,
and writes a markdown report with a ±5 % gate verdict.

Usage::

    python scripts/compare_s10h_fazyrv.py --ts 20260416T230000Z
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO_ROOT / "bench" / "results"

TASK_ID = "e2e_digital_autoresearch_fazyrv_live"


def _load_runs(ts: str, state: str) -> list[dict]:
    pattern = f"s10h_{state}_{ts}_seed*"
    runs: list[dict] = []
    for run_dir in sorted(RESULTS_ROOT.glob(pattern)):
        task_json = run_dir / f"{TASK_ID}.json"
        if not task_json.is_file():
            continue
        try:
            runs.append(json.loads(task_json.read_text()))
        except json.JSONDecodeError:
            continue
    return runs


def _pass_rate(runs: list[dict]) -> float:
    if not runs:
        return float("nan")
    return sum(1 for r in runs if r.get("status") == "PASS") / len(runs)


def _mean(runs: list[dict], key: str) -> float:
    vals = [r.get("metrics", {}).get(key) for r in runs]
    nums = [v for v in vals if isinstance(v, (int, float)) and not math.isnan(v)]
    if not nums:
        return float("nan")
    return statistics.fmean(nums)


def _sum(runs: list[dict], key: str) -> float:
    return float(
        sum(
            v
            for r in runs
            for v in [r.get("metrics", {}).get(key)]
            if isinstance(v, (int, float))
        )
    )


def _mean_duration(runs: list[dict]) -> float:
    nums = [
        r.get("duration_s")
        for r in runs
        if isinstance(r.get("duration_s"), (int, float))
    ]
    if not nums:
        return float("nan")
    return statistics.fmean(nums)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ts", required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    on = _load_runs(args.ts, "on")
    off = _load_runs(args.ts, "off")
    if not on or not off:
        print(f"No runs found under {RESULTS_ROOT} for TS={args.ts!r}")
        return 2

    pass_on = _pass_rate(on)
    pass_off = _pass_rate(off)
    fom_on = _mean(on, "best_fom")
    fom_off = _mean(off, "best_fom")
    kept_on = _mean(on, "iterations_kept")
    kept_off = _mean(off, "iterations_kept")
    tokens_on = _sum(on, "total_tokens")
    tokens_off = _sum(off, "total_tokens")
    dur_on = _mean_duration(on)
    dur_off = _mean_duration(off)

    pass_delta_abs = pass_on - pass_off
    fom_delta_rel = (
        (fom_on - fom_off) / abs(fom_off) if fom_off else float("nan")
    )

    pass_gate_green = pass_delta_abs >= -0.05
    fom_gate_green = (
        fom_delta_rel >= -0.05 if fom_delta_rel == fom_delta_rel else True
    )

    fom_delta_str = (
        f"{fom_delta_rel:+.1%} rel"
        if fom_delta_rel == fom_delta_rel
        else "n/a"
    )

    lines = [
        "# S10h fazyrv digital skill-injection A/B",
        "",
        f"TS: `{args.ts}`",
        f"Task: `{TASK_ID}` (fazyrv-hachure frv_1, budget 4)",
        "",
        "## LLM-backed A/B",
        "",
        f"| metric | skills ON (N={len(on)}) | skills OFF (N={len(off)}) | delta |",
        "|--------|-------------------------|---------------------------|-------|",
        f"| Pass@1 | {pass_on:.2f} | {pass_off:.2f} | {pass_delta_abs:+.2f} abs |",
        f"| mean best_fom | {fom_on:.3e} | {fom_off:.3e} | {fom_delta_str} |",
        f"| mean iterations_kept | {kept_on:.2f} | {kept_off:.2f} | "
        f"{kept_on - kept_off:+.2f} |",
        f"| total tokens (sum) | {tokens_on:.0f} | {tokens_off:.0f} | "
        f"{tokens_on - tokens_off:+.0f} |",
        f"| mean wall-time (s) | {dur_on:.1f} | {dur_off:.1f} | "
        f"{dur_on - dur_off:+.1f} |",
        "",
        f"- Pass@1 gate (>=-5% abs): **{'PASS' if pass_gate_green else 'FAIL'}**",
        f"- best_fom gate (>=-5% rel): **{'PASS' if fom_gate_green else 'FAIL'}**",
        "",
    ]

    if pass_gate_green and fom_gate_green and (pass_delta_abs > 0.05 or (
        fom_delta_rel == fom_delta_rel and fom_delta_rel > 0.05
    )):
        lines.append(
            "**Overall verdict:** POSITIVE — skill injection improves "
            "Pass@1 or best_fom beyond noise."
        )
    elif pass_gate_green and fom_gate_green:
        lines.append(
            "**Overall verdict:** NEUTRAL — no regression, no positive "
            "signal beyond the 5% bands."
        )
    else:
        lines.append(
            "**Overall verdict:** REGRESSION — at least one gate "
            "violated; investigate."
        )
    lines.append("")

    out = args.output or RESULTS_ROOT / f"s10h_{args.ts}_comparison.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"Wrote {out}")
    return 0 if pass_gate_green and fom_gate_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
