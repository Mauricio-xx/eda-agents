# S11 Fase 0 — idea-to-digital-chip live evidence

Evidence for the closure of **S11 Fase 0** (`feat/s11-idea-to-chip-spike`).
Gate task: `e2e_idea_to_digital_counter_live` on GF180MCU-D.

## Result: PASS (Pass@1 = 100%)

- Total duration: **164.03 s** (2m 44s).
- LLM cost: **$0.61 USD** (budget was $8).
- Turns: 12.
- Pass predicate: `gds_exists=1 AND gl_post_synth_ok=1 AND gl_post_pnr_ok=1`.

Agent verdict (from the CC CLI final report):

```
Design:       counter4 — 4-bit sync up-counter, active-low async reset, enable
RTL:          clk, rst_n, en, count[3:0]; ~590 post-PnR instances incl. filler/tie/decap
Timing:       Setup WNS +15.379 ns (closed, target 25 ns); Hold WNS +0.309 ns (closed)
              0 setup violations / 0 hold violations
DRC:          0 violations (Magic)
LVS:          Match (Netgen — circuits match uniquely)
Antenna:      0 violating nets
GDS:          runs/RUN_2026-04-16_23-13-29/final/gds/counter4.gds
Verdict:      SIGNOFF CLEAN
```

## What was exercised

End-to-end, via the bench runner:

1. `bench/tasks/end-to-end/idea_to_digital_counter_live.yaml` -> `harness: callable`.
2. `eda_agents.bench.adapters:run_idea_to_digital_chip` (new, S11 Fase 0).
3. `eda_agents.agents.idea_to_rtl.generate_rtl_draft` (new, S11 Fase 0).
4. `eda_agents.agents.tool_defs.build_from_spec_prompt` (pre-existing).
5. `eda_agents.agents.claude_code_harness.ClaudeCodeHarness` (pre-existing) ->
   Claude Code CLI 2.1.112.
6. LibreLane 3.0.0rc0 Classic flow via
   `/home/montanares/git/librelane/.venv/bin/python`.
7. `eda_agents.core.stages.gl_sim_runner.GlSimRunner.run_post_synth`
   (iverilog + vvp) -> post-synth gate-level sim against the agent's
   own testbench.
8. `GlSimRunner.run_post_pnr` with SDF annotation -> post-PnR GL sim
   (0 SDF warnings).

## Inputs

Natural-language description supplied to the agent:

> 4-bit synchronous up-counter with active-low asynchronous reset and an
> enable input. Inputs: clk, rst_n, en. Output: count[3:0]. When en=1
> and rst_n=1, count increments on each rising edge of clk. When rst_n=0,
> count returns to 0 asynchronously. When en=0, count holds its current
> value.

The agent was given no hand-written RTL, testbench, or config — only the
description above plus the infrastructure prompt from
`build_from_spec_prompt`.

## Artefacts persisted in this directory

- `summary.json` — per-task record written by `scripts/run_bench.py`.
- `report.md` — tabular run summary.
- `e2e_idea_to_digital_counter_live/idea_to_chip_result.json` — the
  structured `IdeaToRTLResult` dict returned by `generate_rtl_draft`.
- `e2e_idea_to_digital_counter_live/config.yaml` — the LibreLane config
  the agent produced from the GF180 template.

## Notes

- The counter RTL and testbench the agent wrote are NOT committed to the
  repo tree — they live inside the gitignored `runs/` directory under the
  bench results. This keeps the evidence of "what got shipped" tied to
  the frozen commit hash, while still showing the pass verdict in the
  summary.
- Dry-mode gate (`e2e_idea_to_digital_counter`) is separately green and
  does not require CC CLI / LibreLane / PDK — it guards the plumbing on
  every CI run.
