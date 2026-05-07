# Agnosticism audit residue (2026-05-07)

Read-only inventory of residual design / PDK / domain leaks across
`src/eda_agents/agents/` and `src/eda_agents/core/` after the T1-T4
cleanup of session `bc6b0371` follow-up. The infrastructure (FoM
profile, design-intent contract, AST evaluator) is now agnostic by
construction, but several call-sites still carry concrete examples,
defaults, or workarounds that name a specific design or PDK. This
table classifies each hit so the next iteration can attack them in
priority order.

## Classification

* **leak**: violates agnosticism with no architectural justification;
  generalising the code is a mechanical change.
* **example**: docstring or default value uses a concrete name as an
  illustration; safe to keep but worth a one-line refactor when the
  surrounding code changes anyway.
* **scoped**: file is intentionally PDK-specific or design-specific
  (e.g. `klayout_lvs.py` is GF180-only by construction); cannot be
  agnostic without a separate refactor.
* **analog**: hit lives in the analog stack
  (`CircuitTopology`-driven, separate ABC), out of scope for the
  digital-flow agnosticism work. Documented for follow-up.
* **justified**: the concrete reference is real context that helps a
  reader (e.g. calibration notes inside `PpaProfile.GF180_EDUCATIONAL`,
  flow-bug workarounds with their reproduction context).

## Findings

| File:line | Hit | Category | Suggestion (NOT applied) |
|---|---|---|---|
| `agents/digital_cc_runner.py:11,14` | imports/uses `FazyRvHachureDesign` in module docstring example | example | Use `GenericDesign(config_path=...)` in the docstring example, matching `digital_autoresearch.py` after T2. |
| `agents/digital_adk_agents.py:374,378,385` | `ProjectManager` defaults to `FazyRvHachureDesign()` when no design is passed | leak | Make `design` mandatory or default to `GenericDesign` from a config path; named-design defaults bake in a particular project. |
| `core/digital_design.py:5,93` | docstring mentions "fazyrv-hachure, Systolic_MAC" as concrete examples | example | "(e.g. fazyrv-hachure, Systolic_MAC)" -> "(see `core/designs/` for concrete subclasses)". |
| `core/designs/__init__.py:3,5` | `__all__` only exports `GoertzelDspDesign`; `GenericDesign` and others not re-exported | leak | Export `GenericDesign` so users can `from eda_agents.core.designs import GenericDesign`. |
| `core/stages/rtl_sim_runner.py:139` | comment names `GoertzelDspDesign` as the example sidecar consumer | example | "designs (e.g. ``GoertzelDspDesign``)" -> "designs that override `extract_measurements`". |
| `core/digital_design.py:44,153,207` | docstrings reference DSP throughput, Nyquist, crypto bits-per-cycle as agnostic examples | justified | Already domain-agnostic phrasing; preserve. |
| `core/flow_metrics.py:89,93,227` | `PpaProfile.GF180_EDUCATIONAL` docstring contains GF180MCU calibration notes | justified | Intended; the calibration is the entry point for new profiles. |
| `core/stage_results.py:44` | "derived measurement (e.g. throughput from CLOCK_PERIOD plus..." | justified | Generic example; preserve. |
| `agents/digital_autoresearch.py:1309-1310,1331` | GF180 PDK conflict workaround comments | justified | Real flow context (inherited `PDK=ihp-sg13g2` bug); preserve. |
| `core/stages/xspice_compile.py:49` | hardcoded `"/home/montanares/personal_exp/ai-ihp-demo/ngspice/ngspice-ngspice"` path | leak | Replace with env-var / fallback path; the absolute home-directory hardcode is unportable. **High priority -- breaks for any user other than the original author.** |
| `core/klayout_lvs.py:1,46,84,143` | GF180MCU-only LVS runner | scoped | Per-PDK runner is the right architectural choice; not a leak. |
| `agents/idea_to_rtl_loop.py:122` | `pdk: str = "gf180mcu"` default | example | Default is reasonable for the bring-up corner; document that other PDKs need explicit override. |
| `agents/digital_adk_agents.py:414,426` | `gf180mcu-precheck` hardcoded as default precheck dir | example | Generalise to "<pdk-name>-precheck" once a non-GF180 precheck path lands; defer until then. |
| `agents/librelane_config_templates.py:34-63` | per-PDK template registry (GF180, IHP SG13G2) | justified | Templates ARE per-PDK by design; not a leak. |
| `agents/gf180_config_template.py` | deprecated GF180 template shim | leak | Verify call sites and remove if unused; legacy redirection module. |
| `agents/rtl_proposal_prompts.py:235` | `PDK_ROOT={pdk_root}\nPDK=gf180mcuD\n` hardcoded in prompt | leak | Read PDK string from `PdkConfig` so a new PDK does not require editing this prompt. |
| `agents/tool_defs.py:317,781,903,1346` | IHP SG13G2 hardcoded in analog OTA prompts | analog | Analog stack scope; defer to a separate analog-agnosticism pass. |
| `agents/adk_agents.py:219,454,457,725,729-730,773` | GF180 OTA defaults in ADK agent factory | analog | Analog stack scope; defer. |
| `agents/postlayout_validator.py:8-48,112,226,289,308-312,438` | OTA / Adc / GBW / PM signatures throughout | analog | Analog scope. |
| `agents/autoresearch_runner.py:17-87,171,243,531` | analog topology registry, Adc/GBW/PM measurement columns | analog | Analog scope. |
| `agents/handler.py:67,161,163,171,285` | analog SPICE pre-filter (`Adc < 35 dB or PM < 45 deg`) | analog | Analog scope. |
| `agents/analog_composition_loop.py:6,39,42,132,907,1046,1065,1068,1166-1172` | analog DAC / opamp composition; PDK-aware primitive registry | analog | Analog scope. |
| `agents/system_handler.py:10,11` | SAR ADC FFT-derived ENOB extraction | analog | Analog scope. |
| `agents/scenarios.py:70,131` | Miller-OTA design-space bounds | analog | Analog scope. |
| `agents/phase_results.py:188,191` | analog Adc reporting | analog | Analog scope. |

