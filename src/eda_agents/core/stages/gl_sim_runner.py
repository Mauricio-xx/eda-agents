"""Gate-level simulation stage runner.

Runs ``iverilog`` + ``vvp`` against an already-hardened LibreLane run
directory using the same testbench the agent authored for RTL sim.
This catches a class of bugs that RTL sim cannot see:

* Stdcell mapping regressions (Yosys emits the wrong cell for a
  primitive).
* Reset / X-propagation issues exposed when every register starts in
  ``x`` instead of a behavioural initial value.
* Clock-gating / enable corruption introduced during synthesis.

Two modes share the same class:

* :meth:`GlSimRunner.run_post_synth` — against the Yosys post-synth
  netlist at ``runs/<tag>/*-yosys-synthesis/<design>.nl.v``. No timing
  annotation; purely functional.
* :meth:`GlSimRunner.run_post_pnr` — against the post-PnR netlist at
  ``runs/<tag>/final/pnl/<design>.pnl.v``, with SDF annotation from
  ``runs/<tag>/final/sdf/<corner>/<design>__<corner>.sdf``. The runner
  generates a small wrapper that calls ``$sdf_annotate`` on the DUT
  instance identified by
  :meth:`DigitalDesign.gl_sim_dut_instance_path`.

The public interface is PDK-agnostic; all PDK-specific values (stdcell
model glob, default STA corner) live in :class:`PdkConfig`. Tests cover
IHP SG13G2 and GF180MCU through the parametrised ``pdk_config``
fixture.
"""

from __future__ import annotations

import glob
import logging
import re
import time
from pathlib import Path

from eda_agents.core.digital_design import DigitalDesign
from eda_agents.core.flow_stage import FlowStage, StageResult
from eda_agents.core.pdk import PdkConfig
from eda_agents.core.tool_environment import ToolEnvironment

logger = logging.getLogger(__name__)

# Same pass/fail heuristic the IVerilogDriver uses for RTL sim.
_SIM_FAIL_RE = re.compile(r"\b(FAIL|ERROR|ASSERT)\b", re.IGNORECASE)
_SIM_PASS_RE = re.compile(r"\bPASS\b")

# iverilog SDF-annotation warnings we want to count but not fatalise.
_SDF_WARN_RE = re.compile(r"sdf (warning|error)|negative delay", re.IGNORECASE)


