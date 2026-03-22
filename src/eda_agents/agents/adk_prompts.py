"""Topology-driven prompt templates for ADK agents.

Generates PDK-agnostic instructions for each agent role using
CircuitTopology metadata. Adding a new circuit type requires
only a new CircuitTopology subclass -- zero changes here.
"""

from __future__ import annotations

from eda_agents.core.topology import CircuitTopology


def explorer_prompt(topology: CircuitTopology, budget: int = 30) -> str:
    """System prompt for a design exploration agent.

    The explorer searches the design space for high-FoM designs
    that meet all specifications.
    """
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


def corner_validator_prompt(topology: CircuitTopology) -> str:
    """System prompt for a corner validation agent.

    The validator takes the best sizing from the explorer and
    checks it across PVT corners.
    """
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


def drc_fixer_prompt() -> str:
    """System prompt for a DRC violation fixer agent."""
    return """You are a DRC violation fixer agent.

Your task:
1. Parse DRC violation reports (Magic or KLayout format).
2. Classify violations by type (spacing, width, enclosure, etc.).
3. Suggest layout corrections for each violation type.
4. Prioritize fixes by severity (shorts > opens > spacing).
5. Re-run DRC after fixes to verify resolution.

Guidelines:
- Never suggest changes that would affect the schematic (W, L, connectivity).
- Focus on layout-level fixes: spacing, alignment, guard rings.
- Report violations that cannot be fixed without schematic changes."""


def orchestrator_prompt(topology: CircuitTopology) -> str:
    """System prompt for the top-level Track D orchestrator.

    Coordinates the full flow: explore -> validate -> DRC -> precheck.
    """
    return f"""You are the Track D orchestrator managing a full analog design flow.

Circuit: {topology.prompt_description()}
Specifications: {topology.specs_description()}

Workflow:
1. EXPLORE: Launch design exploration to find optimal sizing.
   - Multiple explorers can run in parallel on different design space regions.
   - Target: find top-3 designs by FoM that meet all specs.

2. VALIDATE: Run PVT corner validation on the best designs.
   - Corners: TT/FF/SS at -40C/27C/125C (9 combinations).
   - Select the design with best worst-case performance.

3. LAYOUT: Export netlist and create layout (manual or automated).
   - Export SPICE netlist for the winning design.
   - Create layout using analog layout tools.

4. VERIFY: Run DRC and LVS on the layout.
   - DRC: Magic and/or KLayout rule checks.
   - LVS: Netgen schematic-vs-layout comparison.

5. PRECHECK: Run wafer-space precheck for tapeout readiness.
   - Dimensions, chip ID, final DRC.

Report progress at each stage. Stop and report if any stage fails."""


def make_tool_description(topology: CircuitTopology) -> dict:
    """Build an OpenAI/ADK function-calling tool spec from topology."""
    return topology.tool_spec()
