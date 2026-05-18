"""Tests for the SSTADEX primitive library + characterizer.

LUT-gated: skips when the IHP NFET .npz cannot be located. Mirrors
the layout of ``tests/test_ron_gm_lookup.py`` so the same env var
contract (``EDA_AGENTS_IHP_LUT_DIR``) applies.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.core.pdk import get_pdk


def _resolve_ihp_lut_path() -> Path:
    env_val = os.environ.get("EDA_AGENTS_IHP_LUT_DIR")
    if env_val:
        return Path(env_val)
    return Path(get_pdk("ihp_sg13g2").lut_dir_default or "")


_LUT_DIR = _resolve_ihp_lut_path()
_NMOS_NPZ = _LUT_DIR / "sg13_lv_nmos.npz"

pytestmark = pytest.mark.skipif(
    not _NMOS_NPZ.exists(),
    reason=(
        f"IHP NFET LUT not found at {_NMOS_NPZ}. Set "
        "EDA_AGENTS_IHP_LUT_DIR to a clone of ihp-gmid-kit."
    ),
)


@pytest.fixture(scope="module")
def lut() -> GmIdLookup:
    return GmIdLookup(pdk="ihp_sg13g2")


@pytest.fixture(scope="module")
def lib(lut):
    from eda_agents.topologies.sstadex import Library

    return Library(name="ihp_sg13g2", lut=lut)


class TestLibrary:
    def test_lists_four_primitives(self, lib):
        names = lib.list()
        assert set(names) == {
            "simplediffpair",
            "simplecurrentmirror",
            "simplecurrentsource",
            "simplecommonsource",
        }

    def test_get_returns_fresh_instance(self, lib):
        a = lib.get("simplediffpair", il=20e-6)
        b = lib.get("simplediffpair", il=20e-6)
        # Distinct port objects so independent biasing does not leak.
        assert a is not b
        a.set_port_voltage("VINP", 0.5)
        assert b.ports["VINP"].dc_voltage is None

    def test_unknown_primitive_raises(self, lib):
        with pytest.raises(KeyError):
            lib.get("nope")


class TestSimpleDiffPairBuild:
    def test_build_dataframe_shape(self, lib, lut):
        prim = lib.get("simplediffpair", il=20e-6)
        prim.set_port_voltages({
            "VINP": 0.9, "VINN": 0.9,
            "VOUTP": 1.0, "VOUTN": 1.0,
            "VTAIL": np.linspace(0.2, 0.9, 10),
        })
        df = prim.build(lut)
        # 10 VTAIL points x 5 default lengths = 50 rows.
        assert len(df.index) == 50
        for col in (
            "length", "width", "gm", "gds", "gdsid", "Ro",
            "cgg", "cgs", "cgd", "vgs", "vds",
        ):
            assert col in df.columns

    def test_width_positive_in_strong_inversion(self, lib, lut):
        # At Vgs = Vref - VTAIL >= 0.3 V the diff pair is well above
        # threshold; W must be a positive finite number.
        prim = lib.get("simplediffpair", il=20e-6)
        prim.set_port_voltages({
            "VINP": 0.9, "VINN": 0.9,
            "VOUTP": 1.0, "VOUTN": 1.0,
            "VTAIL": np.array([0.3, 0.4, 0.5]),
        })
        df = prim.build(lut)
        strong = df[df["vgs"] > 0.4]
        assert (strong["width"] > 0).all()
        assert np.isfinite(strong["width"]).all()


class TestSimpleCurrentMirrorBuild:
    def test_pmos_mirror_yields_positive_gm(self, lib, lut):
        prim = lib.get("simplecurrentmirror", il=20e-6)
        prim.set_port_voltages({
            "VINP": 1.0, "VINN": 1.0,
            "VOUTP": 1.0, "VOUTN": 1.0,
            "VDD": 1.5,
        })
        df = prim.build(lut)
        # 5 default lengths, single (vds, vgs) point each.
        assert len(df.index) == 5
        # gm and Ro must be physically sensible.
        assert (df["gm"] > 0).all()
        assert (df["Ro"] > 0).all()


class TestSimpleCurrentSourceBuild:
    def test_dual_width_columns(self, lib, lut):
        prim = lib.get("simplecurrentsource", il=20e-6)
        prim.set_port_voltages({
            "VOUTP": np.linspace(0.2, 0.9, 10),
            "VINP": np.linspace(0.2, 0.9, 10),
            "VINN": np.linspace(0.2, 0.9, 10),
            "VSS": 0.0,
        })
        df = prim.build(lut)
        # m1 / m2 mirror -> two distinct width columns + a shared length.
        assert "width_m1" in df.columns
        assert "width_m2" in df.columns
        # In this minimal model both branches carry the same scaled W.
        assert (df["width_m1"] == df["width_m2"]).all()
        # vgs_cs lets the macromodel wire the bias hierarchy.
        assert "vgs_cs" in df.columns
