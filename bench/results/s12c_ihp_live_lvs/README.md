# S12-C re-run with LVS enforced (IHP SG13G2)

Closure update for **S12-C** (`feat/s12c-ihp-live`). This re-runs
`e2e_idea_to_digital_counter_ihp_live` with the IHP LibreLane template
upgraded to enable KLayout LVS as a real signoff gate. Supersedes the
"LVS intentionally skipped" caveat documented in
`bench/results/s12c_ihp_live/README.md`.

## Result: PASS (Pass@1 = 100%, LVS-clean)

- Wall time: **295.87 s** (~5 min).
- LLM cost: **$1.95 USD** (budget was $15).
- Turns: 4.
- Pass predicate (now enforced):
  `gds_exists=1 AND gl_post_synth_ok=1 AND gl_post_pnr_ok=1 AND
  design__lvs_error__count=0`.

Agent verdict (from the CC CLI final report):

```
Design:    counter4_ihp — 4-bit sync up-counter (IHP SG13G2 130nm)
RTL:       clk, rst_n, en -> count[3:0]
           1622 instances post-PnR (4 flops + adder + tap/endcap + fill)
Timing @ CLOCK_PERIOD=25 ns (multi-corner, signoff STA):
           Setup WNS: +18.586 ns at nom_fast_1p32V_m40C
           Hold  WNS: +0.211 ns at nom_fast_1p32V_m40C
           No setup/hold/slew/cap violations in any corner
DRC:       KLayout signoff DRC 0
LVS:       KLayout sg13g2.lvs -> "Congratulations! Netlists match."
           design__lvs_error__count = 0
GDS:       runs/RUN_2026-04-18_16-01-42/final/gds/counter4_ihp.gds
Verdict:   SIGNOFF CLEAN (RTL lint, sim, synth, PnR, DRC, LVS all green)
```

## What changed since the original s12c run

The original `s12c_ihp_live/` evidence reported "LVS intentionally
skipped" on the basis of a session memory that claimed the dev-branch
KLayout LVS deck errored on stdcell CDLs. Direct reproduction with
`run_lvs.py` against the dev branch (`ef9a8bce`) showed the deck
actually handles stdcells AND core-only digital chips cleanly:

- `python3 run_lvs.py --layout=sg13g2_or3_1.gds --netlist=sg13g2_or3_1.cdl`
  → PASS in 3 s.
- `python3 run_lvs.py --net_only --layout=counter4_ihp.gds`
  → extracts cleanly in 4.5 s.
- `klayout -b -r sg13g2.lvs -rd input=counter4_ihp.gds -rd schematic=<chip+stdcell.cdl>`
  → "Congratulations! Netlists match." in 2 s.

The genuine upstream caveat is the IHP **pad** CDL
(`libs.ref/sg13g2_io/cdl/sg13g2_io.cdl::sg13g2_RCClampResistor`),
which uses Cadence Spectre extensions (`$SUB=sub!`, `$[res_rppd]`)
that the KLayout LVS reader rejects. Core-only digital chips
instantiate no pads, so the template now sets `PAD_CDLS: []` and the
LVS step is enforced unconditionally. Designs that *do* use pads will
need an upstream fix (or a per-design `PAD_CDLS` override).

## Template changes

`src/eda_agents/agents/templates/ihp_sg13g2.yaml.tmpl`, two edits:

1. `meta.substituting_steps`:
   - `Magic.SpiceExtraction: OpenROAD.WriteCDL` (was `null`)
   - `Netgen.LVS: KLayout.LVS` (was `null`)
   - dropped `Checker.LVS: null`
   - dropped `RUN_LVS: false`
2. New top-level key `PAD_CDLS: []` to avoid the pad-CDL parser bug.

The flow now ends with `OpenROAD.WriteCDL → KLayout.LVS → Checker.LVS`
in place of the Magic/Netgen chain.

## What was exercised

End-to-end, via the bench runner:

1. `bench/tasks/end-to-end/idea_to_digital_counter_ihp_live.yaml`
   (now also enforces `design__lvs_error__count: {max: 0}`).
2. `eda_agents.bench.adapters:run_idea_to_digital_chip`.
3. `eda_agents.agents.idea_to_rtl.generate_rtl_draft`.
4. `eda_agents.agents.tool_defs.build_from_spec_prompt(pdk_config="ihp_sg13g2",
   tb_framework="iverilog")` — same 12,703-char prompt body.
5. `eda_agents.agents.claude_code_harness.ClaudeCodeHarness` -> Claude
   Code CLI (double-gated dangerous-perms).
6. LibreLane v3 Classic flow with the updated template — flow tail:
   `OpenROAD.WriteCDL → KLayout.LVS → Checker.LVS`.
7. `GlSimRunner.run_post_synth` — PASS.
8. `GlSimRunner.run_post_pnr` with SDF annotation — PASS, 0 SDF warnings.

## Artefacts persisted in this directory

- `summary.json` — per-task record from `scripts/run_bench.py`.
- `report.md` — tabular run summary.
- `e2e_idea_to_digital_counter_ihp_live/idea_to_chip_result.json` —
  structured result dict; `result_text_tail` quotes the LVS clean
  message.
- `e2e_idea_to_digital_counter_ihp_live/config.yaml` — agent-authored
  LibreLane config (note `PAD_CDLS: []` and the new substitution map).

## Environment captured at run time

- IHP-Open-PDK: `dev` branch @
  `ef9a8bce6f1adf8486b7a7c8b367a9da4922647f` (Merge PR #930, unchanged
  since the original s12c run).
- LibreLane: v3 venv at `/home/montanares/git/librelane/.venv`.
- Nix EDA tools: same versions as original s12c run.
- Claude Code CLI on PATH at `/home/montanares/.npm-global/bin/claude`.

## How to reproduce

```bash
cd /home/montanares/personal_exp/eda-agents  # or the worktree
git checkout feat/s12c-ihp-live              # or its merge commit
export PDK_ROOT=/home/montanares/git/IHP-Open-PDK
export EDA_AGENTS_ALLOW_DANGEROUS=1
source .env   # provides OPENROUTER_API_KEY — not used by CC CLI but harmless
.venv/bin/python scripts/run_bench.py \
    --task e2e_idea_to_digital_counter_ihp_live \
    --run-id s12c_ihp_live_lvs \
    --verbose
```

Expected: Pass@1 = 100%, ~5 min wall, ~$2 LLM spend, real LVS clean.
