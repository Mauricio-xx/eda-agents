# eda-agents

Experimental, open-source framework for LLM-assisted analog and
digital design against open PDKs (IHP SG13G2 and GF180MCU), with
SPICE-in-the-loop validation, a skill registry, a benchmark suite,
and a Virtuoso-bridge-shaped orchestrator for the open-source EDA
tool chain. Apache-2.0.

Status: **experimental, with a visible roadmap**. The bench reports
**16/16 PASS (Pass@1 = 100%)** after Session S9-gap-closure
(`feat/s9-gap-closure`). Upstream blockers and deferred follow-ons
are listed under
[Known limitations / roadmap](#known-limitations--roadmap); none of
them are hidden as generic "future work".

## Features

- `CircuitTopology` / `SystemTopology` ABCs with a clean evaluation
  pipeline: `params -> sizing -> netlist -> SpiceRunner -> FoM`.
- Topologies for Miller OTA (IHP + GF180), AnalogAcademy OTA, GF180
  OTA, StrongARM comparator, 7-bit SAR (transistor +
  `SAR7BitTopology`, behavioural + `SAR7BitBehavioralTopology`; old
  "8-bit" names live as deprecation shims), and an 11-bit SAR
  flagged as a `DESIGN_REFERENCE`.
- ngspice integration with async support, measurement parsing, and
  optional OSDI / XSPICE code-model preload.
- gm/ID LUT reader with analytical sizing helpers (`size`,
  `size_from_ft`, `size_from_gmro`, `operating_range`).
- A skill registry (23 skills across analog / digital / flow /
  tools) that callers use through `get_skill(...)` and
  `list_skills(prefix=...)`.
- The Session 6 analog 4-role DAG
  (Librarian / Architect / Designer / Verifier) with pre-simulation
  gates (floating nodes, bulk connections, mirror ratio, bias
  source) and a Pydantic v2 iteration log.
- A benchmark suite (`bench/` + `scripts/run_bench.py`) with
  Pydantic + JSON-schema models, adapter dispatch, and PASS ->
  FAIL_AUDIT downgrade discipline.
- An Apache-2.0 bridge (`bridge/` + `eda-bridge` CLI) with a UUID
  job registry, OpenSSH wrapper, and `xschem` / KLayout operation
  helpers.
- Digital RTL-to-GDS support via LibreLane v3 with ADK and
  Claude Code CLI backends, plus greedy config exploration.

## Install

```bash
pip install -e ".[dev,adc]"           # main venv with bench + ADC metrics
pip install -e ".[agents]"            # + openai (reactive harness)
pip install -e ".[adk]"               # + google-adk + litellm
pip install -e ".[coordination]"      # + context-teleport (optional MCP)
```

The repo vendors upstream LibreLane project templates under
`external/` as git submodules (parity-check fodder, not runtime
inputs):

```bash
git clone --recurse-submodules <this-repo>
# or, after an existing clone:
git submodule update --init --recursive
```

### Requirements

- Python >= 3.11, Pydantic v2 (already pinned).
- `ngspice >= 38`, `openvaf 23.5.0+`, `yosys >= 0.62`, `magic`,
  `klayout`, `netgen`, `openroad` on PATH for the stages that use
  them. A second `.venv-glayout` is expected for the gLayout
  sub-pipeline (see `CLAUDE.md` for layout).
- `PDK_ROOT` pointing at an IHP SG13G2 install (or a GF180MCU
  install, with `EDA_AGENTS_PDK=gf180mcu`). `resolve_pdk_root()`
  validates that the path contains the right model files.

Run `scripts/check_tools.sh` to verify everything the framework
expects is on PATH.

## Quick start

### 1. Bench smoke (no LLM, tool dependencies skipped gracefully)

```bash
PYTHONPATH=src python scripts/run_bench.py --run-id quick_smoke
# 16/16 PASS, Pass@1 = 100% when OPENROUTER_API_KEY is sourced.
# Without the key: 15 PASS + 1 SKIPPED (spec_llm_miller_ota_ihp).
```

Local runs write under `bench/results/<run_id>/` plus a
`bench/results/latest.md` pointer. Only
`bench/results/s9_initial_smoke/` (frozen S9 baseline),
`bench/results/gap_closure_llm_proof/` (LLM live-run evidence), and
`bench/results/gap_closure_parallel/` (workers>1 proof) are tracked
in git — see [`bench/results/README.md`](bench/results/README.md).

### 2. Bridge end-to-end demo (IHP SG13G2)

```bash
PYTHONPATH=src python examples/14_bridge_e2e.py --pdk ihp_sg13g2
# Audit verdict: PASS. Adc = 32.5 dB, GBW = 1.39 MHz.
```

The same demo on GF180MCU works end-to-end since S9-gap-closure
(gap #1) fixed the `miller_ota_gf180_process_params` blocker.

### 3. Analog 4-role DAG dry-run (no tools, no model)

```bash
PYTHONPATH=src python examples/12_analog_roles_demo.py
```

Exercises the full Session 6 DAG with the `DryRunExecutor`. Used as
the "proof the harness is wired right" task in the bench
(`spec_analog_roles_dryrun_dag`).

### Programmatic entry points

```python
from eda_agents.topologies.miller_ota import MillerOTADesigner

designer = MillerOTADesigner()
result = designer.analytical_design(
    gmid_input=12.0, gmid_load=10.0,
    L_input=0.5e-6, L_load=0.5e-6,
    Cc=0.5e-12, Ibias=10e-6,
)
print(result.summary())
```

See `examples/` for the full suite (autoresearch, digital
RTL-to-GDS, post-layout validation, bench-driven sweeps).

## Architecture

`eda_agents` has six top-level packages: `core/`, `topologies/`,
`agents/`, `skills/`, `bench/`, and `bridge/`. Read
[`docs/architecture.md`](docs/architecture.md) for the walkthrough,
the ASCII layering diagram, and pointers to the relevant abstractions
(`CircuitTopology`, `PdkConfig`, `Skill`, `HARNESS_DISPATCH`,
`JobRegistry`).

## Skills

The `eda_agents.skills` registry ships 23 skills, grouped by prefix:

- `analog.*`: `explorer`, `corner_validator`, `orchestrator`,
  `adc_metrics`, `behavioral_primitives`, `gmid_sizing`,
  `sar_adc_design`, plus the four DAG roles
  (`analog.roles.librarian`, `analog.roles.architect`,
  `analog.roles.designer`, `analog.roles.verifier`).
- `digital.*`: `project_manager`, `verification`, `synthesis`,
  `physical`, `signoff`.
- `flow.*`: `runner`, `drc_checker`, `drc_fixer`, `lvs_checker`.
- `tools.*`: legacy tool specs kept for backward compatibility
  (`evaluate_miller_ota`, `simulate_miller_ota`, `gmid_lookup`).
  New topologies should expose `topology.tool_spec()` instead.

```python
from eda_agents.skills import list_skills, get_skill

for s in list_skills(prefix="analog."):
    print(s.name, "-", s.description.splitlines()[0])

skill = get_skill("analog.gmid_sizing")
prompt = skill.prompt_template()
```

## Bench

The benchmark is built around Pydantic v2 frozen models mirrored
against `bench/schemas/{task,result}.json`. Key design decisions:

- **Audit discipline**: `execute_task` downgrades adapter PASS to
  `FAIL_AUDIT` whenever any scoring criterion fails. The bench never
  paints over a failing threshold.
- **Restricted callables**: `inputs.callable` dotted paths resolve
  only inside `eda_agents.bench.adapters`, so task YAMLs cannot
  execute arbitrary code.
- **Skip, don't fake**: the GL sim adapter returns
  `BenchStatus.FAIL_INFRA` (mapped to `SKIPPED`) when no hardened
  LibreLane run is available, rather than inventing a PASS.
- **Session 9 smoke**: 9/11 PASS, Pass@1 = 90% *excluding the one
  deliberate FAIL_SIM* documented in
  [`docs/upstream_issues/miller_ota_gf180_process_params.md`](docs/upstream_issues/miller_ota_gf180_process_params.md).
  The report is frozen at
  [`bench/results/s9_initial_smoke/report.md`](bench/results/s9_initial_smoke/report.md).

Seed tasks live in `bench/tasks/{spec-to-topology,bugfix,tb-generation,end-to-end}/`.
Write new ones as YAML:

```yaml
id: my_new_task
family: spec-to-topology
category: pipeline
domain: voltage
pdk: ihp_sg13g2
difficulty: easy
expected_backend: ngspice-osdi
harness: callable
inputs:
  callable: eda_agents.bench.adapters:analytical_miller_design
  design_params:
    gmid_input: 12.0
    gmid_load: 10.0
    L_input: 1.0e-6
    L_load: 1.0e-6
    Cc: 1.0e-12
scoring: [compile, sim_run, metrics_in_range]
expected_metrics:
  Adc_dB: {min: 25.0}
  GBW_Hz: {min: 5.0e5}
```

## Bridge

```python
from eda_agents.bridge import JobRegistry, KLayoutOps

registry = JobRegistry()          # ~/.cache/eda_agents/jobs/
job = registry.submit(fn, args, kwargs)
registry.wait(job.id, timeout_s=60)

ops = KLayoutOps()                # delegates to core/klayout_*.py
drc = ops.run_drc(gds_path, pdk="gf180mcu")
```

The CLI surface is `eda-bridge init / status / jobs / cancel /
stop / start xschem-netlist`. See `src/eda_agents/bridge/cli.py`
for the argparse definitions and `examples/14_bridge_e2e.py` for
an end-to-end scenario.

## Known limitations / roadmap

All 11 S9 in-tree gaps were closed in session S9-gap-closure on
branch `feat/s9-gap-closure` (11 commits, see `CHANGELOG.md` for
the full mapping). Bench summary: **16/16 PASS, Pass@1 = 100%**
when `OPENROUTER_API_KEY` is sourced; the LLM task SKIPS cleanly
without it. Remaining open items below are upstream blockers (not
our bugs) and deferred follow-ons, not bench regressions.

### Upstream blockers (not our bugs, but they gate us)

- **IHP Magic hangs** on `StreamOut / WriteLEF / SpiceExtraction /
  DRC`. See
  [`docs/upstream_issues/ihp_magic_hang.md`](docs/upstream_issues/ihp_magic_hang.md).
  Effect: post-layout validation on IHP runs KLayout-only signoff;
  Magic PEX is unavailable. The bench routes digital RTL-to-GDS at
  GF180MCU-D precisely to avoid this blocker.
- **IHP KLayout LVS deck** is incomplete; `RUN_LVS: false` is set
  on the IHP flow until upstream lands a working deck. See
  [`docs/upstream_issues/ihp_klayout_lvs_deck.md`](docs/upstream_issues/ihp_klayout_lvs_deck.md).

### Deferred follow-ons (post-gap-closure, each its own session)

- **SAR calibration items 2-5** — `tau_regen` measurement, LDO wiring,
  real bootstrap switch, corner sweep. Tracked in
  [`docs/skills/sar_adc/TODO_calibration.md`](docs/skills/sar_adc/TODO_calibration.md)
  (item 1, the spec anchor, is RESOLVED).
- **Deprecation shim removal** — the `sar_adc_8bit` /
  `sar_adc_8bit_behavioral` modules are thin re-exports of the
  canonical 7-bit names. They stay until external callers migrate.

### Beyond gap-closure: exploratory capabilities

These are **not** gaps (the framework works without them) and they are
**not** in scope for `feat/s9-gap-closure`. They are explicitly
deferred until after the bench is hardened, to avoid dispersing effort.

- **MCP server for eda-agents** — a semantic Model Context Protocol
  server exposing the skill registry, bench runner, bridge
  `JobRegistry`, topology evaluation, and gm/ID sizing to any MCP
  client (Claude Code, Cursor, Gemini CLI, Zed, etc.) without
  requiring the user to write Python. Reference pattern:
  [`luarss/openroad-mcp`](https://github.com/luarss/openroad-mcp)
  (BSD-3, same org that ships ORFS). **Not a shell wrapper** — our
  MCP would expose `evaluate_topology`, `run_bench`,
  `submit_bridge_job`, `render_skill`, etc. as first-class tools,
  keeping the `CircuitTopology` / `PdkConfig` abstractions the repo
  is built around. Scheduled after `feat/s9-gap-closure` closes. A
  short design spike (map each tool candidate to its MCP signature
  + prototype one tool) precedes any implementation session.

### Pre-existing failures (pre-S0, left untouched)

Two tests under `tests/test_handler.py`
(`test_prefilter_bad_design`, `test_export_results`) fail against
upstream `main` and are knowingly ignored. Do not try to "fix" them
on this branch.

## Coordination (optional)

When installed alongside
[Context Teleport](https://github.com/Mauricio-xx/context-teleport)
(`pip install eda-agents[coordination]`), agents can use multi-agent
coordination strategies via MCP. Without it, the strategies degrade
gracefully to independent exploration.

## License

Apache-2.0 (see `LICENSE`). This repo's design is the result of the
multi-session Arcadia-1 integration plan
(`~/.claude/plans/concurrent-beaming-bear.md`). Six of the eight
upstream Arcadia-1 repositories reviewed during the deep-dive have no
LICENSE file; following `docs/license_status.md`, everything taken
from them is reimplemented here rather than copied verbatim. The one
repository with a compatible permissive license that we depend on at
runtime (`adctoolbox`, MIT) is pulled as a PyPI dependency.
