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

import os
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


# ---------------------------------------------------------------------------
# Cocotb backend (S12-A Gap 2)
# ---------------------------------------------------------------------------


def _make_cocotb_env(
    *,
    has_make: bool = True,
    rc: int = 0,
    stdout: str = "",
    stderr: str = "",
):
    """Stub ToolEnvironment whose .run() returns a canned ``make`` outcome.

    Captures the full call payload (cmd, cwd, env) into a list the
    test reads back so it can assert on env vars and Makefile shape.
    """
    env = MagicMock()
    env.which.side_effect = lambda t: Path(f"/usr/bin/{t}") if has_make else None

    captured: list[dict] = []

    def _run(cmd, cwd=None, env=None, timeout_s=None):  # noqa: ARG001
        captured.append({"cmd": cmd, "cwd": Path(cwd) if cwd else None, "env": env})
        return subprocess.CompletedProcess(
            args=cmd, returncode=rc, stdout=stdout, stderr=stderr
        )

    env.run.side_effect = _run
    env._captured = captured  # noqa: SLF001 — test handle
    return env


def _make_cocotb_project(
    project: Path, design_name: str, *, with_makefile: bool = True
) -> None:
    """Lay down a tb/ directory shaped like a cocotb run."""
    tb_dir = project / "tb"
    tb_dir.mkdir(parents=True, exist_ok=True)
    (tb_dir / f"test_{design_name}.py").write_text(
        f"# cocotb test for {design_name}\nimport cocotb\n"
    )
    if with_makefile:
        (tb_dir / "Makefile").write_text(
            "include $(shell cocotb-config --makefiles)/Makefile.sim\n"
        )


class TestDetectTbFlavour:
    """Filesystem-only TB detection inside GlSimRunner."""

    def test_detects_cocotb_when_test_and_makefile_exist(self, tmp_path, pdk_config):
        project = tmp_path / "project"
        _make_cocotb_project(project, "dut_top")
        run_dir = _make_run_dir(tmp_path, "dut_top")
        design = _make_design(project_dir=project, tb=None)

        runner = GlSimRunner(
            design=design, env=_make_env(),
            run_dir=run_dir, pdk_config=pdk_config, pdk_root=tmp_path,
        )
        assert runner._detect_tb_flavour() == "cocotb"  # noqa: SLF001

    def test_detects_iverilog_when_only_v_file_present(self, tmp_path, pdk_config):
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")
        run_dir = _make_run_dir(tmp_path, "dut_top")
        design = _make_design(project_dir=project, tb=None)

        runner = GlSimRunner(
            design=design, env=_make_env(),
            run_dir=run_dir, pdk_config=pdk_config, pdk_root=tmp_path,
        )
        assert runner._detect_tb_flavour() == "iverilog"  # noqa: SLF001

    def test_detects_none_when_tb_dir_empty(self, tmp_path, pdk_config):
        project = tmp_path / "project"
        (project / "tb").mkdir(parents=True)
        run_dir = _make_run_dir(tmp_path, "dut_top")
        design = _make_design(project_dir=project, tb=None)

        runner = GlSimRunner(
            design=design, env=_make_env(),
            run_dir=run_dir, pdk_config=pdk_config, pdk_root=tmp_path,
        )
        assert runner._detect_tb_flavour() == "none"  # noqa: SLF001

    def test_cocotb_test_without_makefile_falls_back(self, tmp_path, pdk_config):
        # If only the cocotb test file exists (no Makefile), we cannot
        # run cocotb — fall back to iverilog detection.
        project = tmp_path / "project"
        _make_cocotb_project(project, "dut_top", with_makefile=False)
        (project / "tb" / "tb_dut_top.v").write_text("module tb; endmodule\n")
        run_dir = _make_run_dir(tmp_path, "dut_top")
        design = _make_design(project_dir=project, tb=None)

        runner = GlSimRunner(
            design=design, env=_make_env(),
            run_dir=run_dir, pdk_config=pdk_config, pdk_root=tmp_path,
        )
        assert runner._detect_tb_flavour() == "iverilog"  # noqa: SLF001


