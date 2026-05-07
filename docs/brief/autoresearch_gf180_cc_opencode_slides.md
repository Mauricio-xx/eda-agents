# Brief: slides on eda-agents autoresearch over GF180 (cc_cli vs opencode)

This is the briefing for the slides agent. The repo is at
`/home/montanares/personal_exp/eda-agents/`. Run `git show <sha>` and
`Read <path>` for any of the references below — the SHAs / paths are
the source of truth, this brief is the index.

## What the deck has to communicate

A short, schematic deck (~6-10 frames) covering:

1. The eda-agents stack at a glance (refresh) — analog vs digital
   loops, agents + skills + bench peers (sibling to the existing
   agents-analog-digital deck, do not duplicate it whole).
2. The autoresearch loop applied to digital flow on GF180 — what
   gets proposed (CLOCK_PERIOD, PL_TARGET_DENSITY_PCT, PDN, etc.)
   and what gets evaluated (LibreLane harden + cocotb GL sim +
   weighted FoM).
3. The new `idea_to_optimize` chain — `idea_loop` (natural-language
   spec to converged RTL) feeds `autoresearch` (greedy exploration
   over flow knobs on top of that converged RTL). Two-phase pipeline.
4. Backend dispatch architecture — same autoresearch greedy loop,
   proposals routed by `--backend` to either Claude Code CLI
   subscription (cc_cli) or opencode CLI (any provider, e.g.
   gpt-5.3-codex). Same TestProposalDispatch hard-guarantees both.
5. Live A/B on GF180 — `examples/11_idea_to_chip_demo_gf180.py
   --case idea_to_optimize` running cc_cli and opencode in parallel
   on a Goertzel FP32. Either show preliminary numbers if the runs
   close in time, or sketch the experimental design if not (see
   "Live run state" below).
6. The today gotcha index (a single slide) — five real blockers we
   hit live and the framework patches that unblocked them.
   Engineering-honest, not marketing.

Existing companion deck (this is the style reference; the new deck
is a *sibling* talking specifically about autoresearch + GF180 +
backends):

- `tutorials/agents-analog-digital/main.tex`
- `tutorials/agents-analog-digital/sections/04-autoresearch.tex` —
  the prior treatment of the autoresearch loop (re-use the tikz
  pattern; do NOT just copy the slide, extend the discussion to
  digital flow specifically).
- `tutorials/rtl2gds-gf180-docker/main.tex` — sibling RTL-to-GDS
  tutorial (architectural reference, do not redo).

Build pattern in those decks: beamer + tikz, `aspectratio=169`,
`make` for audience PDF, `make notes` for speaker notes PDF. Heavy
content lives in `\note{...}` blocks. Mirror that.

## Suggested deck location

```
tutorials/autoresearch-gf180-backends/
  main.tex
  preamble.tex
  Makefile
  sections/
    01-context.tex          (refresh: where autoresearch fits)
    02-loop-applied.tex     (the loop on digital flow + GF180)
    03-idea-to-optimize.tex (chain + diagram)
    04-backends.tex         (cc_cli vs opencode dispatch)
    05-live-ab.tex          (the GF180 demo + numbers if available)
    06-gotchas.tex          (today's blockers + framework patches)
```

Don't actually scaffold the full deck unless you have time; one
clean main.tex with sections inline is fine for a *par de slides*.

## Key commits (SHA-only; review with `git show <sha>`)

These are the commits that define the autoresearch + cc_cli + opencode
arc the deck has to explain. Each line is a hint; the SHA is
authoritative.

### Autoresearch core arc

- `7b5720d` RTL-aware autoresearch: three optimization strategies
  (flow / rtl / hybrid). The strategy axis is referenced in the deck.
- `89ab94f` CC CLI backend for RTL-aware autoresearch proposals
  (first cut, single backend).
- `f1b6d13` agents/digital_autoresearch: backend-correct proposals
  + free-form params. **The architectural fix**: `_propose_params`
  now dispatches by `self.backend` so flow-strategy proposals run
  through the same harness rtl/hybrid use. Without this, a user who
  picks `--backend opencode --model openai/gpt-5.3-codex` ended up
  routed to OpenAI directly via LiteLLM instead of through the
  opencode CLI (subscription bypassed).
