"""Google ADK agent templates for digital RTL-to-GDS flows.

ProjectManager is an ADK LlmAgent master with four specialized
sub-agents (VerificationEngineer, SynthesisEngineer, PhysicalDesigner,
SignoffChecker).  The master decides delegation based on flow state --
ADK handles routing automatically.

Architecture mirrors ``TrackDOrchestrator`` (analog):
- One shared ``LibreLaneRunner`` instance across all sub-agents.
- One shared ``ToolEnvironment`` for tool discovery.
- Lazy ADK imports: the module is importable without ``google-adk``.
- Tool factories wrap Phase 2 stage runners and return dicts.
- Reuses DRC/LVS/flow/timing tool factories from ``adk_agents.py``.

Requires: pip install eda-agents[adk]
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from eda_agents.core.digital_design import DigitalDesign
from eda_agents.core.tool_environment import ToolEnvironment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool set constants (snapshot-tested in test_tool_set_stability.py)
# ---------------------------------------------------------------------------

VERIF_TOOLS: frozenset[str] = frozenset({
    "run_rtl_lint",
    "run_rtl_sim",
})

SYNTH_TOOLS: frozenset[str] = frozenset({
    "run_librelane_flow",
    "read_timing_report",
    "check_flow_status",
    "modify_flow_config",
})

PHYSICAL_TOOLS: frozenset[str] = frozenset({
    "run_librelane_flow",
    "run_physical_slice",
    "modify_flow_config",
    "read_timing_report",
    "check_flow_status",
})

SIGNOFF_TOOLS: frozenset[str] = frozenset({
    "run_klayout_drc",
    "read_drc_summary",
    "run_klayout_lvs",
    "modify_flow_config",
    "rerun_flow",
    "check_flow_status",
    "run_precheck",
})


# ---------------------------------------------------------------------------
# New tool factories (wrapping Phase 2 stage runners)
# ---------------------------------------------------------------------------


def _make_rtl_lint_tool(design: DigitalDesign, env: ToolEnvironment):
    """Create a run_rtl_lint FunctionTool."""
    from google.adk.tools import FunctionTool

    def run_rtl_lint() -> dict:
        """Run RTL lint (verilator or yosys) on the design sources.

        Checks for syntax errors, width mismatches, and undriven signals.
        No simulation budget cost.
        """
        from eda_agents.core.stages.rtl_lint_runner import RtlLintRunner

        runner = RtlLintRunner(design, env)
        result = runner.run()
        return {
            "success": result.success,
            "warnings": result.metrics_delta.get("lint_warnings", 0),
            "errors": result.metrics_delta.get("lint_errors", 0),
            "log_tail": result.log_tail[-1000:] if result.log_tail else "",
            "run_time_s": result.run_time_s,
            "error": result.error,
        }

    return FunctionTool(run_rtl_lint)


def _make_rtl_sim_tool(design: DigitalDesign, env: ToolEnvironment):
    """Create a run_rtl_sim FunctionTool."""
    from google.adk.tools import FunctionTool

    def run_rtl_sim() -> dict:
        """Run RTL simulation (cocotb or iverilog) on the design.

        Executes the testbench and reports pass/fail counts.
        """
        from eda_agents.core.stages.rtl_sim_runner import RtlSimRunner

        runner = RtlSimRunner(design, env)
        result = runner.run()
        return {
            "success": result.success,
            "tests": result.metrics_delta.get("sim_tests", 0),
            "passed": result.metrics_delta.get("sim_pass", 0),
            "failed": result.metrics_delta.get("sim_fail", 0),
            "skipped": result.metrics_delta.get("sim_skip", 0),
            "log_tail": result.log_tail[-1000:] if result.log_tail else "",
            "run_time_s": result.run_time_s,
            "error": result.error,
        }

    return FunctionTool(run_rtl_sim)


def _make_physical_slice_tool(runner):
    """Create a run_physical_slice FunctionTool.

    Parameters
    ----------
    runner : LibreLaneRunner
        Configured runner instance.
    """
    from google.adk.tools import FunctionTool

    def run_physical_slice(
        stage: str = "ROUTE",
        tag: str = "",
        overwrite: bool = True,
    ) -> dict:
        """Run physical implementation up to a specified stage via LibreLane.

        Runs the flow from the beginning up to the specified stage.
        Stages: SYNTH, FLOORPLAN, PLACE, CTS, ROUTE, SIGNOFF_DRC,
        SIGNOFF_LVS, SIGNOFF_STA.

        Args:
            stage: Target stage name (e.g., "ROUTE", "PLACE", "CTS").
            tag: Run tag (creates runs/<tag> subdirectory). Empty = auto.
            overwrite: Overwrite existing run directory if tag matches.
        """
        from eda_agents.core.flow_stage import FlowStage
        from eda_agents.core.stages.physical_slice_runner import (
            STAGE_TO_LIBRELANE,
            PhysicalSliceRunner,
        )

        try:
            target = FlowStage[stage]
        except KeyError:
            valid = [s.name for s in STAGE_TO_LIBRELANE]
            return {"error": f"Unknown stage '{stage}'. Valid: {valid}"}

        ps_runner = PhysicalSliceRunner(runner)
        result = ps_runner.run(target, tag=tag, overwrite=overwrite)

        response: dict[str, Any] = {
            "success": result.success,
            "stage": stage,
            "run_time_s": result.run_time_s,
            "error": result.error,
        }

        # Include key metrics if available
        for key in ("synth_cell_count", "wns_worst_ns", "die_area_um2",
                     "wire_length_um", "utilization_pct"):
            val = result.metrics_delta.get(key)
            if val is not None:
                response[key] = val

        if result.artifacts:
            response["artifacts"] = {
                k: str(v) for k, v in result.artifacts.items()
            }

        return response

    return FunctionTool(run_physical_slice)


def _make_precheck_tool(
    design: DigitalDesign,
    env: ToolEnvironment,
    precheck_dir: Path,
):
    """Create a run_precheck FunctionTool."""
    from google.adk.tools import FunctionTool

    def run_precheck(
        gds_path: str,
        top_cell: str = "",
        slot: str = "1x1",
    ) -> dict:
        """Run wafer-space precheck on a final GDS file.

        Validates the GDS for tapeout readiness (antenna, DRC, LVS).
        Requires the final/gds/<design>.gds file, not step-level GDS.

        Args:
            gds_path: Path to the final GDS file.
            top_cell: Top cell name. Auto-detected from filename if empty.
            slot: Slot size ("1x1", "0p5x1", "1x0p5", "0p5x0p5").
        """
        from eda_agents.core.stages.precheck_runner import PrecheckRunner

        pc_runner = PrecheckRunner(
            precheck_dir=precheck_dir,
            env=env,
            pdk_root=str(design.pdk_root()) if design.pdk_root() else None,
            slot=slot,
        )
        result = pc_runner.run(gds_path, top_cell=top_cell)
        return {
            "success": result.success,
            "precheck_errors": result.metrics_delta.get("precheck_errors", 0),
            "log_tail": result.log_tail[-1000:] if result.log_tail else "",
            "run_time_s": result.run_time_s,
            "error": result.error,
        }

    return FunctionTool(run_precheck)


# ---------------------------------------------------------------------------
# Sub-agent factories
# ---------------------------------------------------------------------------


def _make_verification_engineer(
    design: DigitalDesign,
    env: ToolEnvironment,
    model,
):
    """Build a VerificationEngineer sub-agent."""
    from google.adk.agents import LlmAgent

    from eda_agents.agents.digital_adk_prompts import (
        verification_engineer_prompt,
    )

    return LlmAgent(
        name="verification_engineer",
        model=model,
        instruction=verification_engineer_prompt(design),
        tools=[
            _make_rtl_lint_tool(design, env),
            _make_rtl_sim_tool(design, env),
        ],
    )


def _make_synthesis_engineer(design: DigitalDesign, runner, model):
    """Build a SynthesisEngineer sub-agent."""
    from google.adk.agents import LlmAgent

    from eda_agents.agents.adk_agents import (
        _make_drc_fix_tool,
        _make_flow_status_tool,
        _make_flow_tool,
        _make_timing_tool,
    )
    from eda_agents.agents.digital_adk_prompts import (
        synthesis_engineer_prompt,
    )

    return LlmAgent(
        name="synthesis_engineer",
        model=model,
        instruction=synthesis_engineer_prompt(design),
        tools=[
            _make_flow_tool(runner),
            _make_timing_tool(runner),
            _make_flow_status_tool(runner),
            _make_drc_fix_tool(runner),  # modify_flow_config
        ],
    )


def _make_physical_designer(
    design: DigitalDesign,
    runner,
    model,
):
    """Build a PhysicalDesigner sub-agent."""
    from google.adk.agents import LlmAgent

    from eda_agents.agents.adk_agents import (
        _make_drc_fix_tool,
        _make_flow_status_tool,
        _make_flow_tool,
        _make_timing_tool,
    )
    from eda_agents.agents.digital_adk_prompts import physical_designer_prompt

    return LlmAgent(
        name="physical_designer",
        model=model,
        instruction=physical_designer_prompt(design),
        tools=[
            _make_flow_tool(runner),
            _make_physical_slice_tool(runner),
            _make_drc_fix_tool(runner),  # modify_flow_config
            _make_timing_tool(runner),
            _make_flow_status_tool(runner),
        ],
    )


def _make_signoff_checker(
    runner,
    design: DigitalDesign,
    env: ToolEnvironment,
    precheck_dir: Path,
    model,
):
    """Build a SignoffChecker sub-agent."""
    from google.adk.agents import LlmAgent

    from eda_agents.agents.adk_agents import (
        _make_drc_fix_tool,
        _make_drc_summary_tool,
        _make_drc_tool,
        _make_flow_status_tool,
        _make_lvs_tool,
        _make_rerun_tool,
    )
    from eda_agents.agents.digital_adk_prompts import signoff_checker_prompt

    return LlmAgent(
        name="signoff_checker",
        model=model,
        instruction=signoff_checker_prompt(design),
        tools=[
            _make_drc_tool(),
            _make_drc_summary_tool(),
            _make_lvs_tool(),
            _make_drc_fix_tool(runner),  # modify_flow_config
            _make_rerun_tool(runner),
            _make_flow_status_tool(runner),
            _make_precheck_tool(design, env, precheck_dir),
        ],
    )


# ---------------------------------------------------------------------------
# ProjectManager
# ---------------------------------------------------------------------------


class ProjectManager:
    """ADK multi-agent orchestrator for digital RTL-to-GDS flows.

    Creates a master LlmAgent with four specialized sub-agents:
    VerificationEngineer, SynthesisEngineer, PhysicalDesigner,
    SignoffChecker.

    One shared ``LibreLaneRunner`` and ``ToolEnvironment`` instance
    serve all sub-agents.  ADK routes tasks through the master's LLM.

    Supports two backends:
    - ``"adk"`` (default): ADK multi-agent with LlmAgent sub-agents.
    - ``"cc_cli"``: Single Claude Code CLI agent via ``claude --print``.

    Usage::

        from eda_agents.agents.digital_adk_agents import ProjectManager
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign

        # ADK backend (default)
        pm = ProjectManager(
            design=FazyRvHachureDesign(),
            model="openrouter/anthropic/claude-haiku-4.5",
        )
        result = await pm.run(work_dir=Path("./results"))

        # Claude Code CLI backend
        pm = ProjectManager(
            design=FazyRvHachureDesign(),
            backend="cc_cli",
        )
        result = await pm.run(work_dir=Path("./results"))
    """

    def __init__(
        self,
        design: DigitalDesign,
        model: str = "openrouter/anthropic/claude-haiku-4.5",
        worker_model: str | None = None,
        precheck_dir: Path | str | None = None,
        env: ToolEnvironment | None = None,
        backend: str = "adk",
        allow_dangerous: bool = False,
        cli_path: str = "claude",
        max_budget_usd: float | None = None,
    ):
        if backend not in ("adk", "cc_cli"):
            raise ValueError(f"Unknown backend: {backend!r}. Use 'adk' or 'cc_cli'.")
        self.design = design
        self.model = model
        self.worker_model = worker_model or model
        self.env = env
        self.backend = backend
        self.allow_dangerous = allow_dangerous
        self.cli_path = cli_path
        self.max_budget_usd = max_budget_usd

        # Default precheck dir: $EDA_AGENTS_DIGITAL_DESIGNS_DIR/gf180mcu-precheck
        if precheck_dir:
            self.precheck_dir = Path(precheck_dir)
        else:
            import os

            designs_dir = Path(
                os.environ.get(
                    "EDA_AGENTS_DIGITAL_DESIGNS_DIR",
                    "/home/montanares/git",
                )
            )
            self.precheck_dir = designs_dir / "gf180mcu-precheck"

    def _get_env(self) -> ToolEnvironment:
        """Resolve the ToolEnvironment, creating a default if needed."""
        if self.env is not None:
            return self.env
        from eda_agents.core.tool_environment import LocalToolEnvironment

        return LocalToolEnvironment()

    def _get_runner(self):
        """Create the shared LibreLaneRunner."""
        from eda_agents.core.librelane_runner import LibreLaneRunner

        config_path = self.design.librelane_config()
        return LibreLaneRunner(
            project_dir=config_path.parent,
            config_file=config_path.name,
            pdk_root=str(self.design.pdk_root()) if self.design.pdk_root() else None,
        )

    def _build_agents(self, work_dir: Path):
        """Build the full ADK agent hierarchy.

        Returns the master LlmAgent with sub-agents attached.
        """
        from google.adk.agents import LlmAgent

        from eda_agents.agents.adk_agents import _resolve_model
        from eda_agents.agents.digital_adk_prompts import (
            project_manager_prompt,
        )

        runner = self._get_runner()
        env = self._get_env()
        resolved_worker = _resolve_model(self.worker_model)

        sub_agents = [
            _make_verification_engineer(self.design, env, resolved_worker),
            _make_synthesis_engineer(self.design, runner, resolved_worker),
            _make_physical_designer(self.design, runner, resolved_worker),
            _make_signoff_checker(
                runner, self.design, env, self.precheck_dir, resolved_worker,
            ),
        ]

        master = LlmAgent(
            name="project_manager",
            model=_resolve_model(self.model),
            instruction=project_manager_prompt(self.design),
            sub_agents=sub_agents,
        )
        return master

    def dry_run(self) -> dict[str, Any]:
        """Build agents and report the graph without invoking any LLM.

        Returns a dict with agent names, tool sets per sub-agent,
        and design metadata.  Useful for validation and CI smoke tests.
        """
        # Build sub-agents to inspect — need ADK imported
        master = self._build_agents(Path("/tmp/dry_run"))

        sub_info = []
        for agent in master.sub_agents:
            tool_names = []
            if hasattr(agent, "tools") and agent.tools:
                for t in agent.tools:
                    name = getattr(t, "name", None)
                    if name is None and hasattr(t, "_func"):
                        name = t._func.__name__
                    elif name is None and hasattr(t, "func"):
                        name = t.func.__name__
                    tool_names.append(name or "unknown")

            sub_info.append({
                "name": agent.name,
                "tools": tool_names,
                "tool_count": len(tool_names),
            })

        return {
            "design": self.design.project_name(),
            "model": self.model,
            "worker_model": self.worker_model,
            "master_agent": master.name,
            "sub_agents": sub_info,
            "sub_agent_names": [a.name for a in master.sub_agents],
            "precheck_dir": str(self.precheck_dir),
        }

    def _build_initial_prompt(self) -> str:
        """Build the initial user prompt for the project manager."""
        parts = [
            f"Execute the full RTL-to-GDS flow for '{self.design.project_name()}'.\n",
            f"Specifications: {self.design.specs_description()}\n",
            f"FoM: {self.design.fom_description()}\n",
            "Workflow:\n"
            "1. Start with RTL verification (lint + sim if available).\n"
            "2. Run synthesis and check initial timing.\n"
            "3. Run physical implementation (floorplan -> place -> CTS -> route).\n"
            "4. Run signoff (DRC, LVS, precheck).\n"
            "5. Report final FoM and signoff status.\n\n"
            "Report progress at each phase transition. "
            "If any phase fails critically, stop and report.",
        ]
        return "\n".join(parts)

    async def run(
        self,
        work_dir: Path,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Run the full digital RTL-to-GDS flow.

        Parameters
        ----------
        work_dir : Path
            Output directory for results.
        dry_run : bool
            If True, build agents without executing (validation only).

        Dispatches to the ADK multi-agent path or the Claude Code CLI
        path based on ``self.backend``.
        """
        work_dir.mkdir(parents=True, exist_ok=True)

        if dry_run:
            if self.backend == "cc_cli":
                return self._run_cc_cli_dry(work_dir)
            return self.dry_run()

        if self.backend == "cc_cli":
            return await self._run_cc_cli(work_dir)

        return await self._run_adk(work_dir)

    async def _run_cc_cli(self, work_dir: Path) -> dict[str, Any]:
        """Execute via Claude Code CLI backend."""
        from eda_agents.agents.digital_cc_runner import (
            DigitalClaudeCodeRunner,
        )

        runner = DigitalClaudeCodeRunner(
            design=self.design,
            work_dir=work_dir,
            allow_dangerous=self.allow_dangerous,
            cli_path=self.cli_path,
            model=self.model if self.model != "openrouter/anthropic/claude-haiku-4.5" else None,
            max_budget_usd=self.max_budget_usd,
        )
        return await runner.run()

    def _run_cc_cli_dry(self, work_dir: Path) -> dict[str, Any]:
        """Dry run for CC CLI backend."""
        from eda_agents.agents.digital_cc_runner import (
            DigitalClaudeCodeRunner,
        )

        runner = DigitalClaudeCodeRunner(
            design=self.design,
            work_dir=work_dir,
            allow_dangerous=self.allow_dangerous,
            cli_path=self.cli_path,
            model=self.model if self.model != "openrouter/anthropic/claude-haiku-4.5" else None,
            max_budget_usd=self.max_budget_usd,
        )
        return runner.dry_run()

    async def _run_adk(self, work_dir: Path) -> dict[str, Any]:
        """Execute via ADK multi-agent backend."""
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        master = self._build_agents(work_dir)

        adk_runner = InMemoryRunner(
            agent=master, app_name="digital_rtl2gds"
        )
        session = await adk_runner.session_service.create_session(
            app_name="digital_rtl2gds", user_id="user"
        )

        prompt = self._build_initial_prompt()
        result_text = ""

        async for event in adk_runner.run_async(
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
            "design": self.design.project_name(),
            "model": self.model,
            "agent_output": result_text,
        }
