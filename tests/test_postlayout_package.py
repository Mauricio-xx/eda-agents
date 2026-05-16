"""Tests for ``PostLayoutResult.to_package`` and the package helper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eda_agents.agents.phase_results import (
    PostLayoutResult,
    _PACKAGE_SCHEMA_VERSION,
    package_postlayout_results,
)


def _make_synthetic_result(work_dir: Path) -> PostLayoutResult:
    """Build a PostLayoutResult pointing at temp files inside work_dir."""
    work_dir.mkdir(parents=True, exist_ok=True)

    gds = work_dir / "src_layout.gds"
    sch = work_dir / "src_schematic.spice"
    ext = work_dir / "src_extracted.spice"
    drc = work_dir / "src_drc_report.lyrdb"
    lvs = work_dir / "src_lvs_report.lvsdb"
    gds.write_bytes(b"HEADER\x00fake-gds\x00")
    sch.write_text(".subckt my_ota in out vdd vss\n.ends\n")
    ext.write_text(".subckt my_ota_pex in out vdd vss\nC1 in out 1f\n.ends\n")
    drc.write_text("<drc>fake</drc>\n")
    lvs.write_text("LVS OK\n")

    pre_dir = work_dir / "pre_sim"
    post_dir = work_dir / "post_sim"
    pre_dir.mkdir()
    post_dir.mkdir()
    (pre_dir / "tb.cir").write_text("* pre tb\n")
    (pre_dir / "tb.meas").write_text("adc = 60.0\n")
    (pre_dir / "ignore.bin").write_bytes(b"\x00" * 32)
    (post_dir / "tb.cir").write_text("* post tb\n")
    (post_dir / "tb.meas").write_text("adc = 55.0\n")
    (post_dir / "ngspice.log").write_text("ok\n")

    return PostLayoutResult(
        params={"Ibias_uA": 200.0, "L_dp_um": 2.0},
        pre_layout_fom=1.50e6,
        pdk="gf180mcu",
        topology="gf180_ota",
        gds_path=str(gds),
        netlist_path=str(sch),
        drc_clean=True,
        drc_violations=0,
        drc_report_path=str(drc),
        lvs_match=True,
        lvs_report_path=str(lvs),
        extracted_netlist_path=str(ext),
        pex_corner="ngspice()",
        post_Adc_dB=55.0,
        post_GBW_Hz=8.5e6,
        post_PM_deg=58.0,
        post_fom=1.20e6,
        post_valid=True,
        post_sim_dir=str(post_dir),
        pre_sim_dir=str(pre_dir),
        gain_delta_dB=-5.0,
        gbw_delta_pct=-15.0,
        pm_delta_deg=-7.0,
        fom_delta_pct=-20.0,
        total_time_s=123.4,
    )


class TestToPackage:
    def test_creates_dst_and_manifest(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)

        out = result.to_package(dst)

        assert out == dst
        assert dst.is_dir()
        assert (dst / "manifest.json").is_file()

    def test_manifest_is_valid_json(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)
        result.to_package(dst)

        data = json.loads((dst / "manifest.json").read_text())
        assert isinstance(data, dict)

    def test_manifest_has_required_keys(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)
        result.to_package(dst)

        data = json.loads((dst / "manifest.json").read_text())
        for key in (
            "schema_version",
            "pdk",
            "topology",
            "params",
            "param",
            "const",
            "pre-sim",
            "layout",
            "extract",
            "drc",
            "lvs",
            "post-sim",
            "pre_layout_fom",
            "post_layout_fom",
            "deltas",
            "post_layout_metrics",
            "baseline_metrics",
            "pex_corner",
            "drc_clean",
            "drc_violations",
            "lvs_match",
            "total_time_s",
            "error",
        ):
            assert key in data, f"missing key {key!r}"
        assert data["schema_version"] == _PACKAGE_SCHEMA_VERSION

    def test_manifest_carries_provenance(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)
        result.to_package(dst)

        data = json.loads((dst / "manifest.json").read_text())
        assert data["pdk"] == "gf180mcu"
        assert data["topology"] == "gf180_ota"
        assert data["params"] == {"Ibias_uA": 200.0, "L_dp_um": 2.0}

    def test_deltas_survive_round_trip(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)
        result.to_package(dst)

        data = json.loads((dst / "manifest.json").read_text())
        assert data["deltas"]["fom_pct"] == -20.0
        assert data["deltas"]["gain_dB"] == -5.0
        assert data["deltas"]["gbw_pct"] == -15.0
        assert data["deltas"]["pm_deg"] == -7.0
        assert data["pre_layout_fom"] == 1.50e6
        assert data["post_layout_fom"] == 1.20e6
        assert data["pex_corner"] == "ngspice()"
        assert data["drc_clean"] is True
        assert data["lvs_match"] is True

    def test_artifacts_copied_into_dst(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)
        result.to_package(dst)

        assert (dst / "layout.gds").is_file()
        assert (dst / "schematic.spice").is_file()
        assert (dst / "extracted.spice").is_file()
        assert (dst / "src_drc_report.lyrdb").is_file()
        assert (dst / "src_lvs_report.lvsdb").is_file()
        assert (dst / "pre_sim" / "tb.cir").is_file()
        assert (dst / "pre_sim" / "tb.meas").is_file()
        assert (dst / "post_sim" / "tb.cir").is_file()
        assert (dst / "post_sim" / "tb.meas").is_file()
        assert (dst / "post_sim" / "ngspice.log").is_file()

    def test_default_copy_preserves_source(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)
        result.to_package(dst)

        assert (src / "src_layout.gds").is_file()
        assert (src / "src_schematic.spice").is_file()
        assert (src / "pre_sim" / "tb.cir").is_file()
        assert (src / "post_sim" / "tb.cir").is_file()

    def test_move_removes_source(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)
        result.to_package(dst, copy_artifacts=False)

        assert not (src / "src_layout.gds").is_file()
        assert (dst / "layout.gds").is_file()

    def test_glob_filters_sim_artifacts(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)
        result.to_package(dst)

        # ignore.bin under pre_sim should be filtered out by default globs.
        assert not (dst / "pre_sim" / "ignore.bin").exists()

    def test_pre_sim_list_populated(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)
        result.to_package(dst)

        data = json.loads((dst / "manifest.json").read_text())
        assert "pre_sim/tb.cir" in data["pre-sim"]
        assert "pre_sim/tb.meas" in data["pre-sim"]
        assert "post_sim/tb.cir" in data["post-sim"]
        assert "post_sim/ngspice.log" in data["post-sim"]

    def test_layout_list_uses_canonical_name(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "pkg"
        result = _make_synthetic_result(src)
        result.to_package(dst)

        data = json.loads((dst / "manifest.json").read_text())
        assert data["layout"] == ["layout.gds"]

    def test_missing_artifact_skipped_not_raised(self, tmp_path, caplog):
        dst = tmp_path / "pkg"
        result = PostLayoutResult(
            params={"x": 1.0},
            gds_path=str(tmp_path / "does_not_exist.gds"),
            drc_clean=False,
        )
        result.to_package(dst)

        data = json.loads((dst / "manifest.json").read_text())
        assert data["layout"] == "layout artifacts not found"

    def test_empty_lists_become_sentinel_strings(self, tmp_path):
        dst = tmp_path / "pkg"
        result = PostLayoutResult(params={"x": 1.0})
        result.to_package(dst)

        data = json.loads((dst / "manifest.json").read_text())
        for key in ("param", "const", "pre-sim", "layout", "extract", "drc", "lvs", "post-sim"):
            assert isinstance(data[key], str)
            assert "not found" in data[key]


class TestPackageHelper:
    def test_package_postlayout_results_creates_one_dir_per_result(self, tmp_path):
        src_a = tmp_path / "src_a"
        src_b = tmp_path / "src_b"
        ra = _make_synthetic_result(src_a)
        rb = _make_synthetic_result(src_b)
        dst_root = tmp_path / "out"

        paths = package_postlayout_results([ra, rb], dst_root)

        assert paths == [dst_root / "design_000", dst_root / "design_001"]
        for p in paths:
            assert (p / "manifest.json").is_file()
            assert (p / "layout.gds").is_file()

    def test_package_helper_propagates_copy_flag(self, tmp_path):
        src = tmp_path / "src"
        result = _make_synthetic_result(src)
        dst_root = tmp_path / "out"

        package_postlayout_results([result], dst_root, copy_artifacts=False)
        assert not (src / "src_layout.gds").is_file()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