- `09865df` agents+core: anti-centroid seed + drop literal range
  tuples from program.md (eval 1 baseline + program.md hygiene).
- `10623ab` core: introduce StageResults bag and dict-based
  DigitalDesign API (the data shape that flows from runners back
  into FoM).
- `6dd7210` core/flow_metrics: PPA-style weighted_fom + clock_period_ns
  ingest (the FoM formula the loop scores against).

### Harness family

- `761102a` feat: add LiteLLMAgentHarness and OpenCodeHarness as
  CC CLI alternatives. The three harnesses (ClaudeCodeHarness,
  OpenCodeHarness, LiteLLMAgentHarness) all expose the same async
  `.run() -> HarnessResult` contract — that's what makes backend
  dispatch trivial above them.

### Idea-to-chip / idea_to_optimize chain

- `2d116c8` Merge feat/s11-idea-to-chip-spike: idea-to-chip arc
  (digital NL->GDS + analog topology recommender + gLayout SG13G2
  layout dispatch + cocotb).
- `0e7ce9e` S11 Fase 0 idea->digital-chip: library, MCP tool, bench
  adapter.
- `da9db3c` S11: wire tb_framework=cocotb through the from-spec stack.
- `4deb535` S11 Fase 1.5 fix: cocotb TB path skips GL sim cleanly.
- `0b31417` Merge feat/s12a-digital-verification: IdeaToRTLLoop +
  cocotb critique-feedback + S12-D OpenCode wrapper. **This is when
  IdeaToRTLLoop with critique-feedback shipped + the backend abstraction
  for opencode landed at the loop level.**
- `a73c6ec` S12-A Gap 1: IdeaToRTLLoop with sim/lint critique skills.
- `26c5a02` S12-A stretch fix: per_turn_timeout_s in IdeaToRTLLoop.
- `a4943e4` S12-A loop fix: per-turn timeouts no longer abort outer loop.
- `1106996` S12-A Gap 2: GlSimRunner cocotb backend.

### Toolchain hardening for autoresearch on GF180

- `f77e9d6` Fix PDK env var for GF180 autoresearch + LibreLaneRunner
  env_extra.
- `a617e76` Prepend nix yosys 0.62+ to PATH for LibreLane v3 compat.
- `7d1de3c` Prepend all nix EDA tools (yosys, openroad, magic, klayout,
  netgen).
- `aaa7c2a` Post-synth GL sim gate for the digital flow.
- `8e6ac66` Post-PnR GL sim gate with SDF annotation.
- `dd6cd58` Full verification pipeline: stop skipping stages.
- `108d36f` agents+core: enable cocotb GL sim post-flow (current
  HEAD).
- `e386cf2` core: make `DigitalDesign.testbench` mandatory. **Required
  for the deck's "everything checks against a tb" framing**.
- `6ee285b` designs: register testbenches on systolic_mac_dft + generic.

### Working-tree (not yet committed) — relevant to "today" gotchas

- `src/eda_agents/core/librelane_runner.py` — added
  `_write_librelane_config()` that materialises a sibling
  `config.librelane.yaml` stripping `EDA_AGENTS_*` keys before
  invoking LibreLane (LibreLane v3 schema validator is strict and
  rejects unknown keys, but `GenericDesign.testbench()` reads
  `EDA_AGENTS_TB_DRIVER/TARGET/DIR/ENV` from the same file —
  the strip resolves the conflict).
- `src/eda_agents/core/stages/gl_sim_runner.py` — `_find_post_synth_netlist`
  / `_find_post_pnr_netlist` now try both kebab (`design.project_name()`)
  and snake (`DESIGN_NAME` from config) variants, plus a generic
  `*-yosys-synthesis/*.nl.v` fallback. LibreLane writes filenames
  using `DESIGN_NAME` verbatim; `project_name()` was producing kebab.
- `examples/11_idea_to_chip_demo_gf180.py` — the demo orchestrator
  with `--case {idea_loop, fazyrv_flow, fazyrv_hybrid, idea_to_optimize, all}`,
  `--backend {cc_cli, opencode}`, `--plots`, `--dry-run`,
  `--skip-phase-idea`. New for this slide arc.
- `src/eda_agents/utils/plot_autoresearch.py` — matplotlib helpers
  for FoM evolution, metrics grid, params evolution. Used by the
  example.