class TestCocotbPostSynth:
    """Cocotb backend dispatch from run_post_synth."""

    def _setup(self, tmp_path, pdk_config):
        project = tmp_path / "project"
        _make_cocotb_project(project, "dut_top")
        cell_dir = (
            tmp_path
            / "fake_pdk"
            / pdk_config.stdcell_verilog_models_glob.rsplit("/", 1)[0]
        )
        cell_dir.mkdir(parents=True)
        (cell_dir / "stub.v").write_text("// stub\n")
        run_dir = _make_run_dir(tmp_path, "dut_top")
        design = _make_design(project_dir=project, tb=None)
        return project, design, run_dir, tmp_path / "fake_pdk"

    def test_dispatches_to_cocotb_and_invokes_make_sim(self, tmp_path, pdk_config):
        project, design, run_dir, pdk_root = self._setup(tmp_path, pdk_config)
        env = _make_cocotb_env(stdout="** TESTS=3 PASS=3 FAIL=0 SKIP=0 **\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=pdk_root,
            librelane_python="/path/to/librelane/.venv/bin/python",
        )
        result = runner.run_post_synth()

        assert result.success is True
        assert result.metrics_delta["gl_sim_pass"] == 1
        assert result.metrics_delta["gl_sim_tests"] == 3
        assert result.metrics_delta["gl_sim_test_pass"] == 3
        # make sim invoked, not iverilog/vvp
        calls = env._captured  # noqa: SLF001
        assert len(calls) == 1
        assert calls[0]["cmd"] == ["make", "sim"]
        # cwd is the GL-sim per-mode work dir
        assert calls[0]["cwd"] == run_dir / "gl_sim" / "post_synth"
        # PATH was prepended with the librelane venv bin
        assert calls[0]["env"]["PATH"].startswith("/path/to/librelane/.venv/bin")

    def test_path_prepend_uses_lexical_parent_not_resolved_symlink(
        self, tmp_path, pdk_config
    ):
        """Regression: production venv pythons are symlinks back to the
        system interpreter, so ``Path.resolve().parent`` would land in
        ``/usr/bin`` instead of the venv's bin/. Cocotb-config lives in
        the venv only, so the wrong PATH silently fails ``make sim``
        (exit code 2) the way the first S12-A live attempt did. Lock
        the lexical-parent contract here so this can't re-break.
        """
        project, design, run_dir, pdk_root = self._setup(tmp_path, pdk_config)
        env = _make_cocotb_env(stdout="** TESTS=1 PASS=1 FAIL=0 SKIP=0 **\n")

        # Build a fake venv whose python is a symlink to a script in
        # an unrelated dir (mimics the real /usr/bin symlink target).
        real_dir = tmp_path / "system" / "bin"
        real_dir.mkdir(parents=True)
        real_python = real_dir / "python3.12"
        real_python.write_text("#!/bin/sh\nexit 0\n")
        real_python.chmod(0o755)

        venv_bin = tmp_path / "fake_venv" / "bin"
        venv_bin.mkdir(parents=True)
        venv_python = venv_bin / "python"
        venv_python.symlink_to(real_python)

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=pdk_root,
            librelane_python=str(venv_python),
        )
        runner.run_post_synth()

        calls = env._captured  # noqa: SLF001
        path = calls[0]["env"]["PATH"]
        prepended = path.split(os.pathsep)[0]
        # The PATH segment we add must be the LEXICAL parent — the
        # venv's bin dir — not the real interpreter's directory.
        assert prepended == str(venv_bin), (
            f"PATH prepended {prepended!r}, expected the venv bin "
            f"{str(venv_bin)!r}. If this asserts the resolved parent, "
            "cocotb-config will not be findable in production."
        )
        assert prepended != str(real_dir)

    def test_path_prepend_skipped_for_bare_python_command(
        self, tmp_path, pdk_config
    ):
        """A bare ``python3`` (no path separator) means the caller is
        relying on whatever PATH is already set up. The prepend logic
        must NOT inject ``.`` (parent of a bare name) — that is both
        useless and a privilege-escalation footgun.
        """
        project, design, run_dir, pdk_root = self._setup(tmp_path, pdk_config)
        env = _make_cocotb_env(stdout="** TESTS=1 PASS=1 FAIL=0 SKIP=0 **\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=pdk_root,
            librelane_python="python3",
        )
        runner.run_post_synth()

        calls = env._captured  # noqa: SLF001
        path = calls[0]["env"]["PATH"]
        # PATH must be the inherited os.environ PATH unchanged — no
        # leading "." segment.
        first = path.split(os.pathsep)[0]
        assert first != ".", (
            "PATH was prepended with '.' — bare-command librelane_python "
            "should leave PATH alone."
        )

    def test_writes_makefile_with_verilog_sources(self, tmp_path, pdk_config):
        project, design, run_dir, pdk_root = self._setup(tmp_path, pdk_config)
        env = _make_cocotb_env(stdout="** TESTS=1 PASS=1 FAIL=0 SKIP=0 **\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=pdk_root,
        )
        runner.run_post_synth()

        makefile = (run_dir / "gl_sim" / "post_synth" / "Makefile").read_text()
        assert "TOPLEVEL = dut_top" in makefile
        assert "MODULE = test_dut_top" in makefile
        assert "VERILOG_SOURCES" in makefile
        # netlist + stdcell stub appear in VERILOG_SOURCES
        assert "stub.v" in makefile
        assert "dut_top.nl.v" in makefile
        # No SDF flags for post-synth.
        assert "-gspecify" not in makefile

    def test_copies_cocotb_test_into_workdir(self, tmp_path, pdk_config):
        project, design, run_dir, pdk_root = self._setup(tmp_path, pdk_config)
        env = _make_cocotb_env(stdout="** TESTS=1 PASS=1 FAIL=0 SKIP=0 **\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=pdk_root,
        )
        runner.run_post_synth()

        copied = run_dir / "gl_sim" / "post_synth" / "test_dut_top.py"
        assert copied.is_file()
        assert "cocotb test for dut_top" in copied.read_text()

    def test_failed_summary_marks_stage_failure(self, tmp_path, pdk_config):
        project, design, run_dir, pdk_root = self._setup(tmp_path, pdk_config)
        env = _make_cocotb_env(stdout="** TESTS=2 PASS=1 FAIL=1 SKIP=0 **\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=pdk_root,
        )
        result = runner.run_post_synth()

        assert result.success is False
        assert result.metrics_delta["gl_sim_test_fail"] == 1
        assert "1/2 cocotb tests failed" in (result.error or "")

    def test_missing_summary_marks_stage_failure(self, tmp_path, pdk_config):
        # cocotb did not run (e.g. Makefile syntax error before sim).
        project, design, run_dir, pdk_root = self._setup(tmp_path, pdk_config)
        env = _make_cocotb_env(stdout="cocotb: nothing happened here\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=pdk_root,
        )
        result = runner.run_post_synth()

        assert result.success is False
        assert "summary line not found" in (result.error or "")


