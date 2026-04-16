#!/usr/bin/env python3
"""Compare S10g LLM-sizing skill-injection A/B runs.

Reads every ``bench/results/s10g_{on,off}_<TS>_seed*/`` directory for
a given TS, aggregates the one-shot Miller OTA LLM-sizing adapter's
Pass@1, token budget, and simulated FoM, and writes a markdown
report with the ±5 % gate verdict.

Usage::

    python scripts/compare_s10g_llm_sizing.py --ts 20260416T213000Z
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO_ROOT / "bench" / "results"

TASK_ID = "spec_llm_miller_ota_ihp_ab"


def _load_runs(ts: str, state: str) -> list[dict]:
    pattern = f"s10g_{state}_{ts}_seed*"
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
        r.get("duration_s") for r in runs if isinstance(r.get("duration_s"), (int, float))
    ]
    if not nums:
        return float("nan")
    return statistics.fmean(nums)


def _format_pct(val: float, prec: int = 1) -> str:
    if math.isnan(val):
        return "n/a"
    return f"{val:+.{prec}%}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ts", required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    on = _load_runs(args.ts, "on")
    off = _load_runs(args.ts, "off")
    if not on or not off:
        print(
            f"No runs found under {RESULTS_ROOT} for TS={args.ts!r}",
        )
        return 2

    pass_on, pass_off = _pass_rate(on), _pass_rate(off)
    tokens_on_sum, tokens_off_sum = _sum(on, "total_tokens"), _sum(off, "total_tokens")
    tokens_on_mean = tokens_on_sum / len(on) if on else float("nan")
    tokens_off_mean = tokens_off_sum / len(off) if off else float("nan")
    adc_on, adc_off = _mean(on, "Adc_dB"), _mean(off, "Adc_dB")
    gbw_on, gbw_off = _mean(on, "GBW_Hz"), _mean(off, "GBW_Hz")
    pm_on, pm_off = _mean(on, "PM_deg"), _mean(off, "PM_deg")
    dur_on, dur_off = _mean_duration(on), _mean_duration(off)

    pass_delta_abs = pass_on - pass_off
    tokens_delta_rel = (
        (tokens_on_mean - tokens_off_mean) / tokens_off_mean
        if tokens_off_mean
        else float("nan")
    )

    pass_gate_green = pass_delta_abs >= -0.05

    lines = [
        "# S10g LLM-sizing skill-injection A/B",
        "",
        f"TS: `{args.ts}`",
        f"Task: `{TASK_ID}` (temperature 0.7)",
        "",
        "## LLM-backed one-shot A/B",
        "",
        f"| metric | skills ON (N={len(on)}) | skills OFF (N={len(off)}) | delta |",
        "|--------|-------------------------|---------------------------|-------|",
        f"| Pass@1 | {pass_on:.2f} | {pass_off:.2f} | {pass_delta_abs:+.2f} abs |",
        (
            f"| mean total_tokens | {tokens_on_mean:.0f} | {tokens_off_mean:.0f} "
            f"| {_format_pct(tokens_delta_rel)} rel |"
        ),
        f"| mean Adc (dB) | {adc_on:.2f} | {adc_off:.2f} | {adc_on - adc_off:+.2f} |",
        f"| mean GBW (Hz) | {gbw_on:.3e} | {gbw_off:.3e} | "
        f"{(gbw_on - gbw_off):+.3e} |",
        f"| mean PM (deg) | {pm_on:.2f} | {pm_off:.2f} | {pm_on - pm_off:+.2f} |",
        f"| mean wall-time (s) | {dur_on:.2f} | {dur_off:.2f} | "
        f"{dur_on - dur_off:+.2f} |",
        "",
        f"- Pass@1 gate (>=-5% abs): **{'PASS' if pass_gate_green else 'FAIL'}**",
        f"- Tokens: reported, no gate (expected to rise ON).",
        "",
    ]

    if pass_gate_green and pass_delta_abs > 0.05:
        lines.append(
            f"**Overall verdict:** POSITIVE — skill injection improves "
            f"Pass@1 by {pass_delta_abs*100:+.1f}% absolute."
        )
    elif pass_gate_green:
        lines.append(
            "**Overall verdict:** NEUTRAL — skill injection does not "
            "regress Pass@1 within the ±5% band. No positive signal."
        )
    else:
        lines.append(
            "**Overall verdict:** REGRESSION — skill injection lowers "
            "Pass@1 beyond the 5% tolerance; investigate skill content."
        )
    lines.append("")

    out = args.output or RESULTS_ROOT / f"s10g_{args.ts}_comparison.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"Wrote {out}")
    return 0 if pass_gate_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
