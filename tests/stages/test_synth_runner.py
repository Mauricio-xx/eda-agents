"""Tests for SynthRunner."""

from unittest.mock import MagicMock

from eda_agents.agents.phase_results import FlowResult
from eda_agents.core.flow_stage import FlowStage
from eda_agents.core.stages.synth_runner import SynthRunner, _SYNTH_STEP


def _make_runner(flow_result=None):
    runner = MagicMock()
    runner.run_flow.return_value = flow_result or FlowResult(
        success=True, run_dir="/tmp/runs/test", run_time_s=30.0
    )
    return runner


class TestSynthRunner:
    def test_success_with_metrics(self, tmp_path):
        import json

        # Create a fake run dir with metrics
        run_dir = tmp_path / "runs" / "test"
        run_dir.mkdir(parents=True)
        synth_step = run_dir / "06-yosys-synthesis"
        synth_step.mkdir()
        (synth_step / "state_in.json").write_text(json.dumps({
            "metrics": {
                "design__instance__count": 12201,
                "design__instance__count__stdcell": 5806,
                "design__die__area": 256175.0,
            }
        }))

        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=True, run_dir=str(run_dir), run_time_s=30.0
        )

        synth = SynthRunner(mock_runner)
        result = synth.run(tag="test")

        assert result.success
        assert result.stage == FlowStage.SYNTH
        assert result.metrics_delta["design__instance__count"] == 12201
        assert result.artifacts["run_dir"] == run_dir

        # Verify correct to= arg passed to LibreLaneRunner
        mock_runner.run_flow.assert_called_once_with(
            to=_SYNTH_STEP, tag="test", overwrite=True
        )

    def test_failure_propagated(self):
        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=False, error="Yosys crashed", log_tail="segfault"
        )

        synth = SynthRunner(mock_runner)
        result = synth.run()

        assert not result.success
        assert "Yosys crashed" in result.error
        assert result.log_tail == "segfault"

    def test_no_run_dir(self):
        mock_runner = MagicMock()
        mock_runner.run_flow.return_value = FlowResult(
            success=True, run_dir="", run_time_s=5.0
        )

        synth = SynthRunner(mock_runner)
        result = synth.run()

        assert result.success
        assert result.metrics_delta == {}

    def test_overwrite_flag(self):
        mock_runner = _make_runner()
        synth = SynthRunner(mock_runner)
        synth.run(tag="t1", overwrite=False)

        mock_runner.run_flow.assert_called_once_with(
            to=_SYNTH_STEP, tag="t1", overwrite=False
        )
