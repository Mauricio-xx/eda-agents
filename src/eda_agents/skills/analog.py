"""Analog design skills: exploration, corner validation, orchestration.

Prompt bodies live here. ``eda_agents.agents.adk_prompts`` is kept as a
thin compatibility shim that delegates to ``get_skill(...)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eda_agents.skills.base import Skill
from eda_agents.skills.registry import register_skill

if TYPE_CHECKING:
    from eda_agents.core.topology import CircuitTopology


def _explorer_prompt(topology: "CircuitTopology", budget: int = 30) -> str:
    aux_tools = topology.auxiliary_tools_description()
    aux_section = f"\n\nFree tools:\n{aux_tools}" if aux_tools else ""

    return f"""You are a circuit design explorer optimizing a {topology.topology_name()}.

Circuit: {topology.prompt_description()}

Design variables:
{topology.design_vars_description()}

Specifications: {topology.specs_description()}

Figure of Merit: {topology.fom_description()}

Reference design: {topology.reference_description()}

Budget: You have {budget} SPICE simulation calls. Each costs ~1 eval.
Strategy:
1. Start near the reference point and verify it simulates correctly.
2. Systematically explore: vary one parameter at a time to understand sensitivities.
3. Once you understand the landscape, target high-FoM regions.
4. Balance exploration (new regions) with exploitation (refining best designs).
5. Track your best design and try to improve it.{aux_section}

Return your best design parameters and the achieved FoM."""


def _corner_validator_prompt(topology: "CircuitTopology") -> str:
    return f"""You are a PVT corner validation agent for a {topology.topology_name()}.

Circuit: {topology.prompt_description()}

Your task:
1. Take the best design sizing from the exploration phase.
2. Simulate at multiple corners: TT, FF, SS at -40C, 27C, 125C.
3. Report worst-case performance across all corners.
4. Flag any corner that violates specifications.

Specifications: {topology.specs_description()}

Report format:
- Table of performance (Adc, GBW, PM, FoM) per corner.
- Worst-case values highlighted.
- Overall PASS/FAIL verdict."""


def _orchestrator_prompt(
    topology: "CircuitTopology | None" = None,
    runner=None,
    max_drc_iterations: int = 3,
) -> str:
    circuit_section = ""
    if topology:
        circuit_section = f"""
Analog block: {topology.topology_name()}
Description: {topology.prompt_description()}
Specifications: {topology.specs_description()}

Phase 1 - ANALOG SIZING:
  Delegate to sizing_explorer agents to find optimal transistor sizing.
  Multiple explorers can work in parallel on different design space regions.
  Target: find the highest-FoM design that meets all specs.

Phase 2 - CORNER VALIDATION:
  Delegate to corner_validator with the best sizing from Phase 1.
  Validate across PVT corners (TT/FF/SS at -40C/27C/125C).
  If worst-case fails specs, go back to exploration.
"""

    project_info = ""
    if runner:
        design_name = runner.design_name() or "unknown"
        project_info = f"\nDesign: {design_name}\nProject: {runner.project_dir}\n"

    return f"""You are the Track D orchestrator managing a complete design flow.
{project_info}
You coordinate specialized sub-agents to achieve a working GDS:
{circuit_section}
Phase 3 - HARDENING:
  Delegate to flow_runner to execute LibreLane RTL-to-GDS.
  This runs synthesis, place-and-route, and generates layout.
  Check timing results -- if violated, discuss with flow_runner.

Phase 4 - DRC VERIFICATION:
  Delegate to drc_checker to run KLayout DRC on the generated GDS.
  If violations found, delegate to drc_fixer for the fix loop.
  The fixer can modify config and re-run up to {max_drc_iterations} times.

Phase 5 - LVS VERIFICATION:
  Delegate to lvs_checker to compare layout vs schematic.
  This is the final check before tapeout readiness.

