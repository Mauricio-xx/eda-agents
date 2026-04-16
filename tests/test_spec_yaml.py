"""Tests for the BlockSpec / SpecTarget Pydantic v2 models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eda_agents.specs import SpecTarget, load_spec, load_spec_from_string


SPEC_YAML = """\
block: miller_ota
process: ihp_sg13g2
supply:
  vdd: 1.2
  vss: 0.0
specs:
  dc_gain:      {min: 60, unit: dB}
  gbw:          {min: 10e6, unit: Hz}
  phase_margin: {min: 60, unit: deg}
  power:        {max: 1.0, unit: mW}
corners: [TT_27, FF_m40, SS_125]
"""


def test_load_from_string_roundtrip():
    spec = load_spec_from_string(SPEC_YAML)
    assert spec.block == "miller_ota"
    assert spec.process == "ihp_sg13g2"
    assert spec.supply.vdd == 1.2
    assert spec.supply.vss == 0.0
    assert set(spec.targets) == {"dc_gain", "gbw", "phase_margin", "power"}
    assert spec.corners == ["TT_27", "FF_m40", "SS_125"]


def test_load_from_path(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(SPEC_YAML)
    spec = load_spec(p)
    assert spec.block == "miller_ota"


def test_target_min_check_pass():
    t = SpecTarget(min=60.0, unit="dB")
    passed, margin = t.check(65.0)
    assert passed is True
    assert margin == pytest.approx(5.0)


def test_target_max_check_fail():
    t = SpecTarget(max=1.0, unit="mW")
    passed, margin = t.check(1.5)
    assert passed is False
    assert margin == pytest.approx(-0.5)


def test_target_requires_bound():
    with pytest.raises(ValidationError):
        SpecTarget(unit="dB")


def test_target_min_greater_than_max_rejected():
    with pytest.raises(ValidationError):
        SpecTarget(min=10.0, max=5.0, unit="x")


def test_block_spec_requires_targets():
    bad = SPEC_YAML.replace(
        "specs:\n  dc_gain:      {min: 60, unit: dB}\n"
        "  gbw:          {min: 10e6, unit: Hz}\n"
        "  phase_margin: {min: 60, unit: deg}\n"
        "  power:        {max: 1.0, unit: mW}\n",
        "specs: {}\n",
    )
    with pytest.raises(ValidationError):
        load_spec_from_string(bad)


def test_supply_rejects_zero_vdd():
    with pytest.raises(ValidationError):
        load_spec_from_string(SPEC_YAML.replace("vdd: 1.2", "vdd: 0"))


def test_min_and_max_targets_partition():
    spec = load_spec_from_string(SPEC_YAML)
    mins = spec.min_targets()
    maxs = spec.max_targets()
    assert set(mins) == {"dc_gain", "gbw", "phase_margin"}
    assert set(maxs) == {"power"}
