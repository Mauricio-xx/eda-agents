"""Shared tool definitions and prompts for LLM and ADK experiment harnesses.

Prompt generation is topology-driven: CircuitTopology subclasses provide
their own descriptions, design variables, specs, reference designs, and
tool specs. The harness uses build_*() functions that pull from topology
metadata, so adding a new circuit type requires zero changes here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eda_agents.core.digital_design import DigitalDesign
    from eda_agents.core.pdk import PdkConfig
    from eda_agents.core.topology import CircuitTopology
    from eda_agents.core.system_topology import SystemTopology

# Tools always available to agents (write + read)
BASE_TOOLS = {
    "context_add_knowledge",
    "context_add_convention",
    "context_record_decision",
    "context_search",
    "context_get",
}

# Coordination tools available per strategy
COORDINATION_TOOLS = {
    "context_signal_intent",
    "context_clear_intent",
    "context_acquire_reservation",
    "context_release_reservation",
    "context_heartbeat",
    "context_check_contention",
    "context_add_sensitivity",
    "context_coordination_status",
}

STRATEGY_TOOLS: dict[str, set[str]] = {
    "none": set(),
    "intents_only": {
        "context_signal_intent",
        "context_clear_intent",
        "context_check_contention",
        "context_coordination_status",
    },
    "reservations": {
        "context_acquire_reservation",
        "context_release_reservation",
        "context_heartbeat",
        "context_check_contention",
        "context_coordination_status",
    },
    "full_rep": COORDINATION_TOOLS,
}

WRITE_TOOLS = {
    "context_add_knowledge",
    "context_add_convention",
    "context_record_decision",
}

SYSTEM_PROMPT = """\
You are an AI agent in a multi-agent coordination experiment. \
You share a project context store with other agents working concurrently.

Complete ALL assigned operations. For each write:
1. If coordination tools are available, use them before writing \
(check contention, acquire reservation, signal intent).
2. Write substantive content (2-3 sentences).
3. Release reservations after writing.

When finished, respond with exactly "DONE" and no tool calls."""


# ---------------------------------------------------------------------------
# Topology-driven prompt builders (circuit-agnostic)
# ---------------------------------------------------------------------------


def build_cc_spice_system_prompt(
    topology: CircuitTopology,
    agent_id: str,
    eval_script: str,
    gmid_script: str,
    strategy: str,
    budget: int,
) -> str:
    """Build system prompt for Claude Code CLI SPICE mode from topology metadata.

    This is the topology-agnostic replacement for CLAUDE_CODE_SPICE_SYSTEM_PROMPT.
    """
    n_vars = len(topology.design_space())
    strategy_instructions = CLAUDE_CODE_STRATEGY_INSTRUCTIONS.get(strategy, "")
    param_names = " ".join(f"<{name}>" for name in topology.design_space())

    aux = topology.auxiliary_tools_description()

    if aux:
        workflow = (
            f'WORKFLOW:\n'
            f'1. Use gmid_lookup.py FIRST to check intrinsic gain at your chosen L values:\n'
            f'   python3 {gmid_script} nmos <L_um> <gmid_target>\n'
            f'   python3 {gmid_script} pmos <L_um> <gmid_target>\n\n'
            f'2. Use the SPICE evaluation script (limited budget!):\n'
            f'   python3 {eval_script} {param_names}\n\n'
            f'3. After each SPICE evaluation, write the EXACT JSON output to the shared store '
            f'using MCP tool context_add_knowledge with key "design-point-{agent_id}-<index>". '
            f'The content MUST be the raw JSON string returned by the script.\n\n'
        )
        aux_note = f'- gmid_lookup is FREE (no budget cost). Use it to pre-screen before SPICE.\n'
    else:
        workflow = (
            f'WORKFLOW:\n'
            f'1. Use the SPICE evaluation script (limited budget!):\n'
            f'   python3 {eval_script} {param_names}\n\n'
            f'2. After each SPICE evaluation, write the EXACT JSON output to the shared store '
            f'using MCP tool context_add_knowledge with key "design-point-{agent_id}-<index>". '
            f'The content MUST be the raw JSON string returned by the script.\n\n'
        )
        aux_note = ''

    return (
        f'You are an analog circuit design agent exploring the '
        f'{topology.topology_name()} design space. '
        f'You are agent "{agent_id}". '
        f'{topology.prompt_description()}\n\n'
        f'Goal: find design points with the highest Figure of Merit (FoM).\n'
        f'{topology.fom_description()} '
        f'Specs: {topology.specs_description()}.\n\n'
        f'Design variables ({n_vars}D):\n'
        f'{topology.design_vars_description()}\n\n'
        f'{topology.reference_description()} Can you beat it?\n\n'
        f'{workflow}'
        f'{strategy_instructions}\n\n'
        f'IMPORTANT:\n'
        f'{aux_note}'
        f'- SPICE budget is LIMITED to {budget} evaluations. Be strategic.\n'
        f'- Think about what you learn from each result to guide your next choice.\n'
        f'- When done, output "DONE" as your final message.'
    )


# ---------------------------------------------------------------------------
# System-level (multi-block) prompt builders
# ---------------------------------------------------------------------------


def build_system_explorer_prompt(
    topology: SystemTopology,
    batch_size: int,
    eval_tool_name: str,
    agent_mode: str = "co_tuning",
    block_name: str | None = None,
) -> str:
    """Build system prompt for multi-block system exploration.

    Args:
        topology: SystemTopology instance (e.g., SARADCTopology).
        batch_size: evaluations per round.
        eval_tool_name: name of the evaluation tool/function.
        agent_mode: "co_tuning" (full space) or "per_block" (restricted).
        block_name: required if agent_mode="per_block".
    """
    if agent_mode == "per_block" and block_name is None:
        raise ValueError("block_name required for per_block mode")

    if agent_mode == "per_block":
        space = topology.block_design_space(block_name)
        n_vars = len(space)
        block_desc = topology.block_prompt_description(block_name)
        mode_note = (
            f"You are assigned to the '{block_name}' block ({n_vars}D). "
            f"Other blocks are held at their current best values. "
            f"Your changes affect the SYSTEM-LEVEL FoM, not just your block."
        )
    else:
        space = topology.system_design_space()
        n_vars = len(space)
        block_desc = ""
        mode_note = (
            f"You control the full {n_vars}D system design space. "
            f"All blocks interact -- changing one affects overall ENOB, power, and FoM."
        )

    constraints = topology.inter_block_constraints()
    constraint_text = ""
    if constraints:
        constraint_text = (
            "\n\nINTER-BLOCK CONSTRAINTS (critical for coordination):\n"
            + "\n".join(f"- {c}" for c in constraints)
        )

    prompt = (
        f"You are a multi-block circuit design agent exploring the "
        f"{topology.topology_name()} system design space. "
        f"{topology.prompt_description()}\n\n"
        f"{mode_note}\n\n"
        f"Goal: maximize system-level Figure of Merit.\n"
        f"{topology.fom_description()} "
        f"Specs: {topology.specs_description()}.\n\n"
        f"Design variables ({n_vars}D):\n"
        f"{topology.design_vars_description()}\n\n"
        f"{topology.reference_description()}\n\n"
        f"{constraint_text}\n\n"
        f"Use {eval_tool_name} to test design points. "
        f"IMPORTANT: Each system simulation takes ~20-30 seconds. Budget is limited.\n\n"
        f"Write each result to the knowledge store with context_add_knowledge "
        f'using key "design-point-<agent_id>-r<round>-<index>". '
        f"Include the full JSON result.\n\n"
        f'Evaluate exactly {batch_size} design points this round, then respond "DONE".'
    )

    if block_desc:
        prompt += f"\n\nYour block ({block_name}): {block_desc}"

    return prompt


def build_system_round_prompt(
    agent_id: str,
    round_idx: int,
    n_rounds: int,
    batch_size: int,
    agent_mode: str,
    block_name: str | None,
    own_history: list[dict] | None = None,
    others_summary: list[dict] | None = None,
    strategy: str = "none",
    current_best_fom: float = 0.0,
) -> str:
    """Build per-round prompt for system-level agent.

    In per_block mode, includes info about other blocks' agents and their results.
    """
    lines = [
        f"Round {round_idx + 1}/{n_rounds}. Budget: {batch_size} evaluations.",
        f"Your agent ID: {agent_id}.",
    ]

    if agent_mode == "per_block" and block_name:
        lines.append(f"You are tuning the '{block_name}' block.")

    if current_best_fom > 0:
        lines.append(f"Current system best FoM: {current_best_fom:.2e}")

    if own_history:
        best = max(own_history, key=lambda h: h.get("FoM", 0))
        lines.append("")
        lines.append(f"Your previous best: FoM={best['FoM']:.2e}")
        bp = best.get("params", {})
        if bp:
            params_str = ", ".join(f"{k}={v:.3f}" for k, v in bp.items())
            lines.append(f"  at {params_str}")
        lines.append(f"Total evaluations so far: {len(own_history)}")
    else:
        lines.append("")
        lines.append("This is your first round. Start by exploring broadly.")

    if strategy in ("intents_only", "reservations", "full_rep") and others_summary:
        lines.append("")
        lines.append("Other agents' results:")
        for other in others_summary:
            block = other.get("block_name", "?")
            op = other.get("best_params", {})
            params_str = ", ".join(f"{k}={v:.3f}" for k, v in op.items()) if op else "none"
            lines.append(
                f"  {other['agent']} ({block}): best FoM={other.get('best_fom', 0):.2e} at {params_str}"
            )
        lines.append("")
        lines.append(
            "Consider how their findings affect your block. "
            "Use coordination tools to avoid redundant evaluations."
        )

    lines.append("")
    lines.append(
        f"Evaluate {batch_size} points. Try to improve on your previous best. "
        "Write results to store, then say DONE."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude Code CLI experiment: prompts, MCP config, evaluate script
# ---------------------------------------------------------------------------

CLAUDE_CODE_SYSTEM_PROMPT = """\
You are an analog circuit design agent exploring the Miller OTA design space \
on IHP SG13G2 130nm BiCMOS. You are agent "{agent_id}".

