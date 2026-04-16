"""Tests for the pre-sim structural gates."""

from __future__ import annotations

from eda_agents.checks.pre_sim import (
    Device,
    check_bias_source,
    check_bulk_connections,
    check_floating_nodes,
    check_mirror_ratio,
    check_testbench_pin_match,
    parse_subcircuit,
    run_all,
)

GOOD_NETLIST = """\
* miller-ota-style subcircuit, all gates green
.subckt good inp inn out vdd vss vbn
Mtail vtail vbp vdd vdd sg13_lv_pmos w=4u l=0.5u nf=4 m=1
M1 d1 inp vtail vdd sg13_lv_pmos w=2u l=0.35u nf=2 m=1
M2 d2 inn vtail vdd sg13_lv_pmos w=2u l=0.35u nf=2 m=1
M3 d1 d1 vss vss sg13_lv_nmos w=1u l=0.5u nf=1 m=1
M4 d2 d1 vss vss sg13_lv_nmos w=1u l=0.5u nf=1 m=1
M5 out d2 vss vss sg13_lv_nmos w=8u l=0.5u nf=8 m=1
Mload out vbp vdd vdd sg13_lv_pmos w=8u l=0.5u nf=8 m=1
Cm d2 out 1p
Mdio vbp vbp vdd vdd sg13_lv_pmos w=2u l=0.5u nf=2 m=1
Ibias vbp vss 50u
Mb vbn vbn vss vss sg13_lv_nmos w=1u l=0.5u nf=1 m=1
Iref vbn vss 20u
.ends good
"""


def test_parse_subcircuit_basic():
    sc = parse_subcircuit(GOOD_NETLIST)
    assert sc.name == "good"
    assert sc.ports == ("inp", "inn", "out", "vdd", "vss", "vbn")
    assert any(d.name == "M1" and d.kind == "pmos" for d in sc.devices)
    assert any(d.name == "M3" and d.kind == "nmos" for d in sc.devices)


def test_floating_nodes_pass_on_good_netlist():
    sc = parse_subcircuit(GOOD_NETLIST)
    res = check_floating_nodes(sc)
    assert res.passed, res.messages


def test_floating_nodes_flags_dangling_net():
    text = """\
.subckt bad in out vdd vss
M1 out in vss vss sg13_lv_nmos w=1u l=0.5u nf=1 m=1
* dangling 'orphan' net
M2 orphan vss vss vss sg13_lv_nmos w=1u l=0.5u nf=1 m=1
.ends bad
"""
    res = check_floating_nodes(parse_subcircuit(text))
    assert not res.passed
    assert any("orphan" in m for m in res.messages)


def test_bulk_connections_good():
    sc = parse_subcircuit(GOOD_NETLIST)
    assert check_bulk_connections(sc).passed


def test_bulk_connections_flag_pmos_to_vss():
    text = """\
.subckt bad inp vdd vss
M1 vdd inp vdd vss sg13_lv_pmos w=2u l=0.35u
.ends bad
"""
    res = check_bulk_connections(parse_subcircuit(text))
    assert not res.passed
    assert any("pmos" in m and "M1" in m for m in res.messages)


def test_mirror_ratio_pass_with_declaration():
    sc = parse_subcircuit(GOOD_NETLIST)
    # M3:M4 are both gate=d1 and W=1u nf=1. ratio 1:1.
    res = check_mirror_ratio(sc, declared_ratios={("M3", "M4"): 1.0})
    assert res.passed, res.messages


def test_mirror_ratio_warns_on_mismatch():
    sc = parse_subcircuit(GOOD_NETLIST)
    # Declaring 8:1 against a structurally 1:1 pair must flag.
    res = check_mirror_ratio(sc, declared_ratios={("M3", "M4"): 8.0})
    assert not res.passed
    assert res.severity == "warn"


def test_mirror_ratio_unknown_pair_flags():
    sc = parse_subcircuit(GOOD_NETLIST)
    res = check_mirror_ratio(sc, declared_ratios={("M3", "Mghost"): 1.0})
    assert not res.passed


def test_bias_source_good():
    sc = parse_subcircuit(GOOD_NETLIST)
    res = check_bias_source(sc)
    assert res.passed, res.messages


def test_bias_source_flags_floating_gate():
    text = """\
.subckt bad in out vdd vss
M1 out in vss vss sg13_lv_nmos w=1u l=0.5u
M2 out floatgate vss vss sg13_lv_nmos w=1u l=0.5u
.ends bad
"""
    res = check_bias_source(parse_subcircuit(text))
    assert not res.passed
    assert any("floatgate" in m for m in res.messages)


def test_testbench_pin_match_ok():
    definition = parse_subcircuit(GOOD_NETLIST)
    instance = Device(
        name="Xdut",
        kind="subckt",
        nodes=("inp_tb", "inn_tb", "out_tb", "vdd", "vss", "vbn_tb"),
        model="good",
    )
    res = check_testbench_pin_match(definition, instance)
    assert res.passed


def test_testbench_pin_match_arity_mismatch():
    definition = parse_subcircuit(GOOD_NETLIST)
    instance = Device(
        name="Xdut",
        kind="subckt",
        nodes=("a", "b", "c"),
        model="good",
    )
    res = check_testbench_pin_match(definition, instance)
    assert not res.passed
    assert any("3 terminals" in m for m in res.messages)


def test_testbench_pin_match_model_mismatch():
    definition = parse_subcircuit(GOOD_NETLIST)
    instance = Device(
        name="Xdut",
        kind="subckt",
        nodes=("a", "b", "c", "d", "e", "f"),
        model="not_good",
    )
    res = check_testbench_pin_match(definition, instance)
    assert not res.passed


def test_subcircuit_construct_rejects_invalid_kind():
    import pytest

    with pytest.raises(ValueError):
        Device(name="X1", kind="oops", nodes=())


def test_run_all_returns_four_results():
    sc = parse_subcircuit(GOOD_NETLIST)
    res = run_all(sc, declared_ratios={("M3", "M4"): 1.0})
    assert [r.name for r in res] == [
        "floating_nodes",
        "bulk_connections",
        "mirror_ratio",
        "bias_source",
    ]
    assert all(r.passed for r in res), [m for r in res for m in r.messages]


def test_subcircuit_supply_helpers():
    sc = parse_subcircuit(GOOD_NETLIST)
    assert sc.is_supply("vdd")
    assert sc.is_supply("vss")
    assert sc.is_supply("0")
    assert not sc.is_supply("vbp")
