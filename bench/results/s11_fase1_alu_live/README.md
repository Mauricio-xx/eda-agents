# S11 Fase 1 probe — idea-to-digital-chip medium complexity (ALU 8-bit)

Evidence for Fase 1 single-shot probe on an 8-bit ALU, GF180MCU-D.

## Result: PASS (Pass@1 = 100%) with a prompt bug surfaced and fixed

- Total duration: **763.35 s** (12m 43s).
- LLM cost: **$3.14 USD** (budget was $10).
- Turns: 1 (CLI JSON-output-mode reports the whole session as one turn).
- Pass predicate: `gds_exists=1 AND gl_post_synth_ok=1 AND gl_post_pnr_ok=1`.

Agent verdict (from the CC CLI final report):

```
Design:       alu8 — 8-bit combinational ALU wrapped in a flop-registered top
Ops:          ADD / SUB / AND / OR / XOR / SHL1 / SHR1 / PASSA
RTL:          clk, rst_n, a[7:0], b[7:0], op[2:0] -> result[7:0], zero, cout
Testbench:    covers every op with directed vectors + boundary cases (a=0, b=0, a=255, b=255, SUB borrow)
Timing:       Setup WNS closed, Hold WNS closed, 0 violations
DRC:          0 violations
LVS:          Match
Antenna:      0 violating nets
GDS:          runs/RUN_2026-04-16_23-22-40/final/gds/alu8.gds
Verdict:      SIGNOFF CLEAN
```

## Notable finding: prompt bug

Claude started a `tail -n 0 -F librelane.log | grep ...` pipeline AFTER
the LibreLane flow had already exited. Because `tail -F` waits for new
output, the pipeline stalled indefinitely even though the flow (and
its log writes) were already complete. The session-observer had to
kill the stuck tail to let Claude finalize its report.

Fixed in the same commit: `build_from_spec_prompt` now includes a
"LOG INSPECTION DISCIPLINE" section explicitly forbidding `tail -f` /
`tail -F` against a process whose writer has already exited. A
regression test (`tests/test_from_spec.py::test_prompt_warns_against_tail_f`)
keeps this warning from drifting out.

## What was exercised

Same stack as `s11_fase0_live`:

1. `idea_to_digital_alu8_gf180_live.yaml` -> `harness: callable`.
2. `eda_agents.bench.adapters:run_idea_to_digital_chip`.
3. `eda_agents.agents.idea_to_rtl.generate_rtl_draft`.
4. `build_from_spec_prompt` (with the ALU description's clock-wrapping
   hint).
5. `ClaudeCodeHarness` + Claude Code CLI.
6. LibreLane Classic flow (74 steps, ending at KLayout DRC signoff).
7. `GlSimRunner` post-synth + post-PnR with SDF annotation.

## Fase 1 conclusion

Single-shot is SUFFICIENT for ALU 8-bit on GF180. The iterative
`IdeaToRTLLoop` planned in the session roadmap is NOT required for
medium-complexity designs — a well-structured prompt + a good
testbench directive carry Claude through to signoff in one turn.

The iterative loop remains relevant for Fase 2 (small CPU / FFT)
where design complexity likely exceeds single-shot reliability. That
assessment will be re-evaluated during Fase 2 probing.
