"""Tests for RtlLintRunner."""

from pathlib import Path
from unittest.mock import MagicMock
import subprocess

from eda_agents.core.flow_stage import FlowStage
from eda_agents.core.stages.rtl_lint_runner import (
    RtlLintRunner,
    _parse_verilator_output,
    _parse_yosys_output,
)


def _make_design(sources=None):
    design = MagicMock()
    design.rtl_sources.return_value = (
        sources if sources is not None else [Path("/src/top.sv")]
    )
    design.project_dir.return_value = Path("/project")
    return design


def _make_env(tools=None, proc_stdout="", proc_stderr="", returncode=0):
    """Create a mock ToolEnvironment.

    tools: dict mapping tool name to Path or None (not found).
    """
    tools = tools or {}
    env = MagicMock()
    env.which.side_effect = lambda t: tools.get(t)

    proc = subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=proc_stdout, stderr=proc_stderr
    )
    env.run.return_value = proc
    return env


class TestParseVerilatorOutput:
    def test_clean(self):
        w, e = _parse_verilator_output("-- VERILATOR: done\n")
        assert w == 0
        assert e == 0

    def test_warnings(self):
        output = (
            "%Warning-UNUSED: top.sv:10: Signal unused\n"
            "%Warning-WIDTH: top.sv:20: Width mismatch\n"
            "-- VERILATOR: done\n"
        )
        w, e = _parse_verilator_output(output)
        assert w == 2
        assert e == 0

    def test_errors(self):
        output = (
            "%Error: top.sv:5: Syntax error\n"
            "%Warning-UNUSED: top.sv:10: Signal unused\n"
        )
        w, e = _parse_verilator_output(output)
        assert w == 1
        assert e == 1

    def test_mixed(self):
        output = (
            "%Warning-WIDTH: a.sv:1: w1\n"
            "%Error: a.sv:2: e1\n"
            "%Error: b.sv:3: e2\n"
            "%Warning-UNUSED: b.sv:4: w2\n"
        )
        w, e = _parse_verilator_output(output)
        assert w == 2
        assert e == 2

    def test_exit_summary_not_counted(self):
        output = (
            "%Error: top.sv:51:30: Can't resolve module reference: 'pad'\n"
            "%Error: Exiting due to 1 error(s)\n"
        )
        w, e = _parse_verilator_output(output)
        assert w == 0
        assert e == 1  # only the real error, not the summary

    def test_exit_summary_with_warnings(self):
        output = (
            "%Warning-UNUSED: top.sv:10: Signal unused\n"
            "%Error: top.sv:20: Syntax error\n"
            "%Error: Exiting due to 1 error(s), 1 warning(s)\n"
        )
        w, e = _parse_verilator_output(output)
        assert w == 1
        assert e == 1


class TestParseYosysOutput:
    def test_clean(self):
        w, e = _parse_yosys_output("End of script.\n")
        assert w == 0
        assert e == 0

    def test_warnings(self):
        output = "Warning: Replacing memory \\mem.\nWarning: Another one.\n"
        w, e = _parse_yosys_output(output)
        assert w == 2
        assert e == 0

    def test_errors(self):
        output = "ERROR: Module `missing` referenced but not defined.\n"
        w, e = _parse_yosys_output(output)
        assert w == 0
        assert e == 1


class TestRtlLintRunnerVerilator:
    def test_clean_lint(self):
        env = _make_env(
            tools={"verilator": Path("/usr/bin/verilator")},
            proc_stderr="-- VERILATOR: done\n",
        )
        runner = RtlLintRunner(_make_design(), env)
        result = runner.run()
        assert result.success
        assert result.stage == FlowStage.RTL_LINT
        assert result.metrics_delta["lint_errors"] == 0
        assert result.error is None

    def test_lint_with_warnings(self):
        env = _make_env(
            tools={"verilator": Path("/usr/bin/verilator")},
            proc_stderr="%Warning-UNUSED: top.sv:10: unused\n",
        )
        runner = RtlLintRunner(_make_design(), env)
        result = runner.run()
        assert result.success  # warnings don't fail
        assert result.metrics_delta["lint_warnings"] == 1
        assert result.metrics_delta["lint_errors"] == 0

    def test_lint_with_errors(self):
        env = _make_env(
            tools={"verilator": Path("/usr/bin/verilator")},
            proc_stderr="%Error: top.sv:5: Syntax error\n",
            returncode=1,
        )
        runner = RtlLintRunner(_make_design(), env)
        result = runner.run()
        assert not result.success
        assert result.metrics_delta["lint_errors"] == 1
        assert "1 lint errors" in result.error

    def test_extra_flags_passed(self):
        env = _make_env(tools={"verilator": Path("/usr/bin/verilator")})
        runner = RtlLintRunner(_make_design(), env, extra_flags=["-Wall", "-Wno-fatal"])
        runner.run()
        call_args = env.run.call_args[0][0]
        assert "-Wall" in call_args
        assert "-Wno-fatal" in call_args


class TestRtlLintRunnerYosysFallback:
    def test_fallback_to_yosys(self):
        env = _make_env(
            tools={"yosys": Path("/usr/bin/yosys")},  # no verilator
            proc_stdout="End of script.\n",
        )
        runner = RtlLintRunner(_make_design(), env)
        result = runner.run()
        assert result.success
        assert result.metrics_delta["lint_errors"] == 0

    def test_yosys_errors(self):
        env = _make_env(
            tools={"yosys": Path("/usr/bin/yosys")},
            proc_stdout="ERROR: Module `missing` referenced.\n",
            returncode=1,
        )
        runner = RtlLintRunner(_make_design(), env)
        result = runner.run()
        assert not result.success
        assert result.metrics_delta["lint_errors"] == 1


class TestRtlLintRunnerEdgeCases:
    def test_no_tools_found(self):
        env = _make_env(tools={})  # nothing on PATH
        runner = RtlLintRunner(_make_design(), env)
        result = runner.run()
        assert not result.success
        assert "Neither verilator nor yosys" in result.error

    def test_no_sources(self):
        env = _make_env(tools={"verilator": Path("/usr/bin/verilator")})
        runner = RtlLintRunner(_make_design(sources=[]), env)
        result = runner.run()
        assert not result.success
        assert "No RTL sources" in result.error

    def test_run_time_recorded(self):
        env = _make_env(tools={"verilator": Path("/usr/bin/verilator")})
        runner = RtlLintRunner(_make_design(), env)
        result = runner.run()
        assert result.run_time_s >= 0
