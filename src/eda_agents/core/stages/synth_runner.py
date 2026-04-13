"""Synthesis stage runner.

Thin wrapper over ``LibreLaneRunner.run_flow(to="Yosys.Synthesis")``.
Parses synthesis metrics (cell count, area) from the resulting run
directory via ``FlowMetrics.from_librelane_run_dir``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.flow_stage import FlowStage, StageResult
from eda_agents.core.librelane_runner import LibreLaneRunner

logger = logging.getLogger(__name__)

# LibreLane step class name for synthesis
_SYNTH_STEP = "Yosys.Synthesis"


class SynthRunner:
    """Runs synthesis via LibreLane and returns a ``StageResult``.

    Parameters
    ----------
    runner : LibreLaneRunner
        Pre-configured LibreLane runner for the target design.
    """

    def __init__(self, runner: LibreLaneRunner):
        self.runner = runner

    def run(self, *, tag: str = "", overwrite: bool = True) -> StageResult:
        """Run synthesis and return metrics.

        Parameters
        ----------
        tag : str
            Run tag for the LibreLane invocation.
        overwrite : bool
            Whether to overwrite an existing run directory.
        """
        t0 = time.monotonic()

        flow_result = self.runner.run_flow(
            to=_SYNTH_STEP, tag=tag, overwrite=overwrite
        )
        elapsed = time.monotonic() - t0

        if not flow_result.success:
            return StageResult(
                stage=FlowStage.SYNTH,
                success=False,
                error=flow_result.error or "Synthesis failed",
                log_tail=flow_result.log_tail,
                run_time_s=elapsed,
            )

        # Parse metrics from the run directory
        metrics_delta: dict[str, float] = {}
        artifacts: dict[str, Path] = {}

        run_dir = Path(flow_result.run_dir) if flow_result.run_dir else None
        if run_dir and run_dir.is_dir():
            metrics = FlowMetrics.from_librelane_run_dir(run_dir)
            if metrics.synth_cell_count is not None:
                metrics_delta["design__instance__count"] = metrics.synth_cell_count
            if metrics.stdcell_count is not None:
                metrics_delta["design__instance__count__stdcell"] = metrics.stdcell_count
            if metrics.die_area_um2 is not None:
                metrics_delta["design__die__area"] = metrics.die_area_um2
            artifacts["run_dir"] = run_dir

        return StageResult(
            stage=FlowStage.SYNTH,
            success=True,
            metrics_delta=metrics_delta,
            artifacts=artifacts,
            log_tail=flow_result.log_tail,
            run_time_s=elapsed,
        )
