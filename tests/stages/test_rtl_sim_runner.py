"""Tests for RtlSimRunner, CocotbDriver, and IVerilogDriver."""

from pathlib import Path
from unittest.mock import MagicMock
import subprocess

from eda_agents.core.digital_design import TestbenchSpec
from eda_agents.core.flow_stage import FlowStage
from eda_agents.core.stages.rtl_sim_runner import (
    CocotbDriver,
    IVerilogDriver,
    RtlSimRunner,
    _COCOTB_SUMMARY_RE,
)


def _make_design(tb=None, sources=None):
    design = MagicMock()
    design.testbench.return_value = tb
    design.rtl_sources.return_value = (
        sources if sources is not None else [Path("/src/top.sv")]
    )
    design.project_dir.return_value = Path("/project")
    return design


def _make_env(tools=None, proc_stdout="", proc_stderr="", returncode=0):
    tools = tools or {}
    env = MagicMock()
    env.which.side_effect = lambda t: tools.get(t)

    proc = subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=proc_stdout, stderr=proc_stderr
    )
    env.run.return_value = proc
    return env


class TestCocotbSummaryRegex:
    def test_parse_full_summary(self):
        line = "** TESTS=7 PASS=7 FAIL=0 SKIP=0               700990.01          77.37 **"
        m = _COCOTB_SUMMARY_RE.search(line)
        assert m is not None
        assert m.group(1) == "7"
        assert m.group(2) == "7"
        assert m.group(3) == "0"
        assert m.group(4) == "0"

    def test_parse_with_failures(self):
        line = "** TESTS=10 PASS=8 FAIL=2 SKIP=0 **"
        m = _COCOTB_SUMMARY_RE.search(line)
        assert m is not None
        assert m.group(3) == "2"

    def test_no_match_on_garbage(self):
        assert _COCOTB_SUMMARY_RE.search("random output line") is None


class TestCocotbDriver:
    def test_all_pass(self):
        tb = TestbenchSpec(driver="cocotb", target="make sim")
        summary = "** TESTS=7 PASS=7 FAIL=0 SKIP=0 **"
        env = _make_env(proc_stdout=summary)
        driver = CocotbDriver(_make_design(tb=tb), env, pdk_root="/pdk")
        result = driver.run()
        assert result.success
        assert result.stage == FlowStage.RTL_SIM
        assert result.metrics_delta["sim_pass"] == 7
        assert result.metrics_delta["sim_fail"] == 0

    def test_some_fail(self):
        tb = TestbenchSpec(driver="cocotb", target="make sim")
        summary = "** TESTS=7 PASS=5 FAIL=2 SKIP=0 **"
        env = _make_env(proc_stdout=summary, returncode=1)
        driver = CocotbDriver(_make_design(tb=tb), env, pdk_root="/pdk")
        result = driver.run()
        assert not result.success
        assert result.metrics_delta["sim_fail"] == 2
        assert "2/7" in result.error

    def test_no_testbench_defined(self):
        env = _make_env()
        driver = CocotbDriver(_make_design(tb=None), env)
        result = driver.run()
        assert not result.success
        assert "testbench" in result.error.lower()

    def test_pdk_env_injected(self):
        tb = TestbenchSpec(driver="cocotb", target="make sim")
        env = _make_env(proc_stdout="** TESTS=1 PASS=1 FAIL=0 SKIP=0 **")
        driver = CocotbDriver(
            _make_design(tb=tb), env,
            pdk_root="/my/pdk", pdk="gf180mcuD"
        )
        driver.run()
        # Verify env dict was passed with PDK vars
        call_kwargs = env.run.call_args[1]
        assert call_kwargs["env"]["PDK_ROOT"] == "/my/pdk"
        assert call_kwargs["env"]["PDK"] == "gf180mcuD"

    def test_env_overrides_applied(self):
        tb = TestbenchSpec(
            driver="cocotb", target="make sim",
            env_overrides={"GL": "1", "SIM_FULL_CHIP": "0"},
        )
        env = _make_env(proc_stdout="** TESTS=1 PASS=1 FAIL=0 SKIP=0 **")
        driver = CocotbDriver(_make_design(tb=tb), env)
        driver.run()
        call_kwargs = env.run.call_args[1]
        assert call_kwargs["env"]["GL"] == "1"
        assert call_kwargs["env"]["SIM_FULL_CHIP"] == "0"

    def test_nonzero_exit_no_summary(self):
        tb = TestbenchSpec(driver="cocotb", target="make sim")
        env = _make_env(proc_stdout="some error output", returncode=2)
        driver = CocotbDriver(_make_design(tb=tb), env)
        result = driver.run()
        assert not result.success
        assert "exit" in result.error.lower() or "code" in result.error.lower()


