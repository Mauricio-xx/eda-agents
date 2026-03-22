"""Reactive LLM agent experiment runner (Level 3 evaluation).

Round-based harness where real LLM agents autonomously decide which design
points to evaluate, whether to read the shared store, and how to react to
other agents' discoveries. Each round spawns a fresh LLM conversation per
agent (stateless between rounds; the harness controls injected state).

The key difference from L1 (reactive_harness): the LLM makes the exploration
decisions, not a hardcoded attraction/repulsion policy function.

Requires: pip install openai
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from eda_agents.agents.tool_defs import (
    COORDINATION_TOOLS,
    EVALUATE_TOOL_SPEC,
    REACTIVE_STRATEGY_TOOLS,
    REACTIVE_SYSTEM_PROMPT,
    SIMULATE_TOOL_SPEC,
    GMID_LOOKUP_TOOL_SPEC,
    WRITE_TOOLS,
    build_reactive_round_prompt,
    build_reactive_system_prompt,
)
from eda_agents.agents.scenarios import ScenarioResult
from eda_agents.agents.scenarios import (
    BOUNDS,
    DIM_NAMES,
    DesignPointRecord,
    ReactiveExplorationScenario,
)

logger = logging.getLogger(__name__)

# Check for optional MCP support (requires context-teleport)
try:
    from context_teleport.experiments.mcp_harness import _spawn_session
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def mcp_tool_to_openai(tool) -> dict:
    """Convert an MCP tool definition to OpenAI function calling format."""
    schema = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
    params = dict(schema)
    params.setdefault("type", "object")
    params.setdefault("properties", {})
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": params,
        },
    }


# Threshold for two points to be considered "redundant" (normalized 5D distance)
REDUNDANCY_EPSILON = 0.05


class ReactiveLLMExperimentRunner:
    """Run reactive design exploration experiments with real LLM agents.

    Each round:
        1. Build per-agent prompt with round state and previous results
        2. Start fresh LLM conversation per agent
        3. LLM uses evaluate_miller_ota (local) + MCP tools (strategy-filtered)
        4. Harness intercepts evaluate_miller_ota, runs MillerOTADesigner locally
        5. Collect design points and metrics
        6. Next round: inject updated state into prompts
    """

    STRATEGIES = ("none", "intents_only", "reservations", "full_rep")

    def __init__(
        self,
        scenario: ReactiveExplorationScenario,
        n_agents: int,
        strategy: str = "none",
        model: str | list[str] = "anthropic/claude-haiku-4.5",
        store_path: Path | None = None,
        seed: int = 42,
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        max_turns_per_round: int = 20,
        client: Any | None = None,
    ):
        if strategy not in self.STRATEGIES:
            raise ValueError(f"Invalid strategy: {strategy}. Use: {self.STRATEGIES}")

        self.scenario = scenario
        self.n_agents = n_agents
        self.strategy = strategy
        # model can be a single string (all agents) or a list (one per agent)
        if isinstance(model, list):
            if len(model) != n_agents:
                raise ValueError(
                    f"model list length ({len(model)}) must match n_agents ({n_agents})"
                )
            self.models = model
        else:
            self.models = [model] * n_agents
        self.model = self.models[0]  # backward compat for metadata
        self.store_path = store_path
        self.seed = seed
        self.max_turns_per_round = max_turns_per_round
        self.experiment_id = str(uuid.uuid4())[:8]

        # SPICE mode: set by run_spice_experiment.py or caller
        self._use_spice: bool = False
        self._spice_handler: Any = None  # SpiceEvaluationHandler when active

        if client is not None:
            self.client = client
        else:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError(
                    "openai package required for LLM experiments. "
                    "Install with: pip install openai"
                )
            resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
            if not resolved_key:
                raise ValueError(
                    "API key required. Set OPENROUTER_API_KEY env var or pass api_key="
                )
            self.client = AsyncOpenAI(base_url=base_url, api_key=resolved_key)

    async def run(self) -> ScenarioResult:
        """Execute the round-based reactive experiment with LLM agents."""
        import tempfile

        import git

        # CT coordination is optional
        try:
            from context_teleport.core.store import ContextStore
            HAS_COORDINATION = True
        except ImportError:
            HAS_COORDINATION = False

        work_dir = self.store_path or Path(tempfile.mkdtemp(prefix="ctx-rllm-exp-"))

        if not (work_dir / ".git").exists():
            repo = git.Repo.init(work_dir)
            readme = work_dir / "README.md"
            readme.write_text("# Reactive LLM Experiment\n")
            repo.index.add(["README.md"])
            repo.index.commit("initial")

        store = None
        if HAS_COORDINATION:
            store = ContextStore(work_dir)
            if not store.initialized:
                store.init(project_name=f"reactive-llm-exp-{self.experiment_id}")

            # Write experiment config to store for traceability
            exp_config = {
                "experiment_id": self.experiment_id,
                "model": self.models[0] if len(set(self.models)) == 1 else self.models,
                "models_per_agent": self.models,
                "strategy": self.strategy,
                "seed": self.seed,
                "n_agents": self.n_agents,
                "total_budget": self.scenario.total_budget,
                "batch_size": self.scenario.batch_size,
                "n_rounds": self.scenario.n_rounds,
                "scenario": self.scenario.name,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            store.set_knowledge(
                "experiment-config",
                json.dumps(exp_config, indent=2),
            )

        configs = self.scenario.make_agent_configs(self.n_agents, seed=self.seed)

        start = time.perf_counter()

        # Per-agent tracking
        agent_histories: dict[str, list[dict]] = {cfg.agent_id: [] for cfg in configs}
        all_design_points: list[DesignPointRecord] = []
        best_fom_per_round: list[float] = []
        per_agent_metrics: dict[str, dict[str, Any]] = {
            cfg.agent_id: {
                "evaluations": 0,
                "best_fom": 0.0,
                "best_valid_fom": 0.0,
                "valid_evaluations": 0,
                "tool_calls": 0,
            }
            for cfg in configs
        }

        total_writes = 0
        conflicts = 0
        contention = 0
        sensitivity_triggers = 0
        coord_overhead_ms = 0.0
        total_input_tokens = 0
        total_output_tokens = 0
        total_tool_calls = 0

        n_rounds = self.scenario.n_rounds

        for round_idx in range(n_rounds):
            # Build per-agent prompts
            agent_prompts: list[tuple[str, str, dict, dict]] = []
            for cfg in configs:
                # Others' summary (for coordination strategies)
                others_summary = []
                for other_cfg in configs:
                    if other_cfg.agent_id == cfg.agent_id:
                        continue
                    other_hist = agent_histories[other_cfg.agent_id]
                    if other_hist:
                        best = max(other_hist, key=lambda h: h.get("FoM", 0))
                        others_summary.append({
                            "agent": other_cfg.agent_id,
                            "best_fom": best.get("FoM", 0),
                            "best_params": best.get("params", {}),
                        })

                prompt = build_reactive_round_prompt(
                    agent_id=cfg.agent_id,
                    round_idx=round_idx,
                    n_rounds=n_rounds,
                    batch_size=self.scenario.batch_size,
                    partition_lo=cfg.partition_lo,
                    partition_hi=cfg.partition_hi,
                    own_history=agent_histories[cfg.agent_id] or None,
                    others_summary=others_summary or None,
                    strategy=self.strategy,
                )
                agent_prompts.append((
                    cfg.agent_id, prompt, cfg.partition_lo, cfg.partition_hi
                ))

            # Run all agents concurrently for this round
            results = await asyncio.gather(
                *(
                    self._run_agent_round(
                        work_dir, agent_id, prompt, self.strategy, round_idx,
                        model=self.models[i],
                    )
                    for i, (agent_id, prompt, _, _) in enumerate(agent_prompts)
                ),
                return_exceptions=True,
            )

            # Collect results from this round
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    agent_id = agent_prompts[i][0]
                    logger.warning(
                        "Agent %s round %d failed: %s", agent_id, round_idx, result
                    )
                    continue

                agent_id = agent_prompts[i][0]
                total_writes += result["writes"]
                conflicts += result["conflicts"]
                contention += result["contention"]
                sensitivity_triggers += result["sensitivity_triggers"]
                coord_overhead_ms += result["coord_overhead_ms"]
                total_input_tokens += result["input_tokens"]
                total_output_tokens += result["output_tokens"]
                total_tool_calls += result["tool_calls"]

                for dp in result["design_points"]:
                    all_design_points.append(dp)
                    agent_histories[agent_id].append({
                        "params": dp.params,
                        "FoM": dp.fom,
                        "valid": dp.valid,
                    })
                    per_agent_metrics[agent_id]["evaluations"] += 1
                    if dp.fom > per_agent_metrics[agent_id]["best_fom"]:
                        per_agent_metrics[agent_id]["best_fom"] = dp.fom
                    if dp.valid:
                        per_agent_metrics[agent_id]["valid_evaluations"] += 1
                        if dp.fom > per_agent_metrics[agent_id]["best_valid_fom"]:
                            per_agent_metrics[agent_id]["best_valid_fom"] = dp.fom

                per_agent_metrics[agent_id]["tool_calls"] += result["tool_calls"]

            # Best FoM after this round
            current_best = max(
                (dp.fom for dp in all_design_points), default=0.0
            )
            best_fom_per_round.append(current_best)

        duration = time.perf_counter() - start
        conflict_rate = conflicts / max(total_writes, 1) * 100

        # Compute metrics reused from L1
        redundant = _count_redundant(all_design_points)
        coverage = _compute_coverage(all_design_points)
        convergence_round = _find_convergence_round(best_fom_per_round)

        # Validity statistics
        total_evals = len(all_design_points)
        valid_evals = sum(1 for dp in all_design_points if dp.valid)
        pct_valid = round(valid_evals / max(total_evals, 1) * 100, 1)
        valid_points = [dp for dp in all_design_points if dp.valid]
        best_valid_fom = max((dp.fom for dp in valid_points), default=0.0)

        # Best *valid* FoM per round (cumulative best up to each round)
        best_valid_fom_per_round: list[float] = []
        running_best_valid = 0.0
        for r_idx in range(n_rounds):
            for dp in all_design_points:
                if dp.round_idx == r_idx and dp.valid and dp.fom > running_best_valid:
                    running_best_valid = dp.fom
            best_valid_fom_per_round.append(running_best_valid)

        result = ScenarioResult(
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
                "transport": "reactive_llm",
                "model": self.models[0] if len(set(self.models)) == 1 else self.models,
                "total_budget": self.scenario.total_budget,
                "batch_size": self.scenario.batch_size,
                "n_rounds": n_rounds,
                "best_fom_per_round": best_fom_per_round,
                "best_valid_fom_per_round": best_valid_fom_per_round,
                "best_valid_fom": best_valid_fom,
                "valid_evaluations": valid_evals,
                "pct_valid": pct_valid,
                "redundant_evaluations": redundant,
                "exploration_coverage": coverage,
                "convergence_round": convergence_round,
                "agent_metrics": per_agent_metrics,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "tool_calls": total_tool_calls,
                "evaluations_per_agent": {
                    aid: m["evaluations"] for aid, m in per_agent_metrics.items()
                },
                "expected_conflict_level": self.scenario.expected_conflict_level(),
                "design_points": [
                    {
                        "agent": dp.agent,
                        "round": dp.round_idx,
                        "params": dp.params,
                        "fom": dp.fom,
                        "valid": dp.valid,
                        "results": {
                            "Adc_dB": (dp.results or {}).get("spice", {}).get("Adc_dB")
                                if dp.results else None,
                            "GBW_MHz": (dp.results or {}).get("spice", {}).get("GBW_MHz")
                                if dp.results else None,
                            "PM_deg": (dp.results or {}).get("spice", {}).get("PM_deg")
                                if dp.results else None,
                            "eval_mode": (dp.results or {}).get("eval_mode"),
                            "violations": (dp.results or {}).get("violations", []),
                        },
                        "transistor_sizing": (dp.results or {}).get(
                            "transistor_sizing", {}
                        ),
                    }
                    for dp in all_design_points
                ],
            },
        )

        # Record metrics in store if available
        if store is not None:
            store.record_metric(
                "conflict_rate",
                conflict_rate,
                scenario=self.scenario.name,
                experiment_id=self.experiment_id,
            )
            store.record_metric(
                "best_fom_final",
                best_fom_per_round[-1] if best_fom_per_round else 0.0,
                scenario=self.scenario.name,
                experiment_id=self.experiment_id,
            )

        return result

    async def _run_agent_round(
        self,
        cwd: Path,
        agent_id: str,
        round_prompt: str,
        strategy: str,
        round_idx: int,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Run a single agent for a single round with a fresh LLM conversation."""
        writes = 0
        conflicts = 0
        contention_count = 0
        sensitivity_triggers = 0
        coord_overhead_ms = 0.0
        input_tokens = 0
        output_tokens = 0
        tool_call_count = 0
        design_points: list[DesignPointRecord] = []

        # Tools the agent is allowed to use
        allowed_mcp_tools = {"context_add_knowledge"} | REACTIVE_STRATEGY_TOOLS[strategy]

        # Choose tool spec and eval tool name based on SPICE mode
        if self._use_spice and self._spice_handler is not None:
            topology = self._spice_handler.topology
            eval_tool_spec = topology.tool_spec()
            eval_tool_name = eval_tool_spec["function"]["name"]
            system_prompt = build_reactive_system_prompt(
                topology=topology,
                batch_size=self.scenario.batch_size,
                eval_tool_name=eval_tool_name,
                spice_mode=True,
            )
        elif self._use_spice:
            # Fallback: SPICE mode but handler not set yet (shouldn't happen)
            eval_tool_spec = SIMULATE_TOOL_SPEC
            eval_tool_name = "simulate_ota"
            system_prompt = REACTIVE_SYSTEM_PROMPT.format(
                batch_size=self.scenario.batch_size,
                eval_tool_name=eval_tool_name,
            )
        else:
            eval_tool_spec = EVALUATE_TOOL_SPEC
            eval_tool_name = "evaluate_miller_ota"
            system_prompt = REACTIVE_SYSTEM_PROMPT.format(
                batch_size=self.scenario.batch_size,
                eval_tool_name=eval_tool_name,
            )

        # Build tool list: always include local eval tool
        openai_tools = [eval_tool_spec]

        # Add gm/ID lookup tool in SPICE mode (free, no budget cost)
        if self._use_spice:
            openai_tools.insert(1, GMID_LOOKUP_TOOL_SPEC)

        # Allow both old and new tool names for backward compat
        all_allowed = {eval_tool_name, "simulate_ota", "simulate_miller_ota",
                       "simulate_circuit", "simulate_system",
                       "evaluate_miller_ota", "context_add_knowledge"} | REACTIVE_STRATEGY_TOOLS[strategy]
        if self._use_spice:
            all_allowed.add("gmid_lookup")

        # MCP session for coordination tools (optional)
        if HAS_MCP and strategy != "none":
            return await self._run_agent_round_with_mcp(
                cwd, agent_id, round_prompt, strategy, round_idx, model,
                system_prompt, eval_tool_spec, eval_tool_name, openai_tools,
                all_allowed,
            )

        # No MCP: run with local tools only
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": round_prompt},
        ]

        for turn in range(self.max_turns_per_round):
            try:
                kwargs: dict[str, Any] = {
                    "model": model or self.model,
                    "messages": messages,
                    "max_tokens": 4096,
                }
                if openai_tools:
                    kwargs["tools"] = openai_tools
                    kwargs["tool_choice"] = "auto"

                response = await self.client.chat.completions.create(**kwargs)
            except Exception as e:
                logger.warning(
                    "LLM call failed for %s round %d turn %d: %s",
                    agent_id, round_idx, turn, e,
                )
                break

            if response.usage:
                input_tokens += response.usage.prompt_tokens
                output_tokens += response.usage.completion_tokens

            choice = response.choices[0]

            if choice.message.tool_calls:
                # Add assistant message
                assistant_msg: dict[str, Any] = {"role": "assistant"}
                if choice.message.content:
                    assistant_msg["content"] = choice.message.content
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.message.tool_calls
                ]
                messages.append(assistant_msg)

                for tc in choice.message.tool_calls:
                    tool_name = tc.function.name
                    tool_call_count += 1

                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                    # Only handle local tools when MCP is not available
                    if tool_name == "evaluate_miller_ota":
                        result_text, dp = self._evaluate_design(
                            args, agent_id, round_idx, len(design_points)
                        )
                        if dp is not None:
                            design_points.append(dp)
                    elif tool_name in (
                        "simulate_ota", "simulate_miller_ota",
                        "simulate_circuit", "simulate_system",
                    ):
                        result_text, dp = await self._evaluate_design_spice(
                            args, agent_id, round_idx, len(design_points)
                        )
                        if dp is not None:
                            design_points.append(dp)
                    elif tool_name == "gmid_lookup":
                        result_text = self._handle_gmid_lookup(args)
                    else:
                        # No MCP available -- reject coordination/store tools
                        result_text = json.dumps({
                            "status": "error",
                            "message": f"Tool '{tool_name}' requires context-teleport (not installed)",
                        })

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    })
            else:
                # No tool calls -- check if done
                content = choice.message.content or ""
                messages.append({"role": "assistant", "content": content})
                if "DONE" in content.upper() or choice.finish_reason == "stop":
                    break

        # Count writes from evaluate_miller_ota (each evaluation also writes to store)
        writes += len(design_points)

        return {
            "writes": writes,
            "conflicts": conflicts,
            "contention": contention_count,
            "sensitivity_triggers": sensitivity_triggers,
            "coord_overhead_ms": coord_overhead_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_calls": tool_call_count,
            "design_points": design_points,
        }

    async def _run_agent_round_with_mcp(
        self,
        cwd: Path,
        agent_id: str,
        round_prompt: str,
        strategy: str,
        round_idx: int,
        model: str | None,
        system_prompt: str,
        eval_tool_spec: dict,
        eval_tool_name: str,
        openai_tools: list[dict],
        all_allowed: set[str],
    ) -> dict[str, Any]:
        """Run agent round with MCP session for coordination tools."""
        writes = 0
        conflicts = 0
        contention_count = 0
        sensitivity_triggers = 0
        coord_overhead_ms = 0.0
        input_tokens = 0
        output_tokens = 0
        tool_call_count = 0
        design_points: list[DesignPointRecord] = []

        allowed_mcp_tools = {"context_add_knowledge"} | REACTIVE_STRATEGY_TOOLS[strategy]

        async with _spawn_session(cwd, agent_id) as session:
            # Get MCP tools and filter by strategy
            mcp_tools_result = await session.list_tools()
            filtered_mcp = [
                t for t in mcp_tools_result.tools if t.name in allowed_mcp_tools
            ]
            mcp_openai_tools = [mcp_tool_to_openai(t) for t in filtered_mcp]

            # Combine local eval tools + MCP tools
            combined_tools = list(openai_tools) + mcp_openai_tools

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": round_prompt},
            ]

            for turn in range(self.max_turns_per_round):
                try:
                    kwargs: dict[str, Any] = {
                        "model": model or self.model,
                        "messages": messages,
                        "max_tokens": 4096,
                    }
                    if combined_tools:
                        kwargs["tools"] = combined_tools
                        kwargs["tool_choice"] = "auto"

                    response = await self.client.chat.completions.create(**kwargs)
                except Exception as e:
                    logger.warning(
                        "LLM call failed for %s round %d turn %d: %s",
                        agent_id, round_idx, turn, e,
                    )
                    break

                if response.usage:
                    input_tokens += response.usage.prompt_tokens
                    output_tokens += response.usage.completion_tokens

                choice = response.choices[0]

                if choice.message.tool_calls:
                    # Add assistant message
                    assistant_msg: dict[str, Any] = {"role": "assistant"}
                    if choice.message.content:
                        assistant_msg["content"] = choice.message.content
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in choice.message.tool_calls
                    ]
                    messages.append(assistant_msg)

                    for tc in choice.message.tool_calls:
                        tool_name = tc.function.name
                        tool_call_count += 1

                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            args = {}

                        # Reject hallucinated tools
                        if tool_name not in all_allowed:
                            result_text = json.dumps({
                                "status": "error",
                                "message": f"Tool '{tool_name}' not available",
                            })
                        elif tool_name == "evaluate_miller_ota":
                            # LOCAL: run MillerOTADesigner (analytical)
                            result_text, dp = self._evaluate_design(
                                args, agent_id, round_idx, len(design_points)
                            )
                            if dp is not None:
                                design_points.append(dp)
                        elif tool_name in (
                            "simulate_ota", "simulate_miller_ota",
                            "simulate_circuit", "simulate_system",
                        ):
                            # LOCAL: run SPICE via SpiceEvaluationHandler
                            result_text, dp = await self._evaluate_design_spice(
                                args, agent_id, round_idx, len(design_points)
                            )
                            if dp is not None:
                                design_points.append(dp)
                        elif tool_name == "gmid_lookup":
                            # LOCAL: gm/ID lookup (free, no budget cost)
                            result_text = self._handle_gmid_lookup(args)
                        else:
                            # MCP: forward to session
                            is_coord = tool_name in COORDINATION_TOOLS
                            if is_coord:
                                t0 = time.perf_counter()

                            try:
                                mcp_result = await session.call_tool(
                                    tool_name, arguments=args
                                )
                                result_text = (
                                    mcp_result.content[0].text
                                    if mcp_result.content
                                    else "{}"
                                )
                            except Exception as e:
                                result_text = json.dumps({
                                    "status": "error",
                                    "message": str(e),
                                })

                            if is_coord:
                                coord_overhead_ms += (
                                    time.perf_counter() - t0
                                ) * 1000

                            # Parse result for metrics
                            try:
                                result_data = json.loads(result_text)
                            except (json.JSONDecodeError, TypeError):
                                result_data = {}

                            if tool_name in WRITE_TOOLS:
                                writes += 1
                                if "coordination_warning" in result_data:
                                    conflicts += 1
                                if "sensitivity_triggered" in result_data:
                                    sensitivity_triggers += len(
                                        result_data["sensitivity_triggered"]
                                    )

                            if tool_name == "context_acquire_reservation":
                                if result_data.get("status") == "contention":
                                    contention_count += 1

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_text,
                        })
                else:
                    # No tool calls -- check if done
                    content = choice.message.content or ""
                    messages.append({"role": "assistant", "content": content})
                    if "DONE" in content.upper() or choice.finish_reason == "stop":
                        break

        # Count writes from evaluate_miller_ota (each evaluation also writes to store)
        writes += len(design_points)

        return {
            "writes": writes,
            "conflicts": conflicts,
            "contention": contention_count,
            "sensitivity_triggers": sensitivity_triggers,
            "coord_overhead_ms": coord_overhead_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_calls": tool_call_count,
            "design_points": design_points,
        }

    def _evaluate_design(
        self,
        args: dict,
        agent_id: str,
        round_idx: int,
        point_idx: int,
    ) -> tuple[str, DesignPointRecord | None]:
        """Run MillerOTADesigner locally and return (JSON result, DesignPointRecord).

        Returns (error_json, None) if args are invalid.
        """
        try:
            gmid_input = float(args.get("gmid_input", 10))
            gmid_load = float(args.get("gmid_load", 10))
            L_input_um = float(args.get("L_input_um", 0.5))
            L_load_um = float(args.get("L_load_um", 0.5))
            Cc_pF = float(args.get("Cc_pF", 1.0))
            Ibias_uA = float(args.get("Ibias_uA", 10.0))
        except (TypeError, ValueError) as e:
            return json.dumps({"status": "error", "message": f"Invalid args: {e}"}), None

        # Clamp to valid bounds
        gmid_input = max(5.0, min(25.0, gmid_input))
        gmid_load = max(5.0, min(20.0, gmid_load))
        L_input_um = max(0.13, min(2.0, L_input_um))
        L_load_um = max(0.13, min(2.0, L_load_um))
        Cc_pF = max(0.1, min(5.0, Cc_pF))
        Ibias_uA = max(0.5, min(50.0, Ibias_uA))

        result = self.scenario.designer.analytical_design(
            gmid_input=gmid_input,
            gmid_load=gmid_load,
            L_input=L_input_um * 1e-6,
            L_load=L_load_um * 1e-6,
            Cc=Cc_pF * 1e-12,
            Ibias=Ibias_uA * 1e-6,
        )

        result_dict = {
            "gmid_input": round(gmid_input, 2),
            "gmid_load": round(gmid_load, 2),
            "L_input_um": round(L_input_um, 3),
            "L_load_um": round(L_load_um, 3),
            "Cc_pF": round(Cc_pF, 2),
            "Ibias_uA": round(Ibias_uA, 2),
            "Adc_dB": round(result.Adc_dB, 1),
            "GBW_MHz": round(result.GBW / 1e6, 3),
            "PM_deg": round(result.PM, 1),
            "power_uW": round(result.power_uW, 2),
            "area_um2": round(result.area_um2, 2),
            "raw_FoM": result.raw_FoM,
            "spec_penalty": round(result.spec_penalty, 6),
            "FoM": result.FoM,
            "valid": result.valid,
            "violations": result.violations,
        }

        dp = DesignPointRecord(
            agent=agent_id,
            params={
                "gmid_input": gmid_input,
                "gmid_load": gmid_load,
                "L_input_um": L_input_um,
                "L_load_um": L_load_um,
                "Cc_pF": Cc_pF,
                "Ibias_uA": Ibias_uA,
            },
            fom=result.FoM,
            valid=result.valid,
            round_idx=round_idx,
            results={
                "Adc_dB": round(result.Adc_dB, 1),
                "GBW_MHz": round(result.GBW / 1e6, 3),
                "PM_deg": round(result.PM, 1),
                "power_uW": round(result.power_uW, 2),
                "area_um2": round(result.area_um2, 2),
                "Vos_3sigma_mV": round(3 * result.Vos_sigma * 1e3, 3) if result.Vos_sigma else 0.0,
                "Ib1_uA": round(result.Ib1 * 1e6, 3),
                "Ib2_uA": round(result.Ib2 * 1e6, 3),
                "raw_FoM": result.raw_FoM,
                "spec_penalty": round(result.spec_penalty, 6),
                "violations": result.violations,
                "transistors": {
                    name: t.as_dict()
                    for name, t in result.transistors.items()
                },
            },
        )

        return json.dumps(result_dict), dp

    def _handle_gmid_lookup(self, args: dict) -> str:
        """Handle gm/ID lookup tool call. Free (no budget cost)."""
        try:
            from eda_agents.core.gmid_lookup import GmIdLookup
        except ImportError:
            return json.dumps({"status": "error", "message": "gmid_lookup not available"})

        try:
            mos_type = args.get("mos_type", "nmos")
            L_um = float(args.get("L_um", 1.0))
            gmid_target = float(args.get("gmid_target", 12.0))
            Vds = float(args.get("Vds", 0.6 if mos_type == "nmos" else -0.6))
        except (TypeError, ValueError) as e:
            return json.dumps({"status": "error", "message": f"Invalid args: {e}"})

        if not hasattr(self, "_gmid_lut"):
            self._gmid_lut = GmIdLookup()

        result = self._gmid_lut.query_at_gmid(
            gmid_target, mos_type, L_um, Vds
        )
        if result is None:
            return json.dumps({
                "status": "error",
                "message": f"gm/ID={gmid_target} out of range for {mos_type} at L={L_um}um",
            })

        return json.dumps(result)

    async def _evaluate_design_spice(
        self,
        args: dict,
        agent_id: str,
        round_idx: int,
        point_idx: int,
    ) -> tuple[str, DesignPointRecord | None]:
        """Run SPICE evaluation via SpiceEvaluationHandler.

        Returns (JSON result, DesignPointRecord) or (error_json, None).
        """
        if self._spice_handler is None:
            return json.dumps({
                "status": "error",
                "message": "SPICE handler not configured",
            }), None

        # Extract params based on topology design space
        design_space = self._spice_handler.topology.design_space()
        params: dict[str, float] = {}
        try:
            for key, (lo, hi) in design_space.items():
                default = (lo + hi) / 2.0
                val = float(args.get(key, default))
                params[key] = max(lo, min(hi, val))
        except (TypeError, ValueError) as e:
            return json.dumps({"status": "error", "message": f"Invalid args: {e}"}), None

        spice_eval = await self._spice_handler.evaluate(params, agent_id=agent_id)
        result_text = self._spice_handler.to_json(spice_eval)

        dp = DesignPointRecord(
            agent=agent_id,
            params=params,
            fom=spice_eval.fom,
            valid=spice_eval.valid,
            round_idx=round_idx,
            results={
                "eval_mode": spice_eval.eval_mode,
                "fom": spice_eval.fom,
                "valid": spice_eval.valid,
                "violations": spice_eval.violations,
                "analytical": spice_eval.analytical,
                "spice": spice_eval.spice,
                "transistor_sizing": spice_eval.transistor_sizing,
            },
        )

        return result_text, dp