## Priority recommendations for the next iteration

1. **`core/stages/xspice_compile.py:49`** -- the absolute home-directory
   path is the only finding that is actively broken for anyone other
   than the original author. Replace with an env-var resolver
   (`EDA_AGENTS_NGSPICE_SOURCE_DIR` or similar).

2. **`agents/digital_adk_agents.py:374-385`** -- defaulting
   `ProjectManager(design=FazyRvHachureDesign())` bakes a design name
   into the platform. Make `design` mandatory or accept a config path.

3. **`core/designs/__init__.py:3,5`** -- export `GenericDesign` from the
   package so the documented entry point in
   `docs/design_intent_contract.md` works without a deeper import.

4. **`agents/digital_cc_runner.py:11,14`** and
   **`core/digital_design.py:5,93`** -- swap concrete design names in
   docstring examples for `GenericDesign(config_path=...)` to match
   the agnostic story already adopted in `digital_autoresearch.py`.

5. **`agents/rtl_proposal_prompts.py:235`** -- derive `PDK=...` from
   `PdkConfig` instead of hardcoding `gf180mcuD`.

The analog hits (Adc/GBW/PM/OTA/DAC) and the GF180-specific LVS / PEX
runners belong to a separate iteration: the analog stack uses
`CircuitTopology`, a different ABC than `DigitalDesign`, and the
`klayout_lvs` / postlayout-validator path is intentionally PDK-aware.
A future "analog agnosticism" pass would mirror what T1-T4 did for
the digital flow.

## What was NOT in the audit

* `core/designs/goertzel_dsp.py`, `core/designs/fazyrv_hachure.py`,
  `core/designs/systolic_mac_dft.py` -- design-specific files; their
  contents are supposed to name the design.
* `tutorials/` and `examples/` -- caller code, not infrastructure.
* `tests/` -- test fixtures legitimately reference concrete designs.
