"""Tests for GlSimRunner.

Unit tests use a fully mocked :class:`ToolEnvironment` and a
fixture-built ``runs/<tag>/`` tree so they can run without the real
iverilog/vvp toolchain or a hardened LibreLane artefact. The
parity-check suite across both built-in PDK configs runs through the
parametrised ``pdk_config`` fixture from ``tests/conftest.py`` — tests
are skipped automatically when a PDK isn't installed.

Real tool-runs would need the ``librelane`` marker (iverilog + PDK
installed) and are exercised end-to-end by the example-09 acceptance
runs, not here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from eda_agents.core.digital_design import TestbenchSpec
from eda_agents.core.flow_stage import FlowStage
from eda_agents.core.stages.gl_sim_runner import GlSimRunner


# ---------------------------------------------------------------------------
# Fixtures: fake run-dir tree + stub design
# ---------------------------------------------------------------------------


def _make_run_dir(tmp_path: Path, design_name: str) -> Path:
    """Create a minimal LibreLane-like run directory."""
    run = tmp_path / "runs" / "RUN_fake"
    synth = run / "06-yosys-synthesis"
    synth.mkdir(parents=True)
    (synth / f"{design_name}.nl.v").write_text(
        f"module {design_name}(); endmodule\n"
    )
    return run


def _make_design(
    *, project_dir: Path, tb: TestbenchSpec | None, design_name: str = "dut_top",
    cells_glob: str | None = None,
):
    """Stub DigitalDesign that returns controllable testbench / paths."""
    design = MagicMock()
    design.project_name.return_value = design_name
    design.project_dir.return_value = project_dir
    design.testbench.return_value = tb
    design.gl_sim_cells_glob.return_value = cells_glob
    design.gl_sim_dut_instance_path.return_value = "tb.dut"
    design.pdk_root.return_value = None
    return design


def _make_env(
    *,
    has_iverilog: bool = True,
    compile_rc: int = 0,
    sim_rc: int = 0,
    sim_stdout: str = "",
    sim_stderr: str = "",
):
    """Stub ToolEnvironment whose .run() returns canned CompletedProcess."""
    env = MagicMock()
    env.which.side_effect = lambda t: Path(f"/usr/bin/{t}") if has_iverilog else None

    def _run(cmd, **kwargs):  # noqa: ARG001
        tool = cmd[0]
        if tool == "iverilog":
            return subprocess.CompletedProcess(
                args=cmd, returncode=compile_rc, stdout="", stderr=""
            )
        # vvp
        return subprocess.CompletedProcess(
            args=cmd, returncode=sim_rc, stdout=sim_stdout, stderr=sim_stderr
        )

    env.run.side_effect = _run
    return env


# ---------------------------------------------------------------------------
# run_post_synth: happy path and the concrete failure modes.
# ---------------------------------------------------------------------------


class TestRunPostSynth:
    """Covers the three ways post-synth GL sim can gate a flow."""

    def test_pass_marker_success(self, tmp_path, pdk_config):
        """PASS marker + exit 0 + no fail markers => success."""
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        tb_file = project / "tb" / "tb_dut_top.v"
        tb_file.write_text("module tb; endmodule\n")

        # Fake stdcell models so the glob resolves to something.
        cell_dir = tmp_path / "fake_pdk" / pdk_config.stdcell_verilog_models_glob.rsplit("/", 1)[0]
        cell_dir.mkdir(parents=True)
        (cell_dir / "stub.v").write_text("// stub cells\n")

        run_dir = _make_run_dir(tmp_path, "dut_top")
        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env(sim_stdout="hello\nPASS\n")

        runner = GlSimRunner(
            design=design,
            env=env,
            run_dir=run_dir,
            pdk_config=pdk_config,
            pdk_root=tmp_path / "fake_pdk",
        )
        result = runner.run_post_synth()

        assert result.stage == FlowStage.POST_SYNTH_SIM
        assert result.success is True
        assert result.metrics_delta["gl_sim_pass"] == 1
        assert result.metrics_delta["gl_sim_fail"] == 0
        assert result.error is None

    def test_missing_pass_marker_fails(self, tmp_path, pdk_config):
        """Silent exit 0 is not good enough — must see PASS."""
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")

        cell_dir = tmp_path / "fake_pdk" / pdk_config.stdcell_verilog_models_glob.rsplit("/", 1)[0]
        cell_dir.mkdir(parents=True)
        (cell_dir / "stub.v").write_text("// stub\n")

        run_dir = _make_run_dir(tmp_path, "dut_top")
        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env(sim_stdout="ran but said nothing\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path / "fake_pdk",
        )
        result = runner.run_post_synth()

        assert result.success is False
        assert "PASS" in (result.error or "")

    def test_fail_marker_fails(self, tmp_path, pdk_config):
        """Explicit FAIL in stdout is signoff-blocking."""
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")

        cell_dir = tmp_path / "fake_pdk" / pdk_config.stdcell_verilog_models_glob.rsplit("/", 1)[0]
        cell_dir.mkdir(parents=True)
        (cell_dir / "stub.v").write_text("// stub\n")

        run_dir = _make_run_dir(tmp_path, "dut_top")
        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env(sim_stdout="something\nFAIL: out mismatch\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path / "fake_pdk",
        )
        result = runner.run_post_synth()

        assert result.success is False
        assert result.metrics_delta["gl_sim_fail"] == 1
        assert "FAIL" in (result.error or "")

    def test_missing_netlist_fails_cleanly(self, tmp_path, pdk_config):
        """Run dir without a synth step directory is a runner failure."""
        project = tmp_path / "project"
        project.mkdir()
        run_dir = tmp_path / "runs" / "RUN_empty"
        run_dir.mkdir(parents=True)

        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env()

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path,
        )
        result = runner.run_post_synth()

        assert result.success is False
        assert "Post-synth netlist not found" in (result.error or "")

    def test_no_testbench_fails(self, tmp_path, pdk_config):
        """GL sim without an iverilog-backed TB cannot proceed."""
        project = tmp_path / "project"
        project.mkdir()
        run_dir = _make_run_dir(tmp_path, "dut_top")

        design = _make_design(project_dir=project, tb=None)
        env = _make_env()

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path,
        )
        result = runner.run_post_synth()

        assert result.success is False
        assert "testbench" in (result.error or "").lower()

    def test_cocotb_driver_rejected(self, tmp_path, pdk_config):
        """cocotb-backed TBs are not supported for GL sim yet."""
        project = tmp_path / "project"
        project.mkdir()
        run_dir = _make_run_dir(tmp_path, "dut_top")

        tb = TestbenchSpec(driver="cocotb", target="make sim")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env()

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path,
        )
        result = runner.run_post_synth()

        assert result.success is False

    def test_compile_error_fails(self, tmp_path, pdk_config):
        """iverilog compile-time failure is surfaced as stage failure."""
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")

        cell_dir = tmp_path / "fake_pdk" / pdk_config.stdcell_verilog_models_glob.rsplit("/", 1)[0]
        cell_dir.mkdir(parents=True)
        (cell_dir / "stub.v").write_text("// stub\n")

        run_dir = _make_run_dir(tmp_path, "dut_top")
        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env(compile_rc=1)

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path / "fake_pdk",
        )
        result = runner.run_post_synth()

        assert result.success is False
        assert "compilation failed" in (result.error or "")


class TestPdkConfigFields:
    """Parity checks: both built-in PDKs are wired for GL sim."""

    def test_both_pdks_declare_cells_glob(self, pdk_config):
        assert pdk_config.stdcell_verilog_models_glob, (
            f"PDK {pdk_config.name} must declare stdcell_verilog_models_glob"
        )

    def test_both_pdks_declare_default_corner(self, pdk_config):
        assert pdk_config.default_sta_corner, (
            f"PDK {pdk_config.name} must declare default_sta_corner"
        )


class TestPostPnrStub:
    """Post-PnR mode raises NotImplementedError until the SDF commit."""

    def test_run_post_pnr_stub(self, tmp_path, pdk_config):
        design = _make_design(project_dir=tmp_path, tb=None)
        runner = GlSimRunner(
            design=design, env=_make_env(), run_dir=tmp_path,
            pdk_config=pdk_config, pdk_root=tmp_path,
        )
        with pytest.raises(NotImplementedError):
            runner.run_post_pnr()
