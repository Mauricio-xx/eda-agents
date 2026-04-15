"""Tests for the gm/ID sizing API on GmIdLookup.

These hit the real IHP SG13G2 LUT shipped with ``ihp-gmid-kit`` — if
the kit isn't installed locally, the entire module skips. The checks
are formulaic (arithmetic identities that must hold regardless of
the LUT's exact numerical contents) rather than fixed magic numbers,
so future kit regenerations won't break them.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.core.pdk import get_pdk

_IHP_LUT = Path(get_pdk("ihp_sg13g2").lut_dir_default) / "sg13_lv_nmos.npz"

pytestmark = pytest.mark.skipif(
    not _IHP_LUT.exists(),
    reason=f"IHP NFET LUT not found at {_IHP_LUT}",
)


@pytest.fixture(scope="module")
def lut() -> GmIdLookup:
    return GmIdLookup(pdk="ihp_sg13g2")


class TestSize:
    def test_size_by_id_matches_gmid(self, lut):
        op = lut.size(15.0, "nmos", L_um=1.0, Vds=0.6, Id=10e-6)
        # gm / ID must equal the requested gm/ID within numerical tol
        # (at fixed-operating-point interpolation).
        assert op["gm_uS"] / op["Id_uA"] == pytest.approx(15.0, rel=1e-3)
        assert op["Id_uA"] == pytest.approx(10.0, rel=1e-9)
        assert op["L_um"] == 1.0
        assert op["W_um"] > 0
        # Every canonical key must be present.
        for key in (
            "W_um", "L_um", "Id_uA", "gm_uS", "gds_uS",
            "ft_Hz", "vgs_V", "vds_V", "vbs_V", "gmid",
            "gmro", "vth_V", "mos_type",
        ):
            assert key in op

    def test_size_by_w_inverse_of_id(self, lut):
        # Sizing with W reproduces the same W when you re-size with
        # the Id it predicted.
        op_w = lut.size(12.0, "nmos", L_um=0.5, Vds=0.6, W=4.0)
        op_id = lut.size(
            12.0, "nmos", L_um=0.5, Vds=0.6, Id=op_w["Id_uA"] * 1e-6
        )
        assert op_id["W_um"] == pytest.approx(op_w["W_um"], rel=1e-6)

    def test_size_requires_exactly_one_constraint(self, lut):
        with pytest.raises(ValueError, match="exactly one"):
            lut.size(15.0, "nmos", L_um=1.0, Vds=0.6)
        with pytest.raises(ValueError, match="exactly one"):
            lut.size(
                15.0, "nmos", L_um=1.0, Vds=0.6, Id=10e-6, W=5.0
            )

    def test_size_out_of_range_gmid_raises(self, lut):
        # gm/ID=100 is physically impossible.
        with pytest.raises(ValueError, match="out of range"):
            lut.size(100.0, "nmos", L_um=1.0, Vds=0.6, Id=10e-6)


class TestSizeFromFt:
    def test_ft_target_satisfied(self, lut):
        # 1 GHz is easy at L=1 um for IHP NFET.
        op = lut.size_from_ft(1e9, "nmos", L_um=1.0, Vds=0.6, Id=10e-6)
        assert op["ft_Hz"] is not None
        assert op["ft_Hz"] >= 1e9 * 0.99  # interp tolerance

    def test_shorter_L_gives_higher_achievable_fT(self, lut):
        # At shorter L the LUT can satisfy much higher fT targets.
        short = lut.size_from_ft(5e9, "nmos", L_um=0.13, Vds=0.6, Id=10e-6)
        assert short["ft_Hz"] >= 5e9 * 0.99

    def test_long_L_cannot_hit_huge_ft(self, lut):
        # 20 GHz at L=5 um is not reachable.
        with pytest.raises(ValueError, match="exceeds max achievable"):
            lut.size_from_ft(20e9, "nmos", L_um=5.0, Vds=0.6, Id=10e-6)

    def test_requires_one_of_id_or_w(self, lut):
        with pytest.raises(ValueError):
            lut.size_from_ft(1e9, "nmos", L_um=1.0, Vds=0.6)


class TestSizeFromGmro:
    def test_gmro_target_satisfied(self, lut):
        # L=1 um typically supports gm*ro ~ 30; target 20 is safe.
        op = lut.size_from_gmro(20.0, "nmos", L_um=1.0, Vds=0.6, Id=10e-6)
        assert op["gmro"] >= 20.0 * 0.99

    def test_unreachable_gmro_raises(self, lut):
        with pytest.raises(ValueError, match="exceeds max achievable"):
            lut.size_from_gmro(1000.0, "nmos", L_um=1.0, Vds=0.6, Id=10e-6)


class TestOperatingRange:
    _EXPECTED_KEYS = {
        "gmid_min", "gmid_max",
        "id_density_min", "id_density_max",
        "L_min_um", "L_max_um",
        "vgs_range", "vds_range",
    }

    def test_has_eight_canonical_keys(self, lut):
        rng = lut.operating_range("nmos")
        assert set(rng.keys()) == self._EXPECTED_KEYS

    def test_ranges_are_ordered(self, lut):
        rng = lut.operating_range("nmos")
        assert rng["gmid_min"] < rng["gmid_max"]
        assert rng["L_min_um"] < rng["L_max_um"]
        assert rng["vgs_range"][0] < rng["vgs_range"][1]
        assert rng["vds_range"][0] < rng["vds_range"][1]
        # IHP SG13G2 LV: Vgs max = 1.5 V per sweep config.
        assert math.isclose(rng["vgs_range"][1], 1.5, rel_tol=1e-9)
