# Changelog

All notable changes to this project are documented here. Format follows
Keep a Changelog; versioning will start at 0.1.0 once the first public
release ships. Until then, entries track the multi-session Arcadia-1
integration roadmap
(`~/.claude/plans/concurrent-beaming-bear.md`) on branch
`feat/arcadia-integration`.

Each session entry points at its integration commit. Earlier
pre-roadmap history (IHP digital port, post-PnR GL sim gates, LibreLane
template parity) is in git log; only roadmap sessions are summarized
below.

## Unreleased — branch `feat/arcadia-integration`

### Session 10 — Publication + cross-linking (this session)

Meta-session. No new runtime features; the deliverable is documentation
honest about what works and what does not.

- **Bucket A hygiene**
  - `bench/results/*` is now gitignored except the frozen
    `s9_initial_smoke/` baseline. Local re-runs of
    `scripts/run_bench.py` no longer dirty the working tree. New
    `bench/results/README.md` explains the contract.
  - `digital_autoresearch` adapter stub landed in
    `HARNESS_DISPATCH`. Tasks using that harness return
    `BenchStatus.SKIPPED` with an explicit `NOT_IMPLEMENTED` note
    (never the ambiguous `FAIL_INFRA`). Covered by
    `tests/test_bench_runner.py::test_digital_autoresearch_stub_returns_skipped_with_explicit_note`.
- **CHANGELOG.md** (this file), **`docs/architecture.md`** (new
  consolidated overview), and an expanded top-level **README.md** that
  lists every open gap and the scheduled gap-closure session, not a
  vague "future work" bullet.
- **`docs/external_pr_drafts/awesome_ams_skills.md`** — PR draft for
  the upstream `Arcadia-1/awesome-ams-skills` catalog. The PR is *not*
  opened by this session; the user decides when to file it.

### Session 9 — Bench suite (commit `04dfc91`)

First iteration of the eda-agents benchmark. Schemas were reimplemented
(not copied) from `behavioral-veriloga-eval`; runtime is fully
in-tree and licensed Apache-2.0.

- New package `src/eda_agents/bench/`: Pydantic v2 frozen models
  (`BenchTask`, `BenchResult`, `BenchScores`, `MetricBound`),
  harness adapter layer, and a runner with explicit PASS -> FAIL_AUDIT
  downgrade logic (a sim pass with a failing scoring criterion is
  never painted as PASS).
- Adapters wrap S6/S7/S8 code read-only: `analog_roles_adapter`
  invokes the Session 6 DAG with `DryRunExecutor`;
  `analytical_miller_design` wraps `MillerOTADesigner`;
  `run_gl_sim_post_synth` wraps `GlSimRunner` and returns
  `FAIL_INFRA` (mapped to `SKIPPED`) rather than faking a pass when no
  hardened LibreLane run is available.
- `scripts/run_bench.py` CLI with `--family`, `--task`, `--workers`,
  `--dry-run`, `--no-real-tools`, `--list-tasks`; writes per-task
  JSON + `summary.json` + `report.md` + a `latest.md` pointer.
- 11 seed tasks across `spec-to-topology`, `bugfix`, `tb-generation`,
  and `end-to-end` families.
- **Honesty about the smoke run**
  (`bench/results/s9_initial_smoke/`): 9/11 PASS, Pass@1 = 90%
  excluding skipped. The two non-PASS results are kept in the suite
  deliberately:
  - `spec_miller_ota_gf180_easy` is **FAIL_SIM**. Root cause
    diagnosed and documented in
    `docs/upstream_issues/miller_ota_gf180_process_params.md`:
    `MillerOTADesigner.ProcessParams` is hardcoded to IHP SG13G2, so
    the GF180 sizing produces sub-Wmin transistors that the BSIM4
    binner rejects. Fix path requires a GF180 sEKV port, scheduled for
    the post-S10 gap-closure session.
  - `e2e_gl_sim_post_synth_systolic` is **SKIPPED** because the
    bench intentionally does not harden a fresh LibreLane run; the
    adapter's wiring is covered by tests but not ejected against a
    real run_dir yet.
- 44 new tests under `tests/test_bench_*.py`, marker `bench` added to
  `pyproject.toml`.

### Session 8 — Bridge open-source (commit `cd232e3`)

Reimplementation of the `virtuoso-bridge-lite` patterns under
Apache-2.0 for the open-source EDA tool chain.