Goal: find design points with the highest Figure of Merit (FoM).
FoM = Gain * GBW / (Power * Area), penalized when specs are violated \
(gain < 50dB, GBW < 1MHz, PM < 60deg, Vos > 10mV). Higher FoM is better. \
Designs violating specs get quadratically penalized -- you must balance \
performance against power and area, not just minimize denominators.

Design variables (6D space):
- gmid_input: gm/ID of input pair [5-25 S/A]. Moderate inversion (~10-15) balances gain and speed.
- gmid_load: gm/ID of load [5-20 S/A]. Lower values give more gain but cost area.
- L_input_um: input pair channel length [0.13-2.0 um]. Longer = more gain, more area.
- L_load_um: load channel length [0.13-2.0 um].
- Cc_pF: compensation capacitor [0.1-5.0 pF]. Larger Cc improves phase margin but reduces GBW.
- Ibias_uA: first-stage bias current per branch [0.5-50.0 uA]. More current = higher GBW \
but more power. This is the main knob for the gain-bandwidth vs power tradeoff.

WORKFLOW:
1. Use the Bash tool to evaluate design points by running:
   python3 {eval_script} <gmid_input> <gmid_load> <L_input_um> <L_load_um> <Cc_pF> <Ibias_uA>
   This returns a JSON result with Adc_dB, GBW_MHz, PM_deg, power_uW, area_um2, FoM, valid, violations.

2. After each evaluation, write the EXACT JSON output from the script to the shared store \
using the MCP tool context_add_knowledge with key "design-point-{agent_id}-<index>" \
where index is 0, 1, 2, etc. The content MUST be the raw JSON string returned by the script, \
not a markdown summary.

{strategy_instructions}