- `tests/test_digital_autoresearch.py::TestProposalDispatch` (line 881)
  — the regression guard for the backend-dispatch fix.
- `src/eda_agents/templates/{claude,opencode}_agents/gf180-idea-to-chip.md`
  — orchestrator agent templates for both runtimes.

## Key files for diagrams

- `src/eda_agents/agents/digital_autoresearch.py` — main loop. See
  `_propose_params` (line 504), `_propose_params_via_cc_cli` (line 608),
  `_propose_params_via_opencode` (line 651), `_build_flow_proposal_prompt`
  (line 580), `run` (loop body, around line 1700).
- `src/eda_agents/agents/_autoresearch_core.py` — `proposal_temperature`
  + `extract_json_from_response` shared across analog/digital loops.
- `src/eda_agents/agents/idea_to_rtl.py` — `_build_harness` (line 326)
  is the harness-dispatch pattern that `_propose_params` now mirrors.
- `src/eda_agents/agents/idea_to_rtl_loop.py` — IdeaToRTLLoop with
  per-turn critique feedback.
- `src/eda_agents/agents/{claude_code_harness,opencode_harness}.py`
  — same `.run() -> HarnessResult` contract, different CLI underneath.
- `src/eda_agents/core/librelane_runner.py` — `LibreLaneRunner` +
  `SAFE_CONFIG_KEYS` + the new `_write_librelane_config()` strip.
- `src/eda_agents/core/stages/rtl_sim_runner.py` — `CocotbDriver`,
  `IVerilogDriver`. Note `target.startswith("make")` convention.
- `src/eda_agents/core/stages/gl_sim_runner.py` — post-synth +
  post-PnR GL sim with SDF annotation gate.
- `src/eda_agents/core/designs/generic.py` — `GenericDesign.testbench()`
  resolver: EDA_AGENTS_TB_* keys then iverilog auto-detect glob.
  Source of the kebab-vs-snake naming dance (`project_name()` line 125).
- `src/eda_agents/agents/templates/gf180.yaml.tmpl` — LibreLane
  config template for GF180.
- `examples/11_idea_to_chip_demo_gf180.py` — the demo.
- `examples/10_digital_autoresearch_gf180.py` — autoresearch standalone.
- `examples/09_rtl2gds_gf180.py` — single-shot RTL-to-GDS.
- `docs/architecture.md` — the layered diagram (agents / topologies /
  core / skills / specs / bench-bridge).

## Architectural diagrams to draw (verbatim sketches)

### Diagram A: idea_to_optimize chain

```
   natural-language spec
            |
            v
   +------------------+        +----------------------+
   |   idea_loop      |        |  autoresearch (flow) |
   |  (idea_to_rtl_   |        |  (digital_autoresearch)|
   |   loop with      |        |                      |
   |   critique feed- |        |   propose -> harden  |
   |   back; budget   |---->---|   -> sim -> FoM ->   |
   |   in turns)      | RTL+tb |   keep/discard ->    |
   |                  | conv-  |   propose ...        |
   |  out: phase_idea/| erged  |                      |
   |  config.yaml +   |        |   out: phase_optimize/|
   |  src/, tb/, runs/|        |   results.tsv +      |
   +------------------+        |   plots/             |
                               +----------------------+
```

Key claim: phase_idea is a one-shot RTL synthesis-with-feedback;
phase_optimize is greedy iteration over flow knobs on top.

### Diagram B: backend dispatch in `_propose_params`

```
                 strategy=flow         strategy=rtl/hybrid
                       |                       |
                       v                       v
         async _propose_params         async _propose_<x>
                       |                       |
            switch self.backend       switch self.backend
              /     |     \              /     |     \
          cc_cli  opencode  litellm   cc_cli  opencode  litellm
            |       |          |       |       |          |
            v       v          v       v       v          v
   ClaudeCode  OpenCode  litellm.   Claude  OpenCode  LiteLLMAgent
   Harness    Harness   acompletion  Code   Harness   Harness
   (Opus 4.7) (gpt-5.3)  (provider-  (Opus  (gpt-5.3) (provider-
              codex)     direct)    4.7)    codex)    direct)
```

Key claim: same dispatcher across strategies; harness contract is
uniform; each harness preserves provider OAuth so the user's
subscription is what's used.

