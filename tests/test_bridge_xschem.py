"""Tests for ``eda_agents.bridge.xschem`` — invocation mocked, no real xschem."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from eda_agents.bridge.models import ExecutionStatus
from eda_agents.bridge.xschem import XschemNetlistResult, XschemRunner


@pytest.fixture
def runner():
    return XschemRunner(xschem_cmd="/usr/bin/xschem", timeout_s=10)


def _completed(rc=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr=err)


def test_build_command_uses_required_flags(runner, tmp_path):
    sch = tmp_path / "design.sch"
    sch.write_text("v {xschem version=3.4.4}")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    log = out_dir / "design.xschem.log"
    cmd = runner.build_command(sch, out_dir, "design.spice", log)
    # Must always pass the no-X / spice / quit / netlist trio
    for flag in ("-n", "-s", "-q", "-x", "-r", "--no_x"):
        assert flag in cmd
    assert "-o" in cmd and str(out_dir) in cmd
    assert "-N" in cmd and "design.spice" in cmd
    assert "-l" in cmd and str(log) in cmd
    assert cmd[-1] == str(sch)


def test_export_netlist_missing_schematic(runner, tmp_path):
    res = runner.export_netlist(tmp_path / "nope.sch")
    assert isinstance(res, XschemNetlistResult)
    assert not res.success
    assert "schematic not found" in (res.error or "")


def test_export_netlist_success_writes_artifact(runner, tmp_path):
    sch = tmp_path / "design.sch"
    sch.write_text("dummy")
    out_dir = tmp_path / "netlist"

    def fake_run(argv, **kwargs):
        # xschem would write the netlist + log; emulate that.
        # argv has [...,'-o', out_dir, '-N', name, '-l', log, ..., sch]
        out = Path(argv[argv.index("-o") + 1])
        name = argv[argv.index("-N") + 1]
        log = Path(argv[argv.index("-l") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(".subckt design\n.ends\n")
        log.write_text("xschem: ok\n")
        return _completed(rc=0, out="netlist written", err="")

    with patch("eda_agents.bridge.xschem.subprocess.run", side_effect=fake_run):
        res = runner.export_netlist(sch, out_dir=out_dir)

    assert res.success
    assert res.netlist_path == out_dir / "design.spice"
    assert res.netlist_path.is_file()
    assert res.log_path is not None
    assert res.error is None

    bridge = res.to_bridge_result()
    assert bridge.status is ExecutionStatus.SUCCESS
    assert bridge.tool == "xschem"
    assert str(res.netlist_path) in bridge.artifacts


def test_export_netlist_returns_failure_when_no_artifact(runner, tmp_path):
    sch = tmp_path / "design.sch"
    sch.write_text("dummy")

    with patch("eda_agents.bridge.xschem.subprocess.run") as m:
        m.return_value = _completed(rc=0, out="", err="")
        res = runner.export_netlist(sch)

    assert not res.success
    assert "no netlist was produced" in (res.error or "")
    assert res.to_bridge_result().status is ExecutionStatus.FAILURE


def test_export_netlist_nonzero_rc(runner, tmp_path):
    sch = tmp_path / "design.sch"
    sch.write_text("dummy")
    with patch("eda_agents.bridge.xschem.subprocess.run") as m:
        m.return_value = _completed(rc=2, err="missing component lib")
        res = runner.export_netlist(sch)
    assert not res.success
    assert "exited with code 2" in (res.error or "")
    assert res.to_bridge_result().status is ExecutionStatus.FAILURE


def test_export_netlist_timeout(runner, tmp_path):
    sch = tmp_path / "design.sch"
    sch.write_text("dummy")
    with patch("eda_agents.bridge.xschem.subprocess.run") as m:
        m.side_effect = subprocess.TimeoutExpired(cmd="xschem", timeout=10)
        res = runner.export_netlist(sch)
    assert not res.success
    assert "timed out" in (res.error or "")
    assert res.to_bridge_result().status is ExecutionStatus.ERROR


def test_export_netlist_binary_missing(runner, tmp_path):
    sch = tmp_path / "design.sch"
    sch.write_text("dummy")
    with patch("eda_agents.bridge.xschem.subprocess.run") as m:
        m.side_effect = FileNotFoundError("xschem")
        res = runner.export_netlist(sch)
    assert not res.success
    assert "binary not found" in (res.error or "")


def test_validate_setup_reports_missing_binary():
    r = XschemRunner(xschem_cmd="/nonexistent/xschem-deadbeef")
    problems = r.validate_setup()
    assert any("xschem not found" in p for p in problems)


def test_validate_setup_clean(tmp_path):
    # Use the discovered system xschem; if absent, this just checks a real
    # binary (we don't require xschem in CI here).
    r = XschemRunner(xschem_cmd="/usr/bin/echo")  # any existing binary
    assert r.validate_setup() == []
