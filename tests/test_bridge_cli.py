"""Tests for the ``eda-bridge`` CLI."""

from __future__ import annotations

import json

import pytest

from eda_agents.bridge import cli as cli_mod
from eda_agents.bridge.jobs import JobRegistry, JobStatus


def _run(*argv: str) -> int:
    return cli_mod.main(list(argv))


def test_init_creates_dirs(tmp_path, capsys):
    rc = _run("--jobs-dir", str(tmp_path / "jobs"), "init")
    assert rc == 0
    out = capsys.readouterr().out
    assert "jobs dir" in out
    assert (tmp_path / "jobs").is_dir()


def test_status_json_lists_known_tools(tmp_path, capsys):
    rc = _run("--jobs-dir", str(tmp_path / "jobs"), "status", "--json")
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "tools" in payload
    for k in ("xschem", "ngspice", "klayout", "magic", "openroad", "ssh"):
        assert k in payload["tools"]
    # rc encodes "all tools available"; we don't assert on the result —
    # CI may legitimately lack one of these.
    assert rc in (0, 1)


def test_jobs_lists_records(tmp_path, capsys):
    reg = JobRegistry(jobs_dir=tmp_path / "jobs")
    try:
        for _ in range(2):
            reg.wait(reg.submit(lambda: 1, kind="probe"), timeout=5)
    finally:
        reg.shutdown()
    rc = _run("--jobs-dir", str(tmp_path / "jobs"), "jobs", "--json")
    out = capsys.readouterr().out
    records = json.loads(out)
    assert rc == 0
    assert len(records) == 2
    assert {r["status"] for r in records} == {"done"}


def test_cancel_known_unknown_jobs(tmp_path, capsys):
    reg = JobRegistry(jobs_dir=tmp_path / "jobs")
    try:
        # Submit a slow job, cancel it.
        import time
        job_id = reg.submit(lambda: time.sleep(0.5))
        # need to give the worker a moment so cancel is meaningful
        time.sleep(0.05)
    finally:
        # Cancel via CLI
        rc_known = _run("--jobs-dir", str(tmp_path / "jobs"), "cancel", job_id)
        rc_unknown = _run("--jobs-dir", str(tmp_path / "jobs"), "cancel", "deadbeef")
        # wait for the worker so the registry is quiescent before shutdown
        reg.wait(job_id, timeout=5)
        reg.shutdown()
    assert rc_known == 0
    assert rc_unknown == 1


def test_stop_is_alias_for_cancel(tmp_path, capsys):
    reg = JobRegistry(jobs_dir=tmp_path / "jobs")
    try:
        import time
        job_id = reg.submit(lambda: time.sleep(0.5))
        time.sleep(0.05)
        rc = _run("--jobs-dir", str(tmp_path / "jobs"), "stop", job_id)
        assert rc == 0
        rec = reg.wait(job_id, timeout=5)
        assert rec.status is JobStatus.CANCELLED
    finally:
        reg.shutdown()


def test_start_xschem_netlist_submits_job(tmp_path, capsys, monkeypatch):
    """``start xschem-netlist`` should submit a job that calls XschemRunner."""
    sch = tmp_path / "design.sch"
    sch.write_text("dummy")

    seen = {}

    def fake_export(self, sch_path, out_dir=None, out_name=None, cwd=None):
        seen["sch"] = str(sch_path)
        from eda_agents.bridge.xschem import XschemNetlistResult
        return XschemNetlistResult(
            success=True,
            netlist_path=tmp_path / "out.spice",
            log_path=None,
            duration_s=0.01,
            stdout="ok",
            stderr="",
        )

    monkeypatch.setattr(
        "eda_agents.bridge.xschem.XschemRunner.export_netlist", fake_export
    )

    rc = _run(
        "--jobs-dir", str(tmp_path / "jobs"),
        "start", "xschem-netlist",
        "--sch", str(sch),
        "--wait",
    )
    assert rc == 0
    assert seen["sch"] == str(sch)


def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        _run("--help")
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in ("init", "status", "jobs", "cancel", "stop", "start"):
        assert sub in out
