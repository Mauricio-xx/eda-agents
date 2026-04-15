"""Tests for the YAML-backed iteration log."""

from __future__ import annotations

import pytest

from eda_agents.agents.iteration_log import (
    EscalationError,
    IterationEntry,
    IterationLog,
)


def _log(max_iter: int = 3) -> IterationLog:
    return IterationLog(session_id="abc123", block="miller_ota", max_iterations=max_iter)


def test_record_increments_iteration():
    log = _log()
    e1 = log.record("librarian", "architect", summary="ok")
    e2 = log.record("architect", "designer", summary="ok")
    assert e1.iteration == 1
    assert e2.iteration == 2
    assert log.current_iteration() == 2


def test_record_explicit_iteration():
    log = _log()
    log.record("librarian", "architect", iteration=1)
    log.record("designer", "verifier", iteration=2)
    log.record("verifier", "designer", iteration=2, status="rejected")
    assert log.entries[-1].iteration == 2


def test_iteration_cap_raises():
    log = _log(max_iter=2)
    log.record("designer", "verifier", iteration=1)
    log.record("designer", "verifier", iteration=2)
    with pytest.raises(EscalationError):
        log.append(
            IterationEntry(iteration=3, from_role="designer", to_role="verifier")
        )


def test_escalate_records_entry():
    log = _log()
    log.record("designer", "verifier", iteration=1)
    e = log.escalate(summary="iteration cap reached")
    assert e.status == "escalated"
    assert log.entries[-1] is e


def test_yaml_roundtrip(tmp_path):
    log = _log()
    log.record("librarian", "architect", summary="hello")
    log.record("architect", "designer", summary="world")
    p = log.save(tmp_path / "log.yaml")
    loaded = IterationLog.load(p)
    assert loaded.session_id == log.session_id
    assert [e.summary for e in loaded.entries] == ["hello", "world"]
    assert loaded.max_iterations == log.max_iterations


def test_metadata_roundtrip(tmp_path):
    log = _log()
    log.record(
        "verifier",
        "architect",
        status="accepted",
        metadata={"adc_dB": 65.2, "violations": []},
    )
    p = log.save(tmp_path / "log.yaml")
    loaded = IterationLog.load(p)
    assert loaded.entries[-1].metadata["adc_dB"] == 65.2