IMPORTANT:
- Evaluate exactly {budget} design points total.
- Think about what you learn from each evaluation to guide your next choice.
- When done, output "DONE" as your final message."""


CLAUDE_CODE_STRATEGY_INSTRUCTIONS: dict[str, str] = {
    "none": (
        "You are working independently. Focus on systematic exploration of your assigned region.\n"
        "You have NO coordination tools -- just evaluate and write results."
    ),
    "intents_only": (
        "You share a knowledge store with other agents. Use MCP tools to:\n"
        "- Search the store (context_search) to find other agents' results\n"
        "- Read entries (context_get) to see what others have found\n"
        "- Signal your intent (context_signal_intent) before exploring a region\n"
        "- Check contention (context_check_contention) to avoid duplicate work\n"
        "Use this information to avoid exploring regions that others already covered."
    ),
    "reservations": (
        "You share a knowledge store with other agents. Use MCP tools to:\n"
        "- Search the store (context_search) to find other agents' results\n"
        "- Read entries (context_get) to see what others have found\n"
        "- Acquire reservations (context_acquire_reservation) before writing shared keys\n"
        "- Release reservations (context_release_reservation) after writing\n"
        "- Check contention (context_check_contention) to coordinate writes\n"
        "Use this information to avoid exploring regions that others already covered."
    ),
    "full_rep": (
        "You share a knowledge store with other agents. Use ALL coordination tools:\n"
        "- Search the store (context_search) to find other agents' results\n"
        "- Read entries (context_get) to see what others have found\n"
        "- Signal intent (context_signal_intent) before exploring a region\n"
        "- Acquire/release reservations for shared keys like 'best-design'\n"
        "- Check contention (context_check_contention) to coordinate\n"
        "- Add sensitivity signals (context_add_sensitivity) for important keys\n"
        "- Check coordination status (context_coordination_status)\n\n"
        "You may explore outside your initial region if others found promising areas.\n"
        "Actively use coordination to find the global best collaboratively."
    ),
}


def build_claude_code_prompt(
    agent_id: str,
    budget: int,
    eval_script: str,
    strategy: str,
    partition_lo: dict[str, float],
    partition_hi: dict[str, float],
    own_history: list[dict] | None = None,
    others_summary: list[dict] | None = None,
    phase: int = 1,
    phase_desc: str = "",
) -> str:
    """Build the full prompt for a Claude Code CLI agent.

    In two-phase mode:
        phase=1: broad exploration
        phase=2: refinement around best results (own_history injected)
    """
    strategy_instructions = CLAUDE_CODE_STRATEGY_INSTRUCTIONS.get(strategy, "")

    system = CLAUDE_CODE_SYSTEM_PROMPT.format(
        agent_id=agent_id,
        eval_script=eval_script,
        strategy_instructions=strategy_instructions,
        budget=budget,
    )

    lines = [system, ""]

    if phase_desc:
        lines.append(f"PHASE {phase}: {phase_desc}")
        lines.append("")

    lines.append(
        f"Your assigned region: gmid_input=[{partition_lo.get('gmid_input', 5.0):.1f}, "
        f"{partition_hi.get('gmid_input', 25.0):.1f}]."
    )

    if own_history:
        best = max(own_history, key=lambda h: h.get("FoM", 0))
        lines.append("")
        lines.append(f"Your previous best: FoM={best['FoM']:.2e}")
        bp = best.get("params", {})
        if bp:
            params_str = ", ".join(f"{k}={v:.3f}" for k, v in bp.items())
            lines.append(f"  at {params_str}")
        lines.append(f"Total evaluations so far: {len(own_history)}")

    if strategy in ("intents_only", "reservations", "full_rep") and others_summary:
        lines.append("")
        lines.append("Other agents' results so far:")
        for other in others_summary:
            op = other.get("best_params", {})
            params_str = (
                ", ".join(f"{k}={v:.3f}" for k, v in op.items()) if op else "none"
            )
            lines.append(
                f"  {other['agent']}: best FoM={other.get('best_fom', 0):.2e} at {params_str}"
            )

    lines.append("")
    lines.append(f"Evaluate {budget} design points, write results to store, then say DONE.")
    return "\n".join(lines)


def build_mcp_config(work_dir: str, agent_id: str, venv_python: str) -> dict:
    """Build MCP server config JSON for Claude Code --mcp-config.

    The config points to the Context Teleport MCP server running against
    the experiment's work directory.
    """
    import os

    src_dir = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src")
    return {
        "mcpServers": {
            "context-teleport": {
                "command": venv_python,
                "args": ["-m", "ctx.mcp.server"],
                "cwd": work_dir,
                "env": {
                    "PYTHONPATH": src_dir,
                    "MCP_CALLER": agent_id,
                    "HOME": os.environ.get("HOME", ""),
                    "PATH": os.environ.get("PATH", ""),
                },
            }
        }
    }


def write_evaluate_script(dest_dir: str) -> str:
    """Write the evaluate_miller_ota.py script to dest_dir, return its path."""
    import os
    from pathlib import Path as _Path

    src_dir = str(_Path(__file__).resolve().parents[1] / "src")
    script_path = os.path.join(dest_dir, "evaluate_miller_ota.py")

    content = f'''#!/usr/bin/env python3
"""Evaluate a Miller OTA design point. Used by Claude Code CLI agents.

Usage: python3 evaluate_miller_ota.py <gmid_input> <gmid_load> <L_input_um> <L_load_um> <Cc_pF> <Ibias_uA>
"""
import json
import sys

sys.path.insert(0, {src_dir!r})

from eda_agents.topologies.miller_ota import MillerOTADesigner

def main():
    if len(sys.argv) < 7:
        print(json.dumps(dict(status="error", message="Usage: evaluate_miller_ota.py <gmid_input> <gmid_load> <L_input_um> <L_load_um> <Cc_pF> <Ibias_uA>")))
        sys.exit(1)

    try:
        gmid_input = max(5.0, min(25.0, float(sys.argv[1])))
        gmid_load = max(5.0, min(20.0, float(sys.argv[2])))
        L_input_um = max(0.13, min(2.0, float(sys.argv[3])))
        L_load_um = max(0.13, min(2.0, float(sys.argv[4])))
        Cc_pF = max(0.1, min(5.0, float(sys.argv[5])))
        Ibias_uA = max(0.5, min(50.0, float(sys.argv[6])))
    except ValueError as e:
        print(json.dumps(dict(status="error", message=f"Invalid args: " + str(e))))
        sys.exit(1)

    designer = MillerOTADesigner()
    result = designer.analytical_design(
        gmid_input=gmid_input,
        gmid_load=gmid_load,
        L_input=L_input_um * 1e-6,
        L_load=L_load_um * 1e-6,
        Cc=Cc_pF * 1e-12,
        Ibias=Ibias_uA * 1e-6,
    )

    print(json.dumps(dict(
        gmid_input=round(gmid_input, 2),
        gmid_load=round(gmid_load, 2),
        L_input_um=round(L_input_um, 3),
        L_load_um=round(L_load_um, 3),
        Cc_pF=round(Cc_pF, 2),
        Ibias_uA=round(Ibias_uA, 2),
        Adc_dB=round(result.Adc_dB, 1),
        GBW_MHz=round(result.GBW / 1e6, 3),
        PM_deg=round(result.PM, 1),
        power_uW=round(result.power_uW, 2),
        area_um2=round(result.area_um2, 2),
        raw_FoM=result.raw_FoM,
        spec_penalty=round(result.spec_penalty, 6),
        FoM=result.FoM,
        valid=result.valid,
        violations=result.violations,
    )))

if __name__ == "__main__":
    main()
'''

    with open(script_path, "w") as f:
        f.write(content)
    os.chmod(script_path, 0o755)
    return script_path


def write_simulate_script(dest_dir: str) -> str:
    """Write simulate_miller_ota.py (SPICE) script to dest_dir, return its path."""
    import os
    from pathlib import Path as _Path

    src_dir = str(_Path(__file__).resolve().parents[1] / "src")
    script_path = os.path.join(dest_dir, "simulate_miller_ota.py")

    content = f'''#!/usr/bin/env python3
"""Evaluate a Miller OTA design point with SPICE simulation. Used by Claude Code CLI agents.

Usage: python3 simulate_miller_ota.py <gmid_input> <gmid_load> <L_input_um> <L_load_um> <Cc_pF> <Ibias_uA> [work_dir]
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, {{src_dir!r}})

from eda_agents.topologies.ota_miller import MillerOTATopology
from eda_agents.core.spice_runner import SpiceRunner

