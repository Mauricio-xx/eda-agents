"""Google ADK agent templates for chipathon Track D.

Reusable agent definitions that participants can adopt for their own
circuit designs. Each agent class wraps ADK LlmAgent with appropriate
tools, prompts, and configuration.

These templates are independent of Context Teleport -- they use
eda-agents infrastructure (SpiceRunner, CircuitTopology, GmIdLookup)
directly.

Architecture:
    TrackDOrchestrator is an ADK LlmAgent master with specialized
    sub-agents. The master decides delegation based on flow state.
    Participants customize by changing topology, adding/removing
    sub-agents, or modifying prompts.

Requires: pip install eda-agents[adk]
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from eda_agents.agents.adk_prompts import (
    corner_validator_prompt,
    drc_checker_prompt,
    drc_fixer_prompt,
    explorer_prompt,
    flow_runner_prompt,
    lvs_checker_prompt,
    orchestrator_prompt,
)
from eda_agents.core.pdk import PdkConfig, resolve_pdk
from eda_agents.core.topology import CircuitTopology

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tool factories: SPICE simulation
# ---------------------------------------------------------------------------


def _make_simulate_tool(
    topology: CircuitTopology,
    pdk: PdkConfig,
    work_dir: Path,
    budget: int = 30,
    results_collector: list | None = None,
):
    """Create a simulate_circuit FunctionTool from a topology.

    Returns an ADK-compatible FunctionTool that wraps the full
    params -> sizing -> netlist -> SPICE -> FoM pipeline.

    Builds a proper function signature from the topology's design_space()
    so ADK can advertise the correct parameters to the LLM.

    Parameters
    ----------
    results_collector : list or None
        If provided, each evaluation result dict is appended here
        so the orchestrator can access structured results without
        parsing LLM text.
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
                result_dict = {
                    "success": False,
                    "error": result.error or "simulation failed",
                    "eval_number": eval_count["n"],
                    "budget_remaining": budget - eval_count["n"],
                }
                if results_collector is not None:
                    results_collector.append(result_dict)
                return result_dict

            fom = topology.compute_fom(result, sizing)
            valid, violations = topology.check_validity(result, sizing)

            result_dict = {
                "success": True,
                "params": params,
                "measurements": result.measurements,
                "fom": fom,
                "valid": valid,
                "violations": violations,
                "eval_number": eval_count["n"],
                "budget_remaining": budget - eval_count["n"],
            }
            if results_collector is not None:
                results_collector.append(result_dict)
            return result_dict
        except Exception as e:
            result_dict = {"error": str(e), "eval_number": eval_count["n"]}
            if results_collector is not None:
                results_collector.append(result_dict)
            return result_dict

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


# ---------------------------------------------------------------------------
# Tool factories: DRC / LVS
# ---------------------------------------------------------------------------


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


def _make_lvs_tool():
    """Create a run_klayout_lvs FunctionTool."""
    from google.adk.tools import FunctionTool

    def run_klayout_lvs(
        gds_path: str,
        netlist_path: str,
        top_cell: str = "",
        variant: str = "C",
    ) -> dict:
        """Run KLayout LVS comparing layout GDS against schematic netlist.

        Args:
            gds_path: Path to the GDS layout file.
            netlist_path: Path to the reference SPICE/CDL netlist.
            top_cell: Top cell name. Auto-detected if empty.
            variant: PDK variant (A-D). Default "C".
        """
        from eda_agents.tools.eda_tools import run_klayout_lvs as _run_lvs
        return _run_lvs(
            gds_path=gds_path,
            netlist_path=netlist_path,
            top_cell=top_cell,
            variant=variant,
        )

    return FunctionTool(run_klayout_lvs)


# ---------------------------------------------------------------------------
# Tool factories: LibreLane flow
# ---------------------------------------------------------------------------


