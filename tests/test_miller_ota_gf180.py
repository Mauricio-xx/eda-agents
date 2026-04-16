"""Regression tests for the GF180 Miller OTA sizing fix (gap #1)."""

from __future__ import annotations

import pytest

from eda_agents.topologies.miller_ota import MillerOTADesigner
from eda_agents.topologies.process_params import (
    GF180MCU_PARAMS,
    IHP_SG13G2_PARAMS,
    PDK_TO_PROCESS_PARAMS,
    resolve_process_params,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_has_both_pdks():
    assert set(PDK_TO_PROCESS_PARAMS) == {"ihp_sg13g2", "gf180mcu"}


def test_gf180_process_params_bounds():
    p = GF180MCU_PARAMS
    assert p.Lmin == 280e-9
    assert p.Wmin == 220e-9
    assert p.VDD == 3.3
    assert p.tox > 5e-9  # 180nm has a thicker oxide than 130nm
    # IHP stays unchanged — this is the contract for gap #1.
    assert IHP_SG13G2_PARAMS.Wmin == 150e-9
    assert IHP_SG13G2_PARAMS.VDD == 1.2


def test_resolve_process_params_unknown_falls_back_to_ihp():
    assert resolve_process_params(None) is IHP_SG13G2_PARAMS
    assert resolve_process_params("totally-unknown-pdk") is IHP_SG13G2_PARAMS
    assert resolve_process_params("gf180mcu") is GF180MCU_PARAMS


# ---------------------------------------------------------------------------
# Designer behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def easy_design_params():
    return dict(
        gmid_input=12.0,
        gmid_load=10.0,
        L_input=1.0e-6,
        L_load=1.0e-6,
        Cc=1.0e-12,
    )


def test_ihp_designer_bit_identical_widths(easy_design_params):
    """The IHP numbers must not regress — the S9 baseline depends on them."""
    d = MillerOTADesigner(pdk="ihp_sg13g2")
    r = d.analytical_design(**easy_design_params)
    # Exact value from the pre-gap-closure pipeline.
    assert r.transistors["M1a"].W == pytest.approx(1.7059e-7, rel=1e-3)
    assert d.specs.VDD == 1.2
    # proc is the registry entry, not a fresh dataclass
    assert d.proc is IHP_SG13G2_PARAMS


def test_gf180_designer_clears_wmin_binner(easy_design_params):
    """Every sized transistor must satisfy GF180's Wmin=220nm binner floor.

    This is the regression that makes ``spec_miller_ota_gf180_easy``
    load in ngspice instead of failing with
    ``could not find a valid modelname``.
    """
    d = MillerOTADesigner(pdk="gf180mcu")
    r = d.analytical_design(**easy_design_params)
    assert d.proc is GF180MCU_PARAMS
    assert d.specs.VDD == 3.3
    for name, t in r.transistors.items():
        assert t.W >= GF180MCU_PARAMS.Wmin, (
            f"{name}: W={t.W*1e9:.2f}nm < Wmin={GF180MCU_PARAMS.Wmin*1e9:.0f}nm"
        )
        assert t.L >= GF180MCU_PARAMS.Lmin, (
            f"{name}: L={t.L*1e9:.2f}nm < Lmin={GF180MCU_PARAMS.Lmin*1e9:.0f}nm"
        )


def test_explicit_process_override_wins(easy_design_params):
    """Caller-passed ``process=`` beats the PDK-derived default."""
    custom = IHP_SG13G2_PARAMS  # pretend the caller really wants IHP params
    d = MillerOTADesigner(pdk="gf180mcu", process=custom)
    assert d.proc is custom
    r = d.analytical_design(**easy_design_params)
    # No Wmin clamping against GF180 because the caller said "use IHP"
    assert r.transistors["M1a"].W == pytest.approx(1.7059e-7, rel=1e-3)