def main():
    if len(sys.argv) < 7:
        print(json.dumps(dict(status="error", message="Usage: simulate_miller_ota.py <6 params> [work_dir]")))
        sys.exit(1)

    try:
        gmid_input = max(5.0, min(25.0, float(sys.argv[1])))
        gmid_load = max(5.0, min(20.0, float(sys.argv[2])))
        L_input_um = max(0.13, min(2.0, float(sys.argv[3])))
        L_load_um = max(0.13, min(2.0, float(sys.argv[4])))
        Cc_pF = max(0.1, min(5.0, float(sys.argv[5])))
        Ibias_uA = max(0.5, min(50.0, float(sys.argv[6])))
    except ValueError as e:
        print(json.dumps(dict(status="error", message=f"Invalid args: " + str(e))))
        sys.exit(1)

    work_dir = sys.argv[7] if len(sys.argv) > 7 else tempfile.mkdtemp(prefix="spice-sim-")

    params = dict(
        gmid_input=gmid_input, gmid_load=gmid_load,
        L_input_um=L_input_um, L_load_um=L_load_um,
        Cc_pF=Cc_pF, Ibias_uA=Ibias_uA,
    )

    topo = MillerOTATopology()
    runner = SpiceRunner()

    # Analytical pre-screen
    sizing = topo.params_to_sizing(params)
    ana = sizing.get("_analytical", {{}})

    from pathlib import Path
    sim_dir = Path(work_dir)
    sim_dir.mkdir(parents=True, exist_ok=True)

    cir = topo.generate_netlist(sizing, sim_dir)
    result = runner.run(cir)

    out = dict(
        params=params,
        eval_mode="spice",
        analytical=dict(
            Adc_dB=round(ana.get("Adc_dB", 0), 1),
            GBW_MHz=round(ana.get("GBW_Hz", 0) / 1e6, 3),
            PM_deg=round(ana.get("PM_deg", 0), 1),
            FoM=ana.get("FoM", 0),
            valid=ana.get("valid", False),
        ),
    )

    if result.success:
        out["spice"] = dict(
            Adc_dB=round(result.Adc_dB, 2) if result.Adc_dB else None,
            GBW_MHz=round(result.GBW_Hz / 1e6, 3) if result.GBW_Hz else None,
            PM_deg=round(result.PM_deg, 1) if result.PM_deg else None,
            sim_time_s=round(result.sim_time_s, 2),
        )
        fom = topo.compute_fom(result, sizing)
        valid, violations = topo.check_validity(result)
        out["fom"] = fom
        out["valid"] = valid
        out["violations"] = violations
    else:
        out["spice"] = dict(error=result.error)
        out["fom"] = 0.0
        out["valid"] = False
        out["violations"] = [result.error or "simulation failed"]

    print(json.dumps(out, default=str))

if __name__ == "__main__":
    main()
'''

    with open(script_path, "w") as f:
        f.write(content)
    os.chmod(script_path, 0o755)
    return script_path


def write_simulate_aa_ota_script(dest_dir: str) -> str:
    """Write simulate_aa_ota.py (SPICE) script to dest_dir, return its path."""
    import os
    from pathlib import Path as _Path

    src_dir = str(_Path(__file__).resolve().parents[1] / "src")
    script_path = os.path.join(dest_dir, "simulate_aa_ota.py")

    content = f'''#!/usr/bin/env python3
"""Evaluate an AnalogAcademy OTA design point with SPICE simulation.

Usage: python3 simulate_aa_ota.py <Ibias_uA> <L_dp_um> <L_load_um> <Cc_pF> <W_dp_um> [work_dir]

Returns JSON with SPICE-validated gain, GBW, PM, FoM, and validity.
Reference design: Ibias=80, L_dp=3.64, L_load=9.75, Cc=0.75, W_dp=3.705
  -> Adc=56.7dB, GBW=2.1MHz, PM=74.1deg
"""
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, {{src_dir!r}})

from eda_agents.topologies.ota_analogacademy import AnalogAcademyOTATopology
from eda_agents.core.spice_runner import SpiceRunner

def main():
    if len(sys.argv) < 6:
        print(json.dumps(dict(
            status="error",
            message="Usage: simulate_aa_ota.py <Ibias_uA> <L_dp_um> <L_load_um> <Cc_pF> <W_dp_um> [work_dir]",
        )))
        sys.exit(1)

    try:
        Ibias_uA = max(10.0, min(150.0, float(sys.argv[1])))
        L_dp_um = max(0.5, min(5.0, float(sys.argv[2])))
        L_load_um = max(1.0, min(10.0, float(sys.argv[3])))
        Cc_pF = max(0.3, min(3.0, float(sys.argv[4])))
        W_dp_um = max(0.5, min(10.0, float(sys.argv[5])))
    except ValueError as e:
        print(json.dumps(dict(status="error", message=f"Invalid args: " + str(e))))
        sys.exit(1)

    work_dir = sys.argv[6] if len(sys.argv) > 6 else tempfile.mkdtemp(prefix="spice-aa-")

    params = dict(
        Ibias_uA=Ibias_uA, L_dp_um=L_dp_um, L_load_um=L_load_um,
        Cc_pF=Cc_pF, W_dp_um=W_dp_um,
    )

    topo = AnalogAcademyOTATopology()
    runner = SpiceRunner()

    from pathlib import Path
    sim_dir = Path(work_dir)
    sim_dir.mkdir(parents=True, exist_ok=True)

    sizing = topo.params_to_sizing(params)
    cir = topo.generate_netlist(sizing, sim_dir)

    # Hash the netlist for traceability
    netlist_hash = ""
    try:
        netlist_hash = "sha256:" + hashlib.sha256(cir.read_bytes()).hexdigest()[:16]
    except Exception:
        pass

    result = runner.run(cir)

    # Build transistor sizing dict (exclude metadata keys)
    transistor_sizing = {{
        k: v for k, v in sizing.items()
        if not k.startswith("_") and isinstance(v, dict)
    }}

    out = dict(
        params=params,
        eval_mode="spice",
        transistor_sizing=transistor_sizing,
        netlist_hash=netlist_hash,
        sim_dir=str(sim_dir),
    )

    if result.success:
        out["Adc_dB"] = round(result.Adc_dB, 2) if result.Adc_dB else None
        out["GBW_MHz"] = round(result.GBW_Hz / 1e6, 3) if result.GBW_Hz else None
        out["PM_deg"] = round(result.PM_deg, 1) if result.PM_deg else None
        out["sim_time_s"] = round(result.sim_time_s, 2)
        fom = topo.compute_fom(result, sizing)
        valid, violations = topo.check_validity(result)
        out["FoM"] = fom
        out["valid"] = valid
        out["violations"] = violations
    else:
        out["error"] = result.error
        out["FoM"] = 0.0
        out["valid"] = False
        out["violations"] = [result.error or "simulation failed"]

    print(json.dumps(out, default=str))

if __name__ == "__main__":
    main()
'''

    with open(script_path, "w") as f:
        f.write(content)
    os.chmod(script_path, 0o755)
    return script_path


def write_gmid_lookup_script(dest_dir: str) -> str:
    """Write gmid_lookup.py CLI script to dest_dir, return its path."""
    import os
    from pathlib import Path as _Path

    src_dir = str(_Path(__file__).resolve().parents[1] / "src")
    script_path = os.path.join(dest_dir, "gmid_lookup.py")

    content = f'''#!/usr/bin/env python3
"""Query IHP SG13G2 MOSFET gm/ID lookup table (PSP103 data).

Usage: python3 gmid_lookup.py <mos_type> <L_um> <gmid_target> [Vds]

Examples:
  python3 gmid_lookup.py nmos 9.75 12       # NMOS load at L=9.75um, gm/ID=12
  python3 gmid_lookup.py pmos 3.64 15 -0.6  # PMOS diff pair at L=3.64um, gm/ID=15

Returns JSON with gm_gds (intrinsic gain), gm_gds_dB, id_w_A_m, fT_Hz, etc.
"""
import json
import sys

sys.path.insert(0, {{src_dir!r}})