Rules:
- Execute phases in order. Do not skip ahead.
- Report progress at each phase transition.
- If a phase fails critically (DRC unfixable, LVS mismatch), stop and report.
- Collect and summarize results from each sub-agent.
- The goal is a DRC-clean, LVS-matched GDS file."""


register_skill(
    Skill(
        name="analog.explorer",
        description=(
            "System prompt for a topology-driven design-space exploration "
            "agent. Signature: (topology, budget=30)."
        ),
        prompt_fn=_explorer_prompt,
    )
)

register_skill(
    Skill(
        name="analog.corner_validator",
        description=(
            "System prompt for a PVT corner validation agent over a given "
            "topology. Signature: (topology)."
        ),
        prompt_fn=_corner_validator_prompt,
    )
)

register_skill(
    Skill(
        name="analog.orchestrator",
        description=(
            "System prompt for the top-level Track D analog+hardening "
            "orchestrator. Signature: (topology=None, runner=None, "
            "max_drc_iterations=3)."
        ),
        prompt_fn=_orchestrator_prompt,
    )
)


def _adc_metrics_prompt(topology: "CircuitTopology | None" = None) -> str:
    circuit = ""
    if topology is not None:
        circuit = (
            f"\nActive circuit: {topology.topology_name()}\n"
            f"Description: {topology.prompt_description()}\n"
        )
    return f"""You are analysing the dynamic performance of a data converter.
{circuit}
Use ``eda_agents.tools.adc_metrics.compute_adc_metrics(samples, fs, ...)``
to turn an ADC output trace into a metrics dict. The wrapper is backed
by ADCToolbox (MIT) and is the only supported entry point - do not
roll your own FFT/ENOB code.

The returned dict always contains the following keys (values are
``None`` when the analysis was not computed):

  - enob             Effective Number of Bits
  - sndr_dbc         Signal-to-Noise-and-Distortion Ratio [dBc]
  - sfdr_dbc         Spurious-Free Dynamic Range          [dBc]
  - snr_dbc          Signal-to-Noise Ratio                [dBc]
  - thd_dbc          Total Harmonic Distortion            [dBc]
  - inl              INL curve (numpy array, LSB units)
  - dnl              DNL curve (numpy array, LSB units)
  - walden_fom_fj    Walden FoM                           [fJ/conv-step]
  - coherent_freq_hz Closest coherent input tone to target

How to act on each metric:
  - ENOB below spec: try more samples, check coherent sampling, or
    suspect offset/kickback; if clean signal, size the comparator
    (or input sampling network) for lower noise.
  - SFDR dominated by low-order harmonic: revisit linearity of the
    sampler or C-DAC matching.
  - INL/DNL stair-step > 0.5 LSB: check unit-cell matching in the
    C-DAC (or quantisation mapping if full_scale is misconfigured).
  - walden_fom_fj: report alongside ENOB and sampling rate; lower is
    better. Use it to rank across candidate sizings.

Always request ``num_bits`` and ``full_scale`` when INL/DNL matters.
For spectrum-only analysis, set ``include_inl=False`` to skip the
INL/DNL work and keep the call cheap."""


register_skill(
    Skill(
        name="analog.adc_metrics",
        description=(
            "ADC dynamic/static metric analysis via ADCToolbox. Prompts "
            "the agent on how to read the compute_adc_metrics dict and "
            "act on ENOB/SNDR/SFDR/THD/INL/DNL. Signature: "
            "(topology=None)."
        ),
        prompt_fn=_adc_metrics_prompt,
    )
)


def _gmid_sizing_prompt(topology: "CircuitTopology | None" = None) -> str:
    circuit = ""
    if topology is not None:
        circuit = (
            f"\nActive circuit: {topology.topology_name()}\n"
            f"Description: {topology.prompt_description()}\n"
        )
    return f"""You are doing gm/ID methodology sizing for a MOSFET block.
{circuit}
Use ``eda_agents.core.gmid_lookup.GmIdLookup`` as the single source of
truth for device characteristics. It reads the pre-computed ``.npz``
LUT shipped with the active PDK (IHP SG13G2 via ihp-gmid-kit or
GF180MCU via scripts/generate_gf180_luts.py). No PTM, no BSIM hand-
fits: the LUT is the ground truth.

