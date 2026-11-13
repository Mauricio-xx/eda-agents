# eda-agents

[![CI](https://github.com/Mauricio-xx/eda-agents/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Mauricio-xx/eda-agents/actions/workflows/ci.yml)
[![bench](https://github.com/Mauricio-xx/eda-agents/actions/workflows/bench.yml/badge.svg?branch=main)](https://github.com/Mauricio-xx/eda-agents/actions/workflows/bench.yml)

Experimental, open-source framework for LLM-assisted analog and digital design against open PDKs (IHP SG13G2 and GF180MCU). It combines SPICE-in-the-loop evaluation, a skill registry, an MCP server, several LLM backends, a benchmark suite and a bridge to the open-source EDA tool chain. Apache-2.0.

Status: **experimental**. The bench has 36 tasks covering analog sizing, a SAR ADC, digital RTL-to-GDS on both PDKs and natural-language-to-chip runs. Tasks whose tool chain, PDK or credentials are missing are reported as skipped, never counted as passes. What does not work yet is listed under [Known limitations](#known-limitations).

## Features

- `CircuitTopology` / `SystemTopology` ABCs with a single evaluation pipeline: `params -> sizing -> netlist -> SpiceRunner -> FoM`.
- Analog topologies: Miller OTA (IHP and GF180), AnalogAcademy OTA, GF180 OTA, StrongARM comparator, a 7-bit SAR ADC (transistor-level `SAR7BitTopology` and behavioural `SAR7BitBehavioralTopology`), an 11-bit SAR flagged as a `DESIGN_REFERENCE`, and inverter-based amplifiers on IHP and GF180 sized with the Ron/gm method.
- ngspice integration with async support, measurement parsing, and optional OSDI (Verilog-A) and XSPICE code-model preload.
- gm/ID LUT reader with analytical sizing helpers (`size`, `size_from_ft`, `size_from_gmro`, `operating_range`). GF180 LUTs download on first use.
- Greedy autoresearch loops for analog sizing and for digital flows. The digital loop can tune the flow config, the RTL, or both, and proposals can come from LiteLLM, the Claude Code CLI or OpenCode.
- Hierarchical design-space exploration (primitives, macromodels and a symbolic MNA solver) that returns a Pareto front on IHP or GF180.
- Idea-to-chip: a natural-language spec becomes RTL plus a testbench (iverilog or cocotb), a critique loop feeds lint and simulation failures back to the model, and LibreLane v3 hardens the result to GDS.
- Digital RTL-to-GDS via LibreLane v3 on GF180 and IHP SG13G2, driven by a Google ADK multi-agent hierarchy or the Claude Code CLI, with cocotb gate-level simulation after the flow. `GenericDesign` derives a design wrapper from a LibreLane config file.
- Analog layout and verification: gLayout generation of primitives and composites on GF180 and SG13G2. On GF180 there are also KLayout DRC/LVS and Magic PEX runners and post-layout validation with pre/post deltas.
- A skill registry with 31 skills across `analog.*`, `digital.*` and `flow.*`, used through `get_skill(...)` and `list_skills(prefix=...)`. Topologies and digital designs declare the skills they need through `relevant_skills()`, and the runners inject the rendered text into the system prompt.
- An MCP server (`eda-mcp`) with 9 tools, plus `eda-init` to set up a project for Claude Code or OpenCode with the MCP config and a set of ready-made subagents.
- An analog 4-role DAG (Librarian / Architect / Designer / Verifier), and a set of pre-simulation netlist checks (floating nodes, bulk connections, mirror ratio, bias source, testbench pins, Vds polarity).
- A benchmark suite (`bench/` + `scripts/run_bench.py`) with Pydantic and JSON-schema task models, adapter dispatch, and a rule that an adapter PASS is downgraded to `FAIL_AUDIT` when a scoring criterion fails.
- A bridge (`bridge/` + `eda-bridge` CLI) with a UUID job registry, an OpenSSH wrapper, and `xschem` / KLayout operation helpers.

## Install

```bash
git clone --recurse-submodules https://github.com/Mauricio-xx/eda-agents.git
cd eda-agents
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,adc]"       # core + pytest/ruff + adctoolbox for ADC metrics
```

Optional extras, combine as needed (for example `pip install -e ".[dev,adc,mcp]"`):

```bash
pip install -e ".[mcp]"           # fastmcp + litellm + httpx: MCP server, LiteLLM-backed loops, GF180 LUT download
pip install -e ".[adk]"           # google-adk + litellm + google-generativeai: Google ADK harnesses
pip install -e ".[agents]"        # openai: OpenRouter chat client
pip install -e ".[coordination]"  # context-teleport: optional multi-agent coordination
pip install -e ".[plots]"         # matplotlib: autoresearch plots
```

Anything that calls an LLM through LiteLLM (autoresearch, the LiteLLM harness) needs `litellm`, which comes with either `mcp` or `adk`. Tests that depend on an extra you did not install are skipped.

The install puts three commands on your PATH: `eda-mcp` (the MCP server), `eda-init` (writes MCP and agent config files into a project directory) and `eda-bridge` (job registry and tool bridge CLI).

If you cloned without `--recurse-submodules`, run `git submodule update --init --recursive`. The submodules under `external/` are upstream LibreLane project templates used only by the template parity check, not at runtime.

### Requirements

- Python >= 3.11.
- On PATH for the stages that use them: `ngspice >= 38`, `yosys >= 0.62`, `magic`, `klayout`, `netgen`. `openvaf` is needed for the Verilog-A pipeline, and `openroad` is normally reached through LibreLane. gLayout runs from a separate `.venv-glayout`.
- `PDK_ROOT` pointing at an IHP SG13G2 install, or at a GF180MCU install with `EDA_AGENTS_PDK=gf180mcu`.

Run `scripts/check_tools.sh` to see which tools and PDKs are available on your machine.

## Quick start

### 1. Bench smoke

```bash
python scripts/run_bench.py --no-real-tools --run-id quick_smoke   # no EDA tools needed, same as CI
python scripts/run_bench.py --list-tasks                           # see every task
python scripts/run_bench.py --run-id full                          # everything available on this host
```

Results go to `bench/results/<run_id>/` with a `bench/results/latest.md` pointer. In a full run each task is skipped when its requirements are missing: the live idea-to-chip tasks (`idea_to_digital_*_live.yaml`) need the Claude Code CLI, `EDA_AGENTS_ALLOW_DANGEROUS=1`, LibreLane and the PDK, and the other LLM tasks need `OPENROUTER_API_KEY`. Use `--task <id>` or `--family <name>` to run a subset.

### 2. Bridge end-to-end demo

```bash
python examples/14_bridge_e2e.py --pdk ihp_sg13g2   # or --pdk gf180mcu
```

### 3. Analog 4-role DAG dry-run (no tools, no model)

```bash
python examples/12_analog_roles_demo.py
```

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

See `examples/` for the rest: autoresearch, digital RTL-to-GDS, post-layout validation, idea-to-chip, hierarchical exploration and the MCP smoke test.

## MCP server

eda-agents ships a Model Context Protocol server, so an MCP client (Claude Code, OpenCode, Cursor, Zed) can use its skills, topology evaluation and design flows without writing Python.

```bash
pip install "git+https://github.com/Mauricio-xx/eda-agents.git#egg=eda-agents[mcp]"
eda-init        # run inside your project directory
```

`eda-init` writes `.mcp.json` (Claude Code), `opencode.json` (OpenCode) and subagent definitions under `.claude/agents/` and `.opencode/agent/`. Existing files are kept unless you pass `--force`. Both clients then start `eda-mcp` on their own over stdio. To register it by hand in Claude Code: `claude mcp add eda-agents -- eda-mcp`.

| Tool | What it does |
|------|--------------|
| `list_skills` | List registered skill names, optionally filtered by prefix. |
| `render_skill` | Render a skill's prompt, optionally bound to a topology. |
| `describe_topology` | Return a topology's design space, defaults, target specs and FoM formula. |
| `evaluate_topology` | Run one SPICE evaluation of a topology at the given parameters. |
| `run_autoresearch` | Run the greedy sizing loop on a topology and return the best designs. |
| `recommend_topology` | Map a natural-language analog idea to a registered topology. |
| `explore_custom_topology` | Propose, size, simulate and critique an analog block that has no registered topology. |
| `generate_analog_layout` | Generate a GDS for a gLayout primitive or composite on GF180 or SG13G2. |
| `generate_rtl_draft` | Run the natural-language to RTL to GDS digital flow. Dry run by default. |

The tools that run SPICE or a physical flow need `PDK_ROOT` and the matching EDA tools. The LLM-backed ones need a provider key such as `OPENROUTER_API_KEY`, and a real `generate_rtl_draft` run needs the `claude` CLI. The server has no authentication, so keep it local. See [`docs/mcp_public_install.md`](docs/mcp_public_install.md) for the full setup guide, including a one-command installer for a fresh Ubuntu machine.

## Architecture

The code lives under `src/eda_agents/`:

- `core/`: the PDK-agnostic foundation. `CircuitTopology` and `SystemTopology` ABCs, the `PdkConfig` registry, `SpiceRunner`, gm/ID lookup, the KLayout / Magic / gLayout / LibreLane runners, and the digital flow stages and design wrappers.
- `topologies/`: the concrete circuits and a name-based resolver.
- `agents/`: LLM-facing orchestration. Autoresearch loops, the Google ADK, Claude Code CLI, LiteLLM and OpenCode harnesses, the analog role DAG, the idea-to-RTL and analog composition loops, and post-layout validation.
- `skills/`: the `Skill` abstraction and its registry.
- `mcp/`: the server behind the `eda-mcp` command.
- `bench/`: benchmark task and result models, the adapters registered in `HARNESS_DISPATCH`, and the runner.
- `bridge/`: `JobRegistry`, SSH execution and the `eda-bridge` CLI.
- `specs/`, `checks/`, `parsers/`, `tools/`, `veriloga/`, `utils/`: YAML block specs, pre-simulation gates, DRC / LVS / Liberty / LibreLane parsers, agent-callable tool wrappers, Verilog-A and XSPICE behavioural primitives, and shared helpers.

[`docs/architecture.md`](docs/architecture.md) has a longer walkthrough and the layering diagram.

## Skills

The `eda_agents.skills` registry ships 31 skills, grouped by prefix:

- `analog.*` (16): `adc_metrics`, `behavioral_primitives`, `corner_validator`, `custom_composition`, `explorer`, `gmid_sizing`, `hierarchical_dse`, `idea_to_topology`, `miller_ota_design`, `orchestrator`, `ron_gm_sizing`, `sar_adc_design`, plus the four DAG roles (`analog.roles.librarian`, `analog.roles.architect`, `analog.roles.designer`, `analog.roles.verifier`).
- `digital.*` (8): `project_manager`, `verification`, `synthesis`, `physical`, `signoff`, `cocotb_testbench`, `critique_sim_failure`, `critique_synth_lint`.
- `flow.*` (7): `runner`, `drc_checker`, `drc_fixer`, `lvs_checker`, `rtl2gds_gf180_docker`, `analog_signoff_gf180_docker`, `macro_integration_gf180_docker`.

```python
from eda_agents.skills import list_skills, get_skill

for s in list_skills(prefix="analog."):
    print(s.name, "-", s.description.splitlines()[0])

skill = get_skill("analog.gmid_sizing")
prompt = skill.render()
```

Some skills take arguments, such as a topology or a design. Tool specs are not skills: each topology exposes its own through `topology.tool_spec()`.

## Bench

The benchmark is built around Pydantic v2 models mirrored against `bench/schemas/{task,result}.json`. Key design decisions:

- **Audit discipline**: an adapter PASS is downgraded to `FAIL_AUDIT` whenever a requested scoring criterion fails.
- **Restricted callables**: `inputs.callable` dotted paths resolve only inside `eda_agents.bench.adapters`, so task YAMLs cannot execute arbitrary code.
- **Skip, don't fake**: an adapter that cannot run (missing tools, PDK or credentials, or invalid task inputs) returns `BenchStatus.FAIL_INFRA`, which the summary counts as skipped rather than as a pass.

A curated set of evidence runs is committed under `bench/results/`. The last committed run of the whole suite, [`bench/results/s9_residual_final_evidence/report.md`](bench/results/s9_residual_final_evidence/report.md), passed the 17 tasks that existed at that point. Of the tasks added since then, the live idea-to-chip ones have per-task evidence in other subdirectories of `bench/results/`, and the dry-run ones are exercised by CI. There is no committed single run covering all 36 tasks.

Tasks live in `bench/tasks/{spec-to-topology,bugfix,tb-generation,end-to-end}/`. Write new ones as YAML:

```yaml
id: spec_my_new_task       # the id prefix (spec_, bugfix_, tb_, e2e_) sets the report group
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

registry = JobRegistry()                         # jobs persist under ~/.cache/eda_agents/jobs/
job_id = registry.submit(fn, *args, **kwargs)    # returns the job id
record = registry.wait(job_id, timeout=60)       # JobRecord; may still be running after a timeout

ops = KLayoutOps(pdk_root=pdk_root)              # KLayout DRC/LVS for GF180MCU
drc = ops.run_drc(gds_path, run_dir="drc_run")
```

The CLI surface is `eda-bridge init / status / jobs / cancel / stop / start xschem-netlist`. See `src/eda_agents/bridge/cli.py` for the argparse definitions and `examples/14_bridge_e2e.py` for an end-to-end scenario.

## Known limitations

Upstream issues on IHP SG13G2:

- **Magic does not work with the current IHP tech files**: it hangs on stream-out and LEF writing and fails on SPICE extraction. The IHP LibreLane template bypasses the whole Magic chain and uses KLayout for stream-out, DRC and LVS, so Magic-based parasitic extraction is not available on IHP. Details in [`docs/upstream_issues/ihp_magic_hang.md`](docs/upstream_issues/ihp_magic_hang.md).
- **The IHP pad CDL is not readable by KLayout LVS.** The template sets `PAD_CDLS: []`, which is fine for core-only designs. Designs that instantiate IHP pads need to supply a KLayout-readable pad CDL, or an upstream fix.

Open work in this repo:

- **SAR ADC calibration.** Spec thresholds are anchored to measured results, but a measured regeneration time constant, a real LDO, a real bootstrap switch and a corner sweep are still missing. See [`TODO_calibration.md`](src/eda_agents/skills/_bundles/sar_adc/TODO_calibration.md).
- **Inverter-based amplifiers are not in the name registry yet**, so they can be used from Python but not through `get_topology_by_name` or the MCP tools.
- **Long MCP tools block.** `run_autoresearch`, `explore_custom_topology` and a real `generate_rtl_draft` run hold the call open until they finish and do not stream progress.
- **Deprecated SAR module names.** `sar_adc_8bit` and `sar_adc_8bit_behavioral` are shims that re-export the 7-bit topologies. Their legacy class names emit a `DeprecationWarning` when instantiated.

`CHANGELOG.md` and parts of `docs/` still describe earlier stages of the project and lag behind this README.

## Coordination (optional)

With the `coordination` extra, the ADK experiment harness can use [Context Teleport](https://github.com/Mauricio-xx/context-teleport) for multi-agent coordination over MCP. Without it, agents explore independently.

## License

Apache-2.0 (see `LICENSE`). Parts of the design were informed by repositories from the Arcadia-1 project. Most of them carry no license file, so nothing is copied from them and the relevant functionality is reimplemented here (see `docs/license_status.md`). The one permissively licensed dependency used at runtime, `adctoolbox` (MIT), is installed from PyPI.