from eda_agents.core.gmid_lookup import GmIdLookup

def main():
    if len(sys.argv) < 4:
        print(json.dumps(dict(
            status="error",
            message="Usage: gmid_lookup.py <nmos|pmos> <L_um> <gmid_target> [Vds]",
        )))
        sys.exit(1)

    mos_type = sys.argv[1]
    L_um = float(sys.argv[2])
    gmid_target = float(sys.argv[3])
    Vds = float(sys.argv[4]) if len(sys.argv) > 4 else (0.6 if mos_type == "nmos" else -0.6)

    lut = GmIdLookup()
    result = lut.query_at_gmid(gmid_target, mos_type, L_um, Vds)
    if result is None:
        print(json.dumps(dict(
            status="error",
            message=f"gm/ID={{gmid_target}} out of range for {{mos_type}} at L={{L_um}}um",
        )))
        sys.exit(1)

    print(json.dumps(result))

if __name__ == "__main__":
    main()
'''

    with open(script_path, "w") as f:
        f.write(content)
    os.chmod(script_path, 0o755)
    return script_path


def build_claude_code_spice_prompt(
    agent_id: str,
    budget: int,
    eval_script: str,
    gmid_script: str,
    strategy: str,
    partition_desc: str = "",
    own_history: list[dict] | None = None,
    others_summary: list[dict] | None = None,
    topology: CircuitTopology | None = None,
) -> str:
    """Build the full prompt for a Claude Code CLI agent in SPICE mode.

    If topology is provided, generates the system prompt from topology
    metadata (circuit-agnostic). Otherwise falls back to hardcoded AA OTA.
    """
    if topology is not None:
        system = build_cc_spice_system_prompt(
            topology=topology,
            agent_id=agent_id,
            eval_script=eval_script,
            gmid_script=gmid_script,
            strategy=strategy,
            budget=budget,
        )
    else:
        # Legacy fallback: hardcoded AA OTA prompt
        strategy_instructions = CLAUDE_CODE_STRATEGY_INSTRUCTIONS.get(strategy, "")
        system = _LEGACY_CC_SPICE_PROMPT.format(
            agent_id=agent_id,
            eval_script=eval_script,
            gmid_script=gmid_script,
            strategy_instructions=strategy_instructions,
            budget=budget,
        )

    lines = [system, ""]

    if partition_desc:
        lines.append(partition_desc)
        lines.append("")

    if own_history:
        best = max(own_history, key=lambda h: h.get("FoM", 0))
        lines.append(f"Your previous best: FoM={best['FoM']:.2e}")
        bp = best.get("params", {})
        if bp:
            params_str = ", ".join(f"{k}={v:.3f}" for k, v in bp.items())
            lines.append(f"  at {params_str}")
        lines.append(f"Total evaluations so far: {len(own_history)}")

    if strategy in ("intents_only", "reservations", "full_rep") and others_summary:
        lines.append("")
        lines.append("Other agents' results so far:")
        for other in others_summary:
            op = other.get("best_params", {})
            params_str = (
                ", ".join(f"{k}={v:.3f}" for k, v in op.items()) if op else "none"
            )
            lines.append(
                f"  {other['agent']}: best FoM={other.get('best_fom', 0):.2e} at {params_str}"
            )

    lines.append("")
    lines.append(f"Evaluate {budget} design points with SPICE, write results to store, then say DONE.")
    return "\n".join(lines)


# Legacy hardcoded CC SPICE prompt (AA OTA). Used as fallback when no topology given.
_LEGACY_CC_SPICE_PROMPT = """\
You are an analog circuit design agent exploring the two-stage OTA design space \
on IHP SG13G2 130nm BiCMOS. You are agent "{agent_id}". \
The topology is a PMOS-input diff pair with NMOS mirror load and NMOS CS second \
stage with Miller compensation.

Goal: find design points with the highest Figure of Merit (FoM).
FoM = Adc_linear * GBW / (Power * Area), penalized when specs are violated \
(gain < 50dB, GBW < 1MHz, PM < 45deg). Higher FoM is better.

Design variables (5D):
- Ibias_uA: tail bias current [10-150 uA]. Main power/speed knob.
- L_dp_um: diff pair channel length [0.5-5.0 um]. Affects input stage gain.
- L_load_um: load and second-stage channel length [1.0-10.0 um]. \
Longer = more gain (higher rds). Key gain variable. PDK max is 10um.
- Cc_pF: Miller compensation cap [0.3-3.0 pF]. Larger = better PM, lower GBW.
- W_dp_um: diff pair width [0.5-10.0 um]. Affects gm and matching.

Reference design: Ibias=80, L_dp=3.64, L_load=9.75, Cc=0.75, W_dp=3.705 \
-> Adc=56.7dB, GBW=2.1MHz, PM=74.1deg. Can you beat it?

This is a two-stage OTA: total gain is the product of both stages' gain. \
Use gmid_lookup to understand the gain-speed tradeoffs at different L values \
and inversion levels before committing SPICE budget.

WORKFLOW:
1. Use gmid_lookup.py FIRST to check intrinsic gain at your chosen L values:
   python3 {gmid_script} nmos <L_um> <gmid_target>
   python3 {gmid_script} pmos <L_um> <gmid_target>

2. Use simulate_aa_ota.py for SPICE evaluation (limited budget!):
   python3 {eval_script} <Ibias_uA> <L_dp_um> <L_load_um> <Cc_pF> <W_dp_um>

3. After each SPICE evaluation, write the EXACT JSON output to the shared store \
using MCP tool context_add_knowledge with key "design-point-{agent_id}-<index>". \
The content MUST be the raw JSON string returned by the script.

{strategy_instructions}

IMPORTANT:
- gmid_lookup is FREE (no budget cost). Use it to pre-screen before SPICE.
- SPICE budget is LIMITED to {budget} evaluations. Be strategic.
- Think about what you learn from each result to guide your next choice.
- When done, output "DONE" as your final message."""


# ---------------------------------------------------------------------------
# Generic task prompt builder (used by LLM/ADK harness)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Digital RTL-to-GDS: Claude Code CLI prompt + helper script
# ---------------------------------------------------------------------------


def build_digital_rtl2gds_prompt(
    design: DigitalDesign,
    pdk_config: PdkConfig | str | None = None,
) -> str:
    """Build the full prompt for a Claude Code CLI agent driving RTL-to-GDS.

    The prompt tells the CLI agent:
    - What the design is and its specifications
    - The exact LibreLane config path and project directory
    - Explicit PDK_ROOT and PDK env vars (F5 transferable rule)
    - Step-by-step workflow: lint -> sim -> synth -> P&R -> DRC -> LVS
    - How to modify config, re-run, and read reports
    - What to report when done

    ``pdk_config`` is resolved in order: explicit argument ->
    ``design.pdk_config()`` -> ``resolve_pdk()``.
    """
    from eda_agents.agents.digital_autoresearch import detect_nix_eda_tool_dirs
    from eda_agents.core.pdk import resolve_pdk

    cfg = pdk_config if pdk_config is not None else design.pdk_config()
    cfg = resolve_pdk(cfg)
    librelane_pdk = cfg.librelane_pdk_name
    extra_flags = " ".join(cfg.librelane_extra_flags)
    extra_flags_suffix = f" {extra_flags}" if extra_flags else ""

    nix_dirs = detect_nix_eda_tool_dirs()
    nix_path_prefix = f"PATH={':'.join(nix_dirs)}:$PATH " if nix_dirs else ""

    project = design.project_name()
    project_dir = design.project_dir()
    config_path = design.librelane_config()

    # PDK environment (F5: never rely on inherited env vars)
    pdk_root = design.pdk_root()
    pdk_root_str = str(pdk_root) if pdk_root else "$PDK_ROOT"

    # RTL sources
    rtl_sources = design.rtl_sources()
    rtl_list = "\n".join(f"  - {p}" for p in rtl_sources) if rtl_sources else "  (see project directory)"

    # Testbench info
    tb = design.testbench()
    if tb:
        tb_section = (
            f"Testbench available:\n"
            f"  Driver: {tb.driver}\n"
            f"  Target: {tb.target}\n"
        )
        if tb.env_overrides:
            tb_section += f"  Env overrides: {tb.env_overrides}\n"
    else:
        tb_section = "No testbench configured. Skip simulation.\n"

    # Design space
    ds = design.design_space()
    knob_lines = []
    for key, values in ds.items():
        knob_lines.append(f"  {key}: {list(values)}")
    knobs_str = "\n".join(knob_lines) if knob_lines else "  (none)"

    # Shell wrapper (nix-shell support)
    wrapper = design.shell_wrapper()
    if wrapper:
        # Wrap the LibreLane command inside the shell wrapper
        flow_cmd = (
            f"cd {project_dir} && {wrapper} "
            f"'PDK_ROOT={pdk_root_str} PDK={librelane_pdk} "
            f"python3 -m librelane {config_path.name} --overwrite{extra_flags_suffix}'"
        )
        env_note = (
            f"\nNOTE: This design uses a shell wrapper for its toolchain.\n"
            f"All LibreLane commands MUST be run through: {wrapper} '<command>'\n"
            f"The wrapper provides the correct Python, LibreLane, and EDA tools.\n"
        )
    else:
        flow_cmd = (
            f"cd {project_dir} && {nix_path_prefix}"
            f"PDK_ROOT={pdk_root_str} PDK={librelane_pdk} "
            f"python3 -m librelane {config_path.name} --overwrite{extra_flags_suffix}"
        )
        env_note = (
            f"\nEDA tool PATH prefix: {nix_path_prefix.strip()}\n"
            f"Include it in every shell command invoking LibreLane/EDA tools.\n"
            if nix_path_prefix else ""
        )

    return f"""You are a digital design automation agent executing the full RTL-to-GDS \
flow for '{project}'.

{design.prompt_description()}

Specifications: {design.specs_description()}
FoM: {design.fom_description()}
Reference: {design.reference_description()}

Design variables (tunable knobs):
{knobs_str}

RTL sources:
{rtl_list}

{tb_section}
Project directory: {project_dir}
LibreLane config: {config_path}

CRITICAL ENVIRONMENT RULE:
Always pass explicit PDK environment variables in every shell command.
Never rely on inherited env vars -- they may point to a different PDK.
Use: PDK_ROOT={pdk_root_str} PDK={librelane_pdk}
{env_note}
WORKFLOW:
Execute these phases in order. Stop and report if any phase fails critically.

Phase 1 - RTL VERIFICATION:
  Run lint on RTL sources using verilator:
    verilator --lint-only -sv <sources>
  If a testbench is available, run simulation.
  Report: warnings, errors, pass/fail.

Phase 2 - SYNTHESIS + PHYSICAL IMPLEMENTATION:
  Run the full LibreLane flow:
    {flow_cmd}
  Monitor the output. If it fails, check the error and report.
  After completion, check:
    - Flow status in the runs/<tag>/ directory
    - Timing reports in STA post-PNR step directories
    - DRC/LVS reports in signoff step directories

Phase 3 - SIGNOFF ANALYSIS:
  After the flow completes, check:
  a) Timing: Look for WNS (Worst Negative Slack) in STA reports.
     WNS >= 0 means timing is closed.
  b) DRC: Check KLayout DRC report. Zero violations = clean.
  c) LVS: Check Netgen LVS report. Must match.
  d) Manufacturability: Check the manufacturability report for
     Antenna/LVS/DRC passed status.

Phase 4 - CONFIG TUNING (if needed):
  If timing is violated or DRC has issues, you can modify the LibreLane
  config and re-run. Safe tunable keys:
    PL_TARGET_DENSITY_PCT (placement density, 30-90%)
    CLOCK_PERIOD (ns)
    GRT_OVERFLOW_ITERS (global routing iterations)
    GRT_ANTENNA_REPAIR_ITERS (antenna fix iterations)
    DRT_OPT_ITERS (detailed routing optimization)
    PDN_VPITCH / PDN_HPITCH (power grid spacing)
  Make ONE change at a time. Re-run and compare.

FINAL REPORT:
When done, report:
1. Lint status (warnings/errors)
2. Sim status (if applicable)
3. Synthesis: cell count
4. Timing: WNS per corner (or at least worst corner)
5. DRC: violation count
6. LVS: match/mismatch
7. Overall verdict: SIGNOFF CLEAN or BLOCKED (with reasons)

Then output "DONE" as your final message."""


def build_from_spec_prompt(
    spec: str,
    work_dir: str,
    pdk_root: str,
    librelane_python: str = "python3",
    pdk_config: PdkConfig | str | None = None,
) -> str:
    """Build a prompt for CC CLI to create a design from a natural language spec.

    The agent writes RTL, generates a LibreLane config, runs the flow,
    and iterates until signoff. This is the "from idea to GDS" path.

    Parameters
    ----------
    spec : str
        Natural language description of the desired circuit.
    work_dir : str
        Working directory where the agent writes all files.
    pdk_root : str
        Explicit PDK_ROOT path.
    librelane_python : str
        Python command that can run ``python3 -m librelane``.
    pdk_config : PdkConfig | str | None
        PDK selection. Falls back to EDA_AGENTS_PDK env var, then to the
        resolve_pdk() default.
    """
    from eda_agents.agents.digital_autoresearch import detect_nix_eda_tool_dirs
    from eda_agents.agents.librelane_config_templates import get_config_template
    from eda_agents.core.pdk import resolve_pdk

    cfg = resolve_pdk(pdk_config)
    config_template, defaults = get_config_template(cfg)
    librelane_pdk = cfg.librelane_pdk_name
    extra_flags = " ".join(cfg.librelane_extra_flags)
    extra_flags_suffix = f" {extra_flags}" if extra_flags else ""

    # Prepend Nix EDA tools to PATH so the agent gets yosys 0.62+ and
    # recent OpenROAD (LibreLane v3 requires those).
    nix_dirs = detect_nix_eda_tool_dirs()
    nix_path_prefix = f"PATH={':'.join(nix_dirs)}:$PATH " if nix_dirs else ""
    nix_env_note = (
        f"\nEDA tool PATH prefix (Nix-provided yosys/openroad/magic/netgen/klayout):\n"
        f"  {nix_path_prefix.strip()}\n"
        f"Include this prefix in every shell command that invokes LibreLane or\n"
        f"any EDA tool. Ubuntu's system yosys is too old for LibreLane v3.\n"
        if nix_dirs else ""
    )

    # Derive a PYTHONPATH pointing at the LibreLane venv's site-packages
    # so yosys's embedded Python can import `click` / `ys_common`. Without
    # this, Yosys.JsonHeader fails at the first pyosys script:
    # ``ModuleNotFoundError: No module named 'click'``.
    import site as _site
    import subprocess as _subprocess
    librelane_sitepackages = ""
    try:
        # ``librelane_python -c "import site; ..."`` returns the venv's
        # site-packages regardless of what the caller's active venv is.
        out = _subprocess.check_output(
            [librelane_python, "-c",
             "import site; print(':'.join(site.getsitepackages()))"],
            text=True, timeout=5,
        ).strip()
        librelane_sitepackages = out
    except Exception:  # noqa: BLE001 — best-effort, fall back to parent venv
        librelane_sitepackages = ":".join(_site.getsitepackages())
    pythonpath_prefix = (
        f"PYTHONPATH={librelane_sitepackages}:$PYTHONPATH "
        if librelane_sitepackages else ""
    )

    return f"""You are a digital design automation agent. Your task is to take a
circuit specification and produce a complete RTL-to-GDS implementation
on {cfg.display_name}, from scratch.

SPECIFICATION:
{spec}

WORKING DIRECTORY: {work_dir}
PDK_ROOT: {pdk_root}
PDK: {librelane_pdk}

CRITICAL ENVIRONMENT RULE:
Always pass explicit PDK vars in every shell command:
  PDK_ROOT={pdk_root} PDK={librelane_pdk}
{nix_env_note}

WORKFLOW:
Execute these phases in order. Fix errors before proceeding.

Phase 1 - WRITE RTL:
  Create the Verilog source file at {work_dir}/src/<design_name>.v
  Requirements:
  - Synthesizable Verilog (no $display, no initial blocks, no delays)
  - Clear module interface with clock and reset if needed
  - Use 'clk' for clock, 'rst_n' for active-low async reset (convention)
  - Include all combinational and sequential logic
  - Module name must match the filename (without .v)

Phase 2 - LINT:
  Run: verilator --lint-only -sv {work_dir}/src/<design_name>.v
  Fix ALL errors and warnings. Re-lint until clean.

Phase 2.5 - WRITE TESTBENCH AND SIMULATE:
  Create {work_dir}/tb/tb_<design_name>.v with a basic testbench:
  - Module name must be `tb`.
  - Instantiate the DUT with the instance name `dut` (e.g.
    `<design_name> dut (...);`). This is not cosmetic: the gate-level
    SDF-annotation stage anchors on `tb.dut`.
  - Apply reset sequence (assert rst_n=0 for 5 cycles, then release)
    BEFORE any correctness check runs.
  - Drive representative input patterns covering normal operation
  - Use $display to print key outputs and verify against expected values
  - End with $display("PASS") or $display("FAIL") and $finish. The
    gate-level sim stages require the "PASS" string to appear — a TB
    that hangs in reset or finishes silently is treated as FAIL.
  - Use `timescale 1ns/1ps

  Gate-level-safe constraints (THIS TESTBENCH ALSO RUNS POST-SYNTH
  AND POST-PnR WITH SDF ANNOTATION; rules below are not optional):
  - No absolute `#N` delays driving DUT inputs. SDF provides timing;
    fixed `#` delays corrupt the annotated model. Use clocked
    stimulus instead (`@(posedge clk)`).
  - Do not compare against a known value during the first post-reset
    clock window; registers start at `x` under gate-level and X-prop
    will make `===`/`!==` checks false-alarm. Wait at least one full
    clock after reset release before the first correctness check.
  - No `initial` blocks that drive DUT inputs without going through
    the reset sequence first.

  Run:
    mkdir -p {work_dir}/tb
    iverilog -o {work_dir}/tb/sim.out -sv \\
      {work_dir}/src/<design_name>.v {work_dir}/tb/tb_<design_name>.v
    vvp {work_dir}/tb/sim.out

  The simulation MUST pass before proceeding. Fix RTL or testbench if it
  fails. This testbench will be reused later to verify that RTL
  optimizations preserve functional correctness AND that the post-synth
  and post-PnR netlists still simulate correctly.

Phase 3 - GENERATE LIBRELANE CONFIG:
  Create {work_dir}/config.yaml with the following template, filling in
  the design-specific fields:

{config_template.format(
    design_name="<YOUR_DESIGN_NAME>",
    verilog_file="../src/<design_name>.v",
    clock_port=defaults["clock_port"],
    clock_period=defaults["clock_period"],
    die_width=defaults["die_width"],
    die_height=defaults["die_height"],
)}

  Replace <YOUR_DESIGN_NAME> with the actual module name.
  Replace <design_name>.v with the actual filename.
  Adjust CLOCK_PORT to match your RTL's clock signal name.
  Adjust CLOCK_PERIOD based on your design's speed needs:
    - Simple combinational: 50-100 ns
    - Small sequential (counters, FSMs): 25-50 ns
    - Complex pipelines: 10-25 ns
  Adjust DIE_AREA based on design complexity:
    - Very small (<100 cells): 100x100 um
    - Small (100-1000 cells): 200x200 um
    - Medium (1k-10k cells): 300x400 um
    - Large (10k+ cells): 500x500+ um

Phase 4 - RUN LIBRELANE FLOW:
  cd {work_dir} && {pythonpath_prefix}{nix_path_prefix}PDK_ROOT={pdk_root} PDK={librelane_pdk} \\
    {librelane_python} -m librelane config.yaml --overwrite{extra_flags_suffix}

  PYTHONPATH prefix (required): Yosys (from Nix) embeds its own CPython
  interpreter which, when LibreLane invokes `yosys -y pyosys/json_header.py`,
  imports `click` and `ys_common`. The embedded Python does not inherit
  the active venv's site-packages automatically -- pass the LibreLane
  venv site-packages via PYTHONPATH so the pyosys scripts can import
  their deps. Without this, Yosys.JsonHeader fails with
  `ModuleNotFoundError: No module named 'click'`.

  IMPORTANT: on IHP SG13G2, some steps (notably Magic.StreamOut and
  Magic.SpiceExtraction) can take 20-60+ minutes even for trivial
  designs -- this is known PDK+Magic slowness, not a bug in your flow.
  When invoking the Bash tool for LibreLane, set a high timeout (e.g.
  timeout=3600000 ms = 1h, or launch with run_in_background and poll)
  and be patient. Do NOT kill or restart the process because it seems
  slow; only restart if it actually errors out.
  This runs synthesis, floorplanning, placement, CTS, routing, and signoff.
  It takes 2-15 minutes depending on design size.

Phase 5 - CHECK RESULTS:
  After the flow completes, check:
  a) Look for the runs/ directory. Find the latest run.
  b) Check for GDS: runs/<tag>/final/gds/<design>.gds
  c) Check timing: look for STA reports with WNS (Worst Negative Slack).
     WNS >= 0 means timing is closed.
  d) Check the manufacturability report if available.

Phase 6 - FIX AND ITERATE (if needed):
  If the flow fails:
  - Synthesis error: fix RTL (usually port/signal issues), re-run
  - Timing violation: increase CLOCK_PERIOD in config.yaml, re-run
  - Routing congestion: increase DIE_AREA, re-run
  - DRC violations: increase PL_TARGET_DENSITY_PCT to 50-60%, re-run
  Make ONE change at a time. Maximum 3 re-run attempts.

FINAL REPORT:
When done, report:
1. Design name and description
2. RTL: module interface (ports), cell count estimate
3. Timing: WNS (or best achieved)
4. DRC: violation count
5. LVS: match/mismatch (if available)
6. GDS path (if generated)
7. Overall verdict: SIGNOFF CLEAN or BLOCKED (with reasons)

Then output "DONE" as your final message."""


def write_librelane_flow_script(dest_dir: str, design: DigitalDesign) -> str:
    """Write a helper script for CC CLI agents to query flow results.

    The script wraps LibreLaneRunner and FlowMetrics for easy CLI access.
    Returns the script path.
    """
    import os
    from pathlib import Path as _Path

    src_dir = str(_Path(__file__).resolve().parents[1])
    script_path = os.path.join(dest_dir, "query_flow.py")

    project_dir = str(design.project_dir())
    config_name = design.librelane_config().name
    pdk_root = str(design.pdk_root()) if design.pdk_root() else ""

    content = f'''#!/usr/bin/env python3
