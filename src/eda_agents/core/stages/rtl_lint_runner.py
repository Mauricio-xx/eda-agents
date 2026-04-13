"""RTL lint stage runner.

Primary tool: ``verilator --lint-only``.
Fallback: ``yosys -p "read_verilog -sv ...;  hierarchy -check"`` when
verilator is not on PATH.

Accepts a ``DigitalDesign`` for source paths and a ``ToolEnvironment``
for tool discovery and execution.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from eda_agents.core.digital_design import DigitalDesign
from eda_agents.core.flow_stage import FlowStage, StageResult
from eda_agents.core.tool_environment import ToolEnvironment

logger = logging.getLogger(__name__)


def _parse_verilator_output(output: str) -> tuple[int, int]:
    """Extract warning and error counts from verilator stderr/stdout.

    Skips the ``%Error: Exiting due to N error(s)`` summary line to
    avoid double-counting.

    Returns (warnings, errors).
    """
    warnings = 0
    errors = 0
    for line in output.splitlines():
        if "%Warning" in line:
            warnings += 1
        elif "%Error:" in line and "Exiting due to" not in line:
            errors += 1
    return warnings, errors


def _parse_yosys_output(output: str) -> tuple[int, int]:
    """Extract warning and error counts from yosys output.

    Returns (warnings, errors).
    """
    warnings = 0
    errors = 0
    for line in output.splitlines():
        if line.startswith("Warning:"):
            warnings += 1
        elif line.startswith("ERROR:") or "error:" in line.lower():
            errors += 1
    return warnings, errors


class RtlLintRunner:
    """Runs RTL lint and returns a ``StageResult``.

    Parameters
    ----------
    design : DigitalDesign
        Design whose ``rtl_sources()`` provides file paths to lint.
    env : ToolEnvironment
        Tool discovery and execution environment.
    extra_flags : list[str]
        Additional flags passed to verilator (e.g. ``["-Wall"]``).
    timeout_s : int
        Maximum lint runtime in seconds.
    """

    def __init__(
        self,
        design: DigitalDesign,
        env: ToolEnvironment,
        *,
        extra_flags: list[str] | None = None,
        timeout_s: int = 120,
    ):
        self.design = design
        self.env = env
        self.extra_flags = extra_flags or []
        self.timeout_s = timeout_s

    def run(self) -> StageResult:
        """Execute RTL lint and return the result."""
        t0 = time.monotonic()

        sources = self.design.rtl_sources()
        if not sources:
            return StageResult(
                stage=FlowStage.RTL_LINT,
                success=False,
                error="No RTL sources provided by design.rtl_sources()",
                run_time_s=time.monotonic() - t0,
            )

        # Try verilator first, fall back to yosys
        if self.env.which("verilator"):
            return self._run_verilator(sources, t0)

        if self.env.which("yosys"):
            logger.info("verilator not found, falling back to yosys for lint")
            return self._run_yosys(sources, t0)

        return StageResult(
            stage=FlowStage.RTL_LINT,
            success=False,
            error="Neither verilator nor yosys found on PATH",
            run_time_s=time.monotonic() - t0,
        )

    def _run_verilator(
        self, sources: list[Path], t0: float
    ) -> StageResult:
        cmd = [
            "verilator",
            "--lint-only",
            "-sv",
            *self.extra_flags,
            *[str(s) for s in sources],
        ]
        logger.info("RtlLintRunner: %s", " ".join(cmd))

        try:
            proc = self.env.run(
                cmd,
                cwd=self.design.project_dir(),
                timeout_s=self.timeout_s,
            )
        except FileNotFoundError:
            return StageResult(
                stage=FlowStage.RTL_LINT,
                success=False,
                error="verilator executable not found",
                run_time_s=time.monotonic() - t0,
            )

        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        warnings, errors = _parse_verilator_output(combined)
        elapsed = time.monotonic() - t0

        return StageResult(
            stage=FlowStage.RTL_LINT,
            success=errors == 0,
            metrics_delta={
                "lint_warnings": warnings,
                "lint_errors": errors,
            },
            log_tail=combined[-2000:],
            run_time_s=elapsed,
            error=f"{errors} lint errors" if errors > 0 else None,
        )

    def _run_yosys(
        self, sources: list[Path], t0: float
    ) -> StageResult:
        read_cmds = "; ".join(
            f"read_verilog -sv {s}" for s in sources
        )
        script = f"{read_cmds}; hierarchy -check"
        cmd = ["yosys", "-p", script]
        logger.info("RtlLintRunner (yosys fallback): %s", " ".join(cmd))

        try:
            proc = self.env.run(
                cmd,
                cwd=self.design.project_dir(),
                timeout_s=self.timeout_s,
            )
        except FileNotFoundError:
            return StageResult(
                stage=FlowStage.RTL_LINT,
                success=False,
                error="yosys executable not found",
                run_time_s=time.monotonic() - t0,
            )

        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        warnings, errors = _parse_yosys_output(combined)
        elapsed = time.monotonic() - t0

        # yosys returns non-zero on errors
        success = proc.returncode == 0 and errors == 0

        return StageResult(
            stage=FlowStage.RTL_LINT,
            success=success,
            metrics_delta={
                "lint_warnings": warnings,
                "lint_errors": errors,
            },
            log_tail=combined[-2000:],
            run_time_s=elapsed,
            error=f"{errors} lint errors" if errors > 0 else None,
        )
