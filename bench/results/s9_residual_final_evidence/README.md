# S9-residual-closure — final bench run

Captured at session close on 2026-04-16, with
`OPENROUTER_API_KEY` + LibreLane v3 + GF180MCU-D + nix EDA tools all
sourced so the LLM task and the live autoresearch task both run
end-to-end (not SKIPPED).

## Summary

| metric   | value                                       |
|----------|---------------------------------------------|
| total    | 17                                          |
| PASS     | 17                                          |
| FAIL     | 0                                           |
| SKIPPED  | 0                                           |
| ERROR    | 0                                           |
| Pass@1   | **100%**                                    |

17 = the 16 tasks from the S9-gap-closure close-out plus the new
``e2e_digital_autoresearch_counter_live`` task added by gap #4 in
S9-residual-closure. `summary.json` and `report.md` next to this
README are the authoritative evidence.

## What this bench actually exercises

End-to-end (not mocked):

- LibreLane RTL-to-GDS flow on GF180MCU-D through signoff
  Checker.KLayoutDRC for the 4-bit counter, twice (once for
  `e2e_digital_counter_gf180`, once per eval inside
  `e2e_digital_autoresearch_counter_live`).
- Post-synth + post-pnr GL simulation of the hardened counter via
  iverilog/vvp, TB asserts monotonic +1 mod 16 across 10 cycles.
- SAR 11-bit ENOB measurement via ngspice + PSP103 OSDI + Verilator
  SAR FSM. Lands at ENOB=5.64 bit, SNDR=35.74 dB post-defaults-shift
  with margin 1.14 bit / 7.74 dB against the recalibrated 4.5 / 28
  thresholds.
- Miller OTA analytical sizing + ngspice verify on IHP and GF180.
- Pre-sim gate checks (floating nodes, bulk connections, Vds
  polarity) on bugfix tasks.
- Real OpenRouter LLM calls (model: runner default) for
  `spec_llm_miller_ota_ihp` (spec-to-sizing) and
  `e2e_digital_autoresearch_counter_live` (flow-config optimization).

## Reproducing

```bash
# .env is gitignored; source it so the LLM tasks PASS instead of SKIP.
set -a && source /home/montanares/personal_exp/eda-agents/.env && set +a
PYTHONPATH=src .venv/bin/python scripts/run_bench.py --run-id s9_residual_final
```

Without the API key and the full tool chain, expect a mix of PASS
and SKIPPED (e.g. `total=17 PASS=15 SKIPPED=2` for a host without
OPENROUTER + LibreLane). That is still an honest green bench — the
adapters differentiate FAIL_INFRA (skip) from FAIL_SIM (test
regression) so missing tools never look like correctness failures.
