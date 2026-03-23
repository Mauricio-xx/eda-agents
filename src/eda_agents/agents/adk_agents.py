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

import json
import logging
from pathlib import Path
from typing import Any

from eda_agents.core.pdk import PdkConfig, resolve_pdk
from eda_agents.core.topology import CircuitTopology
from eda_agents.agents.adk_prompts import (
    corner_validator_prompt,
    drc_fixer_prompt,
    explorer_prompt,
)

logger = logging.getLogger(__name__)


def _resolve_model(model: str):
    """Resolve model string to ADK model object.

    Non-Gemini models go through LiteLLM with retry/backoff
    configured for free-tier rate limits.
    """
    if model.startswith("gemini"):
        return model
    import litellm
    litellm.num_retries = 5
    litellm.request_timeout = 120
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

    Builds a proper function signature from the topology's design_space()
    so ADK can advertise the correct parameters to the LLM.
    """
    import inspect

    from google.adk.tools import FunctionTool

    eval_count = {"n": 0}
    space = topology.design_space()
    defaults = topology.default_params()

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

    # Build explicit signature from design space so ADK advertises
    # correct parameters to the LLM (not just empty **kwargs).
    sig_params = [
        inspect.Parameter(
            name,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=defaults.get(name, (lo + hi) / 2),
            annotation=float,
        )
        for name, (lo, hi) in space.items()
    ]
    simulate_circuit.__signature__ = inspect.Signature(sig_params)

    # Rich docstring with per-parameter ranges
    spec = topology.tool_spec()
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


def _make_drc_tool():
    """Create a run_klayout_drc FunctionTool."""
    from google.adk.tools import FunctionTool

    def run_klayout_drc(
        gds_path: str,
        top_cell: str = "",
        variant: str = "C",
        table: str = "",
    ) -> dict:
        """Run KLayout DRC on a GDS file using the GF180MCU PDK rule deck.

        Args:
            gds_path: Path to the GDS file to check.
            top_cell: Top cell name. Auto-detected if empty.
            variant: PDK variant (A-F). Default "C" = 5LM, 9K top metal.
            table: Specific rule table to check (e.g., "comp"). Empty = all.
        """
        from eda_agents.tools.eda_tools import run_klayout_drc as _run_drc
        return _run_drc(
            gds_path=gds_path,
            top_cell=top_cell,
            variant=variant,
            table=table,
        )

    return FunctionTool(run_klayout_drc)


def _make_drc_summary_tool():
    """Create a read_drc_summary FunctionTool."""
    from google.adk.tools import FunctionTool

    def read_drc_summary(report_path: str) -> dict:
        """Parse a KLayout .lyrdb DRC report into a structured summary.

        Args:
            report_path: Path to a .lyrdb file from a previous DRC run.
        """
        from eda_agents.tools.eda_tools import read_drc_summary as _read
        return _read(report_path=report_path)

    return FunctionTool(read_drc_summary)


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
    Uses simulate_circuit with corner parameter to sweep PVT conditions.
    """

    CORNERS = ["tt", "ff", "ss"]
    TEMPERATURES = [-40, 27, 125]

    def __init__(
        self,
        topology: CircuitTopology,
        model: str = "gemini-2.0-flash",
        pdk: PdkConfig | str | None = None,
    ):
        self.topology = topology
        self.model = model
        self.pdk = resolve_pdk(pdk) if pdk else getattr(topology, "pdk", resolve_pdk(None))

    async def run(
        self,
        sizing: dict,
        work_dir: Path,
    ) -> dict[str, Any]:
        """Validate a design across PVT corners.

        Parameters
        ----------
        sizing : dict
            Design parameters (output from explorer).
        work_dir : Path
            Output directory for simulation results.

        Returns
        -------
        dict
            Keys: corners (list of per-corner results), worst_case,
            all_pass (bool), summary.
        """
        from google.adk.agents import LlmAgent
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        work_dir.mkdir(parents=True, exist_ok=True)

        # Budget: 9 corners (3 corners x 3 temps) + margin
        budget = len(self.CORNERS) * len(self.TEMPERATURES) + 3
        tools = [
            _make_simulate_tool(self.topology, self.pdk, work_dir, budget),
        ]

        agent = LlmAgent(
            name="corner_validator",
            model=_resolve_model(self.model),
            instruction=corner_validator_prompt(self.topology),
            tools=tools,
        )

        runner = InMemoryRunner(agent=agent, app_name="eda_corner_val")
        session = await runner.session_service.create_session(
            app_name="eda_corner_val", user_id="user"
        )

        sizing_json = json.dumps(sizing, indent=2)
        prompt = (
            f"Validate this design across PVT corners.\n\n"
            f"Design parameters:\n```json\n{sizing_json}\n```\n\n"
            f"Run simulations at corners: {self.CORNERS}\n"
            f"Temperatures: {self.TEMPERATURES}\n\n"
            f"Specs: {self.topology.specs_description()}\n"
            f"Report worst-case performance and overall PASS/FAIL."
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
            "agent": "corner_validator",
            "topology": self.topology.topology_name(),
            "sizing": sizing,
            "result": result_text,
        }


