"""Claude Code CLI runner for digital RTL-to-GDS flows.

Builds a prompt from ``DigitalDesign`` metadata and drives the full
flow via ``ClaudeCodeHarness`` (``claude --print``).  The CLI agent
uses its built-in Bash/Read/Write tools to invoke LibreLane, read
reports, and modify config.

Usage::

    from eda_agents.agents.digital_cc_runner import DigitalClaudeCodeRunner
    from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign

    runner = DigitalClaudeCodeRunner(
        design=FazyRvHachureDesign(),
        work_dir=Path("./cc_results"),
    )
    result = await runner.run()
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from eda_agents.core.digital_design import DigitalDesign

logger = logging.getLogger(__name__)


class DigitalClaudeCodeRunner:
    """Drive a digital RTL-to-GDS flow via Claude Code CLI.

    Parameters
    ----------
    design : DigitalDesign
        Design to process.
    work_dir : Path
        Working directory for the CLI process and helper scripts.
    allow_dangerous : bool
        First gate for ``--dangerously-skip-permissions``.
    cli_path : str
        Path or name of the ``claude`` CLI binary.
    timeout_s : int
        Maximum wall time for the full flow.
    model : str or None
        Model override passed to ``--model`` (e.g., "sonnet").
    max_budget_usd : float or None
        Maximum dollar amount for API calls.
    """

    def __init__(
        self,
        design: DigitalDesign,
        work_dir: Path | str,
        allow_dangerous: bool = False,
        cli_path: str = "claude",
        timeout_s: int = 3600,
        model: str | None = None,
        max_budget_usd: float | None = None,
    ):
        self.design = design
        self.work_dir = Path(work_dir)
        self.allow_dangerous = allow_dangerous
        self.cli_path = cli_path
        self.timeout_s = timeout_s
        self.model = model
        self.max_budget_usd = max_budget_usd

    def _build_prompt(self) -> str:
        """Build the prompt for the CC CLI agent."""
        from eda_agents.agents.tool_defs import build_digital_rtl2gds_prompt

        return build_digital_rtl2gds_prompt(self.design)

    def _write_helper_script(self) -> str:
        """Write the query_flow.py helper script, return its path."""
        from eda_agents.agents.tool_defs import write_librelane_flow_script

        return write_librelane_flow_script(str(self.work_dir), self.design)

    def dry_run(self) -> dict[str, Any]:
        """Return the prompt and configuration without executing.

        Useful for inspecting what would be sent to the CLI.
        """
        from eda_agents.agents.claude_code_harness import ClaudeCodeHarness

        prompt = self._build_prompt()
        harness = ClaudeCodeHarness(
            prompt=prompt,
            work_dir=self.work_dir,
            allow_dangerous=self.allow_dangerous,
            cli_path=self.cli_path,
            timeout_s=self.timeout_s,
            model=self.model,
            max_budget_usd=self.max_budget_usd,
        )
        argv = harness.build_argv()

        return {
            "design": self.design.project_name(),
            "prompt": prompt,
            "prompt_length": len(prompt),
            "work_dir": str(self.work_dir),
            "cli_path": self.cli_path,
            "argv": argv,
            "timeout_s": self.timeout_s,
            "model": self.model,
            "allow_dangerous": self.allow_dangerous,
            "max_budget_usd": self.max_budget_usd,
        }

    async def run(self, dry_run: bool = False) -> dict[str, Any]:
        """Run the full digital RTL-to-GDS flow via Claude Code CLI.

        Parameters
        ----------
        dry_run : bool
            If True, return prompt and config without executing.
        """
        if dry_run:
            return self.dry_run()

        from eda_agents.agents.claude_code_harness import (
            ClaudeCodeHarness,
        )

        prompt = self._build_prompt()

        # Write helper script for the agent to use
        try:
            script_path = self._write_helper_script()
            logger.info("Helper script written to %s", script_path)
        except Exception as exc:
            logger.warning("Failed to write helper script: %s", exc)

        harness = ClaudeCodeHarness(
            prompt=prompt,
            work_dir=self.work_dir,
            allow_dangerous=self.allow_dangerous,
            cli_path=self.cli_path,
            timeout_s=self.timeout_s,
            model=self.model,
            max_budget_usd=self.max_budget_usd,
        )

        result = await harness.run()

        # Parse metrics from the agent's output
        parsed = self._parse_result(result.result_text)

        return {
            "design": self.design.project_name(),
            "success": result.success,
            "cli_version": result.cli_version,
            "result_text": result.result_text,
            "duration_ms": result.duration_ms,
            "num_turns": result.num_turns,
            "cost_usd": result.total_cost_usd,
            "error": result.error,
            **parsed,
        }

    @staticmethod
    def _parse_result(text: str) -> dict[str, Any]:
        """Extract metrics from the CLI agent's final report.

        Best-effort regex extraction. Returns what it can find.
        """
        parsed: dict[str, Any] = {}

        if not text:
            return parsed

        # Check for DONE sentinel
        parsed["done"] = "DONE" in text.upper()

        # Check verdict
        text_upper = text.upper()
        if "SIGNOFF CLEAN" in text_upper or "TAPEOUT READY" in text_upper:
            parsed["verdict"] = "SIGNOFF_CLEAN"
        elif "BLOCKED" in text_upper:
            parsed["verdict"] = "BLOCKED"
        else:
            parsed["verdict"] = "UNKNOWN"

        # Extract WNS
        wns_match = re.search(r"WNS[:\s]*([+-]?\d+\.?\d*)\s*ns", text, re.IGNORECASE)
        if wns_match:
            parsed["wns_ns"] = float(wns_match.group(1))

        # Extract cell count
        cell_match = re.search(r"cell\s*count[:\s]*(\d[\d,]*)", text, re.IGNORECASE)
        if cell_match:
            parsed["cell_count"] = int(cell_match.group(1).replace(",", ""))

        # Extract DRC count
        drc_match = re.search(r"DRC[:\s]*(\d+)\s*violation", text, re.IGNORECASE)
        if drc_match:
            parsed["drc_count"] = int(drc_match.group(1))
        elif re.search(r"DRC[:\s]*(clean|0 violation|zero)", text, re.IGNORECASE):
            parsed["drc_count"] = 0

        # Extract LVS
        if re.search(r"LVS[:\s]*(match|passed|clean)", text, re.IGNORECASE):
            parsed["lvs_match"] = True
        elif re.search(r"LVS[:\s]*(mismatch|failed)", text, re.IGNORECASE):
            parsed["lvs_match"] = False

        return parsed
