# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

Install in editable mode with dev extras (main venv):

```bash
pip install -e ".[dev]"           # core + pytest/ruff
pip install -e ".[agents]"        # + openai (OpenAI-based harnesses)
pip install -e ".[adk]"           # + google-adk, litellm (adk_harness)
pip install -e ".[coordination]"  # + context-teleport (optional MCP)
```

Two virtual environments coexist. The main `.venv` runs eda-agents and
ngspice-driven tests. `.venv-glayout` is a separate environment holding
gLayout/Magic/KLayout tooling and is driven via subprocess by
`GLayoutRunner`, `MagicPexRunner`, and the KLayout runners — do not
install gLayout into the main venv.

Testing uses pytest markers to gate on external tools (see
`pyproject.toml` and `tests/conftest.py`):

```bash
pytest                                      # run everything available
pytest -m "not spice"                       # CI default — no ngspice/PDK
pytest -m "not spice and not klayout and not glayout and not magic and not librelane"
pytest tests/test_miller_ota.py::test_name  # single test
ruff check src/ tests/                      # lint (matches CI)
```

The `pdk_config` fixture in `conftest.py` parametrizes tests across
`ihp_sg13g2` and `gf180mcu` and auto-skips when the PDK is missing at
`$PDK_ROOT` or the config's `default_pdk_root`.

## Environment

- `PDK_ROOT` — path to the active PDK install. `resolve_pdk_root()`
  accepts it only if it contains the target PDK's model files.
  Raises if neither env var nor explicit arg is provided; the
  built-in `PdkConfig.default_pdk_root` fallbacks are empty for
  pip-installed use (set `PDK_ROOT` or pass `pdk_root=...`).
- `EDA_AGENTS_PDK` — selects the active PDK by registry name
  (`ihp_sg13g2` | `gf180mcu`). Default is IHP SG13G2.
- `OPENROUTER_API_KEY`, `ZAI_API_KEY` — model backends used by the
  agent harnesses (loaded from `.env`, which is gitignored).
- `EDA_AGENTS_DIGITAL_DESIGNS_DIR` — parent directory for external
  digital design repos (fazyrv-hachure, Systolic_MAC, precheck).
  Default: `/home/montanares/git`. Clone with
  `scripts/fetch_digital_designs.sh`.
- `EDA_AGENTS_ALLOW_DANGEROUS` — set to `1` to enable
  `--dangerously-skip-permissions` for Claude Code CLI backend
  (also requires `allow_dangerous=True` in constructor).
- `SESSION_LOG.md` is gitignored and holds per-session plan/blockers
  (see global rules). Do not commit it.

## Architecture

The core evaluation pipeline is `params -> sizing -> netlist -> SpiceRunner -> FoM`.
Everything downstream of a topology — autoresearch, LLM harnesses,
post-layout validation — operates through the `CircuitTopology` ABC,
so adding a new circuit type should require zero changes to runners,
prompts, or tool specs.

### `core/` — PDK-agnostic evaluation layer

- `topology.py` — `CircuitTopology` ABC: `design_space`,
  `params_to_sizing`, `generate_netlist`, `compute_fom`,
  `check_validity`, plus prompt-metadata methods consumed by harnesses.
- `system_topology.py` — `SystemTopology` for multi-block systems
  (e.g. `sar_adc_8bit`) that compose multiple `CircuitTopology`s.
- `pdk.py` — frozen `PdkConfig` dataclass + registry. Built-ins are
  `IHP_SG13G2` (PSP103/OSDI, VDD=1.2, subcircuit `X` prefix) and
  `GF180MCU_D` (BSIM4, VDD=3.3, wafer-space fork layout with
  `design.ngspice` global include + `sm141064*.ngspice` libs).
  `netlist_lib_lines()` / `netlist_osdi_lines()` are the single source
  of truth for how netlists include models, so topologies must call
  them rather than hardcoding `.lib` directives. MIM cap handling
  differs per PDK: when `mimcap_corner` is set, the cap lib is loaded
  via a second `.lib` section against the main model lib to avoid
  double-loading — do not also include `cap_lib_rel` in that case.
- `spice_runner.py` — sync + async ngspice invocation with
  measurement parsing. Builds `SpiceResult` (Adc, GBW, PM, power).
  Accepts `extra_osdi=[paths]` for user-compiled Verilog-A models;
  the runner writes a temporary `.spiceinit` in the work directory
  that pre-loads those OSDIs so `.model` lines bind to them during
  parse. The work directory must not already contain a `.spiceinit`
  when extras are used.
- `gmid_lookup.py` — gm/ID LUT reader used for analytical
  pre-sizing before spending SPICE budget. IHP LUTs resolve from
  `EDA_AGENTS_IHP_LUT_DIR` (clone of ihp-gmid-kit). GF180 LUTs
  auto-download on first use into `~/.cache/eda-agents/gmid_luts/`
  via `lut_fetcher.py`; override with `EDA_AGENTS_GMID_LUT_DIR`.
  `EDA_AGENTS_OFFLINE=1` disables the fetcher.