def _make_flow_tool(runner):
    """Create a run_librelane_flow FunctionTool.

    Parameters
    ----------
    runner : LibreLaneRunner
        Configured LibreLane runner instance.
    """
    from google.adk.tools import FunctionTool

    def run_librelane_flow(
        tag: str = "",
        frm: str = "",
        to: str = "",
    ) -> dict:
        """Run the LibreLane RTL-to-GDS flow (synthesis + P&R + DRC + LVS).

        This executes the full hardening pipeline. Takes several minutes.

        Args:
            tag: Run tag (creates runs/<tag> subdirectory). Empty = auto.
            frm: Start from this step (e.g., "OpenROAD.DetailedRouting").
            to: Stop after this step.
        """
        result = runner.run_flow(
            tag=tag,
            frm=frm or None,
            to=to or None,
        )
        return {
            "success": result.success,
            "gds_path": result.gds_path,
            "def_path": result.def_path,
            "timing_met": result.timing_met,
            "drc_clean": result.drc_clean,
            "run_dir": result.run_dir,
            "run_time_s": result.run_time_s,
            "error": result.error,
            "summary": result.summary,
        }

    return FunctionTool(run_librelane_flow)


def _make_flow_status_tool(runner):
    """Create a check_flow_status FunctionTool."""
    from google.adk.tools import FunctionTool

    def check_flow_status() -> dict:
        """Check status of the latest LibreLane run.

        Returns run directory, GDS path, timing, and DRC status.
        """
        run_dir = runner.latest_run_dir()
        if not run_dir:
            return {"error": "No run directory found"}

        gds = runner.latest_gds()
        timing = runner.read_timing(run_dir)
        drc = runner.read_drc(run_dir)

        return {
            "run_dir": str(run_dir),
            "gds_path": str(gds) if gds else None,
            "timing": timing,
            "drc_violations": drc.total_violations,
            "drc_clean": drc.clean,
            "design_name": runner.design_name(),
        }

    return FunctionTool(check_flow_status)


def _make_timing_tool(runner):
    """Create a read_timing_report FunctionTool."""
    from google.adk.tools import FunctionTool

    def read_timing_report() -> dict:
        """Parse timing report from the latest LibreLane run.

        Returns worst negative slack (WNS), total negative slack (TNS),
        and whether timing is met.
        """
        return runner.read_timing()

    return FunctionTool(read_timing_report)


def _make_drc_fix_tool(runner):
    """Create a modify_flow_config FunctionTool for DRC fixes."""
    from google.adk.tools import FunctionTool

    def modify_flow_config(key: str, value: str) -> dict:
        """Modify a safe parameter in the LibreLane config.json.

        Use this to fix DRC violations by adjusting flow parameters
        like density, halo, PDN pitch/width, etc.

        Args:
            key: Config key (e.g., "PL_TARGET_DENSITY_PCT", "FP_PDN_VPITCH").
            value: New value as a string. Numbers are auto-parsed.

        Allowed keys: PL_TARGET_DENSITY_PCT, FP_PDN_VPITCH, FP_PDN_HPITCH,
        FP_PDN_VOFFSET, FP_PDN_HOFFSET, FP_MACRO_HORIZONTAL_HALO,
        FP_MACRO_VERTICAL_HALO, GRT_ALLOW_CONGESTION, GRT_OVERFLOW_ITERS,
        GPL_CELL_PADDING, DPL_CELL_PADDING, FP_PDN_VWIDTH, FP_PDN_HWIDTH,
        DIE_AREA, FP_SIZING, and more.
        """
        # Auto-parse numeric values
        parsed_value: Any = value
        try:
            if "." in value:
                parsed_value = float(value)
            else:
                parsed_value = int(value)
        except (ValueError, TypeError):
            pass

        try:
            result = runner.modify_config(key, parsed_value)
            return {"success": True, **result}
        except ValueError as e:
            return {"error": str(e)}

    return FunctionTool(modify_flow_config)


