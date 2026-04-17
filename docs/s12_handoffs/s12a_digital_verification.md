# S12-A handoff — Digital verification scaling (IdeaToRTLLoop + cocotb GL sim)

You are picking up **two S12+ gaps** documented during the S11 idea-to-chip
arc. Both touch the digital verification infrastructure, so they share
code ownership and belong in one session.

## Inherited context (everything this session needs to know)

S11 shipped an NL-idea → signoff-clean-GDS pipeline for digital designs
on GF180MCU-D. Four live probes passed @Pass@1=100%:

| design       | cells  | time   | $    | evidence |
|--------------|--------|--------|------|----------|
| counter4     |   233  |  164 s | 0.61 | `bench/results/s11_fase0_live/` |
| ALU 8-bit    |   596  |  763 s | 3.14 | `bench/results/s11_fase1_alu_live/` |
| accum_cpu    |  1865  |  539 s | 1.30 | `bench/results/s11_fase2_cpu_live/` |
| FFT 4-point  |  ~600  |  409 s | 2.10 | `bench/results/s11_fase2_fft_live_retry/` |
| counter4_cocotb | 233 | 232 s | 0.92 | `bench/results/s11_cocotb_final/` |

The accum_cpu run used Claude's internal retry (3 LibreLane iterations
within a single CC CLI turn) — that's the **built-in retry ceiling the
prompt allows**. Designs that cannot close in 3 retries need a **higher-
level loop with sim + lint feedback between agent turns**.

S11 also shipped a cocotb path (`tb_framework="cocotb"`) that skips
post-synth and post-PnR gate-level simulation — `GlSimRunner` is
iverilog-only today. The skip is surfaced honestly as
`gl_sim_skipped=1.0` in adapter metrics.

Merge commit: `2d116c8`. Main tip references this session as S11.

## What this session must deliver

### Gap 1 — `IdeaToRTLLoop` for designs >10k cells

New iterative orchestrator that sits **above** `generate_rtl_draft`:

```
idea → generate_rtl_draft (turn 1) → sim + synth-lint → critique →
        propose_rtl_patch (turn 2) → sim + synth-lint → critique →
        ... (budget = N turns, default 8-20) →
        signoff-clean GDS OR honest-fail after budget exhausted
```

Target design (gate): 8-bit pipelined multiplier-accumulator OR FFT-8.
Acceptance: Pass@3 on one medium target, >= 10k post-synth cells. Honest
failure (Pass@0 with budget exhausted) is OK if documented with
root-cause analysis — the whole point is to find the ceiling of this
pattern.

### Gap 2 — `GlSimRunner` cocotb backend

Generalise `GlSimRunner` so the same cocotb testbench that ran
pre-synth can also run against the post-synth and post-PnR netlists.
Today: `GlSimRunner` hand-compiles `iverilog -o sim.out <stdcells> <nl.v>
<tb.v>`. For cocotb: substitute `VERILOG_SOURCES` in the cocotb
Makefile and invoke `make sim` with `SIM=icarus`.

Acceptance: `bench/results/s11_cocotb_final/` re-run must emit
`gl_post_synth_ok=1` and `gl_post_pnr_ok=1` (not `gl_sim_skipped=1`).
Update `idea_to_digital_counter_cocotb_live.yaml` back to require the
stricter metrics.

## Environment setup

```bash
# Worktree from current main tip (2d116c8).
cd /home/montanares/personal_exp/eda-agents
git worktree add -b feat/s12a-digital-verification \
    /home/montanares/git/eda-agents-worktrees/s12a-digital-verification main

cd /home/montanares/git/eda-agents-worktrees/s12a-digital-verification
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,agents,mcp,adk]"
# Fix python-dotenv conflict after adk install:
.venv/bin/pip install -U "python-dotenv>=1.1.0"

# Source OpenRouter key for any LLM tests.
source /home/montanares/personal_exp/eda-agents/.env

# Live runs need these exactly:
export PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu
export EDA_AGENTS_ALLOW_DANGEROUS=1
```

