# eda-agents architecture

This document is for someone opening the repo for the first time. It
walks the six top-level packages under `src/eda_agents/`, shows how
they interlock, and points at the single source of truth (code,
skill, or doc) for each concern. It does **not** enumerate every
module — read the CHANGELOG for chronology and the package docstrings
for details.

## Layering at a glance

```
+-------------------------------------------------------------------+
|                     Published deliverables                         |
|    scripts/ + examples/ + eda-bridge CLI + scripts/run_bench.py    |
+-------------------------------------------------------------------+
                                   |
                                   v
+--------------------+      +------------+      +-----------------+
|  agents/           |----->|  skills/   |<-----|  bench/         |
|  (LLM harnesses,   |      |  (prompt + |      |  (schemas +     |
|   DAGs, roles)     |      |   tool     |      |   runner that   |
+--------------------+      |   specs)   |      |   invokes the   |
           |                +------------+      |   harnesses     |
           v                                    |   read-only)    |
+--------------------+      +------------+      +-----------------+
|  topologies/       |      |  specs/    |              |
|  (CircuitTopology, |<-----|  (Pydantic |              v
|   SystemTopology)  |      |   YAML)    |       +-------------+
+--------------------+      +------------+       |  bridge/    |
           |                                     |  (orchestr. |
           v                                     |   + SSH +   |
+-------------------------------------------------------------+
|                       core/                                  |
|   PdkConfig, SpiceRunner, GmIdLookup, KLayout/Magic/LibreLane|
|   runners, DigitalDesign ABC, stage runners (GL sim etc.)   |
+-------------------------------------------------------------+
```

Bench and bridge are peers of agents, not replacements. They sit at
the same layer because they both *call into* core + topologies
through the harness and tool surfaces, without reimplementing them.

## The six packages

### `core/` — PDK-agnostic evaluation foundation

`core.topology.CircuitTopology` is the ABC every analog block
implements: `design_space`, `params_to_sizing`, `generate_netlist`,
`compute_fom`, `check_validity`, plus the prompt-metadata methods the
harnesses derive tool specs from. `core.system_topology.SystemTopology`
is the multi-block composition used by SAR ADCs.

`core.pdk.PdkConfig` is a frozen dataclass registry with two built-in
PDKs — IHP-SG13G2 (PSP103 / OSDI, VDD = 1.2, subckt prefix `X`) and
GF180MCU-D (BSIM4, VDD = 3.3). `netlist_lib_lines()` and
`netlist_osdi_lines()` are the single source of truth for model
includes; topologies call them rather than hardcoding `.lib`.

`core.spice_runner.SpiceRunner` wraps ngspice with async support,
measurement parsing (S8 fixed the regex), optional extra OSDI
(`extra_osdi=`), and optional XSPICE code models
(`extra_codemodel=`). `core.gmid_lookup.GmIdLookup` reads the
pre-computed LUTs (`data/gmid_luts/` for GF180; the IHP path defaults
to the external ihp-gmid-kit install).

Physical verification wrappers live alongside:
`glayout_runner.py`, `magic_pex.py`, `klayout_drc.py`,
`klayout_lvs.py`, `librelane_runner.py`. The digital flow adds
`core/stages/` (lint, sim, synth, physical, STA, GL sim, precheck,
veriloga compile, xspice compile) and `core/designs/` (the digital
design wrappers consumed by the RTL-to-GDS pipeline).

Known limitation: IHP Magic steps hang upstream and IHP KLayout LVS
decks are incomplete (see `docs/upstream_issues/`). The code paths
are KLayout-only on IHP and full-chain on GF180.

### `topologies/` — concrete circuits

Each module is either a designer helper (`MillerOTADesigner`,
analytical sEKV-based sizing) or a `CircuitTopology` subclass
consumed by harnesses without further glue:

- `miller_ota` / `ota_miller` — IHP SG13G2 Miller OTA.
  **Caveat**: `ProcessParams` at `miller_ota.py:104` is hardcoded to
  IHP. Passing `pdk=GF180MCU_D` only swaps symbol names; the sizing
  math keeps IHP sEKV constants and produces sub-Wmin transistors
  for GF180. Documented in
  `docs/upstream_issues/miller_ota_gf180_process_params.md`; will be
  fixed in the S9-gap-closure session.
- `ota_analogacademy` — IHP AnalogAcademy PMOS-input OTA.
- `ota_gf180` — GF180MCU OTA (the GF180 path that *does* close).
- `comparator_strongarm` — StrongARM latch.
- `sar_adc_8bit` + `sar_adc_netlist` — 8-bit SAR composed of
  `StrongARM + CDAC + Verilator SAR logic`. Caveat in the class
  docstring: the effective resolution is closer to 7 bits (hence
  the `TODO_naming.md` backlog).
- `sar_adc_8bit_behavioral` — S7 behavioural variant; XSPICE
  comparator in place of the StrongARM.
