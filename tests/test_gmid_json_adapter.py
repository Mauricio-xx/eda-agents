"""Round-trip tests for the gmoverid-skill JSON cache adapter.

The adapter is deliberately thin: extract a slice out of our 4D
``.npz`` LUT, write JSON in gmoverid-skill schema, read it back,
and re-assemble a 4D ``.npz`` from a bag of slices. These tests do
not require the actual IHP LUT; they build a minimal synthetic 4D
``.npz`` in ``tmp_path`` so the adapter code is exercised without
pulling the 100 MB external kit into CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.core.pdk import PdkConfig, register_pdk
from eda_agents.tools.gmid_json_adapter import (
    assemble_npz_from_json_slices,
    load_json_slice,
    npz_slice_to_json_dict,
    save_json_slice,
)


def _build_synthetic_lut(tmp_path: Path) -> tuple[Path, str]:
    """Write a toy 4D .npz matching the ihp-gmid-kit layout."""
    # 3 lengths, 2 Vbs, 15 Vgs, 4 Vds — tiny but 4D.
    lengths = np.array([0.13e-6, 1.0e-6, 5.0e-6])
    vbs = np.array([0.0, -0.6])
    vgs = np.linspace(0.0, 1.2, 15)
    vds = np.array([0.3, 0.6, 0.9, 1.2])
    shape = (len(lengths), len(vbs), len(vgs), len(vds))

    # Synthetic device model (nothing physical, just non-zero and
    # broadcast in Vgs so gm/ID has a peak in the interior):
    L, V_b, V_g, V_d = np.meshgrid(
        lengths, vbs, vgs, vds, indexing="ij"
    )
    id_arr = np.maximum(V_g - 0.3, 0.0) ** 2 * 1e-3
    gm_arr = 2 * np.maximum(V_g - 0.3, 0.0) * 1e-3
    gds_arr = id_arr / 10.0
    vth_arr = np.full(shape, 0.3, dtype=np.float32)
    cgg_arr = np.full(shape, 5e-15, dtype=np.float32)

    payload = {
        "id": id_arr.astype(np.float32),
        "gm": gm_arr.astype(np.float32),
        "gds": gds_arr.astype(np.float32),
        "vth": vth_arr,
        "cgg": cgg_arr,
        "cgs": cgg_arr * 0.7,
        "cgd": cgg_arr * 0.3,
        "vgs": vgs.astype(np.float64),
        "vds": vds.astype(np.float64),
        "vbs": vbs.astype(np.float64),
        "length": lengths.astype(np.float64),
        "model_name": "toy_nfet",
        "parameter_names": ["id", "gm", "gds", "vth", "cgg", "cgs", "cgd"],
        "device_parameters": {"w": 10e-6, "ng": 1, "m": 1},
    }

    out_path = tmp_path / "toy_nfet.npz"
    np.savez(str(out_path), lookup_table={"toy_nfet": payload})
    return out_path, "toy_nfet"


@pytest.fixture
def toy_lut(tmp_path: Path) -> GmIdLookup:
    npz_path, model_key = _build_synthetic_lut(tmp_path)
    # Register a throwaway PdkConfig that points at the synthetic LUT.
    pdk = PdkConfig(
        name="toy_pdk",
        display_name="Toy PDK",
        technology_nm=130,
        VDD=1.2, Lmin_m=130e-9, Wmin_m=150e-9, z1_m=340e-9,
        model_lib_rel="unused",
        model_corner="tt",
        nmos_symbol="toy_nfet",
        pmos_symbol="toy_pfet",
        lut_dir_default=str(tmp_path),
        lut_nmos_file="toy_nfet.npz",
        lut_pmos_file="toy_pfet.npz",
        lut_model_key_nmos="toy_nfet",
        lut_model_key_pmos="toy_pfet",
    )
    register_pdk(pdk)
    return GmIdLookup(pdk=pdk, lut_dir=tmp_path)


class TestNpzToJson:
    def test_slice_schema_matches_gmoverid(self, toy_lut):
        slice_dict = npz_slice_to_json_dict(
            toy_lut, "nmos", L_um=1.0, Vds=0.6, Vbs=0.0
        )
        expected_keys = {
            "schema", "model", "W_um", "L_um", "Vds_V", "Vbs_V",
            "vgs", "id", "gm", "gds", "gmid", "ft", "id_w", "vov",
            "cgg", "cgs", "cgd",
        }
        assert expected_keys.issubset(slice_dict.keys())
        assert slice_dict["schema"] == "gmoverid.v1"
        assert slice_dict["L_um"] == pytest.approx(1.0)
        # All Vgs-indexed arrays share the same length.
        n = len(slice_dict["vgs"])
        for key in ("id", "gm", "gds", "gmid", "ft", "id_w", "vov",
                    "cgg", "cgs", "cgd"):
            assert len(slice_dict[key]) == n

    def test_roundtrip_on_disk(self, toy_lut, tmp_path: Path):
        slice_dict = npz_slice_to_json_dict(
            toy_lut, "nmos", L_um=1.0, Vds=0.6, Vbs=0.0
        )
        json_path = tmp_path / "slice.json"
        save_json_slice(slice_dict, json_path)
        assert json_path.exists()
        loaded = load_json_slice(json_path)
        # Key arrays must survive the round-trip intact.
        assert np.allclose(loaded["vgs"], slice_dict["vgs"])
        assert np.allclose(loaded["id"], slice_dict["id"])
        assert loaded["L_um"] == pytest.approx(slice_dict["L_um"])

    def test_json_is_valid(self, toy_lut, tmp_path: Path):
        slice_dict = npz_slice_to_json_dict(
            toy_lut, "nmos", L_um=1.0, Vds=0.6, Vbs=0.0
        )
        path = tmp_path / "s.json"
        save_json_slice(slice_dict, path)
        # Must be parseable by the stdlib json module.
        assert json.loads(path.read_text())["model"] == "toy_nfet"


class TestAssembleFromJson:
    def test_full_grid_roundtrip(self, toy_lut, tmp_path: Path):
        # Export every (L, Vds) slice at Vbs=0 → JSON; re-assemble →
        # .npz; load with GmIdLookup; assert the slice dict is the
        # same up to float32 precision.
        lengths = [0.13, 1.0, 5.0]
        vds_list = [0.3, 0.6, 0.9, 1.2]
        out_dir = tmp_path / "json_slices"
        slice_paths = []
        for l_um in lengths:
            for vds in vds_list:
                s = npz_slice_to_json_dict(
                    toy_lut, "nmos", L_um=l_um, Vds=vds, Vbs=0.0
                )
                p = out_dir / f"L{l_um}_V{vds}.json"
                save_json_slice(s, p)
                slice_paths.append(p)

        npz_out = tmp_path / "reassembled.npz"
        assemble_npz_from_json_slices(
            slice_paths,
            npz_out,
            model_key="toy_nfet",
            Vbs_V=0.0,
        )
        assert npz_out.exists()

        # Sanity: loading the reassembled .npz reproduces a slice.
        pdk = PdkConfig(
            name="toy_pdk_rt",
            display_name="Toy PDK RT",
            technology_nm=130, VDD=1.2,
            Lmin_m=130e-9, Wmin_m=150e-9, z1_m=340e-9,
            model_lib_rel="unused", model_corner="tt",
            nmos_symbol="toy_nfet", pmos_symbol="toy_pfet",
            lut_dir_default=str(tmp_path),
            lut_nmos_file="reassembled.npz",
            lut_pmos_file="reassembled.npz",
            lut_model_key_nmos="toy_nfet",
            lut_model_key_pmos="toy_nfet",
        )
        register_pdk(pdk)
        rt_lut = GmIdLookup(pdk=pdk, lut_dir=tmp_path)
        orig = npz_slice_to_json_dict(
            toy_lut, "nmos", L_um=1.0, Vds=0.6, Vbs=0.0
        )
        rt = npz_slice_to_json_dict(
            rt_lut, "nmos", L_um=1.0, Vds=0.6, Vbs=0.0
        )
        # Toy id array is quadratic; float32 round-trip is fine at
        # rel=1e-4.
        assert np.allclose(rt["id"], orig["id"], rtol=1e-4, atol=1e-12)
        assert np.allclose(rt["gm"], orig["gm"], rtol=1e-4, atol=1e-12)

    def test_incomplete_grid_raises(self, toy_lut, tmp_path: Path):
        # Produce only one slice and try to assemble.
        s = npz_slice_to_json_dict(
            toy_lut, "nmos", L_um=1.0, Vds=0.6, Vbs=0.0
        )
        p = tmp_path / "lone.json"
        save_json_slice(s, p)
        # One slice is a 1x1 grid, which is technically valid, so we
        # instead force a gap by asking for 2 slices with mismatched
        # Vgs grids.
        s2 = dict(s)
        s2["vgs"] = [v + 1.0 for v in s["vgs"]]
        s2["L_um"] = 5.0
        s2["Vds_V"] = 0.6
        p2 = tmp_path / "bad.json"
        save_json_slice(s2, p2)
        with pytest.raises(ValueError, match="Vgs grid mismatch"):
            assemble_npz_from_json_slices(
                [p, p2], tmp_path / "out.npz",
                model_key="toy_nfet", Vbs_V=0.0,
            )

    def test_non_rectangular_grid_raises(self, toy_lut, tmp_path: Path):
        # Three slices across (L×Vds) can't form a full 2×2 rectangle.
        s1 = npz_slice_to_json_dict(toy_lut, "nmos", 0.13, 0.3, 0.0)
        s2 = npz_slice_to_json_dict(toy_lut, "nmos", 0.13, 0.6, 0.0)
        s3 = npz_slice_to_json_dict(toy_lut, "nmos", 1.0, 0.3, 0.0)
        paths = []
        for i, s in enumerate((s1, s2, s3)):
            p = tmp_path / f"s{i}.json"
            save_json_slice(s, p)
            paths.append(p)
        with pytest.raises(ValueError, match="rectangular grid"):
            assemble_npz_from_json_slices(
                paths, tmp_path / "bad.npz",
                model_key="toy_nfet", Vbs_V=0.0,
            )
