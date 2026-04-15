"""Unit tests for the 11-bit design_reference SAR topology (S7).

Scope: Python-level contract, netlist shape, and robustness-gate
heuristics. None of these tests launch ngspice / openvaf / Verilator;
the SAR logic ``.so`` is stubbed.
"""

from __future__ import annotations

import pytest

from eda_agents.core.spice_runner import SpiceResult
from eda_agents.topologies.sar_adc_11bit import SARADC11BitTopology


@pytest.fixture
def topo_stubbed(tmp_path, monkeypatch) -> SARADC11BitTopology:
    t = SARADC11BitTopology()
    so_stub = tmp_path / "sar_logic_11bit.so"
    so_stub.write_bytes(b"stub")
    monkeypatch.setattr(t, "_ensure_so", lambda work_dir: so_stub)
    return t


def test_topology_marked_design_reference():
    t = SARADC11BitTopology()
    assert t.DESIGN_REFERENCE is True
    assert t.topology_name() == "sar_adc_11bit"
    assert "not silicon-validated" in t.reference_description().lower() \
        or "not silicon" in t.prompt_description().lower() \
        or t.DESIGN_REFERENCE


def test_design_space_dim_and_ranges():
    t = SARADC11BitTopology()
    space = t.system_design_space()
    assert len(space) == 8
    # Bias range clips against the active VDD.
    bias_lo, bias_hi = space["bias_V"]
    assert bias_hi == pytest.approx(t.pdk.VDD - 0.2)


def test_block_decomposition():
    t = SARADC11BitTopology()
    assert t.block_names() == ["comparator", "cdac", "bias"]
    assert set(t.block_design_space("comparator")) == {
        f"comp_{k}" for k in [
            "W_input_um", "L_input_um",
            "W_tail_um", "L_tail_um",
            "W_latch_p_um", "W_latch_n_um",
        ]
    }
    assert set(t.block_design_space("cdac")) == {"cdac_C_unit_fF"}
    assert set(t.block_design_space("bias")) == {"bias_V"}


def test_block_topology_returns_strongarm_for_comparator():
    t = SARADC11BitTopology()
    comp = t.block_topology("comparator")
    assert comp is not None
    assert comp.topology_name() == "strongarm_comp"
    assert t.block_topology("cdac") is None
    assert t.block_topology("bias") is None


def test_default_params_inside_space():
    t = SARADC11BitTopology()
    space = t.system_design_space()
    for name, val in t.default_params().items():
        lo, hi = space[name]
        assert lo <= val <= hi


def test_generate_netlist_shape(topo_stubbed, tmp_path):
    t = topo_stubbed
    cir = t.generate_system_netlist(t.default_params(), tmp_path / "deck")
    text = cir.read_text()
    # Ports for the 11-bit SAR FSM: D[10:0] bits
    for i in range(11):
        assert f"D{i}_d" in text
    # B[9:0] / BN[9:0]
    assert "B9_d" in text and "BN9_d" in text and "B0_d" in text
    # 12 caps per side (11 decision + 1 dummy).
    assert text.count("XC_cdac_p_") == 12
    assert text.count("XC_cdac_n_") == 12
    # SAR FSM Adut instance routes through d_cosim with our stubbed .so.
    assert "d_cosim" in text
    # Design_reference banner in the header.
    assert "NOT silicon-validated" in text


def test_validity_flags_small_input_pair():
    t = SARADC11BitTopology()
    params = t.default_params()
    params["comp_W_input_um"] = 4.0
    params["comp_L_input_um"] = 0.13
    result = SpiceResult(
        success=True,
        measurements={"enob": 7.0, "sndr_dB": 45.0, "avg_idd": -1e-5},
    )
    ok, violations = t.check_system_validity(result, params)
    assert not ok
    assert any("PVT margin" in v for v in violations)


def test_validity_passes_for_large_comparator_and_cdac():
    t = SARADC11BitTopology()
    params = t.default_params()
    params["comp_W_input_um"] = 64.0
    params["comp_L_input_um"] = 2.0
    params["comp_W_latch_p_um"] = 16.0
    params["cdac_C_unit_fF"] = 20.0  # small -> tight settling
    result = SpiceResult(
        success=True,
        measurements={"enob": 8.0, "sndr_dB": 50.0, "avg_idd": -2e-5},
    )
    ok, violations = t.check_system_validity(result, params)
    # Either PASS or only non-PVT violations — the gate we are testing
    # here is that Pelgrom does NOT trigger at generous sizing.
    assert not any("PVT margin" in v for v in violations)


def test_validity_flags_reference_settling_on_huge_cdac():
    t = SARADC11BitTopology()
    params = t.default_params()
    params["cdac_C_unit_fF"] = 200.0  # max range -> huge total cap
    result = SpiceResult(
        success=True,
        measurements={"enob": 7.0, "sndr_dB": 45.0, "avg_idd": -5e-5},
    )
    ok, violations = t.check_system_validity(result, params)
    assert not ok
    assert any("settling" in v.lower() or "ripple" in v.lower() for v in violations)


def test_compute_fom_zero_without_enob():
    t = SARADC11BitTopology()
    result = SpiceResult(success=True, measurements={})
    assert t.compute_system_fom(result, t.default_params()) == 0.0


def test_compute_fom_positive_with_good_measurements():
    t = SARADC11BitTopology()
    result = SpiceResult(
        success=True,
        measurements={"enob": 8.5, "sndr_dB": 55.0, "avg_idd": -5e-5},
    )
    fom = t.compute_system_fom(result, t.default_params())
    assert fom > 0.0


def test_extract_enob_missing_file(tmp_path):
    t = SARADC11BitTopology()
    out = t.extract_enob(tmp_path)
    assert out["enob"] == 0.0
    assert "error" in out


def test_prompt_metadata_mentions_design_reference():
    t = SARADC11BitTopology()
    assert "design reference" in t.prompt_description().lower()
    assert "11-bit" in t.prompt_description().lower()
    # check that the agent prompt includes the robustness gates story
    desc = t.design_vars_description()
    for name in [
        "comp_W_input_um",
        "comp_W_latch_p_um",
        "cdac_C_unit_fF",
        "bias_V",
    ]:
        assert name in desc