- New package `src/eda_agents/bridge/`:
  `models.py` (Pydantic v2 frozen `BridgeResult`, `SimulationResult`,
  `ExecutionStatus` enum with `.ok` / `.save_json` / `.load_json`);
  `jobs.py` (`JobRegistry` with UUID -> JSON persistence under
  `~/.cache/eda_agents/jobs/`, `ThreadPoolExecutor`, serialization-
  error -> `ERROR` rather than hang);
  `ssh.py` (`SSHRunner` Linux-only OpenSSH wrapper with jump-host and
  ControlMaster);
  `xschem.py` (headless `XschemRunner.export_netlist` with
  `infra_error` to separate tool-bug from setup-bug);
  `klayout_ops.py` (facade delegating to the existing
  `core/klayout_*.py` runners — no rewrite).
- `eda-bridge` CLI: `init / status / jobs / cancel / stop /
  start xschem-netlist`.
- Fixed a pre-existing parser bug in `core/spice_runner.py`:
  `_MEAS_LINE_RE` is now anchored to `^token = number` so status
  messages no longer smear into parsed measurements.
- `examples/14_bridge_e2e.py` — IHP SG13G2 demo passes end-to-end
  (Adc = 32.5 dB, GBW = 1.39 MHz). GF180 path surfaces the
  `spec_miller_ota_gf180_easy` bug S9 later diagnosed as the
  designer's IHP-only process params.
- 64 bridge tests, all mocked (zero network).

### Session 7 — SAR ADC portfolio (commits `d85bcc5`, `e11edc4`, `4e25adc`, `6a999f9`)

Four-commit arc covering the SAR ADC deliverables deferred from S5
plus the 11-bit design reference and the skill / docs bundle.

- `topologies/sar_adc_netlist.py` refactored to accept optional
  `comparator_section` / `nand_section` / `extra_model_lines`.
  Default 8-bit output stays bit-identical to S6.
- `topologies/sar_adc_8bit_behavioral.py` —
  `SARADC8BitBehavioralTopology` by composition over the parent SAR,
  with the XSPICE comparator replacing the StrongARM. The class
  docstring opens with the "7-bit-effective" caveat (AnalogAcademy
  naming).
- `topologies/sar_adc_11bit.py` — `SARADC11BitTopology` flagged
  `DESIGN_REFERENCE = True`. The CDAC is verified 11-bit
  (`test_cdac_is_true_11bit`), dummy cap permanently tied to VCM.
  `check_system_validity` implements PVT margin (Pelgrom),
  metastability BER bound, supply-ripple units (no more
  "5400 mA" bug), and reference settling.
- `src/eda_agents/data/sar_logic_11bit.v` — 11-cycle SAR FSM with
  `B/BN/D [10:0]` and MSB-first decode (`D[0]=MSB`).
- `core/pdk.py::IHP_SG13G2.osdi_files` extended with `psp103.osdi`
  after the demo surfaced the missing `psp103va` binding for
  `sg13_lv_*` subcircuits.
- Skill `analog.sar_adc_design` registered; 9 markdown documents
  under `docs/skills/sar_adc/` (reviewed architecture, comparator,
  bootstrap switch, SAR logic, LDO, integration, sim/verification)
  plus `TODO_calibration.md` and `TODO_naming.md` for the deferred
  pieces.
- `evals/sar_adc_arch.json` — 20 architectural prompts.
- `examples/13_sar_adc_11bit.py` and
  `examples/13b_sar_adc_8bit_behavioral.py` — end-to-end demos with
  skip-on-missing-tool logic for ngspice / openvaf / Verilator /
  XSPICE.
- 30 new tests across `test_sar_adc_11bit.py`,
  `test_sar_adc_8bit_behavioral.py`, and an added
  `test_skills.py` case.

### Session 6 — Analog 4-role DAG (commit `89da1c6`)

Reimplementation of the `analog-agents` DAG under Apache-2.0.

- `src/eda_agents/specs/spec_yaml.py` — Pydantic v2 frozen spec
  loader.
- `src/eda_agents/checks/pre_sim/` — inline SPICE subcircuit parser
  plus four gates (`floating_nodes`, `bulk_connections`,
  `mirror_ratio`, `bias_source`). These are what the bench's `bugfix`
  family exercises.
- `src/eda_agents/agents/iteration_log.py` — Pydantic v2 iteration
  log with YAML round-trip and `EscalationError`.
- `src/eda_agents/agents/analog_roles/` — `AnalogRolesHarness` and
  `DryRunExecutor`. The bench invokes this harness verbatim to prove
  the DAG wiring without paying a model.
- `src/eda_agents/skills/analog_roles.py` — four role skills
  (librarian, architect, designer, verifier).
