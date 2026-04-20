# S12-A — Haiku FFT8 v3 — LIVE-VALIDATED CRITIQUE-FEEDBACK PATH

**Status: GREEN. First end-to-end live validation of the
IdeaToRTLLoop critique-feedback path under live LLM.**

`scripts/run_bench.py --task e2e_idea_to_digital_fft8_haiku_gf180_live
--run-id s12a_haiku_fft8_v3_loop_iterates` on GF180MCU-D, with
`EDA_AGENTS_ALLOW_DANGEROUS=1` and
`PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu`. cocotb 2.x in
the LibreLane venv. Driver model: **Claude Haiku 4.5
(`claude-haiku-4-5-20251001`)** via `--model` flag on the CC CLI.

| metric | value | comment |
|---|---|---|
| status | PASS | bench gate clears |
| **converged_turn** | **3** | **PRIMARY acceptance criterion (>=2) MET** |
| loop_turns_used | 3 (out of 8 budget) | turns 1+2 timed out, turn 3 converged |
| loop_total_cost_usd (recorded) | 0.65 | turns 1+2 cost not recorded (CC CLI killed before JSON return); real total likely $2-4 |
| wall_time_s (loop) | ~70 min (4163 s adapter total) | t1 30m + t2 30m + t3 9m + flow setup |
| num_turns (CC CLI in turn 3) | 55 | Haiku's internal sub-iteration |
| gds_exists | 1 | `runs/RUN_2026-04-19_11-55-39/final/gds/fft8.gds` |
| gl_post_synth_ok | 1 | cocotb GL sim against post-synth netlist |
| gl_post_pnr_ok | 1 | cocotb GL sim with SDF annotation |
| Magic DRC | 0 violations | |
| Netgen LVS | match | |
| asserts in TB | **9** | vs **0** in Haiku v1 — skill mandate worked |

## What this validates (load-bearing for the framework story)

This single run validates four S12-A code paths under live LLM
conditions for the first time:

1. **`IdeaToRTLLoop` critique-feedback path** — the loop iterated
   from turn 1 (timeout) → turn 2 (timeout) → turn 3 (convergence).
   Each successive turn received the previous turn's
   `failure_excerpt` in its prompt header, and turn 3's agent
   verdict explicitly diagnoses what went wrong in turn 2:

   > "Root cause diagnosed: The previous turn (turn 2) timed out
   > not due to RTL structural issues, but due to insufficient
   > floorplan/placement resources. The fix was straightforward:
   > adjust the die area and placement density parameters to allow
   > the physical design tools adequate space."

   The agent literally read the partial state from disk, attributed
   the failure to physical-design parameters (not RTL), and applied
   targeted minimal patches:

   > "Minimal patches applied:
   > 1. Testbench fix (tb/test_fft8.py:9): unit="ns" → units="ns"
   >    (plural, cocotb 1.9+ API)
   > 2. Floorplan config: DIE_AREA: 500x500 um (up from 300x300),
   >    PL_TARGET_DENSITY_PCT: 65% (up from 50%),
   >    CLOCK_PERIOD: 200 ns (relaxed from 150 ns for timing margin)"

2. **`per_turn_timeout_s` plumbing** — each turn's harness honoured
   the 1800 s cap. Turns 1 and 2 each closed at exactly 1800 s; the
   total wall clock did not blow past `timeout_s=7200` despite three
   sequential turns. Without this fix (commit `26c5a02`), a single
   slow turn would have eaten the entire 7200 s budget and the loop
   would never have iterated.

3. **`_is_infra_error` per-turn-timeout policy** — commit `a4943e4`
   removed `"Timeout after"` from the fatal-infra indicators. The
   loop correctly classified turn 1's and turn 2's timeouts as
   recoverable failures and dispatched the next turn with the
   timeout text in the failure_excerpt. Without this fix, the loop
   would have aborted after turn 1.

4. **`digital.cocotb_testbench` ASSERTIONS-MANDATORY mandate** —
   the v3 testbench has **9 real `assert` statements** verifying
   FFT outputs against the floating-point reference. Compare to
   the Haiku v1 run (`s12a_haiku_fft8_live/`), which had **zero
   asserts** and used `cocotb.log.warning` as fake failure markers.
   The new skill text (commit `bcc2b8c`) reached Haiku and changed
   its TB-writing behaviour.

## What this does NOT validate

The critique skills `digital.critique_sim_failure` and
`digital.critique_synth_lint` were selected based on the failure
type (`flow_failed and not (sim/gl_sim_failed)` → synth_lint
critique on this run). The skills' specific guidance about minimal
patches reached the agent through the critique header, but the
failure mode here was timeout (physical-design overload), not a
yosys lint error or a sim assertion. A future probe with a real
sim assertion failure would close that loop.

## Cost accounting honesty

`loop_total_cost_usd: 0.65` is **only turn 3**. Turns 1 and 2 each
ran for the full 1800 s `per_turn_timeout_s` cap and were killed by
the harness before the CC CLI emitted its JSON cost field. Estimated
real cost across all three turns: $2-4 (Haiku rates × ~70 min total
agent wall clock).

This is a known recording gap (also seen in the FFT8 v1 timeout run)
and is acceptable for honest-fail accounting because the loop's
`max_budget_usd` cap is enforced on the recorded sum. A turn that
times out without recording cost effectively gets a free pass on
the cost cap; the wall-clock and turn-budget caps are the real
defenses.

## Per-turn timeline

| turn | wall | cost (recorded) | outcome | run dir |
|---|---|---|---|---|
| 1 | 1800 s (cap) | $0.00 | timeout, no JSON | `RUN_2026-04-19_11-00-09` (and 6 sub-dirs from internal retries) |
| 2 | 1800 s (cap) | $0.00 | timeout, no JSON | `RUN_2026-04-19_11-39-02` |
| 3 | 562 s (~9 min) | $0.65 | **CONVERGED** | `RUN_2026-04-19_11-55-39` |

11 LibreLane RUN_* directories total — Haiku used the per-turn
internal CC CLI 3-retry budget plus the outer-loop iteration budget,
producing multiple flow attempts before settling on the final run.

## Final design metrics (turn 3, RUN_2026-04-19_11-55-39)

Per the agent's verdict text:
- **Timing:** WNS = +152.9 ns (setup), +0.37 ns (hold) — clean
- **Power:** 31.1 mW @ 5V, IR drop < 0.01%
- **GDS:** 2.9 MB
- **Cells:** ~3.5k logic + 400+ buffers post-CTS
- **STA:** all 9 PVT corners pass
- **DRC / LVS / Antenna:** clean

(Lower cell count than the Opus FFT8 run's 10.8k because Haiku
opted for a more conservative implementation under iteration
pressure — a smaller design fits the floorplan more easily.)

## Reproduce

```bash
export PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu
export EDA_AGENTS_ALLOW_DANGEROUS=1
.venv/bin/python scripts/run_bench.py \
    --task e2e_idea_to_digital_fft8_haiku_gf180_live \
    --run-id s12a_haiku_fft8_v3_loop_iterates_repro
```

Note that live LLM runs are stochastic; convergence may happen on a
different turn or fail entirely on a re-run. The acceptance criterion
is `converged_turn >= 2`, which this run achieved with `=3`.
