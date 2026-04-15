"""Unit tests for the 8-bit behavioural SAR topology (S7).

Spice / XSPICE / Verilator are NOT required: the tests stub the
compiled ``.cm`` and ``.so`` paths so they exercise the Python API
(design space, netlist generation, FoM / validity) without running any
external tool.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from eda_agents.core.spice_runner import SpiceResult
from eda_agents.topologies.sar_adc_8bit_behavioral import (
    BehavioralComparatorKit,
    SARADC8BitBehavioralTopology,
    behavioral_comparator_section,
)


@pytest.fixture
def _fake_kit(tmp_path) -> BehavioralComparatorKit:
    cm = tmp_path / "behavioral_comparator.cm"
    cm.write_bytes(b"stub")
    return BehavioralComparatorKit(
        cm_path=cm,
        model_card=".model ea_cmp ea_comparator_ideal(vout_high=1.2 vout_low=0.0 hysteresis_v=0.001)",
        instance_line="ACMP cmp_p cmp_n cmp_out ea_cmp",
        model_ref="ea_cmp",
    )


@pytest.fixture
def _topo_with_stubs(tmp_path, _fake_kit, monkeypatch) -> SARADC8BitBehavioralTopology:
    topo = SARADC8BitBehavioralTopology()
    # Short-circuit the XSPICE kit build and the Verilator .so compile.
    monkeypatch.setattr(topo, "_ensure_kit", lambda work_dir: _fake_kit)
    so_stub = tmp_path / "sar_logic.so"
    so_stub.write_bytes(b"stub")
    monkeypatch.setattr(topo._parent, "_ensure_so", lambda work_dir: so_stub)
    topo._kit_cache = _fake_kit
    topo._last_codemodel_path = _fake_kit.cm_path
    return topo


def test_behavioral_section_has_two_instances():
    sec = behavioral_comparator_section(
        vout_high=1.2, vout_low=0.0, hysteresis_v=1e-3
    )
    joined = "\n".join(sec)
    assert "ACMP_p cdac_top_p cdac_top_n comp_outp" in joined
    assert "ACMP_n cdac_top_n cdac_top_p comp_outn" in joined
    assert "ea_comparator_ideal" in joined
    assert "hysteresis_v=0.001" in joined


def test_behavioral_section_model_ref_override():
    sec = behavioral_comparator_section(
        vout_high=1.0, vout_low=0.0, hysteresis_v=5e-3, model_ref="custom_cmp"
    )
    joined = "\n".join(sec)
    assert "ACMP_p cdac_top_p cdac_top_n comp_outp custom_cmp" in joined
    assert ".model custom_cmp ea_comparator_ideal" in joined


def test_topology_api_contract():
    topo = SARADC8BitBehavioralTopology()
    assert topo.topology_name() == "sar_adc_8bit_behavioral"
    assert topo.block_names() == ["comparator", "cdac"]
    # Block topologies are None (no standalone CircuitTopology per block).
    assert topo.block_topology("comparator") is None
    assert topo.block_topology("cdac") is None


def test_design_space_partitions_cleanly():
    topo = SARADC8BitBehavioralTopology()
    space = topo.system_design_space()
    assert set(space) == {
        "cmp_vout_high",
        "cmp_vout_low",
        "cmp_hysteresis_v",
        "cdac_C_unit_fF",
    }
    assert set(topo.block_design_space("comparator")) == {
        "cmp_vout_high",
        "cmp_vout_low",
        "cmp_hysteresis_v",
    }
    assert set(topo.block_design_space("cdac")) == {"cdac_C_unit_fF"}


def test_default_params_inside_space():
    topo = SARADC8BitBehavioralTopology()
    space = topo.system_design_space()
    defaults = topo.default_params()
    for name, val in defaults.items():
        lo, hi = space[name]
        assert lo <= val <= hi, (name, val, lo, hi)


def test_params_to_block_params_schema():
    topo = SARADC8BitBehavioralTopology()
    blocks = topo.params_to_block_params(topo.default_params())
    assert set(blocks["comparator"]) == {"vout_high", "vout_low", "hysteresis_v"}
    assert blocks["cdac"]["C_unit_fF"] == pytest.approx(200.0)


def test_generate_netlist_uses_behavioural_section(_topo_with_stubs, tmp_path):
    topo = _topo_with_stubs
    cir = topo.generate_system_netlist(topo.default_params(), tmp_path / "deck")
    text = cir.read_text()
    # The StrongARM section header must no longer appear.
    assert "STRONGARM DYNAMIC COMPARATOR" not in text
    assert "ACMP_p cdac_top_p cdac_top_n comp_outp" in text
    assert "ea_comparator_ideal" in text
    # Reused infrastructure still present.
    assert "d_cosim" in text
    assert "XC_cdac_p_0" in text
    assert "XC_cdac_n_0" in text


def test_compute_system_fom_zero_when_enob_missing():
    topo = SARADC8BitBehavioralTopology()
    result = SpiceResult(success=True, measurements={})
    fom = topo.compute_system_fom(result, topo.default_params())
    assert fom == 0.0


def test_compute_system_fom_positive_with_good_result():
    topo = SARADC8BitBehavioralTopology()
    result = SpiceResult(
        success=True,
        measurements={"enob": 7.5, "avg_idd": -30e-6, "sndr_dB": 45.0},
    )
    fom = topo.compute_system_fom(result, topo.default_params())
    assert fom > 0.0


def test_check_validity_flags_low_enob():
    topo = SARADC8BitBehavioralTopology()
    result = SpiceResult(
        success=True,
        measurements={"enob": 3.0, "sndr_dB": 20.0, "avg_idd": -1e-4},
    )
    ok, violations = topo.check_system_validity(result, topo.default_params())
    assert not ok
    assert any("ENOB" in v for v in violations)


def test_check_validity_accepts_failed_sim_gracefully():
    topo = SARADC8BitBehavioralTopology()
    bad = SpiceResult(success=False, error="boom", measurements={})
    ok, violations = topo.check_system_validity(bad, topo.default_params())
    assert not ok
    assert violations == ["simulation failed"]


def test_ensure_kit_raises_when_toolchain_missing(tmp_path):
    topo = SARADC8BitBehavioralTopology()
    with patch(
        "eda_agents.topologies.sar_adc_8bit_behavioral."
        "build_behavioral_comparator_kit",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="XSPICE toolchain unavailable"):
            topo._ensure_kit(tmp_path)


def test_extract_enob_delegates(tmp_path):
    topo = SARADC8BitBehavioralTopology()
    # Missing bit_data.txt -> well-formed error dict.
    out = topo.extract_enob(tmp_path)
    assert out["enob"] == 0.0
    assert "error" in out
