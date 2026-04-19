"""LiteLLM-based agent harness with filesystem tools.

Provides a drop-in alternative to ClaudeCodeHarness for users who prefer
API-based models (e.g. via OpenRouter) over a Claude subscription.

Supports tool-calling models via the OpenAI-compatible tool_calls interface.
All file operations are sandboxed to work_dir. run_bash requires allow_bash=True.

Requires: litellm>=1.50 (install with ``pip install -e ".[adk]"``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from eda_agents.agents.claude_code_harness import HarnessResult

logger = logging.getLogger(__name__)

_FILESYSTEM_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from work_dir.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file in the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from work_dir.",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries in a directory within the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from work_dir (default: '.').",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Run a shell command in the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    }
                },
                "required": ["cmd"],
            },
        },
    },
]


def _safe_resolve(work_dir: Path, path: str) -> Path:
    """Resolve path relative to work_dir, raising ValueError if it escapes."""
    resolved = (work_dir / path).resolve()
    work_dir_resolved = work_dir.resolve()
    # Allow work_dir itself or any path strictly under it
    if resolved != work_dir_resolved and not str(resolved).startswith(
        str(work_dir_resolved) + os.sep
    ):
        raise ValueError(f"Path escapes work_dir: {path!r}")
    return resolved


class LiteLLMAgentHarness:
    """Agent harness using LiteLLM tool-calling for filesystem-aware tasks.

    Provides read_file, write_file, list_dir, and (optionally) run_bash tools.
    All file paths are sandboxed to work_dir. Uses the same HarnessResult
    interface as ClaudeCodeHarness so callers can swap backends transparently.

    Parameters
    ----------
    prompt : str
        Task prompt sent as the first user message.
    work_dir : Path
        Working directory. All file operations are sandboxed here.
    model : str
        LiteLLM model string (e.g. "openrouter/google/gemini-flash-1.5").
    timeout_s : int
        Maximum wall time for the entire agent loop.
    max_budget_usd : float or None
        Stop if accumulated cost exceeds this amount.
    allow_bash : bool
        Gate for the run_bash tool. False by default — set True only when
        you trust the prompt and model.
    """

    def __init__(
        self,
        prompt: str,
        work_dir: Path | str,
        model: str,
        timeout_s: int = 600,
        max_budget_usd: float | None = None,
        allow_bash: bool = False,
    ):
        self.prompt = prompt
        self.work_dir = Path(work_dir)
        self.model = model
        self.timeout_s = timeout_s
        self.max_budget_usd = max_budget_usd
        self.allow_bash = allow_bash

    def _tool_specs(self) -> list[dict]:
        if self.allow_bash:
            return _FILESYSTEM_TOOLS
        return [t for t in _FILESYSTEM_TOOLS if t["function"]["name"] != "run_bash"]

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _read_file(self, path: str) -> str:
        try:
            resolved = _safe_resolve(self.work_dir, path)
            return resolved.read_text(errors="replace")
        except Exception as exc:
            return f"ERROR: {exc}"

    def _write_file(self, path: str, content: str) -> dict:
        try:
            resolved = _safe_resolve(self.work_dir, path)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content)
            return {"ok": True, "path": str(resolved)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _list_dir(self, path: str = ".") -> list[str]:
        try:
            resolved = _safe_resolve(self.work_dir, path)
            return sorted(str(p.relative_to(self.work_dir)) for p in resolved.iterdir())
        except Exception as exc:
            return [f"ERROR: {exc}"]

    def _run_bash(self, cmd: str) -> str:
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(self.work_dir),
                timeout=60,
            )
            output = result.stdout + result.stderr
            return output[:8000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "ERROR: command timed out after 60s"
        except Exception as exc:
            return f"ERROR: {exc}"

    def _dispatch(self, name: str, args: dict) -> str:
        if name == "read_file":
            return self._read_file(args.get("path", ""))
        if name == "write_file":
            return json.dumps(
                self._write_file(args.get("path", ""), args.get("content", ""))
            )
        if name == "list_dir":
            return json.dumps(self._list_dir(args.get("path", ".")))
        if name == "run_bash":
            if not self.allow_bash:
                return "ERROR: run_bash not permitted (allow_bash=False)"
            return self._run_bash(args.get("cmd", ""))
        return f"ERROR: unknown tool {name!r}"

    # ------------------------------------------------------------------
    # Agent loop
    # ------------------------------------------------------------------

    async def run(self) -> HarnessResult:
        """Run the tool-calling agent loop until completion or budget/timeout."""
        import litellm  # lazy import — only required if using this harness

        start_time = time.monotonic()
        self.work_dir.mkdir(parents=True, exist_ok=True)

        messages: list[dict] = [{"role": "user", "content": self.prompt}]
        accumulated_cost = 0.0
        num_turns = 0
        final_text = ""
        model_usage: dict[str, Any] = {}

        try:
            async with asyncio.timeout(self.timeout_s):
                while True:
                    resp = await litellm.acompletion(
                        model=self.model,
                        messages=messages,
                        tools=self._tool_specs(),
                        tool_choice="auto",
                    )

                    num_turns += 1

                    try:
                        cost = litellm.completion_cost(completion_response=resp) or 0.0
                    except Exception:
                        cost = 0.0
                    accumulated_cost += cost

                    if resp.usage:
                        usage_dict = (
                            resp.usage.model_dump()
                            if hasattr(resp.usage, "model_dump")
                            else dict(vars(resp.usage))
                        )
                        model_usage[f"turn_{num_turns}"] = usage_dict

                    if self.max_budget_usd and accumulated_cost > self.max_budget_usd:
                        return HarnessResult(
                            success=False,
                            error=(
                                f"Budget exceeded: ${accumulated_cost:.4f}"
                                f" > ${self.max_budget_usd}"
                            ),
                            result_text=final_text,
                            num_turns=num_turns,
                            total_cost_usd=accumulated_cost,
                            duration_ms=(time.monotonic() - start_time) * 1000,
                            model_usage=model_usage,
                        )

                    msg = resp.choices[0].message
                    tool_calls = getattr(msg, "tool_calls", None) or []

                    if not tool_calls:
                        final_text = msg.content or ""
                        break

                    # Append assistant message (with tool_calls) to history
                    messages.append(msg)

                    for tc in tool_calls:
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        result_str = self._dispatch(tc.function.name, args)
                        logger.debug(
                            "Tool %s(%s) → %s",
                            tc.function.name,
                            list(args.keys()),
                            result_str[:120],
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result_str,
                            }
                        )

        except TimeoutError:
            return HarnessResult(
                success=False,
                error=f"Timeout after {self.timeout_s}s",
                result_text=final_text,
                num_turns=num_turns,
                total_cost_usd=accumulated_cost,
                duration_ms=(time.monotonic() - start_time) * 1000,
                model_usage=model_usage,
            )
        except Exception as exc:
            logger.error("LiteLLMAgentHarness error: %s", exc, exc_info=True)
            return HarnessResult(
                success=False,
                error=f"LiteLLM error: {exc}",
                result_text=final_text,
                num_turns=num_turns,
                total_cost_usd=accumulated_cost,
                duration_ms=(time.monotonic() - start_time) * 1000,
                model_usage=model_usage,
            )

        return HarnessResult(
            success=True,
            result_text=final_text,
            num_turns=num_turns,
            total_cost_usd=accumulated_cost,
            duration_ms=(time.monotonic() - start_time) * 1000,
            model_usage=model_usage,
        )
