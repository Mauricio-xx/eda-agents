"""Tests for the GF180 + IIC-OSIC-TOOLS Docker flow skills."""

from __future__ import annotations

from eda_agents.skills import get_skill, list_skills


class TestDockerSkillsRegistered:
    def test_rtl2gds_registered(self):
        names = {s.name for s in list_skills()}
        assert "flow.rtl2gds_gf180_docker" in names

    def test_analog_signoff_registered(self):
        names = {s.name for s in list_skills()}
        assert "flow.analog_signoff_gf180_docker" in names


class TestRtl2gdsSkillBody:
    def test_renders_non_empty(self):
        out = get_skill("flow.rtl2gds_gf180_docker").render()
        assert isinstance(out, str)
        assert len(out) > 1000

    def test_mentions_image_and_pdk_coords(self):
        out = get_skill("flow.rtl2gds_gf180_docker").render()
        assert "hpretl/iic-osic-tools" in out
        assert "gf180mcuD" in out
        assert "sak-pdk-script.sh" in out

    def test_mentions_librelane_invocation(self):
        out = get_skill("flow.rtl2gds_gf180_docker").render()
        assert "make librelane" in out
        assert "SLOT=1x1" in out

    def test_bundle_spans_common_and_rtl2gds_parts(self):
        out = get_skill("flow.rtl2gds_gf180_docker").render()
        # From common.md
        assert "Canonical `docker run`" in out
        # From rtl2gds.md
        assert "wafer-space/gf180mcu-project-template" in out


class TestAnalogSignoffSkillBody:
    def test_renders_non_empty(self):
        out = get_skill("flow.analog_signoff_gf180_docker").render()
        assert isinstance(out, str)
        assert len(out) > 1000

    def test_mentions_both_lvs_toolchains(self):
        out = get_skill("flow.analog_signoff_gf180_docker").render()
        assert "Magic" in out
        assert "Netgen" in out
        assert "KLayout" in out

    def test_mentions_composition_with_drc_skills(self):
        out = get_skill("flow.analog_signoff_gf180_docker").render()
        assert "flow.drc_checker" in out
        assert "flow.drc_fixer" in out

    def test_bundle_spans_common_and_analog_parts(self):
        out = get_skill("flow.analog_signoff_gf180_docker").render()
        # From common.md
        assert "Canonical `docker run`" in out
        # From analog_signoff.md
        assert "Circuits match uniquely" in out
