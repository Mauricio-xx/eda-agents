"""Tests for PrecheckRunner."""

import json
from unittest.mock import MagicMock
import subprocess

from eda_agents.core.flow_stage import FlowStage
from eda_agents.core.stages.precheck_runner import PrecheckRunner


def _make_env(proc_stdout="", proc_stderr="", returncode=0):
    env = MagicMock()
    proc = subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=proc_stdout, stderr=proc_stderr
    )
    env.run.return_value = proc
    return env


class TestPrecheckRunner:
    def test_pass(self, tmp_path):
        # Create fake precheck dir with script
        precheck_dir = tmp_path / "precheck"
        precheck_dir.mkdir()
        (precheck_dir / "precheck.py").write_text("# fake")

        # Create fake GDS
        gds = tmp_path / "final" / "gds" / "chip_top.gds"
        gds.parent.mkdir(parents=True)
        gds.write_bytes(b"\x00")

        env = _make_env(proc_stdout="All checks passed\n")
        runner = PrecheckRunner(precheck_dir, env, slot="1x1")
        result = runner.run(gds)

        assert result.success
        assert result.stage == FlowStage.PRECHECK
        assert result.error is None

    def test_correct_cli_args(self, tmp_path):
        precheck_dir = tmp_path / "precheck"
        precheck_dir.mkdir()
        (precheck_dir / "precheck.py").write_text("# fake")

        gds = tmp_path / "chip_top.gds"
        gds.write_bytes(b"\x00")

        env = _make_env()
        runner = PrecheckRunner(precheck_dir, env, slot="0p5x1")
        runner.run(gds, top_cell="my_top", die_id="AABB0011")

        cmd = env.run.call_args[0][0]
        assert "--input" in cmd
        assert str(gds) in cmd
        assert "--slot" in cmd
        assert "0p5x1" in cmd
        assert "--top" in cmd
        assert "my_top" in cmd
        assert "--id" in cmd
        assert "AABB0011" in cmd
        # Verify we DON'T use the old broken args
        assert "--gds" not in cmd
        assert "--top-cell" not in cmd
        assert "--slot-size" not in cmd

    def test_pdk_env_explicit(self, tmp_path):
        precheck_dir = tmp_path / "precheck"
        precheck_dir.mkdir()
        (precheck_dir / "precheck.py").write_text("# fake")

        gds = tmp_path / "chip.gds"
        gds.write_bytes(b"\x00")

        env = _make_env()
        runner = PrecheckRunner(
            precheck_dir, env, pdk_root="/my/pdk"
        )
        runner.run(gds)

        call_kwargs = env.run.call_args[1]
        assert call_kwargs["env"]["PDK_ROOT"] == "/my/pdk"
        assert call_kwargs["env"]["PDK"] == "gf180mcuD"

    def test_default_pdk_root_is_precheck_local(self, tmp_path):
        precheck_dir = tmp_path / "precheck"
        precheck_dir.mkdir()
        (precheck_dir / "precheck.py").write_text("# fake")

        gds = tmp_path / "chip.gds"
        gds.write_bytes(b"\x00")

        env = _make_env()
        runner = PrecheckRunner(precheck_dir, env)
        runner.run(gds)

        call_kwargs = env.run.call_args[1]
        assert call_kwargs["env"]["PDK_ROOT"] == str(precheck_dir / "gf180mcu")

    def test_missing_gds_f7(self, tmp_path):
        precheck_dir = tmp_path / "precheck"
        precheck_dir.mkdir()
        (precheck_dir / "precheck.py").write_text("# fake")

        env = _make_env()
        runner = PrecheckRunner(precheck_dir, env)
        result = runner.run(tmp_path / "nonexistent.gds")

        assert not result.success
        assert "not found" in result.error
        assert "F7" in result.error

    def test_missing_precheck_dir(self, tmp_path):
        env = _make_env()
        runner = PrecheckRunner(tmp_path / "nope", env)
        gds = tmp_path / "chip.gds"
        gds.write_bytes(b"\x00")
        result = runner.run(gds)

        assert not result.success
        assert "not found" in result.error

    def test_missing_precheck_script(self, tmp_path):
        precheck_dir = tmp_path / "precheck"
        precheck_dir.mkdir()
        # No precheck.py file

        gds = tmp_path / "chip.gds"
        gds.write_bytes(b"\x00")

        env = _make_env()
        runner = PrecheckRunner(precheck_dir, env)
        result = runner.run(gds)

        assert not result.success
        assert "precheck.py not found" in result.error

    def test_failure_exit_code(self, tmp_path):
        precheck_dir = tmp_path / "precheck"
        precheck_dir.mkdir()
        (precheck_dir / "precheck.py").write_text("# fake")

        gds = tmp_path / "chip.gds"
        gds.write_bytes(b"\x00")

        env = _make_env(returncode=1, proc_stderr="KLayout DRC: 5 violations")
        runner = PrecheckRunner(precheck_dir, env)
        result = runner.run(gds)

        assert not result.success
        assert "exit 1" in result.error

    def test_error_count_from_state_out(self, tmp_path):
        precheck_dir = tmp_path / "precheck"
        precheck_dir.mkdir()
        (precheck_dir / "precheck.py").write_text("# fake")

        # Create fake state_out.json files
        runs_dir = precheck_dir / "librelane" / "runs" / "RUN_test"
        step_dir = runs_dir / "09-klayout-antenna"
        step_dir.mkdir(parents=True)
        (step_dir / "state_out.json").write_text(json.dumps({
            "metrics": {"antenna__violating__nets": 2}
        }))
        step_dir2 = runs_dir / "13-klayout-drc"
        step_dir2.mkdir(parents=True)
        (step_dir2 / "state_out.json").write_text(json.dumps({
            "metrics": {"klayout__drc_error__count": 3}
        }))

        gds = tmp_path / "chip.gds"
        gds.write_bytes(b"\x00")

        env = _make_env()
        runner = PrecheckRunner(precheck_dir, env)
        result = runner.run(gds)

        assert result.success
        assert result.metrics_delta["precheck_errors"] == 5

    def test_output_gds_arg(self, tmp_path):
        precheck_dir = tmp_path / "precheck"
        precheck_dir.mkdir()
        (precheck_dir / "precheck.py").write_text("# fake")

        gds = tmp_path / "chip.gds"
        gds.write_bytes(b"\x00")
        out_gds = tmp_path / "output.gds"

        env = _make_env()
        runner = PrecheckRunner(precheck_dir, env)
        runner.run(gds, output_gds=out_gds)

        cmd = env.run.call_args[0][0]
        assert "--output" in cmd
        assert str(out_gds) in cmd

    def test_top_cell_derived_from_filename(self, tmp_path):
        precheck_dir = tmp_path / "precheck"
        precheck_dir.mkdir()
        (precheck_dir / "precheck.py").write_text("# fake")

        gds = tmp_path / "my_design.gds"
        gds.write_bytes(b"\x00")

        env = _make_env()
        runner = PrecheckRunner(precheck_dir, env)
        runner.run(gds)  # no top_cell arg

        cmd = env.run.call_args[0][0]
        top_idx = cmd.index("--top")
        assert cmd[top_idx + 1] == "my_design"
