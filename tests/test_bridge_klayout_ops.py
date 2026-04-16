"""Tests for ``eda_agents.bridge.klayout_ops`` — runners injected as fakes.

We do NOT exercise real klayout here — the existing ``test_klayout_drc`` and
``test_klayout_lvs`` integration tests already cover that path and run as part
of the ``klayout`` marker. These tests verify the BridgeResult mapping only.
"""

from __future__ import annotations

from types import SimpleNamespace

from eda_agents.bridge.klayout_ops import KLayoutOps
from eda_agents.bridge.models import ExecutionStatus
from eda_agents.core.klayout_drc import KLayoutDrcResult
from eda_agents.core.klayout_lvs import KLayoutLvsResult


class _FakeDrc:
    def __init__(self, result: KLayoutDrcResult):
        self._result = result

    def run(self, **kw):
        return self._result

    def validate_setup(self):
        return []


class _FakeLvs:
    def __init__(self, result: KLayoutLvsResult):
        self._result = result

    def run(self, **kw):
        return self._result

    def validate_setup(self):
        return []


# -- DRC mapping ---------------------------------------------------------------------


def test_drc_clean_maps_to_success(tmp_path):
    fake = _FakeDrc(
        KLayoutDrcResult(
            success=True,
            total_violations=0,
            clean=True,
            report_paths=[str(tmp_path / "clean.lyrdb")],
            run_time_s=12.3,
        )
    )
    ops = KLayoutOps(drc_runner=fake, lvs_runner=_FakeLvs(KLayoutLvsResult(success=True, match=True)))
    res = ops.run_drc(gds_path=tmp_path / "x.gds", run_dir=tmp_path / "drc")
    assert res.status is ExecutionStatus.SUCCESS
    assert res.tool == "klayout-drc"
    assert res.metadata["clean"] is True
    assert res.duration_s == 12.3


def test_drc_violations_map_to_partial(tmp_path):
    fake = _FakeDrc(
        KLayoutDrcResult(
            success=True,
            total_violations=3,
            clean=False,
            violated_rules={"min_width": 2, "spacing": 1},
            report_paths=[str(tmp_path / "dirty.lyrdb")],
            run_time_s=10.0,
        )
    )
    ops = KLayoutOps(drc_runner=fake, lvs_runner=_FakeLvs(KLayoutLvsResult(success=True, match=True)))
    res = ops.run_drc(gds_path=tmp_path / "x.gds", run_dir=tmp_path / "drc")
    assert res.status is ExecutionStatus.PARTIAL
    assert res.metadata["total_violations"] == 3
    assert res.metadata["violated_rules"]["min_width"] == 2


def test_drc_failure_maps_to_error(tmp_path):
    fake = _FakeDrc(
        KLayoutDrcResult(
            success=False,
            total_violations=0,
            clean=False,
            error="klayout crashed",
            run_time_s=1.0,
        )
    )
    ops = KLayoutOps(drc_runner=fake, lvs_runner=_FakeLvs(KLayoutLvsResult(success=True, match=True)))
    res = ops.run_drc(gds_path=tmp_path / "x.gds", run_dir=tmp_path / "drc")
    assert res.status is ExecutionStatus.ERROR
    assert "klayout crashed" in res.errors


# -- LVS mapping ---------------------------------------------------------------------


def test_lvs_match_maps_to_success(tmp_path):
    fake = _FakeLvs(
        KLayoutLvsResult(
            success=True,
            match=True,
            extracted_netlist_path=str(tmp_path / "ext.cir"),
            report_path=str(tmp_path / "report.lvsdb"),
            run_time_s=20.5,
        )
    )
    ops = KLayoutOps(drc_runner=_FakeDrc(KLayoutDrcResult(success=True, total_violations=0, clean=True)), lvs_runner=fake)
    res = ops.run_lvs(
        gds_path=tmp_path / "x.gds",
        netlist_path=tmp_path / "ref.cir",
        run_dir=tmp_path / "lvs",
    )
    assert res.status is ExecutionStatus.SUCCESS
    assert res.tool == "klayout-lvs"
    assert res.metadata["match"] is True
    assert str(tmp_path / "ext.cir") in res.artifacts
    assert str(tmp_path / "report.lvsdb") in res.artifacts


def test_lvs_mismatch_maps_to_failure(tmp_path):
    fake = _FakeLvs(
        KLayoutLvsResult(
            success=True,
            match=False,
            extracted_netlist_path=str(tmp_path / "ext.cir"),
            report_path=str(tmp_path / "report.lvsdb"),
        )
    )
    ops = KLayoutOps(drc_runner=_FakeDrc(KLayoutDrcResult(success=True, total_violations=0, clean=True)), lvs_runner=fake)
    res = ops.run_lvs(
        gds_path=tmp_path / "x.gds",
        netlist_path=tmp_path / "ref.cir",
        run_dir=tmp_path / "lvs",
    )
    assert res.status is ExecutionStatus.FAILURE
    assert res.metadata["match"] is False


def test_lvs_runner_failure_maps_to_error(tmp_path):
    fake = _FakeLvs(
        KLayoutLvsResult(
            success=False,
            match=False,
            error="run_lvs.py crashed",
        )
    )
    ops = KLayoutOps(drc_runner=_FakeDrc(KLayoutDrcResult(success=True, total_violations=0, clean=True)), lvs_runner=fake)
    res = ops.run_lvs(
        gds_path=tmp_path / "x.gds",
        netlist_path=tmp_path / "ref.cir",
        run_dir=tmp_path / "lvs",
    )
    assert res.status is ExecutionStatus.ERROR
    assert "run_lvs.py crashed" in res.errors


def test_validate_setup_aggregates(tmp_path):
    drc_fake = SimpleNamespace(
        run=lambda **kw: None,
        validate_setup=lambda: ["drc problem"],
    )
    lvs_fake = SimpleNamespace(
        run=lambda **kw: None,
        validate_setup=lambda: ["lvs problem"],
    )
    ops = KLayoutOps(drc_runner=drc_fake, lvs_runner=lvs_fake)  # type: ignore[arg-type]
    out = ops.validate_setup()
    assert out == {"drc": ["drc problem"], "lvs": ["lvs problem"]}