"""Query digital flow results. Used by Claude Code CLI agents.

Usage:
  python3 query_flow.py status              - Check latest flow run status
  python3 query_flow.py metrics             - Extract metrics from latest run
  python3 query_flow.py timing              - Read timing report summary
  python3 query_flow.py modify KEY VALUE    - Modify a config knob
  python3 query_flow.py list-runs           - List available run directories
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, {src_dir!r})

PROJECT_DIR = Path({project_dir!r})
CONFIG_NAME = {config_name!r}
PDK_ROOT = {pdk_root!r}


def find_latest_run():
    """Find the most recent run directory."""
    runs_dir = PROJECT_DIR / "runs"
    if not runs_dir.exists():
        return None
    runs = sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return runs[0] if runs else None


def cmd_status():
    run_dir = find_latest_run()
    if not run_dir:
        print(json.dumps({{"status": "no_runs", "message": "No run directories found"}}))
        return
    # Check for manufacturability report
    mfg = run_dir / "76-misc-reportmanufacturability" / "manufacturability.rpt"
    if not mfg.exists():
        # Try to find any manufacturability report
        mfg_candidates = list(run_dir.glob("*reportmanufacturability*/manufacturability.rpt"))
        mfg = mfg_candidates[0] if mfg_candidates else None
    mfg_text = mfg.read_text() if mfg and mfg.exists() else ""
    print(json.dumps({{
        "run_dir": str(run_dir),
        "run_name": run_dir.name,
        "manufacturability": mfg_text[-1000:] if mfg_text else "not found",
    }}))


def cmd_metrics():
    run_dir = find_latest_run()
    if not run_dir:
        print(json.dumps({{"error": "No run directories found"}}))
        return
    from eda_agents.core.flow_metrics import FlowMetrics
    try:
        fm = FlowMetrics.from_librelane_run_dir(run_dir)
        print(json.dumps({{
            "wns_worst_ns": fm.wns_worst_ns,
            "cell_count": fm.synth_cell_count,
            "die_area_um2": fm.die_area_um2,
            "power_mw": fm.power_total_mw,
            "wire_length_um": fm.wire_length_um,
            "utilization_pct": fm.utilization_pct,
            "drc_count": fm.drc_count,
            "drc_clean": fm.drc_clean,
            "lvs_match": fm.lvs_match,
            "antenna_violations": fm.antenna_violations,
        }}))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))


def cmd_timing():
    run_dir = find_latest_run()
    if not run_dir:
        print(json.dumps({{"error": "No run directories found"}}))
        return
    # Find STA post-PNR directories
    sta_dirs = sorted(run_dir.glob("*stapostpnr*"))
    results = {{}}
    for d in sta_dirs:
        for rpt in d.glob("*.rpt"):
            # Read last 500 chars of each report for summary
            text = rpt.read_text()
            results[f"{{d.name}}/{{rpt.name}}"] = text[-500:]
    if not results:
        print(json.dumps({{"error": "No STA reports found", "run_dir": str(run_dir)}}))
    else:
        print(json.dumps({{"run_dir": str(run_dir), "reports": results}}))


def cmd_modify(key, value):
    from eda_agents.core.librelane_runner import LibreLaneRunner
    runner = LibreLaneRunner(
        project_dir=PROJECT_DIR,
        config_file=CONFIG_NAME,
        pdk_root=PDK_ROOT or None,
    )
    try:
        # Try to parse as number
        try:
            value = int(value)
        except ValueError:
            try:
                value = float(value)
            except ValueError:
                pass
        result = runner.modify_config(key, value)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))


def cmd_list_runs():
    runs_dir = PROJECT_DIR / "runs"
    if not runs_dir.exists():
        print(json.dumps({{"runs": []}}))
        return
    runs = []
    for d in sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir():
            runs.append({{"name": d.name, "path": str(d)}})
    print(json.dumps({{"runs": runs[:10]}}))


def main():
    if len(sys.argv) < 2:
        print(json.dumps({{"error": "Usage: query_flow.py <status|metrics|timing|modify|list-runs>"}}))
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "metrics":
        cmd_metrics()
    elif cmd == "timing":
        cmd_timing()
    elif cmd == "modify" and len(sys.argv) >= 4:
        cmd_modify(sys.argv[2], sys.argv[3])
    elif cmd == "list-runs":
        cmd_list_runs()
    else:
        print(json.dumps({{"error": f"Unknown command: {{cmd}}"}}))
        sys.exit(1)


if __name__ == "__main__":
    main()
'''

    os.makedirs(dest_dir, exist_ok=True)
    with open(script_path, "w") as f:
        f.write(content)
    os.chmod(script_path, 0o755)
    return script_path


def ops_to_task_prompt(agent_id: str, operations: list) -> str:
    """Convert operation tuples to a natural language task prompt."""
    lines = [f"You are {agent_id}. Complete these operations:\n"]
    for i, op in enumerate(operations, 1):
        action = op[0]
        target_type = op[1]
        target_key = op[2]
        content_hint = op[3] if len(op) > 3 else ""

        if action == "write":
            lines.append(
                f"{i}. Write {target_type} entry with key '{target_key}'. "
                f"Topic: {content_hint or target_key}"
            )
        elif action == "decision":
            lines.append(
                f"{i}. Record decision titled '{target_key}': {content_hint}"
            )
        elif action == "sensitivity":
            lines.append(
                f"{i}. Declare sensitivity: if '{target_key}' changes, "
                f"then {content_hint}"
            )

    lines.append(f"\nTotal: {len(operations)} operations. Complete all, then say DONE.")
    return "\n".join(lines)
