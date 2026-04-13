"""Physical-flow slice runner.

Wraps ``LibreLaneRunner.run_flow(frm=..., to=...)`` for named
physical-design stages (floorplan, placement, CTS, routing, DRC, LVS).

The ``STAGE_TO_LIBRELANE`` mapping translates ``FlowStage`` enum values
to LibreLane step class names.  These names are inferred from the
76-step taxonomy observed in Phase 0 and will be verified against real
runs during integration testing.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.flow_stage import FlowStage, StageResult
from eda_agents.core.librelane_runner import LibreLaneRunner

logger = logging.getLogger(__name__)

# Mapping from FlowStage to LibreLane (frm, to) step class names.
# frm=None means "start from the beginning" (cumulative run).
# These are inferred from the 76-step taxonomy (field notes §1.5.9)
# and need verification during integration testing.
STAGE_TO_LIBRELANE: dict[FlowStage, tuple[str | None, str]] = {
    FlowStage.SYNTH: (None, "Yosys.Synthesis"),
    FlowStage.FLOORPLAN: (None, "OpenROAD.Floorplan"),
    FlowStage.PLACE: (None, "OpenROAD.DetailedPlacement"),
    FlowStage.CTS: (None, "OpenROAD.CTS"),
    FlowStage.ROUTE: (None, "OpenROAD.DetailedRouting"),
    FlowStage.SIGNOFF_DRC: (None, "KLayout.DRC"),
    FlowStage.SIGNOFF_LVS: (None, "Netgen.LVS"),
    FlowStage.SIGNOFF_STA: ("OpenROAD.STAPostPNR", "OpenROAD.STAPostPNR"),
}


class PhysicalSliceRunner:
    """Runs a named physical-design slice via LibreLane.

    Parameters
    ----------
    runner : LibreLaneRunner
        Pre-configured LibreLane runner for the target design.
    """

    def __init__(self, runner: LibreLaneRunner):
        self.runner = runner

    def run(
        self,
        stage: FlowStage,
        *,
        tag: str = "",
        overwrite: bool = True,
    ) -> StageResult:
        """Run a physical-design stage slice.

        Parameters
        ----------
        stage : FlowStage
            Which stage to run.  Must be in ``STAGE_TO_LIBRELANE``.
        tag : str
            Run tag for the LibreLane invocation.
        overwrite : bool
            Whether to overwrite an existing run directory.
        """
        t0 = time.monotonic()

        if stage not in STAGE_TO_LIBRELANE:
            return StageResult(
                stage=stage,
                success=False,
                error=(
                    f"Stage {stage.name} has no LibreLane mapping. "
                    f"Supported: {sorted(s.name for s in STAGE_TO_LIBRELANE)}"
                ),
                run_time_s=time.monotonic() - t0,
            )

        frm, to = STAGE_TO_LIBRELANE[stage]

        logger.info(
            "PhysicalSliceRunner: stage=%s frm=%s to=%s",
            stage.name, frm, to,
        )

        flow_result = self.runner.run_flow(
            frm=frm, to=to, tag=tag, overwrite=overwrite
        )
        elapsed = time.monotonic() - t0

        if not flow_result.success:
            return StageResult(
                stage=stage,
                success=False,
                error=flow_result.error or f"{stage.name} failed",
                log_tail=flow_result.log_tail,
                run_time_s=elapsed,
            )

        # Parse metrics from the run directory
        metrics_delta: dict[str, float] = {}
        artifacts: dict[str, Path] = {}

        run_dir = Path(flow_result.run_dir) if flow_result.run_dir else None
        if run_dir and run_dir.is_dir():
            metrics = FlowMetrics.from_librelane_run_dir(run_dir)
            metrics_delta = self._extract_stage_metrics(stage, metrics)
            artifacts["run_dir"] = run_dir

            # Track GDS if available (post-route or signoff)
            if flow_result.gds_path:
                artifacts["gds"] = Path(flow_result.gds_path)
            if flow_result.def_path:
                artifacts["def"] = Path(flow_result.def_path)

        return StageResult(
            stage=stage,
            success=True,
            metrics_delta=metrics_delta,
            artifacts=artifacts,
            log_tail=flow_result.log_tail,
            run_time_s=elapsed,
        )

    @staticmethod
    def _extract_stage_metrics(
        stage: FlowStage, metrics: FlowMetrics
    ) -> dict[str, float]:
        """Extract the metrics relevant to a specific stage."""
        delta: dict[str, float] = {}

        if stage in (FlowStage.SYNTH, FlowStage.FLOORPLAN, FlowStage.PLACE):
            if metrics.synth_cell_count is not None:
                delta["design__instance__count"] = metrics.synth_cell_count
            if metrics.die_area_um2 is not None:
                delta["design__die__area"] = metrics.die_area_um2

        if stage in (FlowStage.PLACE, FlowStage.CTS, FlowStage.ROUTE):
            if metrics.utilization_pct is not None:
                delta["design__instance__utilization"] = metrics.utilization_pct

        if stage == FlowStage.ROUTE:
            if metrics.wire_length_um is not None:
                delta["route__wirelength"] = metrics.wire_length_um
            if metrics.route_drc_errors is not None:
                delta["route__drc_errors"] = metrics.route_drc_errors

        if stage in (FlowStage.SIGNOFF_STA,):
            if metrics.wns_worst_ns is not None:
                delta["timing__setup__ws"] = metrics.wns_worst_ns
            if metrics.hold_wns_worst_ns is not None:
                delta["timing__hold__ws"] = metrics.hold_wns_worst_ns
            if metrics.power_total_w is not None:
                delta["power__total"] = metrics.power_total_w

        if stage == FlowStage.SIGNOFF_DRC:
            if metrics.klayout_drc_count is not None:
                delta["klayout__drc_error__count"] = metrics.klayout_drc_count
            if metrics.magic_drc_count is not None:
                delta["magic__drc_error__count"] = metrics.magic_drc_count

        if stage == FlowStage.SIGNOFF_LVS:
            if metrics.lvs_match is not None:
                delta["lvs__match"] = 1.0 if metrics.lvs_match else 0.0

        return delta