- `examples/12_analog_roles_demo.py`.
- 40 new tests.

### Session 5 — Behavioural primitives + Docker toolchain (commit `650bd25`)

- `core/flow_stage.py` gains `FlowStage.XSPICE_COMPILE`.
- `core/stages/xspice_compile.py` (`XSpiceCompiler`,
  `CodeModelSource`, `XSpiceToolchain`) and
  `core/spice_runner.py::SpiceRunner(extra_codemodel=...,
  preload_pdk_osdi=...)` for loading compiled `.cm` code models and
  extra OSDI at simulation time.
- `src/eda_agents/veriloga/voltage_domain/` — comparator_ideal,
  clock_gen, opamp_ideal, edge_sampler (XSPICE C sources).
- `src/eda_agents/veriloga/current_domain/` — `filter_1st.va`,
  `opamp_1p.va`, `ldo_beh.va` (Verilog-A for OpenVAF).
- `src/eda_agents/topologies/sar_adc_8bit_behavioral.py` — initial kit
  (the full topology class landed in S7).
- Skill `analog.behavioral_primitives`; Docker image for the XSPICE
  toolchain.
- Demo `examples/11_ams_mixed_domain_demo.py`.

### Session 4 — gm/ID sizing API on top of the LUT (commit `ca9a36e`)

- `core/gmid_lookup.py::GmIdLookup` gained `size`, `size_from_ft`,
  `size_from_gmro`, and `operating_range`. Semantics match the
  gm/ID methodology without reusing any gmoverid-skill code
  (repo has no LICENSE — reimplement, do not copy).
- `scripts/generate_gmid_lut.py` — unified IHP + GF180 LUT generator.
- `src/eda_agents/tools/gmid_json_adapter.py` — npz <-> JSON
  interop (`gmoverid.v1` envelope).
- Skill `analog.gmid_sizing` registered.
- 18 new tests across `test_gmid_sizing.py` and
  `test_gmid_json_adapter.py`.

### Session 3 — ADCToolbox integration (commit `0ea5eb2`)

- Extra `[adc]` (`adctoolbox>=0.6.4`, MIT). Pulled via PyPI rather
  than vendored.
- `src/eda_agents/tools/adc_metrics.py` — `compute_adc_metrics(...)`
  and `calculate_walden_fom(...)` wrappers.
- `topologies/sar_adc_8bit.py::extract_enob` now delegates to the
  toolbox rather than the in-tree 64-sample FFT.
- Skill `analog.adc_metrics`.
- 7 tests.

### Session 2 — OpenVAF -> OSDI pipeline (commit `34c95f0`)

- `core/stages/veriloga_compile.py` + `FlowStage.VERILOGA_COMPILE`.
- `core/pdk.py::netlist_osdi_lines(pdk, extra_osdi=None)` accepts
  user-side OSDI paths; `core/spice_runner.py` propagates them into
  the run directory's `.spiceinit`.
- Marker `veriloga` added for the new pipeline tests.

### Session 1 — Skill registry (commit `06e21f4`)

- New package `src/eda_agents/skills/`: `Skill` dataclass, registry,
  and 15 initial skills (analog / digital / flow / tools). More
  skills added by later sessions (S3, S4, S5, S6, S7) reach ~23 by
  the end of S9.
- `agents/adk_prompts.py` and `agents/digital_adk_prompts.py` were
  converted to thin shims delegating to
  `get_skill(...).render(...)`.
- 25 tests (grew to 26 with the S7 skill addition).

### Session 0 — Legal + tooling matrix (commits `33007fc`, `989a772`)

- `scripts/check_tools.sh` — baseline tooling check (ngspice,
  openvaf, yosys, magic, klayout, netgen, PDK_ROOT for IHP + GF180).
- `docs/license_status.md` — license matrix for the eight Arcadia-1
  repos reviewed during the deep-dive. Five of the six unlicensed
  repos are treated as "study architecture, reimplement under
  Apache-2.0"; the sixth is quote-able but not copy-able.
- Upstream licensing-clarification issues were drafted but
  **deliberately not filed yet**; reopen the decision only if a
  specific repo becomes a blocker.

---

## Baseline (upstream `main`)

Prior work includes the IHP digital RTL-to-GDS port (merge
`2362553`), post-PnR GL sim gates with SDF annotation (`aaa7c2a`,
`8e6ac66`, `09ad783`), LibreLane template parity CI (`407843a`,
`96780bc`), and IHP Magic step skips (`4d20ee9`, `ed5af21`). See
`git log 2362553...04dfc91` for details.