class TestIVerilogDriver:
    def test_pass(self):
        env = _make_env(
            tools={"iverilog": Path("/usr/bin/iverilog")},
            proc_stdout="All tests passed\n$finish",
        )
        driver = IVerilogDriver(_make_design(), env, tb_path=Path("/tb.sv"))
        result = driver.run()
        assert result.success
        assert result.metrics_delta["sim_pass"] == 1

    def test_fail(self):
        env = MagicMock()
        env.which.return_value = Path("/usr/bin/iverilog")
        # First call (compile) succeeds, second call (sim) fails
        ok_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        fail_proc = subprocess.CompletedProcess(
            args=[], returncode=1,
            stdout="ERROR: assertion failed at 100ns\n", stderr=""
        )
        env.run.side_effect = [ok_proc, fail_proc]
        driver = IVerilogDriver(_make_design(), env)
        result = driver.run()
        assert not result.success
        assert result.metrics_delta["sim_fail"] == 1

    def test_iverilog_not_found(self):
        env = _make_env(tools={})
        driver = IVerilogDriver(_make_design(), env)
        result = driver.run()
        assert not result.success
        assert "iverilog not found" in result.error

    def test_no_sources(self):
        env = _make_env(tools={"iverilog": Path("/usr/bin/iverilog")})
        driver = IVerilogDriver(_make_design(sources=[]), env)
        result = driver.run()
        assert not result.success
        assert "No RTL sources" in result.error

    def test_compile_failure(self):
        env = MagicMock()
        env.which.return_value = Path("/usr/bin/iverilog")
        # First call (compile) fails, second call (sim) should not happen
        fail_proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="syntax error"
        )
        env.run.return_value = fail_proc
        driver = IVerilogDriver(_make_design(), env)
        result = driver.run()
        assert not result.success
        assert "compilation failed" in result.error


class TestRtlSimRunner:
    def test_dispatches_to_cocotb(self):
        tb = TestbenchSpec(driver="cocotb", target="make sim")
        env = _make_env(proc_stdout="** TESTS=3 PASS=3 FAIL=0 SKIP=0 **")
        runner = RtlSimRunner(_make_design(tb=tb), env, pdk_root="/pdk")
        result = runner.run()
        assert result.success
        assert result.metrics_delta["sim_pass"] == 3

    def test_dispatches_to_iverilog(self):
        tb = TestbenchSpec(driver="iverilog", target="tb_top.sv")
        env = _make_env(
            tools={"iverilog": Path("/usr/bin/iverilog")},
            proc_stdout="All tests passed\n",
        )
        runner = RtlSimRunner(_make_design(tb=tb), env)
        result = runner.run()
        assert result.success

    def test_no_testbench_falls_back_to_iverilog(self):
        env = _make_env(
            tools={"iverilog": Path("/usr/bin/iverilog")},
            proc_stdout="All tests passed\n",
        )
        runner = RtlSimRunner(_make_design(tb=None), env)
        result = runner.run()
        assert result.success

    def test_unknown_driver(self):
        # Monkey-patch driver to something unknown
        tb_bad = TestbenchSpec(driver="unknown_driver", target="sim")
        runner = RtlSimRunner(_make_design(tb=tb_bad), _make_env())
        result = runner.run()
        assert not result.success
        assert "Unknown sim driver" in result.error