- `sar_adc_11bit` — S7 design_reference (`DESIGN_REFERENCE = True`).
  True 11-bit CDAC (`test_cdac_is_true_11bit`), MSB-first decode,
  PVT / metastability / supply-ripple / reference-settling checks.

### `agents/` — LLM-facing orchestration

Each harness renders prompts and tool specs from skills and the
topology in question; none of them should need to change when a new
topology arrives.

- `autoresearch_runner.AutoresearchRunner` — the greedy
  exploration loop (adapted from Karpathy's autoresearch). Persists
  `program.md` + `results.tsv` and resumes from them.
- `adk_harness.AdkHarness` + `adk_agents.py` +
  `adk_prompts.py` — Google ADK orchestration with
  `FlowRunner / DRCChecker / LVSVerifier` sub-agents. **Do not
  touch** — this is the Track D hardening path.
- `analog_roles/` — the Session 6 DAG
  (Librarian / Architect / Designer / Verifier). Has a
  `DryRunExecutor` that exercises the wiring without calling a
  model. The bench invokes it read-only.
- `digital_adk_agents.py` + `digital_adk_prompts.py` +
  `digital_cc_runner.py` + `digital_autoresearch.py` — the digital
  RTL-to-GDS multi-agent hierarchy (ProjectManager ->
  VerificationEngineer / SynthesisEngineer / PhysicalDesigner /
  SignoffChecker). Two backends: Google ADK (`backend="adk"`) and
  Claude Code CLI (`backend="cc_cli"`). The autoresearch greedy
  loop over flow config knobs (density / clock / PDN) lives in
  `digital_autoresearch.py`.
- `postlayout_validator.py` — orchestrates layout -> DRC -> LVS ->
  PEX -> post-layout SPICE. Works on GF180 today; the IHP Magic
  hang blocks the PEX step on IHP.
- `handler.py` / `system_handler.py` / `scenarios.py` / `tool_defs.py`
  — shared infrastructure; `tool_defs` is where LibreLane helper
  functions and the digital RTL-to-GDS prompt builder live.

### `skills/` — reusable prompt + tool-spec bundles

A `Skill` bundles four things: an optional prompt-template callable,
an optional OpenAI/ADK tool-spec dict, an optional validator, and
zero or more reference-file paths. The registry is populated at
import time; `get_skill(...)` / `list_skills(prefix=...)` are the
public API.

Twenty-three skills are registered today, grouped by prefix:

- `analog.*` (11): `explorer`, `corner_validator`, `orchestrator`,
  `adc_metrics`, `behavioral_primitives`, `gmid_sizing`,
  `sar_adc_design`, plus the four roles under
  `analog.roles.{librarian,architect,designer,verifier}`.
- `digital.*` (5): `project_manager`, `verification`, `synthesis`,
  `physical`, `signoff`.
- `flow.*` (4): `runner`, `drc_checker`, `drc_fixer`, `lvs_checker`.
- `tools.*` (3 legacy): `evaluate_miller_ota`, `gmid_lookup`,
  `simulate_miller_ota`. Kept as legacy tool specs; new topologies
  should expose `topology.tool_spec()` instead.

### `bench/` — benchmark suite (new in S9)

`src/eda_agents/bench/` exposes Pydantic v2 frozen models
(`BenchTask`, `BenchResult`, `BenchScores`, `MetricBound`) and enums
that mirror `bench/schemas/{task,result}.json`. Tests verify the
Pydantic and JSON enums never drift.

`adapters.py` is the only place bench code talks to EDA tools. Four
adapters are registered in `HARNESS_DISPATCH`:

- `dry_run` — deterministic mock.
- `analog_roles` — invokes the S6 harness with `DryRunExecutor`.
- `callable` — resolves a dotted path under
  `eda_agents.bench.adapters` (restricted to prevent arbitrary-code
  execution from YAMLs). Seed tasks use this to point at
  `analytical_miller_design`, `run_pre_sim_gate_on_inline_netlist`,
  and `run_gl_sim_post_synth`.
- `digital_autoresearch` — **stub** (S10). Returns
  `BenchStatus.SKIPPED` with an explicit `NOT_IMPLEMENTED` note.
  The real adapter ships in S9-gap-closure.

`runner.py` implements audit-downgrade logic explicitly: an adapter
PASS with any failing scoring criterion is downgraded to
`FAIL_AUDIT` rather than painted over. `execute_task` and `run_batch`
are the two entry points; `run_batch` will use the Session 8
`JobRegistry` when `workers > 1`.

`scripts/run_bench.py` is the CLI surface. It writes per-task JSON +
`summary.json` + `report.md` under `bench/results/<run_id>/` and a
local `bench/results/latest.md` pointer. Only
`bench/results/s9_initial_smoke/` is committed — see
`bench/results/README.md`.

### `bridge/` — tool-agnostic orchestrator (new in S8)

Reimplementation of the `virtuoso-bridge-lite` shape under
Apache-2.0, pointed at the open-source stack (ngspice / xschem /
KLayout / Magic / OpenROAD) instead of Virtuoso / Spectre.

- `models.py` — `BridgeResult`, `SimulationResult`, `ExecutionStatus`
  enum with `.ok`, `save_json`, `load_json`. Distinguishes
  `SUCCESS / PARTIAL / FAILURE / ERROR / CANCELLED`.
- `jobs.py` — `JobRegistry`: UUID -> JSON under
  `~/.cache/eda_agents/jobs/`. `ThreadPoolExecutor` + Future.
  `submit / get / list / cancel / wait / poll_until_terminal /
  sweep / shutdown`. Jobs whose result fails to serialize are marked
  `ERROR` rather than stuck in `RUNNING`.
- `ssh.py` — Linux-only OpenSSH wrapper with jump-host (`-J`) and
  optional ControlMaster. Entirely mocked in tests.
- `xschem.py` — headless `XschemRunner.export_netlist` with an
  `infra_error` flag to separate tool-bug from setup-bug.
- `klayout_ops.py` — facade that delegates to the existing
  `core/klayout_*.py` runners. Intentionally no logic of its own.
- `cli.py` — `eda-bridge init / status / jobs / cancel / stop /
  start xschem-netlist`.

## How the pieces collaborate end-to-end

1. A spec or LLM request enters via a skill rendered by a harness
   (`agents/`), which wires a tool list onto the specific topology
   instance (`topologies/`).
2. The harness calls topology methods to produce a netlist and the
   topology, in turn, asks `core/pdk.py` for include lines. The
   result is run through `SpiceRunner` with optional OSDI / XSPICE
   code models preloaded.
3. For the physical path: `core/glayout_runner.py` produces a
   layout, `core/klayout_*.py` handle DRC + LVS, `magic_pex.py`
   extracts parasitics, and the post-layout netlist goes through
   `SpiceRunner` again. The digital equivalent is
   `core/stages/*.py` wired together by `librelane_runner.py` and
   the agents in `agents/digital_adk_*.py`.
4. Bench tasks never implement any of the above. They are YAML
   files under `bench/tasks/` that name a harness (mapped to an
   adapter in `HARNESS_DISPATCH`) plus scoring criteria. The
   adapters are thin — they call the harnesses / runners / topologies
   read-only. The runner wraps the adapter result in `audit_adapter_result`
   and emits a `BenchResult`.
5. Bridge jobs use the same runners under the hood. The bridge's
   value is job tracking (JobRegistry), remote execution (SSH), and
   a stable CLI surface (`eda-bridge`). The `examples/14_bridge_e2e.py`
   demo exercises the full IHP path.

## Skill injection contract (S10c)

Autoresearch and Claude Code CLI runners prepend a rendered block of
methodology skills to the system prompt before the run-local
`program.md` content. The pipeline is:

1. Each topology or design declares its skills via
   `relevant_skills() -> list[str | (str, dict)]` (added in S10b, see
   `CircuitTopology`, `SystemTopology`, and `DigitalDesign`).
2. `skills.registry.render_relevant_skills(entries, context)` looks up
   every entry in the skill registry and concatenates the rendered
   bodies with `\n\n---\n\n` separators. A soft 12k-token cap emits a
   warning on overflow; the prompt is still returned whole.
3. The injection happens in four prompt construction sites:
   - `AutoresearchRunner._system_prompt` (analog SPICE).
   - `DigitalAutoresearchRunner._system_prompt` (digital LibreLane).
   - `build_cc_spice_system_prompt` in `agents/tool_defs.py` (analog
     Claude Code CLI).
   - `build_digital_rtl2gds_prompt` in `agents/tool_defs.py` (digital
     Claude Code CLI, used by `DigitalClaudeCodeRunner`).
4. **Order is fixed**: skills → program.md / agent preamble →
   response-format suffix. Skills carry atemporal methodology;
   `program.md` carries run-local strategy and accumulated learnings.
   Do not conflate them — a skill should never talk about "the current
   best" and `program.md` should never redefine gm/ID from scratch.

Escape hatch: setting `EDA_AGENTS_INJECT_SKILLS=0` reverts every
injection site to the pre-S10c prompt. Use it to A/B bench behaviour
or to unblock a session if a newly added skill regresses Pass@1.

## Pointers for deeper reading

- **Plan master**: `~/.claude/plans/concurrent-beaming-bear.md`.
- **License matrix**: `docs/license_status.md` — what can be
  reused verbatim and what must be reimplemented.
- **Upstream blockers**:
  `docs/upstream_issues/ihp_magic_hang.md`,
  `docs/upstream_issues/ihp_klayout_lvs_deck.md`,
  `docs/upstream_issues/miller_ota_gf180_process_params.md`.
- **Per-session diffs**: `CHANGELOG.md` + the listed commit hashes.
- **Session handoff**: `SESSION_HANDOFF.md` is gitignored but
  always refreshed at the end of a session with the state the next
  session needs.