- `glayout_runner.py`, `magic_pex.py`, `klayout_drc.py`,
  `klayout_lvs.py`, `librelane_runner.py` — subprocess wrappers for
  the physical-verification tool chain. They accept paths/venvs so the
  same runner objects can back both tests and the ADK agent tools.

### `topologies/` — concrete circuits

Each module provides either a designer helper (e.g. `MillerOTADesigner`
for analytical sizing) or a full `CircuitTopology` subclass. Current:
`miller_ota` / `ota_miller` (IHP), `ota_analogacademy` (IHP),
`ota_gf180` (GF180), `comparator_strongarm`, and the
`sar_adc_8bit` + `sar_adc_netlist` system topology.

### `agents/` — LLM-facing orchestration

- `autoresearch_runner.py` — topology-agnostic greedy exploration loop
  adapted from Karpathy's autoresearch. Persists `program.md` +
  `results.tsv` in the work dir and resumes from them on re-invocation.
  This is the primary sizing-exploration path.
- `adk_harness.py` + `adk_agents.py` + `adk_prompts.py` — Google ADK
  harness with `FlowRunner`, `DRCChecker`, `LVSVerifier` sub-agents.
  Used by the Track D GF180 end-to-end flow.
- `postlayout_validator.py` — orchestrates layout -> DRC -> LVS -> PEX
  -> post-layout SPICE and emits pre/post deltas. Has overlay and
  hybrid paths (see SESSION_LOG for the degenerate-PEX caveat on
  gLayout GDS — it affects numerical meaningfulness, not mechanics).
- `digital_adk_agents.py` + `digital_adk_prompts.py` — digital
  RTL-to-GDS multi-agent hierarchy: `ProjectManager` master with
  `VerificationEngineer`, `SynthesisEngineer`, `PhysicalDesigner`,
  `SignoffChecker`. Supports `backend="adk"` (ADK multi-agent) or
  `backend="cc_cli"` (Claude Code CLI via `claude --print`).
- `digital_autoresearch.py` — `DigitalAutoresearchRunner`: greedy
  exploration over flow config knobs (density, clock, PDN).
- `claude_code_harness.py` — `ClaudeCodeHarness`: async wrapper
  around `claude --print --output-format json`. Double-gated
  `--dangerously-skip-permissions` (constructor + env var).
- `digital_cc_runner.py` — `DigitalClaudeCodeRunner`: builds
  RTL-to-GDS prompts from `DigitalDesign` metadata, invokes
  `ClaudeCodeHarness`.
- `handler.py`, `system_handler.py`, `phase_results.py`,
  `scenarios.py`, `tool_defs.py` — shared infrastructure
  (`SpiceEvaluationHandler`, scenario records, tool-spec builders).
  `tool_defs.py` also has `build_digital_rtl2gds_prompt()` and
  `write_librelane_flow_script()`.

### `core/stages/` — digital flow stage runners

- `rtl_lint_runner.py` — verilator/yosys lint
- `rtl_sim_runner.py` — cocotb/iverilog simulation
- `synth_runner.py`, `physical_slice_runner.py`, `sta_runner.py` —
  LibreLane sub-flow wrappers
- `precheck_runner.py` — wafer-space precheck

### `core/designs/` — digital design wrappers

- `generic.py` — `GenericDesign`: auto-derives all 13 `DigitalDesign`
  methods from a LibreLane config file. Zero Python class needed.
  Constructor: `GenericDesign(config_path, pdk_root=None)`.
- `fazyrv_hachure.py` — GF180 RISC-V SoC (primary design, nix-shell)
- `systolic_mac_dft.py` — CI fixture (simpler, faster)

### `tools/`, `parsers/`, `utils/`

`tools/eda_tools.py` wraps runners as agent-callable functions.
`parsers/` contains DRC/LVS/Liberty/LibreLane/ORFS result parsers plus
`ExtFileParser` for Magic `.ext` parasitic-cap extraction.
`utils/vlnggen.py` handles Verilog compilation; `utils/detect.py`
detects EDA project layouts.

## Verilog-A → OSDI → ngspice pipeline

User-authored Verilog-A models are compiled to OSDI with `openvaf`
and loaded alongside the PDK OSDI at ngspice startup:

```bash
openvaf mymodel.va     # produces mymodel.osdi next to the source
```

Wire-up from Python:

```python
from eda_agents.core.stages.veriloga_compile import VerilogACompiler
from eda_agents.core.spice_runner import SpiceRunner

result = VerilogACompiler().run("path/to/mymodel.va")
osdi = result.artifacts["osdi"]
runner = SpiceRunner(pdk="ihp_sg13g2", extra_osdi=[osdi])
```

ngspice cir conventions for Verilog-A-backed devices:

