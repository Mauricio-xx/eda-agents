"""Signoff STA stage runner.

Wraps ``LibreLaneRunner.run_flow(frm="OpenROAD.STAPostPNR",
to="OpenROAD.STAPostPNR")`` to run post-PnR static timing analysis.

STA only works after a full PnR run exists (DEF + SPEF artifacts must
be present in the run directory).  The runner extracts per-corner WNS,
hold WNS, and power from ``FlowMetrics``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.flow_stage import FlowStage, StageResult
from eda_agents.core.librelane_runner import LibreLaneRunner

logger = logging.getLogger(__name__)

_STA_STEP = "OpenROAD.STAPostPNR"


class StaRunner:
    """Runs signoff STA via LibreLane and returns timing metrics.

    Parameters
    ----------
    runner : LibreLaneRunner
        Pre-configured LibreLane runner.  The target run directory
        must contain PnR artifacts (DEF, SPEF) for STA to operate on.
    """

    def __init__(self, runner: LibreLaneRunner):
        self.runner = runner

    def run(self, *, tag: str = "", overwrite: bool = True) -> StageResult:
        """Run signoff STA and return per-corner timing metrics.

        Parameters
        ----------
        tag : str
            Run tag for the LibreLane invocation.
        overwrite : bool
            Whether to overwrite existing STA results.
        """
        t0 = time.monotonic()

        flow_result = self.runner.run_flow(
            frm=_STA_STEP, to=_STA_STEP, tag=tag, overwrite=overwrite
        )
        elapsed = time.monotonic() - t0

        if not flow_result.success:
            return StageResult(
                stage=FlowStage.SIGNOFF_STA,
                success=False,
                error=flow_result.error or "STA failed",
                log_tail=flow_result.log_tail,
                run_time_s=elapsed,
            )

        # Parse timing metrics from run directory
        metrics_delta: dict[str, float] = {}
        artifacts: dict[str, Path] = {}

        run_dir = Path(flow_result.run_dir) if flow_result.run_dir else None
        if run_dir and run_dir.is_dir():
            metrics = FlowMetrics.from_librelane_run_dir(run_dir)

            if metrics.wns_worst_ns is not None:
                metrics_delta["timing__setup__ws"] = metrics.wns_worst_ns
            if metrics.tns_worst_ns is not None:
                metrics_delta["timing__setup__tns"] = metrics.tns_worst_ns
            if metrics.hold_wns_worst_ns is not None:
                metrics_delta["timing__hold__ws"] = metrics.hold_wns_worst_ns
            if metrics.power_total_w is not None:
                metrics_delta["power__total"] = metrics.power_total_w
            if metrics.power_internal_w is not None:
                metrics_delta["power__internal__total"] = metrics.power_internal_w
            if metrics.power_switching_w is not None:
                metrics_delta["power__switching__total"] = metrics.power_switching_w

            # Per-corner WNS as individual metrics
            for corner, wns in metrics.wns_per_corner.items():
                metrics_delta[f"timing__setup__ws__corner:{corner}"] = wns

            artifacts["run_dir"] = run_dir

        return StageResult(
            stage=FlowStage.SIGNOFF_STA,
            success=True,
            metrics_delta=metrics_delta,
            artifacts=artifacts,
            log_tail=flow_result.log_tail,
            run_time_s=elapsed,
        )
