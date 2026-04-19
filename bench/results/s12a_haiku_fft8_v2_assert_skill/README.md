# S12-A — Haiku FFT8 v2 — SKILL FIX VALIDATED, LOOP STILL ABORTED

**Status: FAIL_AUDIT, but useful intermediate evidence.**

This is the second of three Haiku FFT8 probes. It validated that
the new `digital.cocotb_testbench` `ASSERTIONS ARE MANDATORY`
section reached the agent (the TB now had real `assert` statements
unlike v1) — but the loop aborted at the per-turn timeout boundary
instead of iterating, exposing a separate gap in `_is_infra_error`.

`scripts/run_bench.py --task e2e_idea_to_digital_fft8_haiku_gf180_live
--run-id s12a_haiku_fft8_v2_assert_skill` on GF180MCU-D, after
landing skill-fix commit `bcc2b8c` but BEFORE landing loop-fix
commit `a4943e4`.

| metric | value |
|---|---|
| status | FAIL_AUDIT |
| converged_turn | None |
| reason | error (per-turn timeout treated as fatal) |
| loop_turns_used | 1 |
| cost_usd (recorded) | 0.00 (CC CLI killed before JSON return) |
| wall_time_s | 2164 (~36 min, broke the 1800 s per-turn cap by ~6 min in monitor overhead) |
| **asserts in TB** | **multiple** (vs 0 in v1) |

## What this validated

Haiku now writes proper assertions:

```python
assert int(dut.out_valid.value) == 1, "out_valid should be 1"
assert X0_re == 100, f"Impulse FFT should have X0_re ~ 100, got {X0_re}"
assert X0_re == 8, f"DC FFT should have X0_re = 8, got {X0_re}"
```

The new skill text (commit `bcc2b8c`'s `ASSERTIONS ARE MANDATORY`
section) reached the agent and changed its TB-writing behaviour.
Direct attribution: between v1 and v2 the only change was the skill
update and a new run-id; the model, the YAML, the design spec, and
the agent harness were identical.

## Why it failed anyway

Haiku is slower than Opus and didn't finish the LibreLane flow
within the 1800 s per-turn timeout. The harness killed the CC CLI;
`run_idea_to_rtl_loop` saw `error="Timeout after 1800s"`; the
loop's `_is_infra_error` classified this as fatal infra and aborted
the outer loop without iterating.

This was the wrong policy: per-turn timeouts are exactly the case
where the loop SHOULD iterate (the partial work_dir state from the
first turn is still on disk; the next turn can apply a smaller
incremental patch). The original `_is_infra_error` design predates
the per-turn timeout — back then any timeout meant the entire task
was lost.

## What this drove

Commit `a4943e4` removed `"Timeout after"` from `_is_infra_error`
and added a regression test
(`test_per_turn_timeout_lets_loop_iterate`) that locks the new
recoverable behaviour. The v3 re-run with both fixes converged on
turn 3 — see `bench/results/s12a_haiku_fft8_v3_loop_iterates/`.

## Lessons (load-bearing for the framework story)

* **Skill mandates change behaviour deterministically.** A single
  mandate paragraph in the cocotb skill flipped Haiku from
  "log.warning failures" to "real assert statements" with no other
  intervention. The skill is the right pressure point when the
  weakness is in the agent's TB-writing pattern.
* **Loop policies must evolve with new defenses.** The
  per-turn-timeout fix (commit `26c5a02`) created a new failure
  mode (per-turn timeout error string) that the existing
  `_is_infra_error` over-matched. The fix is small (one line
  removed from the indicator tuple) but had to be paired with a
  regression test that exercises both directions.