Canonical sizing calls (all return the same dict schema):

  - ``lut.size(gmid, mos_type, L_um, Vds, Vbs, Id=, W=, gm=)``
      Pick an operating point by (gm/ID, L) and pin one of Id, W, or gm.

  - ``lut.size_from_ft(ft_target_hz, mos_type, L_um, Vds, Vbs, Id=|W=)``
      Find the highest gm/ID that still meets a transit-frequency
      target at the given L, then size to Id or W. Fails loud if the
      target is unreachable.

  - ``lut.size_from_gmro(gmro_target, mos_type, L_um, Vds, Vbs, Id=|W=)``
      Same, but the constraint is minimum intrinsic gain (gm * ro).

  - ``lut.operating_range(mos_type)``
      Inspect the envelope of the LUT slice: gm/ID range, ID density
      range (A/m), L bounds, Vgs/Vds axes. Call this first to scope
      what is physically realisable before you target a spec.

Return dict schema (keys always present):

  - ``W_um``      transistor width [um]
  - ``L_um``      channel length [um]
  - ``Id_uA``     bias current [uA]
  - ``gm_uS``     transconductance [uS]
  - ``gds_uS``    output conductance [uS]
  - ``ft_Hz``     transit frequency [Hz] (None if LUT lacks Cgg)
  - ``vgs_V``     interpolated gate-source bias
  - ``vds_V``     LUT-gridded drain-source bias (nearest to request)
  - ``vbs_V``     LUT-gridded body-source bias (nearest to request)
  - ``gmid``      requested gm/ID (or best-achievable for from_ft/gmro)
  - ``gmro``      intrinsic gain at the operating point
  - ``vth_V``     median Vth for the slice
  - ``mos_type``  "nmos" or "pmos"

Design heuristics to act on:
  - Low gm/ID (< 10): strong inversion, fastest devices, small W,
    lower intrinsic gain. Use for RF, output stages.
  - Medium gm/ID (~12-18): moderate inversion, balanced gain / speed.
    Default starting point for OTA input pairs at 1-2 um L.
  - High gm/ID (> 20): weak inversion, highest efficiency and gain
    but poor fT. Use for slow bias networks, low-noise references.
  - If ``size_from_ft`` returns gm/ID ~ minimum of the LUT range,
    reduce L (smaller L -> higher fT) or relax fT.
  - If ``size_from_gmro`` raises, increase L: gm*ro scales with L at
    fixed gm/ID. Do NOT drop gm/ID; that reduces gain too.
  - If ``operating_range`` reports ``id_density_max`` well below the
    Id/W you need, your target current is too high for the slice;
    either raise W or re-check the supply budget.

Never roll your own gm/ID tables from raw SPICE. Ask for a new LUT
via ``scripts/generate_gmid_lut.py --pdk <pdk> --device <dev>`` if the
existing LUT doesn't cover your L/Vbs."""


register_skill(
    Skill(
        name="analog.gmid_sizing",
        description=(
            "gm/ID methodology sizing via GmIdLookup (size, "
            "size_from_ft, size_from_gmro, operating_range). Describes "
            "the canonical sizing dict schema and the tradeoffs to "
            "exploit for each method. Signature: (topology=None)."
        ),
        prompt_fn=_gmid_sizing_prompt,
    )
)


