"""ADK agent experiment runner (Level 4 evaluation).

Uses Google ADK's LlmAgent + MCPToolset to run autonomous agents that
coordinate via Context Teleport's MCP server. ADK manages the agent loop,
tool dispatch, and MCP lifecycle.

Context Teleport coordination is optional: when not installed, agents run
without MCP coordination tools.

Requires: pip install google-adk
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from eda_agents.agents.tool_defs import (
    BASE_TOOLS,
    COORDINATION_TOOLS,
    STRATEGY_TOOLS,
    SYSTEM_PROMPT,
    WRITE_TOOLS,
    ops_to_task_prompt,
)
from eda_agents.agents.scenarios import Scenario, ScenarioResult

logger = logging.getLogger(__name__)


def _resolve_model(model: str):
    """Resolve model string to ADK model object.

    Gemini models are used as-is (native Google AI).
    Others are wrapped in LiteLlm for OpenRouter/other providers.
    """
    if model.startswith("gemini"):
        return model

    from google.adk.models.lite_llm import LiteLlm

    return LiteLlm(model=model)


class ADKExperimentRunner:
    """Run experiments with real LLM agents via Google ADK framework."""

    STRATEGIES = ("none", "intents_only", "reservations", "full_rep")

    def __init__(
        self,
        scenario: Scenario,
        n_agents: int,
        strategy: str = "none",
        model: str = "gemini-2.5-flash",
        store_path: Path | None = None,
        seed: int = 42,
        max_turns: int = 80,
    ):
        if strategy not in self.STRATEGIES:
            raise ValueError(f"Invalid strategy: {strategy}. Use: {self.STRATEGIES}")

        try:
            import google.adk  # noqa: F401
        except ImportError:
            raise ImportError(
                "google-adk package required for ADK experiments. "
                'Install with: pip install google-adk'
            )

        self.scenario = scenario
        self.n_agents = n_agents
        self.strategy = strategy
        self.model = model
        self.store_path = store_path
        self.seed = seed
        self.max_turns = max_turns
        self.experiment_id = str(uuid.uuid4())[:8]

    async def run(self) -> ScenarioResult:
        """Execute the experiment with ADK agents."""
        import tempfile

        import git

        # CT coordination is optional
        try:
            from context_teleport.core.store import ContextStore
            HAS_COORDINATION = True
        except ImportError:
            HAS_COORDINATION = False

        work_dir = self.store_path or Path(tempfile.mkdtemp(prefix="ctx-adk-exp-"))

        if not (work_dir / ".git").exists():
            repo = git.Repo.init(work_dir)
            readme = work_dir / "README.md"
            readme.write_text("# ADK Experiment\n")
            repo.index.add(["README.md"])
            repo.index.commit("initial")

        store = None
        if HAS_COORDINATION:
            store = ContextStore(work_dir)
            if not store.initialized:
                store.init(project_name=f"adk-experiment-{self.experiment_id}")

        tasks = self.scenario.generate_tasks(self.n_agents, seed=self.seed)
        start = time.perf_counter()

        agent_results = await asyncio.gather(
            *(
                self._run_agent(work_dir, task, self.strategy, HAS_COORDINATION)
                for task in tasks
            ),
            return_exceptions=True,
        )

        total_writes = 0
        conflicts = 0
        contention = 0
        sensitivity_triggers = 0
        coord_overhead_ms = 0.0
        total_input_tokens = 0
        total_output_tokens = 0
        total_tool_calls = 0

        for i, result in enumerate(agent_results):
            if isinstance(result, Exception):
                logger.warning("Agent %d failed: %s", i, result)
                continue
            total_writes += result["writes"]
            conflicts += result["conflicts"]
            contention += result["contention"]
            sensitivity_triggers += result["sensitivity_triggers"]
            coord_overhead_ms += result["coord_overhead_ms"]
            total_input_tokens += result["input_tokens"]
            total_output_tokens += result["output_tokens"]
            total_tool_calls += result["tool_calls"]

        duration = time.perf_counter() - start
        conflict_rate = conflicts / max(total_writes, 1) * 100

        return ScenarioResult(
            experiment_id=self.experiment_id,
            scenario=self.scenario.name,
            strategy=self.strategy,
            agent_count=self.n_agents,
            duration_seconds=round(duration, 4),
            total_writes=total_writes,
            conflicts=conflicts,
            conflict_rate=round(conflict_rate, 2),
            contention_events=contention,
            sensitivity_triggers=sensitivity_triggers,
            coordination_overhead_ms=round(coord_overhead_ms, 2),
            metadata={
                "seed": self.seed,
                "transport": "adk",
                "model": self.model,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "tool_calls": total_tool_calls,
                "expected_conflict_level": self.scenario.expected_conflict_level(),
            },
        )

    async def _run_agent(
        self,
        cwd: Path,
        task: dict[str, Any],
        strategy: str,
        has_coordination: bool,
    ) -> dict[str, int | float]:
        """Run a single ADK agent with its own MCP toolset."""
        from google.adk import Agent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        agent_id = task["agent_id"]
        # ADK requires valid Python identifiers for agent names (no hyphens)
        adk_name = agent_id.replace("-", "_")
        task_prompt = ops_to_task_prompt(agent_id, task["operations"])

        # Metrics collected via event stream parsing
        metrics = {
            "writes": 0,
            "conflicts": 0,
            "contention": 0,
            "sensitivity_triggers": 0,
            "coord_overhead_ms": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
        }

        allowed_tools = BASE_TOOLS | STRATEGY_TOOLS[strategy]

        # Track timing for coordination tools via callbacks
        _coord_timers: dict[str, float] = {}

        def _before_tool(*, tool, args, tool_context):
            tool_name = tool.name
            metrics["tool_calls"] += 1
            if tool_name in COORDINATION_TOOLS:
                _coord_timers[tool_name] = time.perf_counter()
            return None

        def _after_tool(*, tool, args, tool_context, tool_response):
            tool_name = tool.name

            # Track coordination overhead
            if tool_name in _coord_timers:
                elapsed = (time.perf_counter() - _coord_timers.pop(tool_name)) * 1000
                metrics["coord_overhead_ms"] += elapsed

            # Parse result for metrics
            # tool_response is a dict from ADK (function response)
            result_data = {}
            if isinstance(tool_response, dict):
                # Try to find JSON text content in the response
                for v in tool_response.values():
                    if isinstance(v, str):
                        try:
                            result_data = json.loads(v)
                            break
                        except (json.JSONDecodeError, TypeError):
                            pass
            elif isinstance(tool_response, str):
                try:
                    result_data = json.loads(tool_response)
                except (json.JSONDecodeError, TypeError):
                    pass

            if tool_name in WRITE_TOOLS:
                metrics["writes"] += 1
                if "coordination_warning" in result_data:
                    metrics["conflicts"] += 1
                if "sensitivity_triggered" in result_data:
                    metrics["sensitivity_triggers"] += len(
                        result_data["sensitivity_triggered"]
                    )

            if tool_name == "context_acquire_reservation":
                if result_data.get("status") == "contention":
                    metrics["contention"] += 1

            return None

        # Build agent tools list
        agent_tools = []

        # Add MCP toolset if coordination is available
        if has_coordination and strategy != "none":
            from mcp import StdioServerParameters
            from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StdioConnectionParams

            toolset = McpToolset(
                connection_params=StdioConnectionParams(
                    server_params=StdioServerParameters(
                        command=sys.executable,
                        args=["-m", "context_teleport.mcp.server"],
                        cwd=str(cwd),
                        env={
                            **os.environ,
                            "MCP_CALLER": agent_id,
                        },
                    ),
                ),
                tool_filter=sorted(allowed_tools),
            )
            agent_tools.append(toolset)

        resolved_model = _resolve_model(self.model)

        agent = Agent(
            name=adk_name,
            model=resolved_model,
            instruction=f"{SYSTEM_PROMPT}\n\n{task_prompt}",
            tools=agent_tools,
            before_tool_callback=_before_tool,
            after_tool_callback=_after_tool,
        )

        session_service = InMemorySessionService()
        app_name = f"ctx_exp_{self.experiment_id}_{adk_name}"

        runner = Runner(
            app_name=app_name,
            agent=agent,
            session_service=session_service,
        )

        session = await session_service.create_session(
            app_name=app_name,
            user_id=adk_name,
        )

        from google.adk.agents.run_config import RunConfig

        run_config = RunConfig(max_llm_calls=self.max_turns)

        content = types.Content(
            role="user",
            parts=[types.Part(text=task_prompt)],
        )

        try:
            async for event in runner.run_async(
                user_id=adk_name,
                session_id=session.id,
                new_message=content,
                run_config=run_config,
            ):
                # Extract token usage from events if available
                if hasattr(event, "usage_metadata") and event.usage_metadata:
                    um = event.usage_metadata
                    if hasattr(um, "prompt_token_count"):
                        metrics["input_tokens"] += um.prompt_token_count or 0
                    if hasattr(um, "candidates_token_count"):
                        metrics["output_tokens"] += um.candidates_token_count or 0

                # Check for final response with DONE
                if event.is_final_response():
                    if event.content and event.content.parts:
                        text = "".join(
                            p.text for p in event.content.parts if hasattr(p, "text") and p.text
                        )
                        if "DONE" in text.upper():
                            break
        except Exception as e:
            logger.warning("Agent %s failed during execution: %s", agent_id, e)
        finally:
            if agent_tools:
                for t in agent_tools:
                    if hasattr(t, "close"):
                        await t.close()

        return metrics