## Files to read before writing any code

**Understand the pipeline first (read-only):**

- `docs/idea_to_chip_s11.md` — the canonical overview of what S11
  built. Sections "Pipeline", "Cocotb testbench framework", "Analog
  side: Fase 4 layout dispatch" are the most relevant.
- `src/eda_agents/agents/idea_to_rtl.py` — the single-source library
  `generate_rtl_draft` you will wrap. Pay attention to
  `IdeaToRTLResult.all_passed` semantics (lines ~60-80) and
  `run_post_flow_gl_sim_check` (lines ~260-370).
- `src/eda_agents/agents/tool_defs.py:1120-1370` —
  `build_from_spec_prompt`. The tb_framework branch at ~1200 is how
  cocotb is opted into. Your loop will replace / extend Phase 6
  (FIX AND ITERATE) with its own proposer.
- `src/eda_agents/agents/claude_code_harness.py` — the async CLI
  wrapper (not cocotb-aware; invoke as-is).
- `src/eda_agents/core/stages/rtl_sim_runner.py` — existing
  `CocotbDriver` (pre-synth) and `IVerilogDriver`. Model for your
  gate-level cocotb driver.
- `src/eda_agents/core/stages/gl_sim_runner.py` — THE file you will
  extend for gap 2. Understand the post_synth + post_pnr branches
  and how SDF annotation is wired.
- `src/eda_agents/skills/digital.py` — registration pattern for
  `digital.cocotb_testbench`. Your critique skills follow the same
  shape.
- `src/eda_agents/bench/adapters.py::run_idea_to_digital_chip` —
  how the loop will plug into the bench. ~25-line function; small.

**S11 artefacts to grep for field examples:**

- `bench/results/s11_fase2_cpu_live/e2e_idea_to_digital_accum_cpu_gf180_live/idea_to_chip_result.json`
  — shape of a successful result; your loop produces the same.
- `bench/results/s11_cocotb_final/e2e_idea_to_digital_counter_cocotb_live/idea_to_chip_result.json`
  — shape with `gl_sim.skipped=True`. Your cocotb GL sim work
  MUST flip this to `skipped=False + all_passed=True`.

## Implementation outline

### Gap 1 — IdeaToRTLLoop

**New module**: `src/eda_agents/agents/idea_to_rtl_loop.py`

Skeleton (design by contract; fill in internals):

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eda_agents.agents.idea_to_rtl import IdeaToRTLResult, generate_rtl_draft

@dataclass
class LoopIteration:
    turn: int
    sim_status: str              # "pass" | "fail" | "skipped"
    lint_status: str             # "pass" | "fail"
    lint_errors: list[str] = field(default_factory=list)
    sim_failures: list[str] = field(default_factory=list)
    rtl_diff_chars: int = 0
    cost_usd: float = 0.0

@dataclass
class IdeaToRTLLoopResult:
    idea_result: IdeaToRTLResult   # the final generate_rtl_draft output
    iterations: list[LoopIteration]
    budget_exhausted: bool
    total_cost_usd: float
    converged_turn: int | None     # first turn where sim + lint both passed

async def run_idea_to_rtl_loop(
    description: str,
    design_name: str,
    work_dir: Path,
    *,
    max_turns: int = 8,
    pdk: str = "gf180mcu",
    pdk_root: str | None = None,
    allow_dangerous: bool = False,
    model: str | None = None,
) -> IdeaToRTLLoopResult:
    ...
