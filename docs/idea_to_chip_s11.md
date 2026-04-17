# S11 — Idea-to-Chip for Digital

Status: **Fase 0 closed** (2026-04-16). Pass@1 = 100% on
`e2e_idea_to_digital_counter_live` (GF180MCU-D). Fase 1 (ALU / 8-bit
CPU) and Fase 3-4 (analog via gLayout) are follow-on work tracked in
`SESSION_LOG.md` and the session plan
`~/.claude/plans/quiero-que-evaluemos-lo-inherited-liskov.md`.

## What this delivers

A reusable pipeline that takes a **natural-language description** of a
digital block and produces a **signoff-clean GDS** — no hand-written
RTL, testbench, or LibreLane config required. Three call sites share
one implementation:

1. **Library** — `eda_agents.agents.idea_to_rtl.generate_rtl_draft`.
2. **MCP tool** — `generate_rtl_draft` (registered in the eda-agents
   FastMCP server). Dry-run by default; clients must opt into the
   real run.
3. **Bench adapter** — `eda_agents.bench.adapters:run_idea_to_digital_chip`
   driven by `bench/tasks/end-to-end/idea_to_digital_*`.

`examples/09_rtl2gds_digital.py --spec …` (Mode 3) also goes through
the library — it is no longer a standalone code path.

## Pipeline

```
   NL description
       |
       v                            (eda_agents.agents.idea_to_rtl)
  generate_rtl_draft()
       |
       |---- build_from_spec_prompt(spec, pdk, ...)       # prompt builder
       |
       v
  ClaudeCodeHarness.run()                                 # agent writes
       |  (claude --print --output-format json)           # RTL + testbench
       |                                                  # + LibreLane
       v                                                  # config, then
  LibreLane Classic flow                                  # runs the flow
       |  (Magic/KLayout/Netgen signoff)                  # itself
       v
  run_post_flow_gl_sim_check()                            # GlSimRunner
       |                                                  # x2 (post-synth
       |  post-synth iverilog + vvp                       # and post-PnR
       |  post-PnR iverilog + vvp + SDF                   # with SDF
       v
   IdeaToRTLResult
     (all_passed = success AND gl_sim.all_passed)
```

## Entry points

### Library

```python
from eda_agents.agents.idea_to_rtl import generate_rtl_draft, result_to_dict

result = await generate_rtl_draft(
    description="4-bit sync up-counter with enable and async-low reset",
    design_name="counter4",
    work_dir="/tmp/counter4_run",
    pdk="gf180mcu",
    pdk_root="/home/montanares/git/wafer-space-gf180mcu",
    librelane_python="/home/montanares/git/librelane/.venv/bin/python",
    allow_dangerous=True,    # with EDA_AGENTS_ALLOW_DANGEROUS=1 in env
    max_budget_usd=8.0,
)

assert result.all_passed                          # gate
print(result_to_dict(result))                     # JSON for logging
```

Pass `dry_run=True` to only build the prompt (fast sanity-check).

### MCP tool

```json
{
  "name": "generate_rtl_draft",
  "description": "Run the NL idea -> digital GDS pipeline (S11 Fase 0). ...",
  "inputSchema": { ... }
}
```

From a Claude Code / Cursor / Zed client configured against the
`eda-agents` MCP server:

```
> Call generate_rtl_draft with description="UART transmitter 9600 baud"
  design_name="uart_tx" pdk="gf180mcu" dry_run=true
```

Dry-run returns immediately with the prompt length + PDK-root
resolution status. `dry_run=false` blocks for minutes while the agent
runs the full flow.

### Bench

Two YAML variants per design:

- `idea_to_digital_counter.yaml` (dry, CI-safe gate) — runs offline,
  verifies prompt + plumbing.
- `idea_to_digital_counter_live.yaml` (live) — runs the full pipeline.
  Requires Claude CLI + PDK + LibreLane; short-circuits to SKIPPED on
  missing deps.

Invocation:

```bash
# Dry gate (fast, CI-safe).
python scripts/run_bench.py --task e2e_idea_to_digital_counter

# Live gate (slow, needs CC CLI / PDK / LibreLane).
PDK_ROOT=/home/montanares/git/wafer-space-gf180mcu \
EDA_AGENTS_ALLOW_DANGEROUS=1 \
python scripts/run_bench.py --task e2e_idea_to_digital_counter_live
```

Evidence of the first live pass is committed under
`bench/results/s11_fase0_live/`.

## Environment prerequisites (for live runs)

