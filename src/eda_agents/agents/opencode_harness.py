"""OpenCode CLI harness for non-interactive agent execution.

Wraps ``opencode run <message> --format json --dir <work_dir>`` as an async
subprocess. opencode supports multiple model providers (Anthropic, OpenAI,
Gemini, OpenRouter, Ollama) including subscription-based platforms (Cursor,
Windsurf), making it accessible to users without a Claude API key.

The ``--format json`` flag emits a newline-delimited stream of JSON events.
We collect all events, extract assistant text, and surface usage metadata.

Requires: opencode CLI (``npm install -g opencode-ai``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from eda_agents.agents.claude_code_harness import HarnessResult

logger = logging.getLogger(__name__)

_DEFAULT_CLI = "opencode"


class OpenCodeHarness:
    """Async wrapper around ``opencode run`` for non-interactive execution.

    Parameters
    ----------
    prompt : str
        Task prompt passed as the message positional argument.
    work_dir : Path
        Working directory for the CLI process (passed via ``--dir``).
    model : str or None
        Model in provider/model format (e.g. "anthropic/claude-sonnet-4-6",
        "openrouter/google/gemini-flash-1.5"). Passed via ``-m``.
        If None, opencode uses its configured default.
    timeout_s : int
        Maximum wall time for the CLI process.
    cli_path : str
        Path or name of the ``opencode`` binary.
    """

    def __init__(
        self,
        prompt: str,
        work_dir: Path | str,
        model: str | None = None,
        timeout_s: int = 1800,
        cli_path: str = _DEFAULT_CLI,
    ):
        self.prompt = prompt
        self.work_dir = Path(work_dir)
        self.model = model
        self.timeout_s = timeout_s
        self.cli_path = cli_path

        self._cli_version: str | None = None

    # ------------------------------------------------------------------
    # CLI version
    # ------------------------------------------------------------------

    async def get_cli_version(self) -> str:
        """Return the opencode CLI version string, caching the result."""
        if self._cli_version is not None:
            return self._cli_version

        cli = shutil.which(self.cli_path)
        if not cli:
            self._cli_version = ""
            return ""

        try:
            proc = await asyncio.create_subprocess_exec(
                cli, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            self._cli_version = stdout.decode().strip()
        except Exception:
            self._cli_version = ""

        return self._cli_version

    # ------------------------------------------------------------------
    # Build argv
    # ------------------------------------------------------------------

    def build_argv(self, include_prompt: bool = False) -> list[str]:
        """Build the CLI argument list.

        Parameters
        ----------
        include_prompt : bool
            When True, append self.prompt as the final positional arg.
            Pass False for dry-run / testing argv without the prompt.
        """
        cli = shutil.which(self.cli_path)
        if not cli:
            return []

        argv = [cli, "run", "--format", "json", "--dir", str(self.work_dir)]

        if self.model:
            argv.extend(["-m", self.model])

        if include_prompt:
            argv.append(self.prompt)

        return argv

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self) -> HarnessResult:
        """Execute the prompt via ``opencode run`` and return results."""
        cli_version = await self.get_cli_version()

        argv = self.build_argv(include_prompt=True)
        if not argv:
            return HarnessResult(
                success=False,
                error=f"opencode CLI not found: {self.cli_path!r}",
                cli_version="",
            )

        self.work_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "OpenCode: launching in %s (timeout=%ds, model=%s)",
            self.work_dir,
            self.timeout_s,
            self.model or "default",
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.work_dir),
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_s,
            )

        except asyncio.TimeoutError:
            try:
                proc.kill()  # type: ignore[possibly-undefined]
                await proc.wait()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            return HarnessResult(
                success=False,
                error=f"Timeout after {self.timeout_s}s",
                cli_version=cli_version,
            )
        except FileNotFoundError:
            return HarnessResult(
                success=False,
                error="opencode CLI not found at resolved path",
                cli_version="",
            )
        except Exception as exc:
            return HarnessResult(
                success=False,
                error=f"Subprocess error: {exc}",
                cli_version=cli_version,
            )

        stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            return HarnessResult(
                success=False,
                error=f"CLI exited with code {proc.returncode}: {stderr_str[-500:]}",
                result_text=stdout_str[-2000:],
                cli_version=cli_version,
            )

        return self._parse_event_stream(stdout_str, cli_version)

    # ------------------------------------------------------------------
    # Event stream parser
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_event_stream(stdout: str, cli_version: str) -> HarnessResult:
        """Parse opencode's JSON event stream (``--format json``) into HarnessResult.

        opencode emits newline-delimited JSON objects. We extract assistant
        text content, turn count, and usage/cost metadata.

        The parser is intentionally resilient: unknown event types are skipped.
        If no JSON events are found, the raw stdout is treated as result text.
        """
        events: list[dict] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("Non-JSON line from opencode: %.200s", line)

        if not events:
            return HarnessResult(
                success=bool(stdout.strip()),
                result_text=stdout.strip(),
                cli_version=cli_version,
                error=None if stdout.strip() else "No output from opencode",
            )

        text_parts: list[str] = []
        total_cost: float = 0.0
        model_usage: dict[str, Any] = {}
        num_turns = 0

        for event in events:
            etype = event.get("type", "")
            part = event.get("part", {})

            # opencode --format json emits events with a "part" envelope:
            #   {"type":"text", "part":{"type":"text","text":"..."}}
            #   {"type":"step_finish", "part":{"type":"step-finish","cost":0,"tokens":{...}}}
            if etype == "text" and part.get("type") == "text":
                text_parts.append(part.get("text", ""))

            elif etype == "step_start":
                num_turns += 1

            elif etype == "step_finish":
                cost = part.get("cost") or 0.0
                total_cost += float(cost)
                tokens = part.get("tokens", {})
                if tokens:
                    model_usage[f"step_{num_turns}"] = tokens

        result_text = "".join(text_parts).strip()
        success = bool(result_text)

        return HarnessResult(
            success=success,
            result_text=result_text,
            num_turns=num_turns,
            total_cost_usd=total_cost,
            model_usage=model_usage,
            cli_version=cli_version,
            error=None if success else "No assistant content in opencode output",
        )
