"""Tests for the EDA bridge job registry — pure-Python, no external IO."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from eda_agents.bridge.jobs import JobRegistry, JobStatus


@pytest.fixture
def registry(tmp_path):
    reg = JobRegistry(jobs_dir=tmp_path / "jobs", max_workers=2)
    yield reg
    reg.shutdown(wait=True)


def test_submit_returns_uuid_and_writes_record(registry, tmp_path):
    job_id = registry.submit(lambda: 42, kind="probe")
    assert isinstance(job_id, str) and len(job_id) == 12

    rec = registry.wait(job_id, timeout=5)
    assert rec is not None
    assert rec.id == job_id
    assert rec.status is JobStatus.DONE
    assert rec["result"] == 42
    assert rec["kind"] == "probe"

    # Disk has the same record
    disk = json.loads((tmp_path / "jobs" / f"{job_id}.json").read_text())
    assert disk["status"] == "done"
    assert disk["result"] == 42


def test_failure_records_error(registry):
    def boom():
        raise RuntimeError("nope")

    job_id = registry.submit(boom, kind="boom")
    rec = registry.wait(job_id, timeout=5)
    assert rec.status is JobStatus.ERROR
    assert rec["errors"]
    assert "RuntimeError" in rec["errors"][0]
    assert "nope" in rec["errors"][0]


def test_metadata_persisted(registry):
    job_id = registry.submit(lambda: None, metadata={"design": "miller_ota"})
    rec = registry.wait(job_id, timeout=5)
    assert rec["metadata"] == {"design": "miller_ota"}


def test_list_orders_by_mtime(registry):
    ids = []
    for _ in range(3):
        ids.append(registry.submit(lambda: None))
        time.sleep(0.01)
    for job_id in ids:
        registry.wait(job_id, timeout=5)
    listed = [r.id for r in registry.list()]
    assert sorted(listed) == sorted(ids)
    assert len(listed) == 3


def test_get_returns_none_for_unknown(registry):
    assert registry.get("doesnotexist") is None


def test_cancel_marks_record(registry):
    # Submit a long-running job, cancel before it can complete.
    started = []

    def slow():
        started.append(True)
        time.sleep(0.5)
        return "done"

    job_id = registry.submit(slow)
    # Give the worker a moment to pick it up so cancel is meaningful.
    time.sleep(0.05)
    ok = registry.cancel(job_id)
    assert ok
    rec = registry.wait(job_id, timeout=5)
    assert rec.status is JobStatus.CANCELLED
    assert "cancelled by user" in rec["errors"]


def test_cancel_unknown_returns_false(registry):
    assert registry.cancel("nope") is False


def test_cancel_terminal_returns_false(registry):
    job_id = registry.submit(lambda: 1)
    registry.wait(job_id, timeout=5)
    assert registry.cancel(job_id) is False


def test_sweep_removes_old_done_records(tmp_path):
    reg = JobRegistry(jobs_dir=tmp_path / "jobs", expiry_seconds=1)
    try:
        job_id = reg.submit(lambda: 1)
        reg.wait(job_id, timeout=5)
        # Backdate the 'finished' timestamp by 1h
        path = (tmp_path / "jobs") / f"{job_id}.json"
        data = json.loads(path.read_text())
        data["finished"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        path.write_text(json.dumps(data))
        removed = reg.sweep()
        assert removed == 1
        assert reg.get(job_id) is None
    finally:
        reg.shutdown()


def test_sweep_keeps_running(registry):
    # A non-terminal record must never be expired
    job_id = registry.submit(lambda: time.sleep(0.3))
    time.sleep(0.05)
    removed = registry.sweep()
    assert removed == 0
    registry.wait(job_id, timeout=5)


def test_pydantic_return_value_is_jsonable(registry):
    """Bridge results returned by jobs must roundtrip cleanly."""
    from eda_agents.bridge.models import BridgeResult, ExecutionStatus

    def do_work():
        return BridgeResult(status=ExecutionStatus.SUCCESS, tool="probe")

    job_id = registry.submit(do_work)
    rec = registry.wait(job_id, timeout=5)
    assert rec.status is JobStatus.DONE
    assert rec["result"]["status"] == "success"
    assert rec["result"]["tool"] == "probe"


def test_dataclass_return_value_is_jsonable(registry):
    from dataclasses import dataclass

    @dataclass
    class Thing:
        a: int
        b: str

    job_id = registry.submit(lambda: Thing(a=1, b="x"))
    rec = registry.wait(job_id, timeout=5)
    assert rec["result"] == {"a": 1, "b": "x"}


def test_poll_until_terminal_cross_process_view(tmp_path):
    """A second registry pointing at the same dir should see status hops."""
    a = JobRegistry(jobs_dir=tmp_path / "jobs", max_workers=1)
    b = JobRegistry(jobs_dir=tmp_path / "jobs", max_workers=1)
    try:
        job_id = a.submit(lambda: time.sleep(0.05))
        rec = b.poll_until_terminal(job_id, timeout=5)
        assert rec is not None
        assert rec.is_terminal
        assert rec.status is JobStatus.DONE
    finally:
        a.shutdown()
        b.shutdown()
