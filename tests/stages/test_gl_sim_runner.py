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


def _add_post_pnr_artifacts(run_dir: Path, design_name: str, corner: str) -> Path:
    """Drop a post-PnR netlist and a per-corner SDF under ``run_dir``."""
    pnl = run_dir / "final" / "pnl"
    pnl.mkdir(parents=True)
    (pnl / f"{design_name}.pnl.v").write_text(
        f"module {design_name}(); endmodule\n"
    )
    sdf_dir = run_dir / "final" / "sdf" / corner
    sdf_dir.mkdir(parents=True)
    sdf = sdf_dir / f"{design_name}__{corner}.sdf"
    sdf.write_text("(DELAYFILE (SDFVERSION \"3.0\"))\n")
    return sdf


class TestRunPostPnr:
    """Post-PnR GL sim with SDF annotation. Same pass/fail contract."""

    def test_happy_path_emits_sdf_wrapper(self, tmp_path, pdk_config):
        """Success case writes an $sdf_annotate wrapper file (always)."""
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")

        cell_dir = tmp_path / "fake_pdk" / pdk_config.stdcell_verilog_models_glob.rsplit("/", 1)[0]
        cell_dir.mkdir(parents=True)
        (cell_dir / "stub.v").write_text("// stub\n")

        run_dir = _make_run_dir(tmp_path, "dut_top")
        _add_post_pnr_artifacts(run_dir, "dut_top", pdk_config.default_sta_corner)

        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env(sim_stdout="PASS\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path / "fake_pdk",
            enable_sdf_annotation=True,
        )
        result = runner.run_post_pnr()

        assert result.stage == FlowStage.GL_SIM_POST_PNR
        assert result.success
        # Wrapper file was generated and points at tb.dut (default).
        wrapper = run_dir / "gl_sim" / "post_pnr" / "_sdf_annotate_wrapper.v"
        assert wrapper.is_file()
        body = wrapper.read_text()
        assert "$sdf_annotate" in body
        assert "tb.dut" in body
        assert pdk_config.default_sta_corner in body
        assert "gl_sim_sdf_warnings" in result.metrics_delta

    def test_default_mode_skips_sdf_annotation(self, tmp_path, pdk_config):
        """Default post-PnR mode is functional only — no SDF warnings."""
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")

        cell_dir = tmp_path / "fake_pdk" / pdk_config.stdcell_verilog_models_glob.rsplit("/", 1)[0]
        cell_dir.mkdir(parents=True)
        (cell_dir / "stub.v").write_text("// stub\n")

        run_dir = _make_run_dir(tmp_path, "dut_top")
        _add_post_pnr_artifacts(run_dir, "dut_top", pdk_config.default_sta_corner)

        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env(sim_stdout="PASS\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path / "fake_pdk",
        )
        result = runner.run_post_pnr()

        assert result.success
        # SDF wrapper still written for discoverability.
        wrapper = run_dir / "gl_sim" / "post_pnr" / "_sdf_annotate_wrapper.v"
        assert wrapper.is_file()
        # SDF warnings absent because annotation is off.
        assert "gl_sim_sdf_warnings" not in result.metrics_delta

    def test_sdf_warnings_counted_non_blocking(self, tmp_path, pdk_config):
        """SDF warnings surface as metrics; functional PASS still wins."""
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")

        cell_dir = tmp_path / "fake_pdk" / pdk_config.stdcell_verilog_models_glob.rsplit("/", 1)[0]
        cell_dir.mkdir(parents=True)
        (cell_dir / "stub.v").write_text("// stub\n")

        run_dir = _make_run_dir(tmp_path, "dut_top")
        _add_post_pnr_artifacts(run_dir, "dut_top", pdk_config.default_sta_corner)

        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        # Simulate three SDF-annotate warnings; final PASS marker is
        # what gates the stage.
        env = _make_env(sim_stdout=(
            "sdf warning: missing specify path for AND2_X1\n"
            "sdf warning: negative delay clipped to 0\n"
            "SDF warning on some cell\n"
            "PASS\n"
        ))

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path / "fake_pdk",
            enable_sdf_annotation=True,
        )
        result = runner.run_post_pnr()

        assert result.success
        assert result.metrics_delta["gl_sim_sdf_warnings"] >= 3

    def test_missing_sdf_fails(self, tmp_path, pdk_config):
        """Netlist without an SDF is a gate failure (no silent skip)."""
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")

        run_dir = _make_run_dir(tmp_path, "dut_top")
        # Netlist only, no SDF directory.
        (run_dir / "final" / "pnl").mkdir(parents=True)
        (run_dir / "final" / "pnl" / "dut_top.pnl.v").write_text(
            "module dut_top(); endmodule\n"
        )

        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env()

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path,
        )
        result = runner.run_post_pnr()

        assert not result.success
        assert "SDF" in (result.error or "")

    def test_corner_fallback_uses_first_sdf(self, tmp_path, pdk_config):
        """Missing default corner falls back to the first available SDF."""
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")

        cell_dir = tmp_path / "fake_pdk" / pdk_config.stdcell_verilog_models_glob.rsplit("/", 1)[0]
        cell_dir.mkdir(parents=True)
        (cell_dir / "stub.v").write_text("// stub\n")

        run_dir = _make_run_dir(tmp_path, "dut_top")
        # Write an SDF under a *different* corner name to force fallback.
        other_corner = "some_other_corner"
        _add_post_pnr_artifacts(run_dir, "dut_top", other_corner)

        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env(sim_stdout="PASS\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path / "fake_pdk",
        )
        result = runner.run_post_pnr()

        assert result.success, result.error
        wrapper = (run_dir / "gl_sim" / "post_pnr" / "_sdf_annotate_wrapper.v").read_text()
        assert other_corner in wrapper

    def test_missing_post_pnr_netlist_fails(self, tmp_path, pdk_config):
        """No post-PnR netlist means nothing to simulate — signoff block."""
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")

        run_dir = _make_run_dir(tmp_path, "dut_top")
        # Only post-synth artefacts; no final/pnl.

        tb = TestbenchSpec(driver="iverilog", target="tb/tb_dut_top.v")
        design = _make_design(project_dir=project, tb=tb)
        env = _make_env()

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=tmp_path,
        )
        result = runner.run_post_pnr()

        assert not result.success
        assert "Post-PnR netlist" in (result.error or "")
