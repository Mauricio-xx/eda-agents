"""RTL simulation stage runner.

Supports two drivers:

* ``CocotbDriver`` — shells out to a Make target (e.g. ``make sim``)
  in the design's project directory.  Parses cocotb's summary table
  for PASS/FAIL counts.
* ``IVerilogDriver`` — runs ``iverilog`` + ``vvp`` directly for
  simpler designs that don't use cocotb.

The ``RtlSimRunner`` dispatches to the appropriate driver based on
the design's ``TestbenchSpec``.
"""

from __future__ import annotations

import logging
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path

from eda_agents.core.digital_design import DigitalDesign
from eda_agents.core.flow_stage import FlowStage, StageResult
from eda_agents.core.tool_environment import ToolEnvironment

logger = logging.getLogger(__name__)

# cocotb summary line: ** TESTS=7 PASS=7 FAIL=0 SKIP=0 ...
_COCOTB_SUMMARY_RE = re.compile(
    r"\*\*\s+TESTS=(\d+)\s+PASS=(\d+)\s+FAIL=(\d+)\s+SKIP=(\d+)"
)

# iverilog $finish / assertion patterns
_IVERILOG_FAIL_RE = re.compile(r"(?:FAIL|ERROR|ASSERT)", re.IGNORECASE)


class SimDriver(ABC):
    """Abstract simulation driver."""

    @abstractmethod
    def run(self) -> StageResult:
        ...


class CocotbDriver(SimDriver):
    """Runs a cocotb-based simulation via a Make/shell target.

    Parameters
    ----------
    design : DigitalDesign
        Design providing ``project_dir()`` and ``testbench()``.
    env : ToolEnvironment
        Execution environment.
    pdk_root : str or None
        Explicit PDK_ROOT to inject into the environment (F5 pattern).
    pdk : str
        PDK name (e.g. ``"gf180mcuD"``).
    timeout_s : int
        Maximum sim runtime.
    """

    def __init__(
        self,
        design: DigitalDesign,
        env: ToolEnvironment,
        *,
        pdk_root: str | None = None,
        pdk: str = "gf180mcuD",
        timeout_s: int = 600,
    ):
        self.design = design
        self.env = env
        self.pdk_root = pdk_root
        self.pdk = pdk
        self.timeout_s = timeout_s

    def run(self) -> StageResult:
        t0 = time.monotonic()

        tb = self.design.testbench()
        if tb is None:
            return StageResult(
                stage=FlowStage.RTL_SIM,
                success=False,
                error="Design does not define a testbench (testbench() returned None)",
                run_time_s=time.monotonic() - t0,
            )

        # Build the command from TestbenchSpec
        # target is typically "make sim" or a python script
        target = tb.target
        if target.startswith("make"):
            parts = target.split()
            cmd = parts  # ["make", "sim"] or ["make", "-C", "cocotb", "sim"]
        else:
            cmd = ["python3", target]

        # Resolve working directory
        work_dir = self.design.project_dir()
        if tb.work_dir_relative != ".":
            work_dir = work_dir / tb.work_dir_relative

        # Build environment with explicit PDK vars (F5 pattern)
        run_env = os.environ.copy()
        if self.pdk_root:
            run_env["PDK_ROOT"] = self.pdk_root
        run_env["PDK"] = self.pdk
        # Apply testbench-specific overrides
        run_env.update(tb.env_overrides)

        logger.info("CocotbDriver: %s in %s", " ".join(cmd), work_dir)

        try:
            proc = self.env.run(
                cmd,
                cwd=work_dir,
                env=run_env,
                timeout_s=self.timeout_s,
            )
        except FileNotFoundError:
            return StageResult(
                stage=FlowStage.RTL_SIM,
                success=False,
                error=f"Command not found: {cmd[0]}",
                run_time_s=time.monotonic() - t0,
            )

        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        elapsed = time.monotonic() - t0

        # Parse cocotb summary
        tests, passed, failed, skipped = 0, 0, 0, 0
        match = _COCOTB_SUMMARY_RE.search(combined)
        if match:
            tests = int(match.group(1))
            passed = int(match.group(2))
            failed = int(match.group(3))
            skipped = int(match.group(4))

        success = proc.returncode == 0 and failed == 0

        return StageResult(
            stage=FlowStage.RTL_SIM,
            success=success,
            metrics_delta={
                "sim_tests": tests,
                "sim_pass": passed,
                "sim_fail": failed,
                "sim_skip": skipped,
            },
            log_tail=combined[-2000:],
            run_time_s=elapsed,
            error=f"{failed}/{tests} tests failed" if failed > 0 else (
                f"Sim exited with code {proc.returncode}" if proc.returncode != 0 else None
            ),
        )


