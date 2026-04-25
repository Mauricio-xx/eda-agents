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

    def test_macro_integration_registered(self):
        names = {s.name for s in list_skills()}
        assert "flow.macro_integration_gf180_docker" in names


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


class TestMacroIntegrationSkillBody:
    """The macro-integration skill encodes the 5 chip-flow pitfalls
    that cost real iteration time on the chipathon ex04 validation
    (commit de63a84). Every anchor below is a regression guard."""

    def test_renders_non_empty(self):
        out = get_skill("flow.macro_integration_gf180_docker").render()
        assert isinstance(out, str)
        assert len(out) > 5000

    def test_bundle_spans_common_and_macro_parts(self):
        out = get_skill("flow.macro_integration_gf180_docker").render()
        # From common.md
        assert "Canonical `docker run`" in out
        # From macro_integration.md
        assert "pre-hardened macros" in out

    def test_warns_about_when_to_compose(self):
        # Agent must know NOT to pull this skill for bare-block designs.
        out = get_skill("flow.macro_integration_gf180_docker").render()
        assert "When to compose this skill" in out
        assert "flow.rtl2gds_gf180_docker" in out

    def test_documents_save_views_layout(self):
        # PITFALL #2: --save-views-to writes directly under <dir>/, no final/.
        out = get_skill("flow.macro_integration_gf180_docker").render()
        assert "--save-views-to" in out
        assert "no `final/` wrapper" in out or "no `final/' wrapper" in out

    def test_documents_nine_gf180_corners(self):
        # PITFALL #1: silent multi-corner collapse if the lib map uses *.
        out = get_skill("flow.macro_integration_gf180_docker").render()
        for corner in (
            "nom_tt_025C_5v00", "nom_ss_125C_4v50", "nom_ff_n40C_5v50",
            "min_tt_025C_5v00", "min_ss_125C_4v50", "min_ff_n40C_5v50",
            "max_tt_025C_5v00", "max_ss_125C_4v50", "max_ff_n40C_5v50",
        ):
            assert corner in out, f"corner {corner!r} missing from skill body"

    def test_documents_pdn_macro_connections_string_format(self):
        # PITFALL #3: dict format is rejected outright by v3.0.2.
        out = get_skill("flow.macro_integration_gf180_docker").render()
        assert "PDN_MACRO_CONNECTIONS" in out
        # The exact failure string the agent will see when it gets it wrong.
        assert "Refusing to automatically convert" in out
        # The required canonical shape.
        assert "vdd_net" in out and "vss_net" in out
        assert "vdd_pin" in out and "vss_pin" in out

    def test_documents_macro_rtl_blackbox_rule(self):
        # PITFALL #4: macro RTL stays out of VERILOG_FILES.
        out = get_skill("flow.macro_integration_gf180_docker").render()
        assert "VERILOG_FILES" in out
        # Spell out the consequence.
        assert "stdcells" in out

    def test_documents_no_param_overrides_rule(self):
        # PITFALL #5: counter #(.WIDTH(8)) trips Verilator/yosys.
        out = get_skill("flow.macro_integration_gf180_docker").render()
        assert "Parameter not found" in out
        assert "PINNOTFOUND" in out

    def test_lists_all_five_pitfalls(self):
        # Numbered as 1..5 so callers can cite by number.
        out = get_skill("flow.macro_integration_gf180_docker").render()
        for n in (1, 2, 3, 4, 5):
            assert f"PITFALL #{n}" in out

    def test_documents_verification_beyond_metrics(self):
        # PITFALL #1 lets metrics.csv lie: must teach the agent the
        # 9-corner cross-check.
        out = get_skill("flow.macro_integration_gf180_docker").render()
        assert "Verification beyond" in out
        assert "9" in out and ("corner count" in out or "corner directories" in out
                               or "actual STA corner count" in out)

    def test_composes_with_cocotb_and_drc_skills(self):
        out = get_skill("flow.macro_integration_gf180_docker").render()
        assert "digital.cocotb_testbench" in out
        assert "flow.drc_checker" in out
        assert "flow.drc_fixer" in out