class TestCocotbPostPnr:
    """Cocotb backend dispatch from run_post_pnr (with optional SDF)."""

    def _setup_with_pnr(self, tmp_path, pdk_config):
        project = tmp_path / "project"
        _make_cocotb_project(project, "dut_top")
        cell_dir = (
            tmp_path
            / "fake_pdk"
            / pdk_config.stdcell_verilog_models_glob.rsplit("/", 1)[0]
        )
        cell_dir.mkdir(parents=True)
        (cell_dir / "stub.v").write_text("// stub\n")
        run_dir = _make_run_dir(tmp_path, "dut_top")
        # Add post-PnR netlist + SDF.
        _add_post_pnr_artifacts(run_dir, "dut_top", pdk_config.default_sta_corner)
        design = _make_design(project_dir=project, tb=None)
        return project, design, run_dir, tmp_path / "fake_pdk"

    def test_post_pnr_without_sdf_annotation(self, tmp_path, pdk_config):
        project, design, run_dir, pdk_root = self._setup_with_pnr(tmp_path, pdk_config)
        env = _make_cocotb_env(stdout="** TESTS=1 PASS=1 FAIL=0 SKIP=0 **\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=pdk_root,
            enable_sdf_annotation=False,
        )
        result = runner.run_post_pnr()

        assert result.success is True
        makefile = (run_dir / "gl_sim" / "post_pnr" / "Makefile").read_text()
        assert "VERILOG_SOURCES" in makefile
        # Wrapper not included when SDF annotation disabled.
        assert "_sdf_annotate_wrapper.v" not in makefile

    def test_post_pnr_with_sdf_annotation_includes_wrapper(self, tmp_path, pdk_config):
        project, design, run_dir, pdk_root = self._setup_with_pnr(tmp_path, pdk_config)
        env = _make_cocotb_env(stdout="** TESTS=1 PASS=1 FAIL=0 SKIP=0 **\n")

        runner = GlSimRunner(
            design=design, env=env, run_dir=run_dir,
            pdk_config=pdk_config, pdk_root=pdk_root,
            enable_sdf_annotation=True,
        )
        result = runner.run_post_pnr()

        assert result.success is True
        work_dir = run_dir / "gl_sim" / "post_pnr"
        wrapper = work_dir / "_sdf_annotate_wrapper.v"
        assert wrapper.is_file()
        # Wrapper anchors on the design top, NOT tb.dut, because cocotb
        # instantiates TOPLEVEL=<design> directly.
        wrapper_text = wrapper.read_text()
        assert "$sdf_annotate(" in wrapper_text
        assert "dut_top" in wrapper_text
        assert "tb.dut" not in wrapper_text
        # Wrapper is part of VERILOG_SOURCES.
        makefile = (work_dir / "Makefile").read_text()
        assert "_sdf_annotate_wrapper.v" in makefile
        # Compile flags carry the SDF-annotation enablers.
        assert "-gspecify" in makefile
        assert "-ginterconnect" in makefile
