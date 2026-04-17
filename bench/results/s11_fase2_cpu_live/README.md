# S11 Fase 2 probe — idea-to-digital-chip complex (8-bit accumulator CPU)

Evidence for the Fase 2 (complex) single-shot probe. Target: small
accumulator-based CPU ~1800 cells on GF180MCU-D.

## Result: PASS (Pass@1 = 100%)

- Total duration: **539.35 s** (8m 59s).
- LLM cost: **$1.30 USD** (budget was $15).
- Turns: 18.
- Pass predicate: `gds_exists=1 AND gl_post_synth_ok=1 AND gl_post_pnr_ok=1`.
- LibreLane iterations inside the single agent call: **3** (Claude
  auto-retried on timing, driving CLOCK_PERIOD 25 ns → 30 ns → 50 ns
  until timing closed).

Agent verdict:

```
Design:         acpu — 8-bit accumulator CPU with Harvard memory,
                single-cycle, 16 opcodes
RTL:            1865 cells (90 seq + 331 multi-input comb + …)
Core area:      154_103 um^2 at 15.6 % utilization
Timing:         WNS = 0.000 ns @ EVERY PVT corner (inc. ss_125C_4v50);
                Hold clean. Final clock period 50 ns (20 MHz).
DRC:            Magic 0, Route 0
LVS:            0 errors (device/net/property all 0)
TB PASS:        acc=0xAA, Z=0, N=1, dmem[0]=0xAA, dmem[2]=0x08,
                dmem[3]=0x30
Verdict:        SIGNOFF CLEAN
```

## What was exercised

Same stack as Fase 0/1 (counter, ALU):

1. `idea_to_digital_accum_cpu_gf180_live.yaml`.
2. `eda_agents.bench.adapters:run_idea_to_digital_chip`.
3. `generate_rtl_draft(..., complexity="complex")`.
4. `build_from_spec_prompt` (with CPU description + memory-wrap hint).
5. `ClaudeCodeHarness` + Claude Code CLI 2.1.112.
6. LibreLane Classic flow, **3 iterations** (Claude re-tuned config
   per the Phase 6 prompt guidance). The final PASS run is
   `RUN_2026-04-16_23-42-37`.
7. `GlSimRunner` post-synth + post-PnR GL sim — both PASS.

The iterate-and-retry pattern worked as documented: Claude saw
timing violations in runs 1 and 2, backed off CLOCK_PERIOD each
time, and closed cleanly on run 3. This is single-shot from the
caller's perspective (one `generate_rtl_draft` call, one CC CLI
invocation) but multi-run from LibreLane's perspective — the
prompt allows up to 3 retries.

## Fase 2 conclusion

Single-shot + prompt-allowed retries IS SUFFICIENT for a ~2 k-cell
CPU on GF180 with a registered-testbench harness. The programmatic
`IdeaToRTLLoop` that the session plan kept as a fallback remains
UNNEEDED for designs up through this complexity tier.

Next gap (S12+): designs >10 k cells (FFT / medium CPU pipeline) —
at that scale single-shot reliability drops and the
per-iteration critique loop becomes the natural next step.

## Natural-language input

The agent received only this description plus the generic
from-spec infrastructure prompt:

> Simple 8-bit accumulator-based CPU ("acpu") with Harvard memory.
> ...16 opcodes (NOP, LDI, LDA, STA, ADDI, ADDM, SUBI, ANDI, ORI,
> XORI, JMP, JZ, JN, SHL, SHR, HALT) with 4-bit opcode field...

No hand-written RTL, no hand-written testbench, no pre-tuned
config. Everything came out of the agent.
