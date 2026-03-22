"""Google ADK agent templates for chipathon Track D.

Reusable agent definitions that participants can adopt for their own
circuit designs. Each agent class wraps ADK LlmAgent with appropriate
tools, prompts, and configuration.

These templates are independent of Context Teleport -- they use
eda-agents infrastructure (SpiceRunner, CircuitTopology, GmIdLookup)
directly.

Requires: pip install eda-agents[adk]
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from eda_agents.core.pdk import PdkConfig, resolve_pdk
from eda_agents.core.topology import CircuitTopology
from eda_agents.agents.adk_prompts import (
    explorer_prompt,
    corner_validator_prompt,
    drc_fixer_prompt,
    orchestrator_prompt,
)

logger = logging.getLogger(__name__)


def _resolve_model(model: str):
    """Resolve model string to ADK model object."""
    if model.startswith("gemini"):
        return model
    from google.adk.models.lite_llm import LiteLlm
    return LiteLlm(model=model)


def _make_simulate_tool(
    topology: CircuitTopology,
    pdk: PdkConfig,
    work_dir: Path,
    budget: int = 30,
):
    """Create a simulate_circuit FunctionTool from a topology.

    Returns an ADK-compatible FunctionTool that wraps the full
    params -> sizing -> netlist -> SPICE -> FoM pipeline.
    """
    from google.adk.tools import FunctionTool

    eval_count = {"n": 0}

    def simulate_circuit(**params: float) -> dict:
        """Run SPICE simulation with given design parameters."""
        eval_count["n"] += 1
        if eval_count["n"] > budget:
            return {"error": f"Budget exhausted ({budget} evals)"}

        try:
            from eda_agents.core.spice_runner import SpiceRunner

            sizing = topology.params_to_sizing(params)
            if "error" in sizing:
                return {"error": sizing["error"]}

            sim_dir = work_dir / f"eval_{eval_count['n']:03d}"
            sim_dir.mkdir(parents=True, exist_ok=True)

            cir = topology.generate_netlist(sizing, sim_dir)
            runner = SpiceRunner(pdk=pdk)
            result = runner.run(cir, sim_dir)

            if not result.success:
                return {
                    "success": False,
                    "error": result.error or "simulation failed",
                    "eval_number": eval_count["n"],
                    "budget_remaining": budget - eval_count["n"],
                }

            fom = topology.compute_fom(result, sizing)
            valid, violations = topology.check_validity(result, sizing)

            return {
                "success": True,
                "measurements": result.measurements,
                "fom": fom,
                "valid": valid,
                "violations": violations,
                "eval_number": eval_count["n"],
                "budget_remaining": budget - eval_count["n"],
            }
        except Exception as e:
            return {"error": str(e), "eval_number": eval_count["n"]}

    # Build the function spec from topology
    spec = topology.tool_spec()
    func_params = spec["function"]["parameters"]
    simulate_circuit.__doc__ = spec["function"]["description"]

    return FunctionTool(simulate_circuit)


def _make_gmid_tool(pdk: PdkConfig):
    """Create a gmid_lookup FunctionTool."""
    from google.adk.tools import FunctionTool

    def gmid_lookup(
        mos_type: str = "nmos",
        L_um: float = 1.0,
        target_gmid: float = 12.0,
        Vds: float = 0.6,
    ) -> dict:
        """Look up transistor performance at a target gm/ID operating point.

        Free tool (no budget cost). Returns intrinsic gain (gm/gds),
        current density (ID/W), and transit frequency (fT).

        Args:
            mos_type: "nmos" or "pmos"
            L_um: Channel length in micrometers
            target_gmid: Target gm/ID in S/A (e.g., 12 for moderate inversion)
            Vds: Drain-source voltage in V
        """
        try:
            from eda_agents.core.gmid_lookup import GmIdLookup
            lut = GmIdLookup(pdk=pdk)
            result = lut.query_at_gmid(target_gmid, mos_type, L_um, Vds)
            if result is None:
                return {"error": f"gm/ID={target_gmid} out of range for {mos_type} at L={L_um}um"}
            return result
        except Exception as e:
            return {"error": str(e)}

    return FunctionTool(gmid_lookup)


class DesignExplorerAgent:
    """ADK agent that explores circuit sizing via SPICE-in-the-loop.

    Participants subclass or configure this with their topology + target specs.

    Usage::

        from eda_agents.agents.adk_agents import DesignExplorerAgent
        from eda_agents.topologies import GF180OTATopology

        agent = DesignExplorerAgent(
            topology=GF180OTATopology(),
            model="gemini-2.0-flash",
            budget=30,
        )
        result = await agent.run(work_dir=Path("./results"))
    """

    def __init__(
        self,
        topology: CircuitTopology,
        model: str = "gemini-2.0-flash",
        budget: int = 30,
        pdk: PdkConfig | str | None = None,
        agent_name: str = "explorer",
    ):
        self.topology = topology
        self.model = model
        self.budget = budget
        self.pdk = resolve_pdk(pdk) if pdk else getattr(topology, "pdk", resolve_pdk(None))
        self.agent_name = agent_name

    async def run(self, work_dir: Path) -> dict[str, Any]:
        """Run the exploration loop and return best design."""
        from google.adk.agents import LlmAgent
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        work_dir.mkdir(parents=True, exist_ok=True)

        tools = [_make_simulate_tool(self.topology, self.pdk, work_dir, self.budget)]

        # Add gmid_lookup if LUT data is available
        if self.topology.auxiliary_tools_description():
            try:
                tools.append(_make_gmid_tool(self.pdk))
            except Exception:
                pass  # LUT not available, skip

        agent = LlmAgent(
            name=self.agent_name,
            model=_resolve_model(self.model),
            instruction=explorer_prompt(self.topology, self.budget),
            tools=tools,
        )

        runner = InMemoryRunner(agent=agent, app_name="eda_explorer")
        session = await runner.session_service.create_session(
            app_name="eda_explorer", user_id="user"
        )

        prompt = (
            f"Optimize the {self.topology.topology_name()} circuit. "
            f"You have {self.budget} SPICE evaluations. Find the highest FoM "
            f"design that meets specs: {self.topology.specs_description()}"
        )

        result_text = ""
        async for event in runner.run_async(
            user_id="user",
            session_id=session.id,
            new_message=types.Content(
                role="user",
                parts=[types.Part(text=prompt)],
            ),
        ):
            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        result_text = part.text

        return {
            "agent": self.agent_name,
            "topology": self.topology.topology_name(),
            "pdk": self.pdk.name,
            "result": result_text,
        }


class CornerValidatorAgent:
    """ADK agent that validates a design across PVT corners.

    Takes a fixed sizing and runs TT/FF/SS corners at -40/27/125C.
    """

    def __init__(
        self,
        topology: CircuitTopology,
        model: str = "gemini-2.0-flash",
        pdk: PdkConfig | str | None = None,
    ):
        self.topology = topology
        self.model = model
        self.pdk = resolve_pdk(pdk) if pdk else getattr(topology, "pdk", resolve_pdk(None))


class DRCFixerAgent:
    """ADK agent that parses DRC reports and suggests fixes."""

    def __init__(self, model: str = "gemini-2.0-flash"):
        self.model = model


class TrackDOrchestrator:
    """Multi-agent workflow: explorer -> validator -> DRC -> precheck.

    Configurable with any PdkConfig + CircuitTopology.
    Participants use this as their top-level entry point.

    Usage::

        from eda_agents.agents.adk_agents import TrackDOrchestrator
        from eda_agents.topologies import GF180OTATopology

        orch = TrackDOrchestrator(
            topology=GF180OTATopology(),
            model="gemini-2.0-flash",
        )
        await orch.run(work_dir=Path("./trackd_results"))
    """

    def __init__(
        self,
        topology: CircuitTopology,
        model: str = "gemini-2.0-flash",
        pdk: PdkConfig | str | None = None,
        n_explorers: int = 2,
        budget_per_explorer: int = 30,
    ):
        self.topology = topology
        self.model = model
        self.pdk = resolve_pdk(pdk) if pdk else getattr(topology, "pdk", resolve_pdk(None))
        self.n_explorers = n_explorers
        self.budget_per_explorer = budget_per_explorer

    async def run(self, work_dir: Path) -> dict[str, Any]:
        """Run the full Track D flow."""
        import asyncio

        work_dir.mkdir(parents=True, exist_ok=True)

        # Phase 1: Parallel exploration
        logger.info("Phase 1: Launching %d explorers", self.n_explorers)
        explorers = [
            DesignExplorerAgent(
                topology=self.topology,
                model=self.model,
                budget=self.budget_per_explorer,
                pdk=self.pdk,
                agent_name=f"explorer_{i}",
            )
            for i in range(self.n_explorers)
        ]

        explorer_results = await asyncio.gather(*[
            e.run(work_dir / f"explorer_{i}")
            for i, e in enumerate(explorers)
        ])

        return {
            "topology": self.topology.topology_name(),
            "pdk": self.pdk.name,
            "n_explorers": self.n_explorers,
            "explorer_results": explorer_results,
        }
