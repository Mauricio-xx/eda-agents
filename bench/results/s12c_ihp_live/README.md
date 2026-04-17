# S12-C — idea-to-digital-chip live evidence (IHP SG13G2)

Evidence for the closure of **S12-C** (`feat/s12c-ihp-live`). Gate task:
`e2e_idea_to_digital_counter_ihp_live` on IHP SG13G2. This is the first
live gate for the NL → digital-chip pipeline on IHP — S11 had only shipped
the dry IHP gate and the live pass on GF180MCU-D.

## Result: PASS (Pass@1 = 100%)

- Wall time: **140.02 s** (~2 m 20 s).
- LLM cost: **$0.62 USD** (budget was $15).
- Turns: 13.
- Pass predicate: `gds_exists=1 AND gl_post_synth_ok=1 AND gl_post_pnr_ok=1`.

Agent verdict (from the CC CLI final report):

```
Design:     counter4_ihp — 4-bit sync up-counter, active-low async reset, enable
            IHP SG13G2 130nm
RTL:        clk, rst_n, en -> count[3:0]
            26 stdcells (4 DFFs + 7 comb + 3 clk buffers + 12 repair) + 644
            filler; utilisation 6.5 % of a 100 × 100 um die
Timing @ CLOCK_PERIOD=10 ns:
            typ  (1.20V, 25 C):  Setup WS +7.04 ns, Hold WS +0.43 ns (0 vio)
            fast (1.32V, -40 C): Setup WS +7.28 ns, Hold WS +0.21 ns (0 vio)
            slow (1.08V, 125 C): Setup WS +6.61 ns, Hold WS +0.84 ns (0 vio)
            0 setup violations / 0 hold violations at every corner
DRC:        KLayout signoff DRC 0, OpenROAD route DRC 0
Antenna:    0 violating nets
LVS:        Intentionally skipped (known IHP KLayout LVS deck issue
            upstream; template sets RUN_LVS=false and substitutes
            Netgen.LVS with null)
Power:      118.6 uW total (internal 97.9, switching 20.2, leakage 0.5 nW)
GDS:        runs/RUN_2026-04-17_19-18-29/final/gds/counter4_ihp.gds (137 KiB)
Verdict:    SIGNOFF CLEAN (LVS intentionally skipped; no other signoff gates failed)
```

## What was exercised

End-to-end, via the bench runner:

1. `bench/tasks/end-to-end/idea_to_digital_counter_ihp_live.yaml` -> `harness: callable`.
2. `eda_agents.bench.adapters:run_idea_to_digital_chip`.
3. `eda_agents.agents.idea_to_rtl.generate_rtl_draft`.
4. `eda_agents.agents.tool_defs.build_from_spec_prompt(pdk_config="ihp_sg13g2", tb_framework="iverilog")` — 12,731-char prompt with the LOG INSPECTION DISCIPLINE + Magic-slowness + no-`tail -f` guard rails intact.
5. `eda_agents.agents.claude_code_harness.ClaudeCodeHarness` -> Claude Code CLI (double-gated dangerous-perms: `allow_dangerous=True` + `EDA_AGENTS_ALLOW_DANGEROUS=1`).
6. LibreLane v3 Classic flow via `/home/montanares/git/librelane/.venv/bin/python`, rendered from `src/eda_agents/agents/templates/ihp_sg13g2.yaml.tmpl`.
7. `eda_agents.core.stages.gl_sim_runner.GlSimRunner.run_post_synth` (iverilog + vvp) — post-synth gate-level sim against the agent-authored testbench. PASS.
8. `GlSimRunner.run_post_pnr` with SDF annotation — post-PnR GL sim, **0 SDF warnings**. PASS.

## Inputs

Natural-language description supplied to the agent (same body as the
GF180 live counter gate for direct comparison):

> 4-bit synchronous up-counter with active-low asynchronous reset and an
> enable input. Inputs: clk, rst_n, en. Output: count[3:0]. When en=1
> and rst_n=1, count increments on each rising edge of clk. When rst_n=0,
> count returns to 0 asynchronously. When en=0, count holds its current
> value.

No hand-written RTL, testbench, or config were provided — only the
description plus the infrastructure prompt from `build_from_spec_prompt`.

## Artefacts persisted in this directory

- `summary.json` — per-task record written by `scripts/run_bench.py`.
- `report.md` — tabular run summary.
- `e2e_idea_to_digital_counter_ihp_live/idea_to_chip_result.json` — the
  structured `IdeaToRTLResult` dict returned by `generate_rtl_draft`.
- `e2e_idea_to_digital_counter_ihp_live/config.yaml` — the LibreLane
  config the agent produced from the IHP template.

The agent-authored RTL and testbench live inside the gitignored `runs/`
and `src/` + `tb/` directories (same convention as the S11 evidence
dirs).

## Environment captured at run time

- IHP-Open-PDK: `dev` branch @ `ef9a8bce6f1adf8486b7a7c8b367a9da4922647f`
  (Merge PR #930 *KLayout PCell Libraries: properly register technology*,
  up-to-date with `origin/dev`).
- LibreLane: v3 venv at `/home/montanares/git/librelane/.venv`.
- Nix EDA tools (resolved by
  `eda_agents.agents.digital_autoresearch.detect_nix_eda_tool_dirs`):
  yosys 0.62, OpenROAD 2026-02-17, KLayout 0.30.2, Netgen 1.5.295,
  Magic 8.3.581 (installed but unused — see template note below).
- Claude Code CLI on PATH at `/home/montanares/.npm-global/bin/claude`.

## Why 140 s instead of the expected 20–60 min

The S11 handoff document warned that Magic.StreamOut on IHP can take
20–60+ minutes per design, which was the dominant operational risk for
shipping a live IHP gate. That concern is obsolete for this pipeline:
`src/eda_agents/agents/templates/ihp_sg13g2.yaml.tmpl` already routes
around the Magic chain entirely by substituting `Magic.StreamOut`,
`Magic.WriteLEF`, `Magic.SpiceExtraction`, `Magic.DRC`,
`Checker.MagicDRC`, `Checker.IllegalOverlap`,
`Odb.CheckDesignAntennaProperties`, `Netgen.LVS`, and `Checker.LVS`
with null, and streaming out GDS via KLayout
(`PRIMARY_GDSII_STREAMOUT_TOOL: klayout`). This matches the signoff path
recommended by the IHP-Open-PDK team themselves in their own LibreLane
config. The Magic binary in the Nix store is therefore never invoked
during this flow.

The one caveat introduced by this mitigation: LVS is disabled
(`RUN_LVS: false`) because the KLayout LVS deck on the current IHP dev
branch errors out while parsing the stdcell CDLs (upstream issue). The
run enforces `gds_exists + gl_post_synth_ok + gl_post_pnr_ok` —
sufficient for a digital signoff-shape gate — but NOT LVS match. That
limitation is declared in the task YAML (`notes:` field) and in this
README; it is the only honest-caveat on this PASS.

## How to reproduce

```bash
cd /home/montanares/personal_exp/eda-agents  # or the worktree
git checkout feat/s12c-ihp-live              # or its merge commit
export PDK_ROOT=/home/montanares/git/IHP-Open-PDK
export EDA_AGENTS_ALLOW_DANGEROUS=1
source .env   # provides OPENROUTER_API_KEY — not used by CC CLI but harmless
.venv/bin/python scripts/run_bench.py \
    --task e2e_idea_to_digital_counter_ihp_live \
    --run-id s12c_ihp_live \
    --verbose
```

Expected: Pass@1 = 100%, ~2 m wall, < $1 LLM spend.