# ---------------------------------------------------------------------------
# Metrics helpers (reused from reactive_harness.py)
# ---------------------------------------------------------------------------

def _normalize_point(params: dict[str, float]) -> list[float]:
    """Normalize a 5D point to [0,1]^5 for distance computation."""
    normalized = []
    for dim in DIM_NAMES:
        lo, hi = BOUNDS[dim]
        val = params.get(dim, (lo + hi) / 2)
        normalized.append((val - lo) / (hi - lo) if hi > lo else 0.5)
    return normalized


def _count_redundant(points: list[DesignPointRecord]) -> int:
    """Count points within REDUNDANCY_EPSILON of another agent's point."""
    redundant = 0
    normalized = [(_normalize_point(dp.params), dp.agent) for dp in points]

    for i, (pi, ai) in enumerate(normalized):
        for j in range(i + 1, len(normalized)):
            pj, aj = normalized[j]
            if ai == aj:
                continue
            dist = sum((a - b) ** 2 for a, b in zip(pi, pj)) ** 0.5
            if dist < REDUNDANCY_EPSILON:
                redundant += 1
                break

    return redundant


def _compute_coverage(
    points: list[DesignPointRecord], grid_size: int = 5
) -> float:
    """Compute fraction of grid cells occupied in the 5D design space."""
    total_cells = grid_size ** len(DIM_NAMES)
    occupied: set[tuple[int, ...]] = set()

    for dp in points:
        cell = []
        for dim in DIM_NAMES:
            lo, hi = BOUNDS[dim]
            val = dp.params.get(dim, (lo + hi) / 2)
            idx = int((val - lo) / (hi - lo) * grid_size)
            idx = max(0, min(grid_size - 1, idx))
            cell.append(idx)
        occupied.add(tuple(cell))

    return len(occupied) / total_cells if total_cells > 0 else 0.0


def _find_convergence_round(
    best_fom_per_round: list[float], patience: int = 3
) -> int | None:
    """Find first round where best FoM doesn't improve for `patience` rounds."""
    if len(best_fom_per_round) < patience + 1:
        return None

    for i in range(len(best_fom_per_round) - patience):
        window = best_fom_per_round[i : i + patience + 1]
        if all(v <= window[0] for v in window[1:]):
            return i

    return None