class GlSimRunner:
    """Run gate-level simulation against a LibreLane run directory.

    Parameters
    ----------
    design
        Design under test. Must expose ``testbench()`` and, for
        simulations to anchor properly, a stable DUT instance path
        (``gl_sim_dut_instance_path``).
    env
        Execution environment used to invoke ``iverilog`` / ``vvp``.
    run_dir
        LibreLane run directory (contains ``*-yosys-synthesis/``,
        ``final/pnl/``, ``final/sdf/``). Path must exist; absence is
        surfaced as a runner failure, never as success.
    pdk_config
        Active PDK config. Supplies the stdcell Verilog models glob
        and the default STA corner.
    pdk_root
        PDK root (absolute path) against which
        ``pdk_config.stdcell_verilog_models_glob`` is expanded.
    design_name
        Top-level module name. Defaults to ``design.project_name()``.
        Netlists and SDFs are discovered by globbing on this name.
    timeout_s
        Per-invocation timeout. Post-PnR SDF annotation can be slow, so
        the default is 900 s; callers that know their design is small
        can shorten it.
    """

    def __init__(
        self,
        design: DigitalDesign,
        env: ToolEnvironment,
        *,
        run_dir: Path,
        pdk_config: PdkConfig,
        pdk_root: Path | str,
        design_name: str | None = None,
        timeout_s: int = 900,
    ):
        self.design = design
        self.env = env
        self.run_dir = Path(run_dir)
        self.pdk_config = pdk_config
        self.pdk_root = Path(pdk_root)
        self.design_name = design_name or design.project_name()
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run_post_synth(self) -> StageResult:
        """Functional GL sim against the post-synth netlist."""
        t0 = time.monotonic()

        netlist = self._find_post_synth_netlist()
        if netlist is None:
            return self._fail(
                FlowStage.POST_SYNTH_SIM,
                "Post-synth netlist not found under "
                f"{self.run_dir}/*-yosys-synthesis/{self.design_name}.nl.v",
                t0,
            )

        cell_sources = self._resolve_cell_sources()
        if not cell_sources:
            return self._fail(
                FlowStage.POST_SYNTH_SIM,
                "No stdcell Verilog models resolved: "
                f"glob {self._cells_glob()!r} matched nothing under "
                f"{self.pdk_root}",
                t0,
            )

        tb_path = self._tb_path()
        if tb_path is None:
            return self._fail(
                FlowStage.POST_SYNTH_SIM,
                "Design has no iverilog-compatible testbench "
                "(testbench() returned None or non-iverilog driver)",
                t0,
            )

        work_dir = self.run_dir / "gl_sim" / "post_synth"
        work_dir.mkdir(parents=True, exist_ok=True)

        return self._invoke(
            stage=FlowStage.POST_SYNTH_SIM,
            work_dir=work_dir,
            sources=[*cell_sources, str(netlist), str(tb_path)],
            sdf_path=None,
            t0=t0,
        )

    # run_post_pnr is introduced in the SDF-annotation commit; defined
    # here as a stub so callers that probe via hasattr get a clean
    # NotImplementedError rather than AttributeError.
    def run_post_pnr(self, corner: str | None = None) -> StageResult:
        """Post-PnR GL sim with SDF annotation.

        Not implemented in this commit; arrives with the post-PnR SDF
        gate. The ``corner`` parameter will select a specific SDF under
        ``final/sdf/<corner>/``; when ``None``, the runner will fall
        back to :attr:`PdkConfig.default_sta_corner`.
        """
        raise NotImplementedError(
            "run_post_pnr requires the SDF-annotation commit"
        )

    # ------------------------------------------------------------------
    # Discovery helpers
    # ------------------------------------------------------------------

    def _find_post_synth_netlist(self) -> Path | None:
        """Glob for ``<run>/*-yosys-synthesis/<design>.nl.v``."""
        pattern = str(
            self.run_dir / "*-yosys-synthesis" / f"{self.design_name}.nl.v"
        )
        matches = sorted(glob.glob(pattern))
        if not matches:
            return None
        if len(matches) > 1:
            logger.warning(
                "Multiple post-synth netlists matched %s; using %s",
                pattern, matches[-1],
            )
        return Path(matches[-1])

    def _cells_glob(self) -> str:
        """Effective glob for stdcell Verilog models.

        Order: design override -> PdkConfig default. Empty string if
        neither is set.
        """
        return (
            self.design.gl_sim_cells_glob()
            or self.pdk_config.stdcell_verilog_models_glob
            or ""
        )

    def _resolve_cell_sources(self) -> list[str]:
        """Expand the cells glob to absolute paths."""
        rel = self._cells_glob()
        if not rel:
            return []
        pattern = str(self.pdk_root / rel)
        return sorted(glob.glob(pattern))

    def _tb_path(self) -> Path | None:
        """Resolve the testbench file the agent authored.

        Reuses the exact TB the RTL sim stage ran against. For cocotb
        targets we return ``None`` (GL sim via cocotb is out of scope
        for now — cocotb tests live behind a Python layer that would
        need separate plumbing).
        """
        tb = self.design.testbench()
        if tb is None or tb.driver != "iverilog":
            return None
        if not tb.target or tb.target.startswith("make"):
            return None
        candidate = self.design.project_dir() / tb.target
        return candidate if candidate.is_file() else None

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    def _invoke(
        self,
        *,
        stage: FlowStage,
        work_dir: Path,
        sources: list[str],
        sdf_path: Path | None,
        t0: float,
    ) -> StageResult:
        """Shared compile + run pipeline for both GL sim modes."""
        if not self.env.which("iverilog"):
            return self._fail(stage, "iverilog not found on PATH", t0)

        sim_out = work_dir / "sim.out"
        compile_cmd = ["iverilog", "-g2012", "-o", str(sim_out), *sources]
        logger.info("GlSimRunner compile: %s", " ".join(compile_cmd))

        try:
            proc_c = self.env.run(
                compile_cmd, cwd=work_dir, timeout_s=self.timeout_s
            )
        except FileNotFoundError:
            return self._fail(stage, "iverilog executable not found", t0)

        if proc_c.returncode != 0:
            combined = (proc_c.stdout or "") + "\n" + (proc_c.stderr or "")
            return StageResult(
                stage=stage,
                success=False,
                error="iverilog compilation failed",
                log_tail=combined[-2000:],
                run_time_s=time.monotonic() - t0,
            )

        sim_cmd = ["vvp", str(sim_out)]
        logger.info("GlSimRunner simulate: %s", " ".join(sim_cmd))
        try:
            proc_s = self.env.run(
                sim_cmd, cwd=work_dir, timeout_s=self.timeout_s
            )
        except FileNotFoundError:
            return self._fail(stage, "vvp executable not found", t0)

        combined = (proc_s.stdout or "") + "\n" + (proc_s.stderr or "")
        elapsed = time.monotonic() - t0

        # Pass/fail heuristic: PASS marker + no FAIL/ERROR/ASSERT hits +
        # vvp exit 0. We require an explicit PASS because a TB that
        # never runs the check (e.g. hung in reset) would silently
        # produce no FAIL output and look clean otherwise.
        has_fail = bool(_SIM_FAIL_RE.search(combined))
        has_pass = bool(_SIM_PASS_RE.search(combined))
        sdf_warnings = (
            len(_SDF_WARN_RE.findall(combined)) if sdf_path is not None else 0
        )
        success = proc_s.returncode == 0 and not has_fail and has_pass

        metrics: dict[str, float] = {
            "gl_sim_pass": 1 if success else 0,
            "gl_sim_fail": 1 if has_fail else 0,
        }
        if sdf_path is not None:
            metrics["gl_sim_sdf_warnings"] = sdf_warnings

        error: str | None
        if proc_s.returncode != 0:
            error = f"vvp exited with code {proc_s.returncode}"
        elif has_fail:
            error = "Simulation reported FAIL/ERROR/ASSERT"
        elif not has_pass:
            error = "Simulation did not print PASS marker"
        else:
            error = None

        return StageResult(
            stage=stage,
            success=success,
            metrics_delta=metrics,
            log_tail=combined[-2000:],
            run_time_s=elapsed,
            error=error,
        )

    @staticmethod
    def _fail(stage: FlowStage, message: str, t0: float) -> StageResult:
        return StageResult(
            stage=stage,
            success=False,
            error=message,
            run_time_s=time.monotonic() - t0,
        )