```

**New skills**: register in `src/eda_agents/skills/digital.py`:

- `digital.critique_sim_failure` — zero-arg prompt that tells the LLM
  how to read an iverilog / cocotb failure log and propose a minimal
  RTL patch. Include: "do not rewrite the whole module", "preserve
  the port list", "touch only the lines relevant to the failing
  assertion".
- `digital.critique_synth_lint` — zero-arg prompt for yosys synth
  errors (undriven signals, width mismatches, combinational loops).
  Same conciseness bias.

**Integration**: `generate_rtl_draft` gains an optional
`loop_budget: int = 1` kwarg. `loop_budget=1` keeps today's single-
shot behaviour (default). `loop_budget>1` dispatches to
`IdeaToRTLLoop`. This keeps all S11 evidence reproducible.

**MCP tool**: extend `generate_rtl_draft` in
`src/eda_agents/mcp/server.py` with the same `loop_budget` arg.

**Bench adapter**: extend `IdeaToDigitalChipInputs` in
`src/eda_agents/bench/adapter_inputs.py` with `loop_budget`. Thread
through `run_idea_to_digital_chip`.

**Bench YAMLs** under `bench/tasks/end-to-end/`:
- `idea_to_digital_medium_target_gf180.yaml` (dry, loop_budget=8)
- `idea_to_digital_medium_target_gf180_live.yaml` (live, budget=$20,
  timeout 2h). Design: 8-bit pipelined MAC or FFT-8 — pick one, DO
  NOT attempt both in the first probe. Acceptance = Pass@3 or
  honest-fail.

**Tests**:
- Mock harness pattern already exists: see
  `tests/test_idea_to_rtl.py::TestGenerateRtlDraftLivePaths`. Copy
  that pattern for loop behaviour (budget exhausted, early-success,
  critique feedback propagation).

### Gap 2 — GlSimRunner cocotb backend

**Generalize** `src/eda_agents/core/stages/gl_sim_runner.py`:

Today's `run_post_synth()` and `run_post_pnr()` invoke `iverilog -o sim.out ... && vvp sim.out`. For cocotb:

1. Detect tb flavour: if `tb/Makefile` + `tb/test_<design>.py` exist,
   route to cocotb path. If `tb/tb_<design>.v` exists, iverilog path.
2. Cocotb path: set `VERILOG_SOURCES` env var for the `make sim`
   subprocess to point at the gate-level netlist + stdcell verilog
   + primitives. Invoke `make sim` with cwd=tb/.
3. Parse cocotb's `** TESTS=N PASS=N FAIL=N` summary line (existing
   regex in `rtl_sim_runner.py::_COCOTB_SUMMARY_RE` at line ~31).
4. For post-PnR: set `COCOTB_MODULE_ARGS` or `SDF_FILE` env var to
   point at `<run>/final/sdf/<corner>/<design>.sdf`. cocotb supports
   `$sdf_annotate` via plusargs.

**Update `run_post_flow_gl_sim_check`** in
`src/eda_agents/agents/idea_to_rtl.py`:

- Remove the cocotb-specific early-return added in S11 Fase 1.5 fix
  (lines ~320-340). Instead, detect TB flavour and dispatch to the
  right GlSimRunner method.
- Adapter `run_idea_to_digital_chip` simplifies: always emit
  `gl_post_synth_ok` + `gl_post_pnr_ok`. The `gl_sim_skipped` metric
  becomes a true-skip flag (e.g. user set `skip_gl_sim=True`), not a
  cocotb workaround.

**Bench YAML update** (after Gap 2 lands):
- `bench/tasks/end-to-end/idea_to_digital_counter_cocotb_live.yaml`
  `expected_metrics`: restore `gl_post_synth_ok: {min: 1}` +
  `gl_post_pnr_ok: {min: 1}`. Remove `gl_sim_skipped`.

**Existing regression guard to update**:
- `tests/test_idea_to_rtl.py::TestGlSimHelperErrors::test_cocotb_testbench_skips_cleanly`
  — this test currently pins the skip behaviour. Flip its intent
  to "cocotb TB runs through GL sim and passes" once the wiring is
  in place.

## Success criteria

**Gap 1 gate**: `bench/results/s12a_medium_live/` emits either:
- Pass@3 on one of:
  - `idea_to_digital_mac_pipelined_gf180_live.yaml`
  - `idea_to_digital_fft8_gf180_live.yaml`
- OR honest-fail with `bench/results/s12a_medium_live/README.md`
  documenting root cause (budget, specific RTL failure modes, cost
  per iteration).

**Gap 2 gate**: `bench/results/s12a_cocotb_gl_sim_live/` re-runs
`idea_to_digital_counter_cocotb_live.yaml` (with updated
`expected_metrics`) and emits Pass@1=100% with real `gl_post_synth_ok=1`
+ `gl_post_pnr_ok=1`.

**Suite check**: `pytest` full matrix must stay >= 988 green (S11
baseline). New tests additive.

**No regressions on S11 iverilog evidence**: re-run of
`e2e_idea_to_digital_counter_live` / `_alu8_gf180_live` /
`_accum_cpu_gf180_live` must still Pass@1.

## Risks / known gotchas

- **Rate limit** (Claude CLI subscription): the session plan's FFT
  first-attempt failed at 429 mid-run on the night of 2026-04-16. If
  you run more than 4 live probes in < 4 h, subsequent ones may
  rate-limit. Spread out or raise the subscription tier.
- **Tail-F prompt bug** (fixed in S11 but worth re-checking): the
  `build_from_spec_prompt` now includes a LOG INSPECTION DISCIPLINE
  section. If you modify Phase 4/6 for the loop, DO NOT remove that
  warning.
- **cocotb `ReadOnly` footgun**: S11 cocotb skill now documents this
  in a dedicated READONLY IS READ-ONLY section. The loop's critique
  skills should reinforce it, not duplicate it.
- **SDF corner name must match**: `PdkConfig.default_sta_corner`.
  GF180 = `nom_tt_025C_5v00`. IHP = `nom_typ_1p20V_25C`. Hardcoding
  anywhere else in new code is a regression waiting to happen.
- **LibreLane Nix PATH**: `detect_nix_eda_tool_dirs()` in
  `src/eda_agents/agents/digital_autoresearch.py` finds yosys /
  openroad / magic / netgen / klayout and prepends to PATH. Any new
  subprocess you spawn that touches EDA tools must include this
  prefix.
- **cocotb for GL sim** — you'll need to figure out the SDF
  annotation mechanism. Start with iverilog + cocotb's
  `COCOTB_PLUSARGS` env var; fall back to a generated Makefile
  fragment that injects `$sdf_annotate`.
- **cocotb in LibreLane venv**: S11 installed cocotb 2.0.1 into
  `/home/montanares/git/librelane/.venv` to unblock the wiring
  probe. Keep it there. Don't install into the worktree venv — that
  breaks the "prompt instructs agent to use librelane_python" flow.

## Memory + doc references

- `~/.claude/projects/-home-montanares-personal-exp-eda-agents/memory/project_s11_idea_to_chip.md`
  — full S11 entry.
- `~/.claude/projects/-home-montanares-personal-exp-eda-agents/memory/MEMORY.md`
  — index; your arc becomes a new line under `S12a`.
- `~/.claude/projects/-home-montanares-personal-exp-eda-agents/memory/feedback_full_verification.md`
  — the no-skip-verification rule. Your critique skills MUST NOT
  teach the agent to skip sim/DRC/LVS.
- `docs/idea_to_chip_s11.md` — architecture overview.
- `docs/mcp_spike_design.md` — MCP server arc (pre-S11).
- `bench/results/s11_*/README.md` — per-run evidence with honest
  failure-mode diagnostics baked into READMEs (e.g. the three-attempt
  cocotb diagnostic path in `s11_cocotb_final/README.md`). Match
  this discipline when documenting S12a evidence.

## Suggested session log entry

Append to `SESSION_LOG.md` (gitignored) in the new worktree:

```
# S12-A — Digital verification scaling

Branch: feat/s12a-digital-verification (off main @ 2d116c8).
Scope: IdeaToRTLLoop (gap 1) + GlSimRunner cocotb backend (gap 2).

...
```

## How to start the session

Copy this file into the new Claude session's initial prompt context,
plus a one-line ask:

> "Take S12-A and run it to GATE GREEN or honest-fail. Start by
> reading `docs/s12_handoffs/s12a_digital_verification.md` for
> context. Use the todo tracker to break the scope into tasks.
> Commit early, commit often per the global CLAUDE.md rules."
