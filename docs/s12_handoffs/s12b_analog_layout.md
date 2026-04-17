# S12-B handoff — Analog layout: SG13G2 opamp + custom-composition loop

You are picking up the **analog half** of the S12+ roadmap. Two gaps,
one session: they share the gLayout code surface + the analog
topology skill ecosystem, so splitting them would fragment ownership.

## Inherited context

S11 analog deliverables in production:

- `analog.idea_to_topology` skill + `recommend_topology` MCP tool map
  NL to one of: `miller_ota`, `aa_ota`, `gf180_ota`, `strongarm_comp`,
  `sar_adc_{7,11}bit`, or `"custom"` (with `confidence` flag).
- `generate_analog_layout` MCP tool + generalised
  `scripts/glayout_driver.py` dispatch by `spec['pdk']`. Supports
  **primitives** (nmos / pmos / mimcap) and **composites** (diff_pair,
  current_mirror, FVF) on both GF180 and SG13G2.
- Live-verified: SG13G2 `diff_pair` GDS produced in ~7 s through the
  full MCP → `GLayoutRunner` → `.venv-glayout` → gLayout → SG13G2
  path.

S11 analog gap (this session's starting point):

- `opamp_twostage` is **GF180-only**. Driver fails fast on SG13G2 with
  a clear error. Why? The gLayout upstream SG13G2 port of
  `opamp_twostage` is **WIP on branch `feature/sg13g2-pdk-support`**
  in the fork at `/home/montanares/personal_exp/gLayout`. Earlier
  commits on that branch already delivered LVS-clean `diff_pair`,
  `current_mirror`, and `FVF` for SG13G2; opamp is next.

- `recommend_topology` returns `confidence: low` or `topology: custom`
  when the NL idea doesn't match the registered set (e.g. delta-sigma
  modulator). No downstream path exists today — the call leaves the
  user staring at a "good luck" message. This is the entry point the
  S11 vision doc (`docs/idea_to_chip_s11.md`, section "Analog side:
  Fase 3 topology recommender") explicitly flags:

  > "downstream caller should NOT commit to the recommended sizing
  > without a human expert in the loop. For novel compositions, a
  > Claude-Code-driven Python+ngspice+XSPICE+KLayout loop is the S12+
  > arc."

Merge commit where you fork: `2d116c8`. Main contains the full
S11 delivery.

## What this session must deliver

### Gap 4 — SG13G2 `opamp_twostage` LVS-clean

**Work happens in the gLayout fork, NOT in eda-agents.** Per the S11
plan:

> "Changes to gLayout itself will live in /home/montanares/personal_exp/gLayout (separate repo). eda-agents will only add a wrapper runner + skill."

Scope:
1. In gLayout fork, branch `feature/s12-opamp-sg13g2-integration` off
   the existing `feature/sg13g2-pdk-support` branch.
2. Port `opamp_twostage` to SG13G2: extend
   `src/glayout/blocks/composite/opamp/opamp_twostage.py` with
   SG13G2-aware generator (or add a decorator chain if clean).
3. Validate LVS-clean (gLayout uses KLayout LVS native on SG13G2, NOT
   Magic/Netgen). `sg13g2_mapped_pdk.lvs_klayout(component, design_name, netlist)`
   is the entry point.
4. Add a tutorial script `tutorial/ihp130_opamp_twostage.py` (mirrors
   the existing `tutorial/ihp130_FVF.py` pattern).

Then **in eda-agents**:

5. Update `scripts/glayout_driver.py::generate()`: remove the
   SG13G2-opamp-only hard block (`"opamp_twostage is gf180mcu-only
   today"`). Let the driver dispatch to gLayout's opamp function for
   both PDKs.
6. Extend `tests/test_glayout_runner.py::TestGLayoutPdkDispatch` with
   a SG13G2 opamp_twostage case (marker `@pytest.mark.glayout`).
7. Bench evidence: add `bench/results/s12b_sg13g2_opamp_layout/` with
   a README + `idea_to_chip_result.json`-like structured output (this
   is analog, so the shape is different — document it).

Acceptance: SG13G2 opamp_twostage GDS through the `generate_analog_
layout` MCP tool, LVS-clean (KLayout LVS match unique), committed
evidence.

### Gap 5 — Custom-composition analog loop

**The research gap.** Today's `recommend_topology` with
`confidence: low` or `topology: custom` is a dead end. This arc
opens a path from there to "try to synthesise something that works".

Design principle (from S11 vision):

> "Claude Code using Python + ngspice + XSPICE + KLayout in a closed
> loop: explore compositions of gLayout primitives, simulate each,
> iterate layout → DRC/LVS → post-layout SPICE until a valid solution
> emerges, or honest-fail."

Minimum viable deliverable:

1. New skill `analog.custom_composition` in
   `src/eda_agents/skills/analog.py` — guides the LLM on how to
   combine gLayout primitives (diff_pair + current_mirror + FVF +
   tapring) into a candidate topology, simulate it with ngspice
   (via `SpiceRunner`), and iterate on sizing.
2. New library module
   `src/eda_agents/agents/analog_composition_loop.py` — orchestrator
   class `AnalogCompositionLoop` with:
   ```
   propose_composition(nl_spec) → (sub-blocks, connection graph)
   size_sub_blocks(composition)  → sizing dict
   generate_layout(composition, sizing) → GDS path + netlist path
   run_spice(netlist)            → SpiceResult
   run_drc_lvs(gds, netlist)     → verdict
   critique(result, targets)     → patch proposal
   loop(nl_spec, budget) → AnalogCompositionResult
   ```
3. New MCP tool `explore_custom_topology(description, constraints,
   max_iterations)` — exposes the loop for novel NL ideas.
4. Bench task (dry + live) with a simple-ish target that's OUTSIDE
   the registry — e.g. "current DAC with 4-bit binary-weighted
   output" or "simple bandgap reference". Pick something small so
   budget doesn't explode.
5. Honest-fail is a valid outcome. Document the ceiling.

Acceptance: evidence dir with at minimum **one** NL description that
took the loop to a LVS-clean GDS + matching SPICE sim. Or a
documented honest-fail analysis (what the loop converged on, why it
couldn't close, what rule would need to change).

## Environment setup

**eda-agents worktree:**

```bash
cd /home/montanares/personal_exp/eda-agents
git worktree add -b feat/s12b-analog-layout \
    /home/montanares/git/eda-agents-worktrees/s12b-analog-layout main

cd /home/montanares/git/eda-agents-worktrees/s12b-analog-layout
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,agents,mcp,adk]"
.venv/bin/pip install -U "python-dotenv>=1.1.0"

source /home/montanares/personal_exp/eda-agents/.env   # OPENROUTER_API_KEY

# PDKs: IHP is the default for analog S12-B work.
export PDK_ROOT=/home/montanares/git/IHP-Open-PDK
# For GF180 cross-checks:
#   export PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu
```

**gLayout fork workflow (for gap 4):**

```bash
cd /home/montanares/personal_exp/gLayout
git fetch origin
git checkout feature/sg13g2-pdk-support
git pull
git checkout -b feature/s12-opamp-sg13g2-integration

# Iterative test loop uses .venv-glayout from the main eda-agents repo:
/home/montanares/personal_exp/eda-agents/.venv-glayout/bin/python \
    tutorial/ihp130_opamp_twostage.py   # your new tutorial
```

The `.venv-glayout` already has gLayout installed via
`pip install --no-deps -e /home/montanares/personal_exp/gLayout`. Any
change you push on the fork branch is immediately visible to
eda-agents tests.

## Files to read before writing code

**Understand gLayout SG13G2 state (read-only):**

- `/home/montanares/personal_exp/gLayout/src/glayout/pdk/sg13g2_mapped/sg13g2_mapped.py`
  — 221 lines. Layer map + MappedPDK instance.
- `/home/montanares/personal_exp/gLayout/src/glayout/pdk/sg13g2_mapped/sg13g2_grules.py`
  — SG13G2 design rules. opamp port work likely pokes at this.
- `/home/montanares/personal_exp/gLayout/src/glayout/pdk/sg13g2_mapped/sg13g2_decorator.py`
  — nSD-layer auto-removal (LVS workaround).
- `/home/montanares/personal_exp/gLayout/src/glayout/blocks/composite/opamp/opamp_twostage.py`
  — the composite you will extend. Expect gf180-first code paths.
- `/home/montanares/personal_exp/gLayout/tutorial/ihp130_FVF.py`
  — pattern for a SG13G2 tutorial script. Your opamp tutorial
  follows this shape.
- Recent SG13G2 commits on the fork:
  - `efe1779` Improve netlist flattener for multi-level hierarchy…
  - `9d5faaf` Isolate SG13G2 changes to preserve sky130/gf180…
  - `1938853` Achieve LVS-clean diff_pair and current_mirror for SG13G2
  - `18f2d3e` WIP: diff_pair LVS investigation for SG13G2
  - `d0a9fac` Reduce lv_cmirror DRC violations for SG13G2 (6 → 3)

These commits are the template: incremental, one-composite-at-a-time,
LVS-clean as the gate.

**Understand the eda-agents glue (read-only):**

- `src/eda_agents/core/glayout_runner.py` — `GLayoutRunner` subprocess
  wrapper. No change expected; just read to understand the JSON spec
  contract.
- `scripts/glayout_driver.py:_generate_opamp` and the `if
  component_lower in ("opamp", ...)` + `if pdk_name != "gf180mcu"`
  guards. Gap 4's fix is here.
- `src/eda_agents/mcp/server.py::generate_analog_layout` — the tool
  surface. Don't change its signature; just let the underlying
  driver handle SG13G2 opamp once gLayout supports it.
- `src/eda_agents/skills/analog.py::_idea_to_topology_prompt` — the
  classifier prompt. Your new `analog.custom_composition` skill
  complements it; the recommender returns `custom` → user can
  explicitly call `explore_custom_topology`.

**Understand the simulation stack (read-only):**

- `src/eda_agents/core/spice_runner.py` — `SpiceRunner` sync/async
  ngspice wrapper. Your analog composition loop uses this.
- `src/eda_agents/core/stages/veriloga_compile.py` +
  `src/eda_agents/veriloga/current_domain/` — Verilog-A primitives
  available for OSDI-based simulation (filter_1st, opamp_1p, ldo_beh).
- `src/eda_agents/core/stages/xspice_compile.py` +
  `src/eda_agents/veriloga/voltage_domain/` — XSPICE primitives
  (ea_comparator_ideal, ea_opamp_ideal, ea_clock_gen, ea_edge_sampler).
- `src/eda_agents/topologies/sar_adc_8bit.py` — example of a
  composite with behavioural and transistor-level variants; template
  for your composition loop outputs.

**Understand the KLayout verification stack:**

- `src/eda_agents/core/klayout_drc.py`
- `src/eda_agents/core/klayout_lvs.py`
- `src/eda_agents/core/magic_pex.py` — for post-layout extraction if
  your loop targets post-layout SPICE correctness.

## Implementation outline

### Gap 4 — step-by-step

1. **Characterise the breakage**: run the existing gf180 opamp_twostage
   tutorial to know what a "working" opamp composite looks like. Then
   try it on SG13G2 in an informal scratch script; note every exception
   / LVS error. That's your backlog.

2. **Port incrementally**: one sub-block at a time (diffpair already
   works; load pair; bias network; CS stage; Miller compensator;
   tapring). Each sub-block LVS-clean before moving on.

3. **KLayout LVS is the gate**: SG13G2 does NOT use Magic/Netgen. The
   workflow is:
   ```python
   from glayout.pdk.sg13g2_mapped import sg13g2_mapped_pdk
   component = opamp_twostage(sg13g2_mapped_pdk, ...)
   component.write_gds("opamp.gds")
   netlist_text = component.info['netlist'].generate_netlist()
   Path("opamp.spice").write_text(netlist_text)
   result = sg13g2_mapped_pdk.lvs_klayout(component, "opamp_twostage", "opamp.spice")
   ```
   The `result` is a dict with pass/fail + detailed report.

4. **Commit cadence**: mirror the existing fork's branch — one
   commit per sub-block LVS-clean milestone. Commit messages in
   English, clear and precise.

5. **Merge path**: push `feature/s12-opamp-sg13g2-integration` to
   the user's fork at `github.com/Mauricio-xx/gLayout`. DO NOT push
   to the OpenFASOC upstream. The user decides whether to open a PR
   upstream.

6. **eda-agents follow-up**: after the gLayout branch is green,
   update `.venv-glayout` (or recreate if needed) so it picks up the
   new opamp code, flip the SG13G2 guard in
   `scripts/glayout_driver.py`, add the MCP / bench test, commit.

### Gap 5 — step-by-step

1. **Pick one NL target** outside the registry. Recommended first
   target: "4-bit current-steering DAC, 1 µA LSB, differential
   output, IHP SG13G2". Simple enough that gLayout primitives
   (nmos + current_mirror arrays) can reach it, but OUTSIDE the
   `recommend_topology` answer set.

2. **Skeleton the loop** (`analog_composition_loop.py`):
   - `propose_composition(nl)` calls OpenRouter via
     `eda_agents.agents.openrouter_client` with a custom system
     prompt (new skill `analog.custom_composition`). Output is JSON
     describing sub-blocks + connectivity + target specs.
   - `size_sub_blocks(composition)` uses existing `GmIdLookup` to
     pick W/L/fingers for each sub-block. Leverage
     `src/eda_agents/skills/analog.py::_gmid_sizing_prompt` as
     context for the sizing sub-call.
   - `generate_layout(composition, sizing)` calls `generate_analog_
     layout` MCP tool repeatedly for each sub-block, then composes
     them via gdsfactory (this is the hardest part — may need a
     helper inside gLayout or a thin placer on top of primitives).
   - `run_spice(netlist)` uses `SpiceRunner`.
   - `run_drc_lvs(gds, netlist)` uses `klayout_drc` + `klayout_lvs`.
   - `critique(result, targets)` uses another LLM call with the
     sim/DRC/LVS output + proposes a patch.

3. **MCP tool**: `explore_custom_topology` is async; under the hood
   it runs the loop to a budget (default 10 iterations). Return
   shape: `{success, gds_path, spice_result, drc_clean, lvs_match,
   iterations_spent, honest_fail_reason}`.

4. **Bench evidence**: `bench/results/s12b_custom_<target>/README.md`
   documents the full loop state for the single target you try.
   Include every iteration's proposal, sim output, critique, and
   the final verdict. This is the scientific artefact of the session
   — don't skimp.

5. **Honest-fail documentation**: if the loop doesn't converge on
   the first target, that's valuable data. Document what *almost*
   worked (best LVS mismatch count, closest SPICE result to the
   target spec, where the critique-proposed-patch went wrong).
   Don't lie the gate green.

## Success criteria

**Gap 4 gate**: SG13G2 opamp_twostage LVS-clean (KLayout LVS unique
match). Evidence in `bench/results/s12b_sg13g2_opamp_layout/`. The
MCP smoke test (`examples/15_mcp_smoke.py`) should now succeed when
calling `generate_analog_layout` with `pdk="ihp_sg13g2"`,
`component="opamp_twostage"`.

**Gap 5 gate (realistic)**: One NL target → LVS-clean GDS + passing
SPICE sim, OR a rigorous honest-fail analysis (which is STILL a
successful outcome for this kind of exploratory arc).

**Suite check**: `pytest` full matrix stays >= 988 green. New
analog-composition tests additive.

**No regressions on S11 analog**: `recommend_topology` still
answers with the same confidence distribution on the S11 test
prompts (biomedical amp → miller_ota@high, delta-sigma → custom@low).

## Risks / known gotchas

- **gLayout sg13g2 LVS requires nSD decorator**: the
  `sg13g2_decorator.py` auto-removes the nSD layer for LVS
  compatibility. If you add new cells to opamp_twostage and forget
  this, LVS will fail. Read the decorator before writing
  anything new on SG13G2.
- **`.venv-glayout` share**: the venv lives at
  `/home/montanares/personal_exp/eda-agents/.venv-glayout` and is
  shared across worktrees. Your upstream gLayout changes take
  effect immediately for ALL worktrees that share that venv —
  both blessing and curse. Communicate if you break it.
- **numpy 1.24 pin in gLayout setup.py**: the install was done with
  `pip install --no-deps -e .` to bypass. If you need `pip install
  -e .` later, set up a fresh venv or relax the pin upstream.
- **Custom-composition MCP tool is async + long-running**: follow
  the `generate_analog_layout` pattern (`asyncio.to_thread` on the
  blocking subprocess). Don't block the MCP event loop.
- **LLM cost in the composition loop**: each iteration is a full
  LLM turn for proposal + critique. At 10 iterations × $0.30-1 per
  call, budget to $10-20 per live run. CAP `max_budget_usd` in the
  tool signature and honour it.
- **Post-layout SPICE is expensive**: if your loop includes PEX +
  post-layout ngspice, each iteration adds minutes. Start with
  pre-layout SPICE only; add PEX once the loop is proven.
- **Honest-fail is a first-class result**: per `feedback_full_
  verification.md` memory — NEVER skip verification stages. If the
  loop's budget exhausts before LVS-clean, report Pass@0 with
  detailed analysis. Do not fabricate a "close enough" verdict.

## Memory + doc references

- `~/.claude/projects/-home-montanares-personal-exp-eda-agents/memory/project_s11_idea_to_chip.md`
- `~/.claude/projects/-home-montanares-personal-exp-eda-agents/memory/feedback_full_verification.md`
- `~/.claude/projects/-home-montanares-personal-exp-eda-agents/memory/feedback_openrouter_model.md`
  — Gemini Flash default (cheapest). Revisit for the loop's
  proposal/critique calls; heavier model may be worth the cost.
- `docs/idea_to_chip_s11.md` — architecture overview with explicit
  S12+ "custom composition arc" section (read the "Next step: use
  `evaluate_topology`..." and "CONFIDENCE LOW" paragraphs).
- `docs/skills/miller_ota/` — markdown skill bundle pattern; your
  `analog.custom_composition` skill may want a similar bundle
  (e.g. `docs/skills/custom_composition/{prompt,primitives,examples}.md`).

## How to start the session

Copy this handoff into the new Claude session's context, then:

> "Take S12-B. Work Gap 4 (gLayout SG13G2 opamp_twostage upstream)
> first — it unblocks Gap 5. Read `docs/s12_handoffs/s12b_analog_
> layout.md` for full context. Commit frequently. Honest-fail on
> the custom-composition loop is acceptable per the S11 memory."