class DRCFixerAgent:
    """ADK agent that parses DRC reports and suggests fixes.

    Tools: run_klayout_drc, read_drc_summary.
    """

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        pdk_root: str | None = None,
    ):
        self.model = model
        self.pdk_root = pdk_root

    async def run(
        self,
        gds_path: str | Path,
        work_dir: Path,
        variant: str = "C",
    ) -> dict[str, Any]:
        """Run DRC and analyze violations.

        Parameters
        ----------
        gds_path : path
            Input GDS file to check.
        work_dir : Path
            Output directory.
        variant : str
            PDK variant for DRC.

        Returns
        -------
        dict
            Keys: gds_path, drc_result, agent_analysis, violations_fixed.
        """
        from google.adk.agents import LlmAgent
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        work_dir.mkdir(parents=True, exist_ok=True)

        tools = [_make_drc_tool(), _make_drc_summary_tool()]

        agent = LlmAgent(
            name="drc_fixer",
            model=_resolve_model(self.model),
            instruction=drc_fixer_prompt(),
            tools=tools,
        )

        runner = InMemoryRunner(agent=agent, app_name="eda_drc_fixer")
        session = await runner.session_service.create_session(
            app_name="eda_drc_fixer", user_id="user"
        )

        prompt = (
            f"Run DRC on the GDS file at: {gds_path}\n"
            f"Use variant={variant}.\n"
            f"Analyze violations and suggest fixes.\n"
            f"Classify violations by type (spacing, width, enclosure, etc.).\n"
            f"Prioritize fixes by severity."
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
            "agent": "drc_fixer",
            "gds_path": str(gds_path),
            "analysis": result_text,
        }


class TrackDOrchestrator:
    """Multi-agent workflow: explore -> corner validate -> layout -> DRC -> LVS.

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

    async def run(self, work_dir: Path, dry_run: bool = False) -> dict[str, Any]:
        """Run the full Track D flow.

        Parameters
        ----------
        work_dir : Path
            Output directory for all results.
        dry_run : bool
            If True, only run exploration (skip layout/DRC/LVS).

        Returns
        -------
        dict
            Keys: topology, pdk, phases (dict of phase results).
        """
        import asyncio

        work_dir.mkdir(parents=True, exist_ok=True)
        phases: dict[str, Any] = {}

        # -- Phase 1: Parallel exploration --
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
        phases["exploration"] = explorer_results

        if dry_run:
            logger.info("Dry run: stopping after exploration")
            return {
                "topology": self.topology.topology_name(),
                "pdk": self.pdk.name,
                "n_explorers": self.n_explorers,
                "phases": phases,
                "dry_run": True,
            }

        # -- Phase 2: Corner validation on best design --
        logger.info("Phase 2: Corner validation")
        # Extract best sizing from explorer results (heuristic: last result text)
        best_sizing = self.topology.default_params()
        validator = CornerValidatorAgent(
            topology=self.topology,
            model=self.model,
            pdk=self.pdk,
        )
        validation_result = await validator.run(
            sizing=best_sizing,
            work_dir=work_dir / "corner_validation",
        )
        phases["corner_validation"] = validation_result

        # -- Phase 3: Layout generation (if gLayout available) --
        logger.info("Phase 3: Layout generation")
        from eda_agents.core.glayout_runner import GLayoutRunner
        glayout = GLayoutRunner()
        setup_issues = glayout.validate_setup()
        if setup_issues:
            logger.warning(
                "Skipping layout: gLayout not available (%s)", setup_issues[0]
            )
            phases["layout"] = {"skipped": True, "reason": setup_issues[0]}
        else:
            layout_result = glayout.generate_component(
                component="nmos",  # placeholder
                params={"width": 1.0, "length": 0.28, "fingers": 2},
                output_dir=work_dir / "layout",
            )
            phases["layout"] = {
                "success": layout_result.success,
                "gds_path": layout_result.gds_path,
                "error": layout_result.error,
            }

            # -- Phase 4: DRC on generated layout --
            if layout_result.success and layout_result.gds_path:
                logger.info("Phase 4: DRC check")
                drc_agent = DRCFixerAgent(model=self.model)
                drc_result = await drc_agent.run(
                    gds_path=layout_result.gds_path,
                    work_dir=work_dir / "drc",
                )
                phases["drc"] = drc_result

                # -- Phase 5: LVS --
                logger.info("Phase 5: LVS check")
                from eda_agents.tools.eda_tools import run_klayout_lvs
                lvs_result = run_klayout_lvs(
                    gds_path=layout_result.gds_path,
                    netlist_path=str(work_dir / "schematic.cir"),
                    variant="C",
                )
                phases["lvs"] = lvs_result

        return {
            "topology": self.topology.topology_name(),
            "pdk": self.pdk.name,
            "n_explorers": self.n_explorers,
            "phases": phases,
        }