- Instance names must begin with `N` (ngspice's OSDI instance letter).
- The `.model` name is arbitrary; the model **type** must match the
  Verilog-A `module` name verbatim.
- `SpiceRunner` writes a transient `.spiceinit` in the work directory
  that pre-registers every `extra_osdi` file before the deck is
  parsed, then removes it after the run. Do not leave your own
  `.spiceinit` in that directory or the runner will refuse to
  overwrite it.

Example deck skeleton (`netlist_osdi_lines(pdk, extra_osdi=...)` emits
the `osdi ...` lines inside `.control` as an idempotent reload; the
pre-parse registration comes from the auto-written `.spiceinit`):

```spice
.control
  osdi '/abs/path/mymodel.osdi'
  dc V1 0 1.0 0.05
  meas dc i_out FIND i(V1) AT=0.5
.endc
V1 a 0 DC 0
.model m1 mymodel r=1000
Nr1 a 0 m1
.end
```

Verilog-A stage lives at `src/eda_agents/core/stages/veriloga_compile.py`.
Three current-domain primitives authored in-house ship in
`src/eda_agents/veriloga/current_domain/`: `filter_1st.va`,
`opamp_1p.va`, `ldo_beh.va`. They are referenced by the
`analog.behavioral_primitives` skill and exercised by
`tests/test_veriloga_current_primitives.py -m veriloga`.

## Behavioural primitives — XSPICE (voltage-domain)

XSPICE code models fill the gap where pure Verilog-A cannot express
event-driven voltage-domain behaviour (comparator edges, clock
generators, edge-triggered latches). Sources live in
`src/eda_agents/veriloga/voltage_domain/<primitive>/{cfunc.mod,
ifspec.ifs}` and are compiled by
`eda_agents.core.stages.xspice_compile.XSpiceCompiler` into a single
`.cm` shared object loaded via `codemodel` lines injected by
`SpiceRunner(extra_codemodel=...)`.

The compiler needs an ngspice source tree with `cmpp` built and
in-tree headers. On developer machines this is typically absent, so
the repo ships a pinned container image:

```bash
# Builds the image on first use (ngspice-45 + openvaf 23.5.0).
scripts/xspice_docker.sh pytest -m xspice tests/test_xspice_primitives.py
```

See `docker/README.md` for details. The `xspice` pytest marker gates
all tests that need the toolchain, so native `pytest -m "not xspice"`
runs stay unaffected on hosts without the compiler chain.

Primitives shipped:

- `ea_comparator_ideal(inp, inn, out)` with hysteresis + bounded
  output swing.
- `ea_clock_gen(out)` with `period_s`, `duty`, `v_high`, `v_low`,
  `delay_s`.
- `ea_opamp_ideal(inp, inn, out)` — behavioural single-pole op-amp.
- `ea_edge_sampler(din, clk, q)` — rising-edge D-latch.

The behavioural SAR comparator kit lives at
`src/eda_agents/topologies/sar_adc_8bit_behavioral.py`; it's the
Session-7 seed for the full SAR ADC behavioural variant.

## LibreLane templates

Two RTL-to-GDS config templates live at
`src/eda_agents/agents/templates/{gf180,ihp_sg13g2}.yaml.tmpl` and are
loaded via `importlib.resources` by
`src/eda_agents/agents/librelane_config_templates.py`. They are
**infrastructure, not design knobs** — mirror upstream conventions
where applicable, do not tune them for QoR. Autoresearch optimises
designs, never templates.

The upstream project templates that inform ours are pinned as git
submodules under `external/` (reference only, not build inputs). A
parity script
(`scripts/check_librelane_template_upstream.py`) enforces that
curated verbatim fields (VDD/VSS, `meta.version`,
`PRIMARY_GDSII_STREAMOUT_TOOL`) stay in sync when both sides define
them, and logs informational deltas otherwise. See
`docs/librelane_templates.md` for the full workflow and upstream-bump
process. Contributors must `git submodule update --init --recursive`
to run the parity test.

## Adding a new topology

Implement `CircuitTopology` (`src/eda_agents/core/topology.py`) and
register it wherever the caller constructs one. The harness
infrastructure derives prompts, tool specs, and validity logic from
the topology's metadata methods — do not hardcode topology names in
runners or prompt code. Netlists must go through
`netlist_lib_lines()` / `netlist_osdi_lines()` so both PDKs keep
working.

## Examples and scripts

Numbered `examples/` scripts are the canonical end-to-end entry points
(Miller/GF180 sweeps, single/multi-agent runs, ADK validation, Track D
GF180 flow, autoresearch, post-layout validation with `--dry-run` and
`--from-autoresearch`). Digital examples: `09_rtl2gds_gf180.py`
(full pipeline, supports `--backend adk|cc_cli`) and
`10_digital_autoresearch_gf180.py` (greedy config exploration).
`scripts/` holds utility drivers (LUT generation, GF180 OTA
validation, LibreLane setup check, digital flow validation, post-layout
evaluation, gLayout driver, design fetcher).