### Diagram C: greedy autoresearch loop (recap from existing deck)

The existing `04-autoresearch.tex` already has this. Reuse the tikz.
Annotate that the *Propose* node is what dispatches by backend.

## Live A/B run state (today, may finish during deck authoring)

Two parallel runs on GF180MCU with the same Goertzel FP32 RTL:

- `/home/montanares/i2o_claude_demo/idea_to_optimize/` — backend=cc_cli,
  budget=3, strategy=flow.
- `/home/montanares/i2o_opencode_demo/idea_to_optimize/` —
  backend=opencode, model=`openai/gpt-5.3-codex`, budget=3, strategy=flow.

Inputs are pre-staged in `phase_idea/` (config.yaml + src + tb +
runs from prior idea_loop). Outputs land in `phase_optimize/results.tsv`
and `plots/phase_optimize/{fom_evolution,metrics_grid,params_evolution}.png`.

The runs may not produce a meaningful A/B comparison in time. If the
deck is needed before they close, present the experimental design as
the proof point and defer numbers to a follow-up.

## Today's gotchas (one slide, honest engineering)

Five real blockers that came up turning idea_to_optimize live on
fresh main:

1. **`DigitalDesign.testbench()` is mandatory** (commit `e386cf2`).
   Pre-staged configs that predated this commit failed at runtime
   because `RtlSimRunner.run()` calls `design.testbench()` and
   `GenericDesign.testbench()` raises if neither
   `EDA_AGENTS_TB_DRIVER`+`EDA_AGENTS_TB_TARGET` are declared nor
   a `tb/tb_*.v` Verilog testbench exists. Fix: add the four
   `EDA_AGENTS_TB_*` keys to the config (canonical:
   `fixtures/sample_librelane_config.yaml`).
2. **`CocotbDriver` requires `target=make sim`, not just `target=sim`.**
   The driver dispatches on `target.startswith("make")` — anything
   else falls into a `["python3", target]` invocation that does not
   work for cocotb projects.
3. **Cocotb subprocess needs librelane venv on PATH.** The Makefile
   embeds `$(shell cocotb-config --makefiles)` and that resolves to
   nothing without prepending `/home/montanares/git/librelane/.venv/bin`
   to `PATH`. Symptomatic when running from `/usr/bin/python3.12`.
4. **LibreLane v3 strict schema rejects `EDA_AGENTS_*` keys.** The
   same config that `GenericDesign.testbench()` reads gets passed to
   LibreLane, which errors with `Unknown key 'EDA_AGENTS_TB_DIR'`
   etc. Fix today (uncommitted): `LibreLaneRunner._write_librelane_config()`
   materialises a sibling `config.librelane.yaml` with the
   eda-agents-only namespace stripped, and that's what LibreLane
   sees. `dir::` paths still resolve because the sibling lives in
   the same project directory.
5. **GL sim file globs vs `DESIGN_NAME` casing.** `GenericDesign.project_name()`
   converts `_` to `-` (returns `demo-goertzel-fp32`), but LibreLane
   writes the synthesized netlist using the raw `DESIGN_NAME`
   (`demo_goertzel_fp32.nl.v`). `GlSimRunner._find_post_synth_netlist`
   was globbing for the kebab spelling and missing the snake-cased
   file. Fix today (uncommitted): try both spellings, fall back to
   `*-yosys-synthesis/*.nl.v`.

These are good "what we learned" deck content because they show the
framework is real (it tripped on a real day) and that the fixes are
small (handful-of-lines diffs each). Don't sugarcoat.

## What NOT to put in the deck

- LiteLLM as a "third backend on equal footing": it is a fallback
  for development, not a recommended path for the demo. The deck
  should focus on cc_cli vs opencode.
- The autoresearch budget=3 in the demo is a smoke gate, not a real
  optimization. If the deck shows numbers, label them "smoke A/B"
  and reserve "real comparison" for a higher-budget follow-up.
- Cost in dollars. Both are subscription-billed; comparing $/run is
  noise.

## How to build (when ready)

```bash
cd tutorials/autoresearch-gf180-backends/
make            # main.pdf  (audience)
make notes      # main-with-notes.pdf  (speaker view)
```

Mirror the `agents-analog-digital/Makefile` if you scaffold from
scratch.