class IVerilogDriver(SimDriver):
    """Runs simulation via iverilog + vvp.

    Parameters
    ----------
    design : DigitalDesign
        Design providing ``rtl_sources()``.
    env : ToolEnvironment
        Execution environment.
    tb_path : Path
        Path to the testbench file.
    timeout_s : int
        Maximum sim runtime.
    """

    def __init__(
        self,
        design: DigitalDesign,
        env: ToolEnvironment,
        *,
        tb_path: Path | None = None,
        timeout_s: int = 300,
    ):
        self.design = design
        self.env = env
        self.tb_path = tb_path
        self.timeout_s = timeout_s

    def run(self) -> StageResult:
        t0 = time.monotonic()

        if not self.env.which("iverilog"):
            return StageResult(
                stage=FlowStage.RTL_SIM,
                success=False,
                error="iverilog not found on PATH",
                run_time_s=time.monotonic() - t0,
            )

        sources = self.design.rtl_sources()
        if not sources:
            return StageResult(
                stage=FlowStage.RTL_SIM,
                success=False,
                error="No RTL sources provided by design.rtl_sources()",
                run_time_s=time.monotonic() - t0,
            )

        all_sources = [str(s) for s in sources]
        if self.tb_path:
            all_sources.append(str(self.tb_path))

        work_dir = self.design.project_dir()
        sim_out = work_dir / "sim.out"

        # Compile
        compile_cmd = ["iverilog", "-sv", "-o", str(sim_out), *all_sources]
        logger.info("IVerilogDriver compile: %s", " ".join(compile_cmd))

        try:
            proc_compile = self.env.run(
                compile_cmd, cwd=work_dir, timeout_s=self.timeout_s
            )
        except FileNotFoundError:
            return StageResult(
                stage=FlowStage.RTL_SIM,
                success=False,
                error="iverilog executable not found",
                run_time_s=time.monotonic() - t0,
            )

        if proc_compile.returncode != 0:
            combined = (proc_compile.stdout or "") + "\n" + (proc_compile.stderr or "")
            return StageResult(
                stage=FlowStage.RTL_SIM,
                success=False,
                error="iverilog compilation failed",
                log_tail=combined[-2000:],
                run_time_s=time.monotonic() - t0,
            )

        # Simulate
        sim_cmd = ["vvp", str(sim_out)]
        logger.info("IVerilogDriver simulate: %s", " ".join(sim_cmd))

        try:
            proc_sim = self.env.run(
                sim_cmd, cwd=work_dir, timeout_s=self.timeout_s
            )
        except FileNotFoundError:
            return StageResult(
                stage=FlowStage.RTL_SIM,
                success=False,
                error="vvp executable not found",
                run_time_s=time.monotonic() - t0,
            )

        combined = (proc_sim.stdout or "") + "\n" + (proc_sim.stderr or "")
        elapsed = time.monotonic() - t0

        # Heuristic pass/fail from output
        has_fail = bool(_IVERILOG_FAIL_RE.search(combined))
        success = proc_sim.returncode == 0 and not has_fail

        return StageResult(
            stage=FlowStage.RTL_SIM,
            success=success,
            metrics_delta={
                "sim_pass": 1 if success else 0,
                "sim_fail": 1 if has_fail else 0,
            },
            log_tail=combined[-2000:],
            run_time_s=elapsed,
            error="Simulation reported failures" if has_fail else (
                f"vvp exited with code {proc_sim.returncode}" if proc_sim.returncode != 0 else None
            ),
        )


class RtlSimRunner:
    """Dispatches RTL simulation to the appropriate driver.

    Parameters
    ----------
    design : DigitalDesign
        Design under test.
    env : ToolEnvironment
        Execution environment.
    pdk_root : str or None
        Explicit PDK_ROOT for cocotb sims (F5 pattern).
    pdk : str
        PDK name.
    timeout_s : int
        Maximum sim runtime.
    """

    def __init__(
        self,
        design: DigitalDesign,
        env: ToolEnvironment,
        *,
        pdk_root: str | None = None,
        pdk: str = "gf180mcuD",
        timeout_s: int = 600,
    ):
        self.design = design
        self.env = env
        self.pdk_root = pdk_root
        self.pdk = pdk
        self.timeout_s = timeout_s

    def run(self) -> StageResult:
        """Run simulation using the driver specified by the design's testbench."""
        tb = self.design.testbench()

        if tb is None:
            # No testbench defined — try iverilog if sources exist
            driver = IVerilogDriver(
                self.design, self.env, timeout_s=self.timeout_s
            )
            return driver.run()

        if tb.driver == "cocotb":
            driver = CocotbDriver(
                self.design,
                self.env,
                pdk_root=self.pdk_root,
                pdk=self.pdk,
                timeout_s=self.timeout_s,
            )
        elif tb.driver == "iverilog":
            tb_path = None
            if tb.target and not tb.target.startswith("make"):
                tb_path = self.design.project_dir() / tb.target
            driver = IVerilogDriver(
                self.design,
                self.env,
                tb_path=tb_path,
                timeout_s=self.timeout_s,
            )
        else:
            return StageResult(
                stage=FlowStage.RTL_SIM,
                success=False,
                error=f"Unknown sim driver: {tb.driver!r}",
            )

        return driver.run()