- **Claude Code CLI** on PATH. `npm install -g @anthropic-ai/claude-code`.
- **EDA_AGENTS_ALLOW_DANGEROUS=1** in the env if the YAML sets
  `allow_dangerous: true`. Without it the CLI subprocess hangs on the
  first permission prompt — the adapter pre-checks this and returns
  FAIL_INFRA.
- **LibreLane venv** (v3.0.0rc0+) with Python 3.11+. The prompt
  prepends Nix EDA tool dirs (`/nix/store/…-yosys-…/bin`, etc.) to
  PATH so the child process gets the right toolchain.
- **PDK** reachable via `PDK_ROOT` or its registered `default_pdk_root`.
  For GF180MCU-D the bench looks for the wafer-space fork at
  `/home/montanares/git/wafer-space-gf180mcu`. For IHP SG13G2 it looks
  for `/home/montanares/git/IHP-Open-PDK`.

## Audit signals (what "PASS" actually proves)

The bench `run_idea_to_digital_chip` adapter emits these metrics in
live mode; the YAML `expected_metrics` block asserts against them:

| metric | semantics |
|---|---|
| `gds_exists` | agent's run produced `runs/<tag>/final/gds/<design>.gds` |
| `gl_post_synth_ok` | post-synth iverilog sim against the agent's testbench passed (netlist from `06-yosys-synthesis/<design>.nl.v`) |
| `gl_post_pnr_ok` | post-PnR iverilog sim with SDF annotation passed (netlist from `final/nl/<design>.nl.v`, SDF from `final/sdf/<corner>`) |

Dry mode emits only `prompt_length`, which confirms the PDK template
routing works end-to-end.

A real PASS therefore means:

- The agent wrote synthesisable RTL that LibreLane took through signoff.
- The agent also wrote a usable testbench.
- That same testbench exercises the hardened netlist (post-synth AND
  post-PnR) without failing, which catches classes of bugs the synth
  step alone can miss (X-prop on startup, SDF timing violations at
  gate-level).

## What's deliberately out of scope in Fase 0

- **Iterative loop with sim-in-the-loop feedback.** `complexity="simple"`
  is single-shot. The `"medium"` / `"complex"` labels are accepted as
  forward-compatible hooks but no loop is wired yet — Fase 1 owns that.
- **Skills (`digital.idea_to_rtl`, `digital.idea_to_testbench`).** The
  monolithic `build_from_spec_prompt` already carries the author-one-shot
  guidance. Modular skills become necessary when the loop needs
  per-iteration critique prompts.
- **Analog.** Handled via gLayout in Fase 3-4 (separate worktree work;
  gLayout changes live in `/home/montanares/personal_exp/gLayout`, not
  here).
- **CPU / FFT-class designs.** Fase 2 target; acceptance is Pass@3 not
  Pass@1 because single-shot is not guaranteed above ~10 k gates.

## Files to know

- `src/eda_agents/agents/idea_to_rtl.py` — library implementation,
  single source of truth for the pipeline.
- `src/eda_agents/agents/tool_defs.py::build_from_spec_prompt` —
  prompt template + LibreLane template selection (pre-existing, used
  as-is).
- `src/eda_agents/agents/claude_code_harness.py` — async Claude CLI
  wrapper (pre-existing).
- `src/eda_agents/mcp/server.py::generate_rtl_draft` — MCP tool wrapper.
- `src/eda_agents/bench/adapters.py::run_idea_to_digital_chip` — bench
  adapter; uses typed `IdeaToDigitalChipInputs`.
- `tests/test_idea_to_rtl.py` — 36 unit tests covering library + MCP
  tool + adapter.

## Analog side: Fase 3 topology recommender

Shipped alongside the digital pipeline, deliberately smaller in scope
because analog topology synthesis is not a solved problem. What
`recommend_topology` does: **map a natural-language idea to one of the
registered topologies** (miller_ota, aa_ota, gf180_ota, strongarm_comp,
sar_adc_{7,8,11}bit), or say "custom" with low confidence when nothing
fits. It does not size, does not simulate, does not layout.

Skill: `analog.idea_to_topology` (zero-arg, renders a classifier prompt).
MCP tool: `recommend_topology(description, constraints, model, dry_run)`.

Example (live, needs `OPENROUTER_API_KEY`):

