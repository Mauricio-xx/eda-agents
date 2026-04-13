"""Claude Code CLI harness for non-interactive agent execution.

Wraps ``claude --print --output-format json`` as an async subprocess.
The prompt is piped via stdin; the JSON result is parsed into a
``HarnessResult`` dataclass.

Uses ``--bare`` mode by default to prevent the child Claude from
loading the parent's CLAUDE.md, hooks, and auto-memory.  MCP config
is optional (for Context Teleport coordination, not needed for basic
digital flow execution).

Requires: Claude Code CLI installed (``npm install -g @anthropic-ai/claude-code``
or via the desktop app).

Security: ``--dangerously-skip-permissions`` is double-gated:
``allow_dangerous=True`` in constructor AND ``EDA_AGENTS_ALLOW_DANGEROUS=1``
in env.  Both must be set for the flag to appear in argv.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HarnessResult:
    """Result from a Claude Code CLI invocation."""

    success: bool
    result_text: str = ""
    duration_ms: float = 0.0
    num_turns: int = 0
    total_cost_usd: float = 0.0
    session_id: str = ""
    model_usage: dict[str, Any] = field(default_factory=dict)
    raw_json: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    cli_version: str = ""


class ClaudeCodeHarness:
    """Async wrapper around ``claude --print`` for non-interactive execution.

    Parameters
    ----------
    prompt : str
        The full prompt to send to the CLI agent.
    work_dir : Path
        Working directory for the CLI process.
    mcp_config : dict or None
        MCP server configuration dict.  Written to a temp JSON file
        and passed via ``--mcp-config``.
    allow_dangerous : bool
        First gate for ``--dangerously-skip-permissions``.
    cli_path : str
        Path or name of the ``claude`` CLI binary.
    timeout_s : int
        Maximum wall time for the CLI process.
    model : str or None
        Model override (e.g., "sonnet", "opus").  Passed to ``--model``.
        Note: the CLI uses the logged-in session's subscription, not an
        API key.  The model flag selects which model to use within that
        session.
    max_budget_usd : float or None
        Maximum dollar amount for API calls (``--max-budget-usd``).
    append_system_prompt : str or None
        Extra system prompt appended via ``--append-system-prompt``.
    bare : bool
        Use ``--bare`` mode (skip hooks, CLAUDE.md, auto-memory).
        **Warning**: ``--bare`` also skips OAuth keychain reads, so it
        only works with ``ANTHROPIC_API_KEY`` auth, not interactive login.
    """

    def __init__(
        self,
        prompt: str,
        work_dir: Path | str,
        mcp_config: dict[str, Any] | None = None,
        allow_dangerous: bool = False,
        cli_path: str = "claude",
        timeout_s: int = 1800,
        model: str | None = None,
        max_budget_usd: float | None = None,
        append_system_prompt: str | None = None,
        bare: bool = False,
    ):
        self.prompt = prompt
        self.work_dir = Path(work_dir)
        self.mcp_config = mcp_config
        self.allow_dangerous = allow_dangerous
        self.cli_path = cli_path
        self.timeout_s = timeout_s
        self.model = model
        self.max_budget_usd = max_budget_usd
        self.append_system_prompt = append_system_prompt
        self.bare = bare

        self._cli_version: str | None = None

    # ------------------------------------------------------------------
    # CLI version
    # ------------------------------------------------------------------

    async def get_cli_version(self) -> str:
        """Return the Claude CLI version string, caching the result."""
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
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=10,
            )
            self._cli_version = stdout.decode().strip()
        except Exception:
            self._cli_version = ""

        return self._cli_version

    # ------------------------------------------------------------------
    # MCP config
    # ------------------------------------------------------------------

    def _write_mcp_config(self) -> Path | None:
        """Write MCP config to a JSON file in work_dir, return path."""
        if not self.mcp_config:
            return None

        self.work_dir.mkdir(parents=True, exist_ok=True)
        config_path = self.work_dir / "mcp_config.json"
        config_path.write_text(json.dumps(self.mcp_config, indent=2))
        return config_path

    # ------------------------------------------------------------------
    # Build argv
    # ------------------------------------------------------------------

    def build_argv(self) -> list[str]:
        """Build the CLI argument list.

        Exposed for testing and dry-run inspection.
        """
        cli = shutil.which(self.cli_path)
        if not cli:
            return []

        argv = [
            cli,
            "--print",
            "--output-format", "json",
            "--no-session-persistence",
        ]

        if self.bare:
            argv.append("--bare")

        if self.model:
            argv.extend(["--model", self.model])
            logger.info(
                "CC CLI: --model %s passed. The CLI uses the logged-in "
                "session's subscription, not an API key.",
                self.model,
            )

        if self.max_budget_usd is not None:
            argv.extend(["--max-budget-usd", str(self.max_budget_usd)])

        if self.append_system_prompt:
            argv.extend(["--append-system-prompt", self.append_system_prompt])

        # Double gate: both flags must be true
        if self.allow_dangerous and os.environ.get("EDA_AGENTS_ALLOW_DANGEROUS") == "1":
            argv.append("--dangerously-skip-permissions")
            logger.warning(
                "CC CLI: --dangerously-skip-permissions enabled "
                "(allow_dangerous=True + EDA_AGENTS_ALLOW_DANGEROUS=1)"
            )

        mcp_path = self._write_mcp_config()
        if mcp_path:
            argv.extend(["--mcp-config", str(mcp_path)])

        return argv

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self) -> HarnessResult:
        """Execute the prompt via ``claude --print`` and return results.

        The prompt is piped via stdin.  Output is expected as a single
        JSON object on stdout (``--output-format json``).
        """
        cli_version = await self.get_cli_version()

        argv = self.build_argv()
        if not argv:
            return HarnessResult(
                success=False,
                error=f"Claude CLI not found: {self.cli_path!r}",
                cli_version="",
            )

        self.work_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "CC CLI: launching %s in %s (timeout=%ds)",
            " ".join(argv[:4]) + " ...",
            self.work_dir,
            self.timeout_s,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.work_dir),
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=self.prompt.encode("utf-8")),
                timeout=self.timeout_s,
            )

        except asyncio.TimeoutError:
            # Kill the process on timeout
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
                error="Claude CLI not found at resolved path",
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

        # Parse JSON output
        return self._parse_json_output(stdout_str, cli_version)

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_output(
        stdout: str,
        cli_version: str,
    ) -> HarnessResult:
        """Parse the JSON output from ``claude --print --output-format json``."""
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return HarnessResult(
                success=False,
                error=f"Failed to parse CLI JSON output: {exc}",
                result_text=stdout[-2000:],
                cli_version=cli_version,
            )

        is_success = (
            data.get("type") == "result"
            and data.get("subtype") == "success"
            and not data.get("is_error", True)
        )

        return HarnessResult(
            success=is_success,
            result_text=data.get("result", ""),
            duration_ms=data.get("duration_ms", 0.0),
            num_turns=data.get("num_turns", 0),
            total_cost_usd=data.get("total_cost_usd", 0.0),
            session_id=data.get("session_id", ""),
            model_usage=data.get("modelUsage", {}),
            raw_json=data,
            error=None if is_success else data.get("result", "Unknown error"),
            cli_version=cli_version,
        )
