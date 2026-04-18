# S12-A stretch — FFT8 LIVE — STRUCTURAL HONEST-FAIL

**Status: HONEST-FAIL of a different shape than expected**.

The probe was meant to push the IdeaToRTLLoop into turn 2 by exposing
the agent to a design (8-point FFT with non-trivial twiddles) where
single-shot RTL generation is unlikely to converge. Instead, it
exposed a structural bug in the loop's wall-clock budget plumbing:
the inner CC CLI consumed the entire 7200 s task timeout on turn 1
and was killed before returning its JSON verdict. The outer loop
never iterated. Critique-feedback path remains structurally
unreachable on long-running designs until the bug is fixed.

`scripts/run_bench.py --task e2e_idea_to_digital_fft8_gf180_live`
on GF180MCU-D, with `EDA_AGENTS_ALLOW_DANGEROUS=1` and
`PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu`.

| metric | value | comment |
|---|---|---|
| status | FAIL_AUDIT | timeout |
| cost_usd (recorded) | 0.0 | **WRONG — CC CLI was killed before JSON return; actual cost is unknown** |
| wall_time_s | 7200.4 (= 2 h 0 min 0 s, hit cap) | exact `timeout_s` |
| num_turns (recorded) | 0 | same JSON-not-returned issue |
| loop_turns_used | 1 (out of 8 budget) | infra-error abort on turn 1 |
| loop_converged | 0 | |
| loop_budget_exhausted | 0 | budget unconsumed; wall clock was the binding constraint |
| loop_total_cost_usd | 0.0 | recording artifact, not real |
| gds_exists | 1 (final/gds/fft8.gds) | LibreLane DID close cleanly |
| gl_post_synth_ok | missing | never recorded |
| gl_post_pnr_ok | missing | never recorded |

## What actually happened (timeline)

- **14:42:52 UTC** — bench start, IdeaToRTLLoop turn 1/8.
- **14:42:52 UTC** — CC CLI launched.
- **15:00:51 UTC** — agent had RTL + TB ready, LibreLane invoked
  (`runs/RUN_2026-04-18_15-00-51`). ~18 min of agent work.
- **15:03:14 UTC** — LibreLane flow complete. **DRC PASS, LVS PASS,
  Antenna PASS, no setup/hold violations**, only common max_slew /
  max_cap warnings. ~21 min total wall clock.
- **15:03:14 → 16:42:52 UTC** — agent kept running for **1 h 39 min
  more** without producing a final verdict. Most likely stuck inside
  CC CLI's internal sub-iteration loop on cocotb GL sim or an
  iterative correctness check. CC CLI never emitted its terminating
  JSON.
- **16:42:52 UTC** — `idea_to_rtl_loop` hit `timeout_s=7200`,
  classified as infra error, aborted the outer loop.

## What the agent produced (real, on disk)

- `src/fft8.v` (200 lines): 8-point radix-2 DIT FFT, 3 pipeline
  stages, bit-reversal at input boundary, Q1.7 twiddle constant
  `C_TW = 8'sd91`. Math structure looks correct on inspection.
- `tb/test_fft8.py` (120 lines): cocotb test with proper
  `RisingEdge`/`ReadOnly` discipline, floating-point reference
  computation, tolerance bins (trivial vs non-trivial twiddles).
- `tb/Makefile`: cocotb-2.x compatible.
- `runs/RUN_2026-04-18_15-00-51/`: complete LibreLane run, GDS at
  `final/gds/fft8.gds`. `final/metrics.json` has full QoR.

I did NOT inspect whether the cocotb sim itself passed (RTL or
gate-level). The agent never reported back, and re-running cocotb
manually would consume budget without informing the bug fix.

## Root cause and fix

`run_idea_to_rtl_loop` in `src/eda_agents/agents/idea_to_rtl_loop.py`
plumbs the per-turn `timeout_s` argument equal to the total task
`timeout_s`. Combined with `loop_budget=8`, a single runaway CC CLI
turn can consume the entire wall-clock budget before the outer loop
gets a chance to read the harness verdict and inject critique
feedback. The loop is therefore unable to defend against
agent-internal hang scenarios (which is exactly the class of failure
we wanted the loop to catch).

**Fix** (deferred to a follow-up commit; no live spend until it lands):
- Add `per_turn_timeout_s: int | None = None` kwarg to
  `run_idea_to_rtl_loop`. Default = `timeout_s // max_turns`. Each
  turn passes this as the inner harness `timeout_s`.
- Bench adapter and MCP tool surface the new kwarg.
- Bench YAMLs gain a `per_turn_timeout_s` field; the FFT8 live YAML
  should set it to `1800` (~30 min, ample slack over the 21 min
  LibreLane wall clock observed here).
- Cost accounting: investigate whether the CC CLI's killed JSON
  leaves any partial cost record on disk. If not, accept the gap and
  document that infra-error turns have unknown cost (the loop's
  `_classify_infra_error` already returns `cost_usd=0.0` for these,
  which is what allowed `loop_total_cost_usd=0.0` here).

The actual cost of this run is unknown but bounded — the agent ran
for ~2 h of CC CLI wall clock. By extrapolation from the MAC live
run (46 CC CLI sub-turns, 33 min wall clock, $8.09), this probe
likely consumed somewhere between $5 and $15 of subscription quota.
**Adjust the running S12-A live spend estimate accordingly.**

## Validation status (relative to S12-A stretch acceptance)

- PRIMARY (`converged_turn >= 2` with all metrics green): **NOT
  achieved**. Loop did not iterate.
- HONEST-FAIL acceptance (`loop_converged=0 + budget_exhausted=1`
  with documented root cause in `loop_result.json`): **partially
  achieved**. `loop_converged=0` ✓, but `budget_exhausted=0` —
  wall-clock killed the run, not the cost cap. The
  `loop_result.json` does name the failure mode (`reason: "error"`,
  `error: "Timeout after 7200s"`).
- The critique-feedback path (`digital.critique_sim_failure`,
  `digital.critique_synth_lint`, per-turn description augmentation,
  failure-excerpt extraction) was **NOT exercised** under live LLM
  conditions on this run.

The S12-A stretch validation goal is not met. Re-attempt requires
the per-turn timeout fix above. The fix is small (~30 lines of
plumbing + a regression test) and contained to existing files;
no new code paths.

## Reproduce (after fix lands)

```bash
export PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu
export EDA_AGENTS_ALLOW_DANGEROUS=1
.venv/bin/python scripts/run_bench.py \
    --task e2e_idea_to_digital_fft8_gf180_live \
    --run-id s12a_stretch_fft8_live_v2
```

The task YAML should be amended to set
`per_turn_timeout_s: 1800` (default after fix would be
`7200 // 8 = 900`, which is too tight given the 21 min LibreLane
wall clock alone).