def _make_rerun_tool(runner):
    """Create a rerun_flow FunctionTool for post-fix re-execution."""
    from google.adk.tools import FunctionTool

    def rerun_flow(tag: str = "drc_fix") -> dict:
        """Re-run LibreLane flow after config modifications.

        Use this after modify_flow_config to verify DRC fixes.

        Args:
            tag: Run tag for this re-run. Default "drc_fix".
        """
        result = runner.run_flow(tag=tag, overwrite=True)
        return {
            "success": result.success,
            "gds_path": result.gds_path,
            "drc_clean": result.drc_clean,
            "run_dir": result.run_dir,
            "run_time_s": result.run_time_s,
            "error": result.error,
            "summary": result.summary,
        }

    return FunctionTool(rerun_flow)


# ---------------------------------------------------------------------------
# Standalone agent classes (backward-compatible)
# ---------------------------------------------------------------------------


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

        results_collector: list[dict] = []
        tools = [
            _make_simulate_tool(
                self.topology, self.pdk, work_dir, self.budget,
                results_collector=results_collector,
            )
        ]

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

        # Extract best result from collector
        best = _extract_best(results_collector)

        return {
            "agent": self.agent_name,
            "topology": self.topology.topology_name(),
            "pdk": self.pdk.name,
            "result": result_text,
            "all_evals": results_collector,
            "best": best,
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
        """Validate a design across PVT corners."""
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
        """Run DRC and analyze violations."""
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


# ---------------------------------------------------------------------------
# Multi-agent Track D orchestrator
# ---------------------------------------------------------------------------


class TrackDOrchestrator:
    """ADK multi-agent orchestrator for Track D flow.

    Creates a master LlmAgent with specialized sub-agents. The master
    decides task delegation based on flow state. ADK handles routing
    automatically -- the LLM decides the workflow, not hardcoded Python.

    Supports three exploration modes:
      - "adk" (default): ADK sub-agents do exploration via tool calling
      - "autoresearch": Tight greedy loop, no tool calling, LLM proposes JSON
      - "hybrid": Autoresearch explores first, then ADK refines + runs flow

    Participants can customize by:
      - Changing topology (CircuitTopology subclass)
      - Adding/removing sub-agents
      - Modifying prompts (adk_prompts.py)
      - Adding tools to any agent
      - Switching exploration_mode

    Usage::

        from eda_agents.agents.adk_agents import TrackDOrchestrator
        from eda_agents.topologies import GF180OTATopology
        from pathlib import Path

        orch = TrackDOrchestrator(
            project_dir=Path("data/gf180-template"),
            topology=GF180OTATopology(),
            model="gemini-2.0-flash",
            exploration_mode="hybrid",
        )
        result = await orch.run(work_dir=Path("./trackd_results"))
    """

    EXPLORATION_MODES = ("adk", "autoresearch", "hybrid")

    def __init__(
        self,
        project_dir: Path | str,
        topology: CircuitTopology | None = None,
        model: str = "gemini-2.0-flash",
        worker_model: str | None = None,
        pdk: PdkConfig | str | None = None,
        n_explorers: int = 2,
        budget_per_explorer: int = 30,
        max_drc_iterations: int = 3,
        exploration_mode: str = "adk",
    ):
        from eda_agents.core.librelane_runner import LibreLaneRunner

        if exploration_mode not in self.EXPLORATION_MODES:
            raise ValueError(
                f"exploration_mode must be one of {self.EXPLORATION_MODES}, "
                f"got '{exploration_mode}'"
            )

        self.project_dir = Path(project_dir)
        self.topology = topology
        self.model = model
        self.worker_model = worker_model or model
        self.n_explorers = n_explorers
        self.budget_per_explorer = budget_per_explorer
        self.max_drc_iterations = max_drc_iterations
        self.exploration_mode = exploration_mode

        if topology:
            self.pdk = resolve_pdk(pdk) if pdk else getattr(
                topology, "pdk", resolve_pdk(None)
            )
        else:
            self.pdk = resolve_pdk(pdk) if pdk else resolve_pdk("gf180mcu")

        self.runner = LibreLaneRunner(self.project_dir)

        # Shared results collector for structured output
        self._results_collector: list[dict] = []

    def _make_explorer_agent(self, work_dir: Path, agent_id: int = 0):
        """Build a SizingExplorer sub-agent."""
        from google.adk.agents import LlmAgent

        tools = [
            _make_simulate_tool(
                self.topology, self.pdk, work_dir / f"explorer_{agent_id}",
                self.budget_per_explorer,
                results_collector=self._results_collector,
            )
        ]

        if self.topology.auxiliary_tools_description():
            try:
                tools.append(_make_gmid_tool(self.pdk))
            except Exception:
                pass

        return LlmAgent(
            name=f"sizing_explorer_{agent_id}",
            model=_resolve_model(self.worker_model),
            instruction=explorer_prompt(self.topology, self.budget_per_explorer),
            tools=tools,
        )

    def _make_validator_agent(self, work_dir: Path):
        """Build a CornerValidator sub-agent."""
        from google.adk.agents import LlmAgent

        budget = 12  # 9 corners + margin
        return LlmAgent(
            name="corner_validator",
            model=_resolve_model(self.worker_model),
            instruction=corner_validator_prompt(self.topology),
            tools=[
                _make_simulate_tool(
                    self.topology, self.pdk, work_dir / "corner_val",
                    budget,
                ),
            ],
        )

    def _make_flow_agent(self):
        """Build a FlowRunner sub-agent."""
        from google.adk.agents import LlmAgent

        return LlmAgent(
            name="flow_runner",
            model=_resolve_model(self.worker_model),
            instruction=flow_runner_prompt(self.project_dir),
            tools=[
                _make_flow_tool(self.runner),
                _make_flow_status_tool(self.runner),
                _make_timing_tool(self.runner),
            ],
        )

    def _make_drc_checker_agent(self):
        """Build a DRCChecker sub-agent."""
        from google.adk.agents import LlmAgent

        return LlmAgent(
            name="drc_checker",
            model=_resolve_model(self.worker_model),
            instruction=drc_checker_prompt(),
            tools=[
                _make_drc_tool(),
                _make_drc_summary_tool(),
            ],
        )

    def _make_drc_fixer_agent(self):
        """Build a DRCFixer sub-agent with config modification + rerun tools."""
        from google.adk.agents import LlmAgent

        return LlmAgent(
            name="drc_fixer",
            model=_resolve_model(self.worker_model),
            instruction=drc_fixer_prompt(max_iterations=self.max_drc_iterations),
            tools=[
                _make_drc_fix_tool(self.runner),
                _make_rerun_tool(self.runner),
                _make_drc_tool(),
                _make_drc_summary_tool(),
            ],
        )

    def _make_lvs_agent(self):
        """Build a LVSChecker sub-agent."""
        from google.adk.agents import LlmAgent

        return LlmAgent(
            name="lvs_checker",
            model=_resolve_model(self.worker_model),
            instruction=lvs_checker_prompt(),
            tools=[_make_lvs_tool()],
        )

    def _build_agents(self, work_dir: Path):
        """Build the full ADK agent hierarchy."""
        from google.adk.agents import LlmAgent

        sub_agents = []

        # Analog sizing (optional, only if topology provided)
        if self.topology:
            for i in range(self.n_explorers):
                sub_agents.append(self._make_explorer_agent(work_dir, i))
            sub_agents.append(self._make_validator_agent(work_dir))

        # Hardening (always)
        sub_agents.append(self._make_flow_agent())

        # Verification (always)
        sub_agents.append(self._make_drc_checker_agent())
        sub_agents.append(self._make_drc_fixer_agent())
        sub_agents.append(self._make_lvs_agent())

        master = LlmAgent(
            name="track_d_orchestrator",
            model=_resolve_model(self.model),
            instruction=orchestrator_prompt(
                self.topology, self.runner, self.max_drc_iterations
            ),
            sub_agents=sub_agents,
        )
        return master

    def _build_initial_prompt(self) -> str:
        """Build the initial user prompt for the orchestrator."""
        design_name = self.runner.design_name() or "unknown"
        parts = [
            f"Execute the Track D flow for design '{design_name}' "
            f"at {self.project_dir}.\n"
        ]

        if self.topology:
            parts.append(
                f"The design includes an analog block: {self.topology.topology_name()}. "
                f"Start with analog sizing exploration ({self.n_explorers} explorers, "
                f"{self.budget_per_explorer} SPICE evals each), then validate corners.\n"
            )

        parts.append(
            "After analog work (if any), run the LibreLane hardening flow. "
            "Then check DRC -- if violations are found, use the DRC fixer "
            f"to modify config and re-run (up to {self.max_drc_iterations} iterations). "
            "Finally, run LVS to verify layout matches schematic.\n"
        )
        parts.append(
            "Report results at each stage. If any critical stage fails "
            "(DRC cannot be fixed, LVS mismatch), report the failure and stop."
        )

        return "\n".join(parts)

    async def run(self, work_dir: Path, dry_run: bool = False) -> dict[str, Any]:
        """Run the full Track D flow via ADK agent loop.

        Parameters
        ----------
        work_dir : Path
            Output directory for all results.
        dry_run : bool
            If True, only build agents without executing (for validation).

        Returns
        -------
        dict
            Keys: topology, pdk, project_dir, phases (collected results),
            agent_output (final LLM text).
        """
        work_dir.mkdir(parents=True, exist_ok=True)

        if self.exploration_mode == "autoresearch":
            return await self._run_autoresearch(work_dir, dry_run)
        elif self.exploration_mode == "hybrid":
            return await self._run_hybrid(work_dir, dry_run)
        else:
            return await self._run_adk(work_dir, dry_run)

    async def _run_adk(self, work_dir: Path, dry_run: bool) -> dict[str, Any]:
        """Original ADK-only exploration mode."""
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        master = self._build_agents(work_dir)

        if dry_run:
            sub_names = [a.name for a in master.sub_agents]
            return {
                "topology": self.topology.topology_name() if self.topology else None,
                "pdk": self.pdk.name,
                "project_dir": str(self.project_dir),
                "exploration_mode": "adk",
                "dry_run": True,
                "master_agent": master.name,
                "sub_agents": sub_names,
            }

        runner = InMemoryRunner(agent=master, app_name="trackd")
        session = await runner.session_service.create_session(
            app_name="trackd", user_id="user"
        )

        prompt = self._build_initial_prompt()

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

        best = _extract_best(self._results_collector)

        return {
            "topology": self.topology.topology_name() if self.topology else None,
            "pdk": self.pdk.name,
            "project_dir": str(self.project_dir),
            "exploration_mode": "adk",
            "n_explorers": self.n_explorers if self.topology else 0,
            "agent_output": result_text,
            "spice_evals": self._results_collector,
            "best_design": best,
        }

    async def _run_autoresearch(
        self, work_dir: Path, dry_run: bool
    ) -> dict[str, Any]:
        """Pure autoresearch exploration -- no ADK sub-agents."""
        from eda_agents.agents.autoresearch_runner import AutoresearchRunner

        total_budget = self.budget_per_explorer * self.n_explorers

        if dry_run:
            return {
                "topology": self.topology.topology_name() if self.topology else None,
                "pdk": self.pdk.name,
                "project_dir": str(self.project_dir),
                "exploration_mode": "autoresearch",
                "dry_run": True,
                "budget": total_budget,
                "model": self.worker_model,
            }

        ar = AutoresearchRunner(
            topology=self.topology,
            model=self.worker_model,
            budget=total_budget,
            pdk=self.pdk,
        )
        ar_result = await ar.run(work_dir / "exploration")

        return {
            "topology": self.topology.topology_name() if self.topology else None,
            "pdk": self.pdk.name,
            "project_dir": str(self.project_dir),
            "exploration_mode": "autoresearch",
            "autoresearch_result": ar_result,
            "best_design": {
                "params": ar_result.best_params,
                "fom": ar_result.best_fom,
                "valid": ar_result.best_valid,
            } if ar_result.best_valid else None,
            "spice_evals": ar_result.history,
        }

    async def _run_hybrid(self, work_dir: Path, dry_run: bool) -> dict[str, Any]:
        """Autoresearch explores, ADK refines top-N across corners + runs flow."""
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        from eda_agents.agents.autoresearch_runner import AutoresearchRunner

        total_budget = self.budget_per_explorer * self.n_explorers

        if dry_run:
            master = self._build_agents(work_dir)
            sub_names = [a.name for a in master.sub_agents]
            return {
                "topology": self.topology.topology_name() if self.topology else None,
                "pdk": self.pdk.name,
                "project_dir": str(self.project_dir),
                "exploration_mode": "hybrid",
                "dry_run": True,
                "autoresearch_budget": total_budget,
                "master_agent": master.name,
                "sub_agents": sub_names,
            }

        # Phase 1: Autoresearch finds top designs
        logger.info("Hybrid mode: starting autoresearch exploration (%d evals)", total_budget)
        ar = AutoresearchRunner(
            topology=self.topology,
            model=self.worker_model,
            budget=total_budget,
            pdk=self.pdk,
        )
        ar_result = await ar.run(work_dir / "exploration")
        logger.info(
            "Autoresearch complete: %d evals, %d kept, best FoM=%.2e",
            ar_result.total_evals, ar_result.kept, ar_result.best_fom,
        )

        if not ar_result.top_n:
            return {
                "topology": self.topology.topology_name() if self.topology else None,
                "pdk": self.pdk.name,
                "project_dir": str(self.project_dir),
                "exploration_mode": "hybrid",
                "autoresearch_result": ar_result,
                "best_design": None,
                "agent_output": "Autoresearch found no valid designs. ADK phase skipped.",
            }

        # Phase 2: ADK validates and runs flow on best design
        # Build ADK agents without explorers (we already have designs)
        master = self._build_agents(work_dir)

        runner = InMemoryRunner(agent=master, app_name="trackd_hybrid")
        session = await runner.session_service.create_session(
            app_name="trackd_hybrid", user_id="user"
        )

        top_designs = json.dumps(
            [{"params": d["params"], "fom": d["fom"]} for d in ar_result.top_n],
            indent=2,
        )
        prompt = (
            f"Autoresearch exploration has found these top designs:\n"
            f"```json\n{top_designs}\n```\n\n"
            f"Skip the exploration phase. Instead:\n"
            f"1. Validate the best design across PVT corners using corner_validator\n"
            f"2. Run the LibreLane hardening flow using flow_runner\n"
            f"3. Check DRC and fix if needed using drc_checker/drc_fixer\n"
            f"4. Run LVS verification using lvs_checker\n\n"
            f"Report results at each stage."
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
            "topology": self.topology.topology_name() if self.topology else None,
            "pdk": self.pdk.name,
            "project_dir": str(self.project_dir),
            "exploration_mode": "hybrid",
            "autoresearch_result": ar_result,
            "agent_output": result_text,
            "spice_evals": ar_result.history + self._results_collector,
            "best_design": {
                "params": ar_result.best_params,
                "fom": ar_result.best_fom,
                "valid": ar_result.best_valid,
            },
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_best(results: list[dict]) -> dict | None:
    """Extract the best valid design from a results collector list."""
    valid = [r for r in results if r.get("valid") and r.get("success")]
    if not valid:
        return None
    return max(valid, key=lambda r: r.get("fom", 0))
