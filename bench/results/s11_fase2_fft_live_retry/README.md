# S11 Fase 2 extra — FFT 4-point probe (RETRY: PASS)

Re-run of the inconclusive probe under
`bench/results/s11_fase2_fft_live/` after the Claude subscription
rate limit reset. Same bench task YAML, fresh agent invocation.

## Result: PASS (Pass@1 = 100%)

- Total duration: **409.70 s** (6m 49s).
- LLM cost: **$2.10 USD** (budget was $10).
- Turns: 7.
- Pass predicate: `gds_exists=1 AND gl_post_synth_ok=1 AND gl_post_pnr_ok=1`.

Agent verdict:

```
Design:         fft4 — 4-point radix-2 DIT FFT, 8-bit signed real inputs,
                10-bit signed complex outputs, trivial twiddles (no
                multipliers).
Testbench:      five vectors covering all-zero, impulse, DC,
                {1, 0, -1, 0}, {100, 50, -50, -100}.
Timing:         Setup WNS +27.349 ns @ worst corner max_ss_125C_4v50
                (nom_tt_025C_5v00 setup WNS = +33.29 ns).
                Hold WHS +10.178 ns @ worst corner min_ff_n40C_5v50.
                0 setup/hold violations. CLOCK_PERIOD = 50 ns.
DRC:            Magic = 0, routing = 0 (converged on iteration 3).
Antenna:        0 violations (34 diodes auto-inserted).
LVS:            device/net/pin/property differences all 0.
Power:          4.43 mW.
GDS:            runs/RUN_2026-04-17_09-50-42/final/gds/fft4.gds
Verdict:        SIGNOFF CLEAN
```

## Why this PASSes what the first attempt rate-limited

The first probe (`s11_fase2_fft_live`) was blocked by a Claude
subscription 429 before the agent wrote any RTL. This retry:
- Started with quota headroom (9:45 AM local, 7+ hours after the
  2 am subscription reset).
- Completed in 7 turns with a single LibreLane iteration (agent
  picked CLOCK_PERIOD = 50 ns directly from the DSP context cue in
  the prompt, no retry needed).
- Hit the gate-level-safe testbench conventions cleanly — no x-prop
  sim stumbles, no SDF warnings at post-PnR.

## Fase 2 final state

Both targets in the original plan (compute CPU + dataflow FFT) now
pass Pass@1. Fase 2 closes unambiguously for `complex`-tier single-
shot digital designs up to the ~2 k-cell range on GF180MCU-D.

The `IdeaToRTLLoop` fallback (iterative propose → sim → critique →
re-propose) remains unneeded at this complexity tier. Its
applicability threshold moves up to S12+ for >10 k-cell designs.

## Natural-language input (same as first probe)

> 4-point radix-2 decimation-in-time FFT for 8-bit REAL inputs,
> producing 4 complex outputs. ... No multipliers are needed because
> all twiddles are {1, -1, j, -j}. ...

No hand-written RTL, no hand-written testbench, no pre-tuned config
— everything came out of the agent in 7 turns.
