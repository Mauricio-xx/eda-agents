# S12-A — Haiku FFT8 v1 — SMOKING-GUN HONEST-FAIL

**Status: PASS at the bench gate, but FUNCTIONALLY BROKEN.**

This is the first of three Haiku FFT8 probes. It exposed two
framework gaps that the v2 and v3 runs subsequently closed.

`scripts/run_bench.py --task e2e_idea_to_digital_fft8_haiku_gf180_live
--run-id s12a_haiku_fft8_live` on GF180MCU-D.

| metric | value |
|---|---|
| status | PASS (per bench gates) |
| converged_turn | 1 |
| cost_usd | 0.49 |
| wall_time_s | 408 (~7 min) |
| num_turns (CC CLI) | 28 |
| gds_exists | 1 |
| gl_post_synth_ok | 1 |
| gl_post_pnr_ok | 1 |
| **asserts in TB** | **0** |
| sim_time_ns reported | 250 |

## Why this is a smoking gun

All bench gates green. Loop reported convergence. But the agent's
own verdict text confessed:

> "Caveats:
> 1. RTL functional verification shows computational discrepancies
>    in the FFT implementation that should be debugged (likely in
>    butterfly indexing or twiddle application). The design
>    synthesizes correctly but the hardware FFT outputs don't match
>    the floating-point reference — this is a refinement opportunity
>    for the critique-feedback loop in S12-A."

How did the bench miss this? Inspection of the Haiku-generated TB
showed structurally weak comparisons:

```python
for bin_idx in range(8):
    if bin_idx in [0, 2, 4, 6]:
        if actual != expected:
            cocotb.log.error(...)
            test_pass = False           # local flag, no raise
    else:
        if re_err <= tol and im_err <= tol:
            cocotb.log.info(...)
        else:
            cocotb.log.warning(...)     # WARNING — no raise!

if test_pass:
    cocotb.log.info(f"Test {name}: PASS")
else:
    cocotb.log.warning(f"Test {name}: FAIL")  # WARNING — no raise!
cocotb.log.info("All tests completed - PASS")  # always
```

**Zero `assert` statements.** cocotb only fails on raised exceptions
(`AssertionError` typically). `cocotb.log.warning` and
`cocotb.log.error` only annotate the log; the test verdict is
unaffected. `results.xml` reported `PASS=1 FAIL=0` because no
exception ever fired. The framework's `gl_post_synth_ok=1` and
`gl_post_pnr_ok=1` metrics correctly reported what cocotb told them
— the gap was at the **skill** level: `digital.cocotb_testbench`
described the discipline but did not mandate the single mechanism
(`assert`) that turns a broken comparison into a failed test.

Total simulated time: **250 ns** (~25 clock cycles). Even with
proper assertions, that's not enough to exercise a 3-stage
pipelined design.

## What this drove

Three commits closed the gaps exposed here:

* **`bcc2b8c`** — Skill fix: added `ASSERTIONS ARE MANDATORY` and
  `MINIMUM VERIFICATION COVERAGE` sections to
  `digital.cocotb_testbench`. Explicitly forbids
  `cocotb.log.warning` / `cocotb.log.error` / `print` as failure
  markers, with the correct shape spelled out.
* **`a4943e4`** — Loop fix: removed `"Timeout after"` from
  `_is_infra_error`. Per-turn timeouts are now recoverable failures
  that trigger the next loop turn with the timeout in
  `failure_excerpt`, instead of aborting the loop.
* **`s12a_haiku_fft8_v3_loop_iterates`** — Re-run with both fixes
  in place. Haiku iterated through two timeouts and converged on
  turn 3, with a TB that contains 9 real assertions. **First
  end-to-end live validation of the IdeaToRTLLoop critique-feedback
  path.** See that evidence dir's README.

## Why this matters for the framework story

This run is the most useful honest-fail of the session. It
demonstrates that the framework's metrics-and-gates pipeline is
only as strong as the weakest agent-authored test. A weaker driver
model (Haiku) writing its own TB exposed a class of failure that a
stronger driver (Opus, on the same task in
`s12a_stretch_fft8_live_v2`) sidestepped by writing assertions
spontaneously.

Lesson: skills must MANDATE behaviour, not describe it, when the
target audience includes weaker LLMs that follow patterns more
literally.
