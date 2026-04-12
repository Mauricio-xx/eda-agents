# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

Install in editable mode with dev extras (main venv):

```bash
pip install -e ".[dev]"           # core + pytest/ruff
pip install -e ".[agents]"        # + openai (reactive_harness)
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
  accepts it only if it contains the target PDK's model files;
  otherwise falls back to `PdkConfig.default_pdk_root`.
- `EDA_AGENTS_PDK` — selects the active PDK by registry name
  (`ihp_sg13g2` | `gf180mcu`). Default is IHP SG13G2.
- `OPENROUTER_API_KEY`, `ZAI_API_KEY` — model backends used by the
  agent harnesses (loaded from `.env`, which is gitignored).
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
- `gmid_lookup.py` — gm/ID LUT reader used for analytical
  pre-sizing before spending SPICE budget. GF180 LUTs live under
  `data/gmid_luts/`; IHP LUTs default to the external ihp-gmid-kit
  path in `IHP_SG13G2.lut_dir_default`.
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
- `reactive_harness.py` — multi-agent round-based OpenAI harness with
  optional Context Teleport MCP coordination (gated on
  `HAS_MCP`/`context-teleport`); degrades gracefully when CT is absent.
- `adk_harness.py` + `adk_agents.py` + `adk_prompts.py` — Google ADK
  harness with `FlowRunner`, `DRCChecker`, `LVSVerifier` sub-agents.
  Used by the Track D GF180 end-to-end flow.
- `postlayout_validator.py` — orchestrates layout -> DRC -> LVS -> PEX
  -> post-layout SPICE and emits pre/post deltas. Has overlay and
  hybrid paths (see SESSION_LOG for the degenerate-PEX caveat on
  gLayout GDS — it affects numerical meaningfulness, not mechanics).
- `handler.py`, `system_handler.py`, `phase_results.py`,
  `scenarios.py`, `tool_defs.py` — shared infrastructure
  (`SpiceEvaluationHandler`, scenario records, tool-spec builders).

### `tools/`, `parsers/`, `utils/`

`tools/eda_tools.py` wraps runners as agent-callable functions.
`parsers/` contains DRC/LVS/Liberty/LibreLane/ORFS result parsers plus
`ExtFileParser` for Magic `.ext` parasitic-cap extraction.
`utils/vlnggen.py` handles Verilog compilation; `utils/detect.py`
detects EDA project layouts.

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
`--from-autoresearch`). `scripts/` holds utility drivers (LUT
generation, GF180 OTA validation, LibreLane setup check, post-layout
evaluation, gLayout driver).
