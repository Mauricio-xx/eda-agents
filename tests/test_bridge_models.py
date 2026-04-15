"""Tests for ``eda_agents.bridge.models`` — Pydantic v2 result types."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from eda_agents.bridge.models import (
    BridgeResult,
    ExecutionStatus,
    SimulationResult,
)


def test_bridge_result_ok_only_for_success():
    r = BridgeResult(status=ExecutionStatus.SUCCESS, tool="xschem")
    assert r.ok is True

    for status in (
        ExecutionStatus.FAILURE,
        ExecutionStatus.PARTIAL,
        ExecutionStatus.ERROR,
        ExecutionStatus.CANCELLED,
    ):
        assert BridgeResult(status=status, tool="xschem").ok is False


def test_bridge_result_is_frozen():
    r = BridgeResult(status=ExecutionStatus.SUCCESS, tool="xschem")
    with pytest.raises(ValidationError):
        # frozen=True -> assignment must raise
        r.status = ExecutionStatus.FAILURE  # type: ignore[misc]


def test_bridge_result_extra_forbidden():
    with pytest.raises(ValidationError):
        BridgeResult(
            status=ExecutionStatus.SUCCESS,
            tool="xschem",
            unknown_field=1,  # type: ignore[call-arg]
        )


def test_bridge_result_save_load_roundtrip(tmp_path):
    r = BridgeResult(
        status=ExecutionStatus.PARTIAL,
        tool="klayout-drc",
        output="2 violations",
        warnings=["edge-too-close"],
        duration_s=12.5,
        artifacts=[str(tmp_path / "report.lyrdb")],
        metadata={"variant": "C"},
    )
    out = r.save_json(tmp_path / "result.json")
    assert out.is_file()
    r2 = BridgeResult.load_json(out)
    assert r2 == r
    # JSON itself is well-formed and contains the enum value as string
    payload = json.loads(out.read_text())
    assert payload["status"] == "partial"
    assert payload["tool"] == "klayout-drc"


def test_bridge_result_save_creates_parents(tmp_path):
    nested = tmp_path / "a" / "b" / "c" / "result.json"
    BridgeResult(status=ExecutionStatus.SUCCESS, tool="xschem").save_json(nested)
    assert nested.is_file()


def test_simulation_result_ok_and_measurements():
    r = SimulationResult(
        status=ExecutionStatus.SUCCESS,
        netlist="/tmp/foo.cir",
        measurements={"adc_db": 65.4, "gbw_hz": 1.2e6, "pm_deg": 60.0},
        duration_s=3.4,
    )
    assert r.ok
    assert r.measurements["adc_db"] == pytest.approx(65.4)


def test_simulation_result_default_tool_is_ngspice():
    r = SimulationResult(status=ExecutionStatus.SUCCESS)
    assert r.tool == "ngspice"


def test_simulation_result_roundtrip(tmp_path):
    r = SimulationResult(
        status=ExecutionStatus.SUCCESS,
        netlist="deck.cir",
        measurements={"enob": 7.4, "sndr_dbc": 46.2, "fom_fj": 18.3},
        artifacts=["bit_data.txt"],
        warnings=["gmin stepping"],
        duration_s=12.0,
        metadata={"pdk": "ihp_sg13g2"},
    )
    p = r.save_json(tmp_path / "sim.json")
    r2 = SimulationResult.load_json(p)
    assert r2 == r


def test_simulation_result_failure_has_no_measurements():
    r = SimulationResult(
        status=ExecutionStatus.ERROR,
        errors=["ngspice exited with code 1 (no measurements)"],
    )
    assert not r.ok
    assert r.measurements == {}
    assert r.errors == ["ngspice exited with code 1 (no measurements)"]


def test_execution_status_string_value():
    assert ExecutionStatus.SUCCESS.value == "success"
    assert ExecutionStatus.CANCELLED.value == "cancelled"