def _behavioral_primitives_prompt(topology: "CircuitTopology | None" = None) -> str:
    circuit = ""
    if topology is not None:
        circuit = (
            f"\nActive circuit: {topology.topology_name()}\n"
            f"Description: {topology.prompt_description()}\n"
        )
    return f"""You are assembling a behavioural / mixed-signal simulation
deck. eda-agents ships a small library of original open-source
primitives for the cases where transistor-level SPICE is too slow
or blocks in-loop agent iteration.
{circuit}
Three loading mechanisms, all orchestrated by ``SpiceRunner``:

  1. ``SpiceRunner(extra_codemodel=[path/to/*.cm])`` — XSPICE voltage-
     domain primitives compiled from our C sources via
     ``eda_agents.core.stages.xspice_compile.XSpiceCompiler``. ngspice
     needs the ``codemodel`` line to fire before the netlist is
     parsed; the runner writes a transient ``.spiceinit`` in the work
     directory for you. **Never list the ``.cm`` files shipped inside
     ``/usr/local/lib/ngspice`` — ngspice autoloads them and duplicate
     registration segfaults.**

  2. ``SpiceRunner(extra_osdi=[path/to/*.osdi])`` — current-domain
     Verilog-A primitives compiled by openvaf (see
     ``core.stages.veriloga_compile.VerilogACompiler``). Same
     ``.spiceinit`` plumbing, with the ``osdi`` command instead of
     ``codemodel``. Parameters belong on the ``.model`` card, **not**
     the instance card — ngspice rejects OSDI instance parameters.

  3. ``topologies/sar_adc_8bit.py`` already invokes Verilator +
     cocotb for sequential digital logic; re-use it when an
     ``@(cross())``/event-driven state machine doesn't fit into XSPICE.

Catalog of ``src/eda_agents/veriloga/voltage_domain/`` (XSPICE,
loaded via ``extra_codemodel``):

  - ``ea_comparator_ideal`` — voltage comparator, ports ``(inp, inn,
    out)``; params ``vout_high``, ``vout_low``, ``hysteresis_v``.
    Use as a drop-in for a StrongArm when you want a clean bit-decision
    without transistor solver cost.

  - ``ea_clock_gen`` — free-running clock, port ``out``; params
    ``period_s``, ``duty``, ``v_high``, ``v_low``, ``delay_s``. Good
    for stimulus; never replaces an on-die clock tree.

  - ``ea_opamp_ideal`` — behavioural single-pole op-amp,
    ``(inp, inn, out)``; params ``a0``, ``fp_hz``, ``vmax``, ``vmin``.
    Use for hierarchy tops where only output swing matters.

  - ``ea_edge_sampler`` — rising-edge D-latch, ``(din, clk, q)``;
    params ``clk_threshold``, ``delay_s``. Use inside an ADC digital
    back-end when Verilator is overkill.

Catalog of ``src/eda_agents/veriloga/current_domain/`` (OpenVAF /
OSDI, loaded via ``extra_osdi``):

  - ``filter_1st.va`` — first-order RC low-pass with exposed R and C.
    Good behavioural anti-alias filter.

  - ``opamp_1p.va`` — single-pole op-amp in Verilog-A, params ``a0``,
    ``fp_hz``. The XSPICE ``ea_opamp_ideal`` and this module cover
    the same function; pick XSPICE when the surrounding deck is
    already mostly voltage-domain and this one when you want every
    device in a single simulator domain.

  - ``ldo_beh.va`` — LDO with parametric PSRR and bandwidth. Use as
    a supply-reference top so the DUT sees a realistic rail.

Decision recipe when a hierarchy needs a behavioural stand-in:
  - Voltage-level edge events (clk edges, threshold crossings): XSPICE.
  - Continuous-time filter / amplifier: Verilog-A (OSDI).
  - Sequential RTL: Verilator + cocotb in the existing SAR ADC path.

When you invoke ``XSpiceCompiler``, always use the ``CodeModelSource``
dataclass to bundle the (cfunc.mod, ifspec.ifs) pair; the runner
produces a single ``.cm`` per call, so bundle all primitives you need
in one compile to share the dlmain object."""


register_skill(
    Skill(
        name="analog.behavioral_primitives",
        description=(
            "Catalog of in-house XSPICE and Verilog-A behavioural "
            "primitives plus the decision recipe for picking between "
            "codemodel, OSDI, and Verilator backends. Signature: "
            "(topology=None)."
        ),
        prompt_fn=_behavioral_primitives_prompt,
    )
)
