"""Validate PdkConfig digital-flow fields for all registered PDKs."""

from __future__ import annotations

import pytest

from eda_agents.agents.librelane_config_templates import get_config_template
from eda_agents.core.pdk import GF180MCU_D, IHP_SG13G2, list_pdks, resolve_pdk


@pytest.mark.parametrize("pdk", [GF180MCU_D, IHP_SG13G2])
class TestDigitalFields:
    def test_librelane_pdk_name_is_nonempty(self, pdk):
        assert pdk.librelane_pdk_name
        assert " " not in pdk.librelane_pdk_name

    def test_stdcell_library_is_nonempty(self, pdk):
        assert pdk.stdcell_library
        assert pdk.stdcell_library.startswith(("sg13g2", "gf180mcu"))

    def test_librelane_flow_is_known(self, pdk):
        assert pdk.librelane_flow in ("Classic", "Chip")

    def test_default_clock_period_positive(self, pdk):
        assert pdk.default_clock_period_ns > 0

    def test_default_die_positive(self, pdk):
        w, h = pdk.default_die_um
        assert w > 0 and h > 0

    def test_default_density_in_range(self, pdk):
        assert 10 <= pdk.default_density_pct <= 90

    def test_rt_max_layer_set(self, pdk):
        assert pdk.rt_max_layer

    def test_template_selector_resolves(self, pdk):
        template, defaults = get_config_template(pdk)
        assert "DESIGN_NAME" in template
        assert defaults["clock_port"] == "clk"


class TestPdkValues:
    def test_gf180_values(self):
        assert GF180MCU_D.librelane_pdk_name == "gf180mcuD"
        assert GF180MCU_D.stdcell_library == "gf180mcu_fd_sc_mcu7t5v0"
        assert GF180MCU_D.rt_max_layer == "Metal4"

    def test_ihp_values(self):
        assert IHP_SG13G2.librelane_pdk_name == "ihp-sg13g2"
        assert IHP_SG13G2.stdcell_library == "sg13g2_stdcell"
        assert IHP_SG13G2.rt_max_layer == "TopMetal2"

    def test_resolve_by_name(self):
        assert resolve_pdk("gf180mcu") is GF180MCU_D
        assert resolve_pdk("ihp_sg13g2") is IHP_SG13G2

    def test_both_pdks_registered(self):
        names = list_pdks()
        assert "gf180mcu" in names
        assert "ihp_sg13g2" in names
