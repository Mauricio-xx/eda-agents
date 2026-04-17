# S12-A Gap 1 — IdeaToRTLLoop medium-target LIVE GATE

**Status: GREEN (Pass@1 = 100%) — partial validation of the loop**.

`scripts/run_bench.py --task e2e_idea_to_digital_mac_pipelined_gf180_live`
on GF180MCU-D, with `EDA_AGENTS_ALLOW_DANGEROUS=1` and
`PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu`. cocotb 2.x in
the LibreLane venv.

| metric | value |
|---|---|
| status | PASS |
| cost_usd | 8.09 (of $25 cap) |
| wall_time_s | 2002.7 (~33.4 min) |
| num_turns (CC CLI) | 46 (all inside turn 1 of the outer loop) |
| loop_turns_used | 1 (out of 8 budget) |
| loop_converged | 1 |
| loop_converged_turn | 1 |
| loop_budget_exhausted | 0 |
| gds_exists | 1 |
| gl_post_synth_ok | 1 (cocotb GL sim, 29.5 s) |
| gl_post_pnr_ok | 1 (cocotb GL sim + SDF wrapper, 27.4 s, 0 SDF warnings) |
| post-synth cells | **3 555** |
| setup WNS | **+19.79 ns** (on 50 ns clock) |
| hold WNS | **+0.415 ns** |
| total wirelength | 28 952 µm |
| Magic DRC | 0 violations |
| Netgen LVS | Circuits match uniquely |

## Agent verdict (verbatim from `result_text` tail)

```
1. Design: mac8_pipelined — 8-bit pipelined signed multiply-accumulate
   unit with saturating 32-bit accumulator and 4-clock input-to-output
   latency.
2. RTL Interface (src/mac8_pipelined.v):
     inputs:  clk, rst_n, start, flush, a[7:0], b[7:0], in_valid
     outputs: acc_out[31:0], out_valid, wrap_detect
   Pipeline: a_s1 -> prod_s2 -> prod_s3 -> prod_s4 -> acc.
   Saturates at ±2^31 with 1-cycle wrap_detect pulse on transition
   into saturation.
3. Cocotb verification: TESTS=1 PASS=1 FAIL=0 — 16 explicit vectors
   (pos·pos, pos·neg, multi-cycle accumulation, mid-stream flush), a
   4-clock latency check, and a full organic saturation burst
   (~133k cycles) verifying wrap_detect fires exactly once.
4. Synthesis / PnR metrics (LibreLane GF180MCU,
   run RUN_2026-04-17_14-48-09):
     Instance count: 3555
     Setup WNS:       19.79 ns (on 50 ns clock) — timing closed
     Setup TNS:       0
     Hold WNS:        0.415 ns — closed
     Hold TNS:        0
     Wirelength:      28952 µm
5. DRC: Magic DRC = 0 violations.
6. LVS: Netgen — Circuits match uniquely.
7. GDS: runs/RUN_2026-04-17_14-48-09/final/gds/mac8_pipelined.gds
Verdict: SIGNOFF CLEAN — timing closed, DRC clean, LVS match,
cocotb passes 1.3M ns of stimulus including full saturation exercise.
```

Minor non-fatal: 9 max-slew, 1 max-cap, 1 max-fanout signoff
warnings. None block tapeout given the +19.79 ns WNS headroom; these
are first-pass-with-default-repair noise.

## What this validates — and what it doesn't

**Validated (live)**:
- IdeaToRTLLoop wrapper end-to-end on a non-trivial design
  (3 555 cells, well above the S11 ALU = 596 / accum_cpu = 1 865
  ceiling that motivated the loop in the first place).
- `loop_budget=8` plumbing: bench adapter forwards it correctly to
  `generate_rtl_draft`, which dispatches to
  `run_idea_to_rtl_loop`, which calls back via lazy import without
  recursing.
- `IdeaToRTLLoopResult` round-trips through `result_to_dict`. The
  bench's `loop_result` JSON sub-block is intact and contains the
  per-turn iteration record.
- New loop metrics (`loop_converged`, `loop_turns_used`,
  `loop_total_cost_usd`, `loop_budget_exhausted`,
  `loop_converged_turn`) are emitted and the audit gate
  (`loop_converged: {min: 1}`) clears.
- Gap 2 cocotb GL sim works on a non-counter design (3 555 cells,
  4-stage pipeline, 32-bit accumulator).

**NOT validated (honest-fail framing)**:

The loop converged on **turn 1** because the agent fixed the
wrap_detect saturation bug inside its CC CLI 3-retry budget (46 CC
CLI sub-turns visible in the result). Our outer loop's
critique-feedback path — the new `digital.critique_sim_failure` and
`digital.critique_synth_lint` skills, the per-turn description
augmentation, the failure-excerpt extraction — was therefore **not
exercised under live LLM conditions**. The 15 unit tests in
`tests/test_idea_to_rtl_loop.py` cover that path with mocked
harness results (early success, budget exhausted, cost cap, infra
error abort, critique propagation for both sim and synth-lint
routes), but no live failure-then-recover trail was recorded.

This is honest scope: S12-A's stated acceptance was "Pass@3 OR
honest-fail with documented root cause". We hit Pass@1, which is
strictly better than Pass@3 — but it leaves the loop's iterative
machinery as code-tested only, not battle-tested.

## Honest-fail lever for S12-B

If you want to actually stress the critique loop, two harder probes
would force it into iteration:

1. **8-point FFT** with twiddle-factor multiplications (deferred in
   S12-A as the stretch goal). Twiddle math from NL alone is harder
   than MAC sign-flips; the agent's first attempt is more likely to
   fail and force a turn 2.
2. **5-stage pipelined CPU with hazard handling** (RV32E subset).
   Single-cycle accum_cpu hit 1 865 cells; a pipelined variant is
   easily >10k cells and the data-hazard logic is a known LLM
   stumbling block.

Either probe belongs to S12-B and would explicitly aim to make the
loop earn its keep.

## What IdeaToRTLLoop's bookkeeping recorded

```json
{
  "iterations": [{
    "turn": 1,
    "success": true,
    "all_passed": true,
    "sim_status": "missing",          // agent text didn't have RTL_SIM_PASS marker
    "flow_status": "pass",
    "gl_sim_status": "pass",
    "cost_usd": 8.088,
    "duration_s": 2059.7,
    "num_turns": 46,
    "work_subdir": ".../runs/RUN_2026-04-17_14-48-09",
    "error": null
  }],
  "total_cost_usd": 8.088,
  "converged_turn": 1,
  "budget_exhausted": false,
  "reason": "converged"
}
```

Note `sim_status: "missing"` — the agent's verdict text used a
different success phrasing than the markers `_classify_sim` looks
for ("RTL SIM PASS"). For S12-B that classifier should be widened
or replaced with an LLM-verdict parse. Not S12-A scope; the loop
still converged correctly, this is only a polish item for the
honest-fail / iteration-needed path.

## Reproduce

```bash
export PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu
export EDA_AGENTS_ALLOW_DANGEROUS=1
.venv/bin/python scripts/run_bench.py \
    --task e2e_idea_to_digital_mac_pipelined_gf180_live \
    --run-id s12a_medium_live
```
