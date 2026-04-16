"""Tests for the ``relevant_skills()`` hook added in S10b.

Ensures:
- Default implementation on the three ABCs returns an empty list.
- Every name declared by concrete topologies / designs resolves to a
  registered skill (blocks drift between declaration and registry).
- ``get_topology_by_name`` / ``list_topology_names`` behave correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign
from eda_agents.core.designs.generic import GenericDesign
from eda_agents.core.designs.systolic_mac_dft import SystolicMacDftDesign
from eda_agents.core.digital_design import DigitalDesign
from eda_agents.core.topology import CircuitTopology
from eda_agents.skills import get_skill
from eda_agents.topologies import get_topology_by_name, list_topology_names


# --------------------------------------------------------------------- #
# Default implementation on ABCs
# --------------------------------------------------------------------- #


class _BareCircuitTopology(CircuitTopology):
    """Minimal concrete CircuitTopology to exercise the default hook."""

    def topology_name(self) -> str:
        return "bare"

    def design_space(self):
        return {}

    def params_to_sizing(self, params):
        return {}

    def generate_netlist(self, sizing, work_dir):
        return Path(work_dir)

    def compute_fom(self, spice_result, sizing):
        return 0.0

    def check_validity(self, spice_result, sizing=None):
        return (False, [])

    def prompt_description(self) -> str:
        return ""

    def design_vars_description(self) -> str:
        return ""

    def specs_description(self) -> str:
        return ""

    def fom_description(self) -> str:
        return ""

    def reference_description(self) -> str:
        return ""


class _BareDigitalDesign(DigitalDesign):
    """Minimal concrete DigitalDesign to exercise the default hook."""

    def project_name(self) -> str:
        return "bare"

    def specification(self) -> str:
        return ""

    def design_space(self):
        return {}

    def flow_config_overrides(self):
        return {}

    def project_dir(self) -> Path:
        return Path(".")

    def librelane_config(self) -> Path:
        return Path(".")

    def compute_fom(self, metrics) -> float:
        return 0.0

    def check_validity(self, metrics):
        return (False, [])

    def prompt_description(self) -> str:
        return ""

    def design_vars_description(self) -> str:
        return ""

    def specs_description(self) -> str:
        return ""

    def fom_description(self) -> str:
        return ""

    def reference_description(self) -> str:
        return ""


def test_default_relevant_skills_empty_circuit_topology():
    assert _BareCircuitTopology().relevant_skills() == []


def test_default_relevant_skills_empty_digital_design():
    assert _BareDigitalDesign().relevant_skills() == []


# --------------------------------------------------------------------- #
# Declaration vs. registry drift guard
# --------------------------------------------------------------------- #


def _entry_name(entry):
    return entry if isinstance(entry, str) else entry[0]


@pytest.mark.parametrize("topology_name", list_topology_names())
def test_every_declared_topology_skill_resolves(topology_name):
    topo = get_topology_by_name(topology_name)
    for entry in topo.relevant_skills():
        # get_skill raises KeyError if the name is not registered.
        get_skill(_entry_name(entry))


def test_every_declared_digital_design_skill_resolves(tmp_path: Path):
    # Digital designs don't share a by-name registry yet; instantiate
    # each concrete class directly. GenericDesign needs a config path.
    cfg = tmp_path / "config.yaml"
    cfg.write_text("DESIGN_NAME: generic_test\n")

    designs: list[DigitalDesign] = [
        FazyRvHachureDesign(),
        SystolicMacDftDesign(),
        GenericDesign(cfg),
    ]
    for design in designs:
        for entry in design.relevant_skills():
            get_skill(_entry_name(entry))


# --------------------------------------------------------------------- #
# Topology-by-name resolver
# --------------------------------------------------------------------- #


def test_topology_by_name_resolver_happy():
    topo = get_topology_by_name("miller_ota")
    assert topo.topology_name() == "miller_ota"


def test_topology_by_name_resolver_unknown():
    with pytest.raises(KeyError, match="Unknown topology"):
        get_topology_by_name("does_not_exist")


def test_list_topology_names_includes_canonical():
    names = list_topology_names()
    assert "miller_ota" in names
    assert "sar_adc_11bit" in names
    assert "gf180_ota" in names
    # Deprecation shims are intentionally not listed.
    assert "sar_adc_8bit" not in names
