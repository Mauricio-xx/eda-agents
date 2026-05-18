"""Tests for the Ron/gm sizing lookup.

The class derives Ron analytically from the same PSP103 LUT that powers
``GmIdLookup``, so the tests hit the real IHP SG13G2 NFET/PFET .npz files
shipped via ``ihp-gmid-kit``. They skip when the LUT is not on disk.

Checks are formulaic (arithmetic identities and qualitative
relationships) rather than fixed magic numbers, so they survive LUT
regenerations as long as the device physics stays roughly intact.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eda_agents.core.pdk import get_pdk
from eda_agents.core.ron_gm_lookup import RonGmLookup


def _resolve_ihp_lut_path() -> Path:
    """Resolve the IHP LUT path with the same fallback chain
    ``GmIdLookup`` itself uses.

    Order:
      1. ``EDA_AGENTS_IHP_LUT_DIR`` env var.
      2. ``PdkConfig.lut_dir_default`` (rarely set for IHP).
    Returns the resolved directory path; the ``.npz`` existence check
    happens at the call site so the skip reason is meaningful.
    """
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
def lut() -> RonGmLookup:
    return RonGmLookup(pdk="ihp_sg13g2")


class TestPointLookup:
    def test_id_per_w_positive_in_strong_inversion(self, lut):
        # Above Vth, Id/W must be strictly positive for both polarities.
        p_n = lut._point("nmos", L_um=1.0, Vgs=1.0, Vds=0.6)
        p_p = lut._point("pmos", L_um=1.0, Vgs=1.0, Vds=0.6)
        assert p_n["id_per_w_Apm"] > 0
        assert p_p["id_per_w_Apm"] > 0

    def test_gm_per_w_positive_in_strong_inversion(self, lut):
        p_n = lut._point("nmos", L_um=1.0, Vgs=0.7, Vds=0.6)
        p_p = lut._point("pmos", L_um=1.0, Vgs=0.7, Vds=0.6)
        assert p_n["gm_per_w_Spm"] > 0
        assert p_p["gm_per_w_Spm"] > 0

    def test_pmos_sign_convention(self, lut):
        # Callers pass positive VSG / VSD magnitudes for PMOS; the
        # returned ``vgs_V`` / ``vds_V`` must echo those magnitudes
        # (not the LUT's stored negative values).
        p = lut._point("pmos", L_um=0.5, Vgs=0.8, Vds=0.3)
        assert p["vgs_V"] == pytest.approx(0.8, abs=0.05)
        assert p["vds_V"] == pytest.approx(0.3, abs=0.06)


class TestRonScaling:
    def test_ron_scales_inversely_with_w(self, lut):
        # Ron = Vds / Id at fixed Vgs. Id scales with W, so Ron must
        # halve when W doubles.
        op_a = lut.ron_gm("nmos", L_um=1.0, W_um=1.0,
                          Vgs_bias=0.5, Vds_on=0.05, Vds_bias=0.6)
        op_b = lut.ron_gm("nmos", L_um=1.0, W_um=2.0,
                          Vgs_bias=0.5, Vds_on=0.05, Vds_bias=0.6)
        assert op_b["Ron_ohm"] == pytest.approx(op_a["Ron_ohm"] / 2.0, rel=1e-6)

    def test_gm_scales_with_w(self, lut):
        op_a = lut.ron_gm("nmos", L_um=1.0, W_um=1.0,
                          Vgs_bias=0.5, Vds_on=0.05, Vds_bias=0.6)
        op_b = lut.ron_gm("nmos", L_um=1.0, W_um=2.0,
                          Vgs_bias=0.5, Vds_on=0.05, Vds_bias=0.6)
        assert op_b["gm_S"] == pytest.approx(op_a["gm_S"] * 2.0, rel=1e-6)

    def test_ron_gm_scales_inverse_w_squared(self, lut):
        # The Ron/gm metric scales as 1/W^2 across width changes at
        # fixed operating point. Within numerical noise.
        op_a = lut.ron_gm("nmos", L_um=1.0, W_um=1.0,
                          Vgs_bias=0.5, Vds_on=0.05, Vds_bias=0.6)
        op_b = lut.ron_gm("nmos", L_um=1.0, W_um=2.0,
                          Vgs_bias=0.5, Vds_on=0.05, Vds_bias=0.6)
        # (Ron/gm)_b ≈ (Ron/gm)_a / 4
        assert op_b["Ron_gm"] == pytest.approx(op_a["Ron_gm"] / 4.0, rel=1e-5)


class TestSizeFromRonGm:
    def test_returns_canonical_dict_shape(self, lut):
        out = lut.size_from_ron_gm(
            ron_gm_target=5e7, mos_type="nmos",
            L_um=3.0, Ibias_uA=5.0,
        ).as_sizing_dict()
        # gm/ID schema parity (essential keys).
        for key in (
            "W_um", "L_um", "Id_uA", "gm_uS", "gds_uS",
            "ft_Hz", "vgs_V", "vds_V", "vbs_V", "gmid",
            "gmro", "vth_V", "mos_type",
        ):
            assert key in out
        # Ron-specific extensions.
        for key in (
            "Ron_ohm", "Ron_gm", "Ipeak_uA", "Vds_on_V",
            "Vgs_on_V", "deadzone_bias_V", "deadzone_threshold",
        ):
            assert key in out

    def test_id_matches_request(self, lut):
        out = lut.size_from_ron_gm(
            ron_gm_target=5e7, mos_type="nmos",
            L_um=3.0, Ibias_uA=5.0,
        )
        # The Id at the bias point must equal the requested Ibias to
        # within numerical noise; this is the algorithm's invariant.
        assert out.Id_uA == pytest.approx(5.0, rel=5e-2)

    def test_gmid_max_excludes_subthreshold(self, lut):
        # With gmid_max=20, the operating point must have gm/ID <= 20.
        # Without the constraint the search dives into subthreshold
        # where gm/ID can climb to ~30.
        out = lut.size_from_ron_gm(
            ron_gm_target=5e7, mos_type="nmos",
            L_um=3.0, Ibias_uA=5.0,
            gmid_max=20.0,
        )
        assert out.gmid <= 20.0 + 1e-3

    def test_pmos_symmetric_api(self, lut):
        # PMOS sized with the same positive-magnitude API succeeds
        # and produces a positive W.
        out = lut.size_from_ron_gm(
            ron_gm_target=5e7, mos_type="pmos",
            L_um=0.5, Ibias_uA=5.0,
        )
        assert out.W_um > 0
        assert out.gm_uS > 0
        assert out.Ron_ohm > 0
        # gm/ID stays in inversion.
        assert out.gmid <= 20.0 + 1e-3

    def test_ron_gm_meets_target_when_reachable(self, lut):
        # At Ibias=5 uA, ron_gm_target=5e7 is reachable on the IHP LUT
        # for L in the few-um range; the algorithm picks the meeting
        # candidate.
        out = lut.size_from_ron_gm(
            ron_gm_target=5e7, mos_type="nmos",
            L_um=3.0, Ibias_uA=5.0,
        )
        assert out.Ron_gm <= 5e7 * 1.1  # 10 % slack on the discrete LUT

    def test_unreachable_target_returns_closest_miss_with_warning(self, lut, caplog):
        # When Ron/gm is unreachable, the helper returns the closest
        # achievable Ron/gm rather than raising, but it must emit a
        # warning so the caller can detect the miss programmatically.
        import logging
        caplog.set_level(logging.WARNING, logger="eda_agents.core.ron_gm_lookup")
        out = lut.size_from_ron_gm(
            ron_gm_target=1e3,  # absurdly tight (sub-MΩ Ron/gm).
            mos_type="nmos",
            L_um=3.0, Ibias_uA=5.0,
        )
        assert out.Ron_gm > 1e3  # closest miss, not the target
        assert any(
            "not achievable" in rec.message
            for rec in caplog.records
        ), "warning was not emitted on unreachable target"

    def test_no_valid_vbias_raises(self, lut):
        # Vbias_min above the LUT axis maximum yields no candidates --
        # the empty-candidate-list path should raise.
        with pytest.raises(ValueError, match=r"Vbias range"):
            lut.size_from_ron_gm(
                ron_gm_target=5e7, mos_type="nmos",
                L_um=3.0, Ibias_uA=5.0,
                Vbias_min=5.0,  # above any LUT Vgs
                Vbias_max=10.0,
            )


class TestDeadzone:
    def test_returns_achievable_flag(self, lut):
        out = lut.deadzone_bias(
            "nmos", L_um=3.0, W_um=1.0,
            ron_gm_threshold=1e8,
            Vds_on=0.05, Vds_bias=0.6,
        )
        assert "Vbias_V" in out
        assert "Ron_gm" in out
        assert "achievable" in out
        if out["achievable"]:
            assert 0.0 <= out["Vbias_V"] <= 1.5

    def test_lower_threshold_means_higher_vbias(self, lut):
        # A tighter Ron/gm threshold (smaller value) requires more
        # current density, hence higher Vbias. Monotone relation.
        out_loose = lut.deadzone_bias(
            "nmos", L_um=3.0, W_um=10.0,
            ron_gm_threshold=1e9,
            Vds_on=0.05, Vds_bias=0.6,
        )
        out_tight = lut.deadzone_bias(
            "nmos", L_um=3.0, W_um=10.0,
            ron_gm_threshold=1e7,
            Vds_on=0.05, Vds_bias=0.6,
        )
        if out_loose["achievable"] and out_tight["achievable"]:
            assert out_tight["Vbias_V"] >= out_loose["Vbias_V"]