```python
from eda_agents.mcp.server import mcp

result = await mcp.call_tool("recommend_topology", {
    "description": "clocked comparator for a SAR ADC, 10 mV input, 1 mV offset tolerance",
    "constraints": {"td_max": 1e-9, "sigma_Vos_max": 1e-3},
})
# -> {'success': True, 'topology': 'strongarm_comp', 'confidence': 'high',
#     'starter_specs': {'td_max': 1, 'sigma_Vos_max': 0.001}, 'valid_topology': True, ...}
```

When `confidence == "low"` or `topology == "custom"`, the downstream
caller should NOT commit to a sized design — instead that's the handoff
point for the future custom-composition arc (Claude-Code-driven loop
over gLayout primitives + ngspice, S12+).

Tests: `tests/test_recommend_topology.py` — 10 cases with OpenRouter
mocked. Manual live smoke-tests in the repo root README are the
high-trust validation; mocks verify schema + error paths.

## Cocotb testbench framework (opt-in Phase 2.5 swap)

`digital.cocotb_testbench` is a zero-arg skill that teaches the
agent to write a gate-level-safe cocotb testbench + cocotb-config
Makefile. Same TB runs against RTL, post-synth, and post-PnR
(SDF-annotated) netlists; the latter two are driven by the existing
`GlSimRunner`.

Opt in from any entrypoint via the `tb_framework="cocotb"` knob
(default is `"iverilog"` to preserve the legacy plain-Verilog TB):

```python
# Library
await generate_rtl_draft(..., tb_framework="cocotb")

# MCP tool
await mcp.call_tool("generate_rtl_draft", {..., "tb_framework": "cocotb"})

# Bench YAML
inputs:
  tb_framework: cocotb   # in addition to the usual keys
```

When the flag is on, `build_from_spec_prompt` inlines the skill body
inside Phase 2.5 and the agent writes `tb/test_<design>.py` +
`tb/Makefile` instead of `tb/tb_<design>.v`. The runner still
locates the DUT through the `tb.dut` hierarchical path — that
convention is preserved by cocotb's default test-module-is-top
behaviour.

When the flag is off (default), Phase 2.5 keeps emitting a plain
Verilog TB as before; all S11 Fase 0/1/2 evidence was produced with
the default, so nothing that was green stops being green.

Callers MAY also get the skill body stand-alone via
`render_skill("digital.cocotb_testbench")` and concatenate it into
a custom prompt outside the from-spec path.

## Analog side: Fase 4 layout dispatch

`scripts/glayout_driver.py` was generalised to route `spec['pdk']` to
either `gf180_mapped_pdk` or `sg13g2_mapped_pdk` (and the
Sky130 alias, unused today). It now dispatches six components:

- **Primitives**: `nmos`, `pmos`, `mimcap`.
- **Composites**: `diff_pair`, `current_mirror`, `fvf` (flipped
  voltage follower). All LVS-clean on SG13G2 per the upstream
  `feature/sg13g2-pdk-support` fork.

`opamp_twostage` stays GF180-only until the SG13G2 upstream port
lands. The driver fails fast with a clear message rather than
crashing mid-build. The `GLayoutRunner` also now parses the
driver's stdout JSON before checking exit code, so structured errors
("PDK not importable", "opamp_twostage is gf180mcu-only") survive
to the caller.

New MCP tool `generate_analog_layout(pdk, component, params,
output_dir, glayout_venv, timeout_s)` wraps all of this in an async
subprocess call:

```python
result = await mcp.call_tool("generate_analog_layout", {
    "pdk": "ihp_sg13g2",
    "component": "diff_pair",
    "params": {"width": 5.0, "length": 1.0, "fingers": 4},
    "output_dir": "/tmp/my_dpair",
    "glayout_venv": "/home/.../.venv-glayout",
})
# -> {success, gds_path, netlist_path, top_cell, run_time_s, error}
```

Live evidence: tests/test_glayout_runner.py::TestGenerateAnalogLayoutMCP
exercises the tool end-to-end against real SG13G2, producing a GDS
in ~7 seconds.

Setup prerequisite: the `.venv-glayout` must have the gLayout fork
installed (see `setup_glayout_venv.md` or run
`/path/to/.venv-glayout/bin/pip install --no-deps -e
/home/montanares/personal_exp/gLayout`). The stock gLayout 0.1.1 on
PyPI lacks SG13G2.

## See also

- `bench/results/s11_fase0_live/README.md` — first-pass evidence.
- `SESSION_LOG.md` — session plan and follow-on arcs.
- `docs/mcp_spike_design.md` — MCP server architecture that hosts the
  `generate_rtl_draft` tool.
