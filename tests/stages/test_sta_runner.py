"""Tests for StaRunner."""

import json
from unittest.mock import MagicMock

from eda_agents.agents.phase_results import FlowResult
from eda_agents.core.flow_stage import FlowStage
from eda_agents.core.stages.sta_runner import StaRunner, _STA_STEP


class TestStaRunner:
    def test_success_with_timing_metrics(self, tmp_path):
        run_dir = tmp_path / "runs" / "test"
        run_dir.mkdir(parents=True)
        sta_step = run_dir / "56-openroad-stapostpnr"
        sta_step.mkdir()
        (sta_step / "state_in.json").write_text(json.dumps({
            "metrics": {
                "timing__setup__ws": 1.407,
                "timing__setup__ws__corner:nom_tt_025C_5v00": 19.566,
                "timing__setup__ws__corner:max_ss_125C_4v50": 1.407,
                "timing__hold__ws": 0.268,
                "power__total": 0.05185,
                "power__internal__total": 0.03762,
                "power__switching__total": 0.01424,
            }
        }))

        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=True, run_dir=str(run_dir), run_time_s=45.0
        )

        sta = StaRunner(mock_runner)
        result = sta.run(tag="test")

        assert result.success
        assert result.stage == FlowStage.SIGNOFF_STA
        assert result.metrics_delta["timing__setup__ws"] == 1.407
        assert result.metrics_delta["timing__hold__ws"] == 0.268
        assert result.metrics_delta["power__total"] == 0.05185
        assert result.metrics_delta["timing__setup__ws__corner:nom_tt_025C_5v00"] == 19.566
        assert result.metrics_delta["timing__setup__ws__corner:max_ss_125C_4v50"] == 1.407

        mock_runner.run_flow.assert_called_once_with(
            frm=_STA_STEP, to=_STA_STEP, tag="test", overwrite=True
        )

    def test_failure_propagated(self):
        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=False, error="STA crashed: missing SPEF",
            log_tail="FileNotFoundError: design.spef"
        )

        sta = StaRunner(mock_runner)
        result = sta.run()

        assert not result.success
        assert "missing SPEF" in result.error
        assert "FileNotFoundError" in result.log_tail

    def test_no_run_dir(self):
        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=True, run_dir="", run_time_s=10.0
        )

        sta = StaRunner(mock_runner)
        result = sta.run()

        assert result.success
        assert result.metrics_delta == {}

    def test_overwrite_flag(self):
        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=True, run_dir="", run_time_s=5.0
        )

        sta = StaRunner(mock_runner)
        sta.run(tag="t1", overwrite=False)

        mock_runner.run_flow.assert_called_once_with(
            frm=_STA_STEP, to=_STA_STEP, tag="t1", overwrite=False
        )
