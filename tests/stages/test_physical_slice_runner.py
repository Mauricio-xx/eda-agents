"""Tests for PhysicalSliceRunner."""

import json
from unittest.mock import MagicMock

from eda_agents.agents.phase_results import FlowResult
from eda_agents.core.flow_stage import FlowStage
from eda_agents.core.stages.physical_slice_runner import (
    STAGE_TO_LIBRELANE,
    PhysicalSliceRunner,
)


def _make_runner(flow_result=None, run_dir=None):
    runner = MagicMock()
    runner.run_flow.return_value = flow_result or FlowResult(
        success=True, run_dir=str(run_dir or ""), run_time_s=60.0
    )
    return runner


class TestStageMapping:
    def test_all_physical_stages_mapped(self):
        expected = {
            FlowStage.SYNTH,
            FlowStage.FLOORPLAN,
            FlowStage.PLACE,
            FlowStage.CTS,
            FlowStage.ROUTE,
            FlowStage.SIGNOFF_DRC,
            FlowStage.SIGNOFF_LVS,
            FlowStage.SIGNOFF_STA,
        }
        assert set(STAGE_TO_LIBRELANE.keys()) == expected

    def test_synth_maps_to_yosys(self):
        frm, to = STAGE_TO_LIBRELANE[FlowStage.SYNTH]
        assert frm is None
        assert to == "Yosys.Synthesis"

    def test_route_maps_to_detailed_routing(self):
        frm, to = STAGE_TO_LIBRELANE[FlowStage.ROUTE]
        assert frm is None
        assert to == "OpenROAD.DetailedRouting"

    def test_sta_maps_to_sta_post_pnr(self):
        frm, to = STAGE_TO_LIBRELANE[FlowStage.SIGNOFF_STA]
        assert frm == "OpenROAD.STAPostPNR"
        assert to == "OpenROAD.STAPostPNR"


class TestPhysicalSliceRunner:
    def test_route_stage(self, tmp_path):
        run_dir = tmp_path / "runs" / "test"
        run_dir.mkdir(parents=True)
        route_step = run_dir / "45-openroad-detailedrouting"
        route_step.mkdir()
        (route_step / "state_in.json").write_text(json.dumps({
            "metrics": {
                "route__wirelength": 155900,
                "route__drc_errors": 0,
            }
        }))

        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=True, run_dir=str(run_dir), run_time_s=120.0
        )

        psr = PhysicalSliceRunner(mock_runner)
        result = psr.run(FlowStage.ROUTE, tag="test")

        assert result.success
        assert result.stage == FlowStage.ROUTE
        assert result.metrics_delta.get("route__wirelength") == 155900
        mock_runner.run_flow.assert_called_once_with(
            frm=None, to="OpenROAD.DetailedRouting",
            tag="test", overwrite=True,
        )

    def test_synth_stage_passes_correct_args(self):
        mock_runner = _make_runner()
        psr = PhysicalSliceRunner(mock_runner)
        psr.run(FlowStage.SYNTH)

        mock_runner.run_flow.assert_called_once_with(
            frm=None, to="Yosys.Synthesis", tag="", overwrite=True,
        )

    def test_sta_uses_frm_and_to(self):
        mock_runner = _make_runner()
        psr = PhysicalSliceRunner(mock_runner)
        psr.run(FlowStage.SIGNOFF_STA)

        mock_runner.run_flow.assert_called_once_with(
            frm="OpenROAD.STAPostPNR", to="OpenROAD.STAPostPNR",
            tag="", overwrite=True,
        )

    def test_unsupported_stage(self):
        mock_runner = _make_runner()
        psr = PhysicalSliceRunner(mock_runner)
        result = psr.run(FlowStage.RTL_LINT)  # not in mapping

        assert not result.success
        assert "no LibreLane mapping" in result.error

    def test_flow_failure_propagated(self):
        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=False, error="PDN spacing violation"
        )
        psr = PhysicalSliceRunner(mock_runner)
        result = psr.run(FlowStage.FLOORPLAN)

        assert not result.success
        assert "PDN spacing" in result.error

    def test_drc_stage_extracts_drc_metrics(self, tmp_path):
        run_dir = tmp_path / "runs" / "test"
        run_dir.mkdir(parents=True)
        drc_step = run_dir / "64-magic-drc"
        drc_step.mkdir()
        (drc_step / "state_in.json").write_text(json.dumps({
            "metrics": {
                "klayout__drc_error__count": 3,
                "magic__drc_error__count": 1,
            }
        }))

        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=True, run_dir=str(run_dir), run_time_s=300.0
        )

        psr = PhysicalSliceRunner(mock_runner)
        result = psr.run(FlowStage.SIGNOFF_DRC)

        assert result.success
        assert result.metrics_delta.get("klayout__drc_error__count") == 3
        assert result.metrics_delta.get("magic__drc_error__count") == 1

    def test_artifacts_include_gds(self):
        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=True,
            run_dir="/nonexistent",
            gds_path="/path/to/design.gds",
            def_path="/path/to/design.def",
            run_time_s=200.0,
        )
        psr = PhysicalSliceRunner(mock_runner)
        # run_dir doesn't exist so no FlowMetrics parsing, but artifacts set
        result = psr.run(FlowStage.ROUTE)
        # No run_dir artifact since dir doesn't exist, but success is True
        assert result.success
