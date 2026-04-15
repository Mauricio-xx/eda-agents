"""Topology-driven prompt templates for ADK agents.

This module is a compatibility shim: prompt bodies now live in
``eda_agents.skills`` (``analog.*`` and ``flow.*``). Every public
function here delegates to the corresponding skill via ``get_skill``.

Keeping the original callables preserves callers (tests, examples,
ADK wiring) without churn. New code should call ``get_skill(...)``
directly.
"""

from __future__ import annotations

from pathlib import Path

from eda_agents.core.topology import CircuitTopology
from eda_agents.skills import get_skill


def explorer_prompt(topology: CircuitTopology, budget: int = 30) -> str:
    """System prompt for a design-exploration agent. Delegates to ``analog.explorer``."""
    return get_skill("analog.explorer").render(topology, budget)


def corner_validator_prompt(topology: CircuitTopology) -> str:
    """System prompt for a PVT corner validator. Delegates to ``analog.corner_validator``."""
    return get_skill("analog.corner_validator").render(topology)


def flow_runner_prompt(project_dir: Path | str) -> str:
    """System prompt for the FlowRunner. Delegates to ``flow.runner``."""
    return get_skill("flow.runner").render(project_dir)


def drc_checker_prompt() -> str:
    """System prompt for the DRCChecker. Delegates to ``flow.drc_checker``."""
    return get_skill("flow.drc_checker").render()


def drc_fixer_prompt(max_iterations: int = 3) -> str:
    """System prompt for the DRCFixer. Delegates to ``flow.drc_fixer``."""
    return get_skill("flow.drc_fixer").render(max_iterations)


def lvs_checker_prompt() -> str:
    """System prompt for the LVSChecker. Delegates to ``flow.lvs_checker``."""
    return get_skill("flow.lvs_checker").render()


def orchestrator_prompt(
    topology: CircuitTopology | None = None,
    runner=None,
    max_drc_iterations: int = 3,
) -> str:
    """System prompt for the Track D orchestrator. Delegates to ``analog.orchestrator``."""
    return get_skill("analog.orchestrator").render(
        topology, runner, max_drc_iterations
    )


def make_tool_description(topology: CircuitTopology) -> dict:
    """Build an OpenAI/ADK function-calling tool spec from topology."""
    return topology.tool_spec()
