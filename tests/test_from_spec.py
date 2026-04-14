"""Tests for the from-spec RTL-to-GDS path (Mode 3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from eda_agents.agents.librelane_config_templates import (
    GF180_CONFIG_TEMPLATE,
    GF180_DEFAULTS,
    IHP_SG13G2_CONFIG_TEMPLATE,
    IHP_SG13G2_DEFAULTS,
    get_config_template,
)
from eda_agents.agents.tool_defs import build_from_spec_prompt
from eda_agents.core.pdk import GF180MCU_D, IHP_SG13G2

PYTHON = sys.executable
EXAMPLE_09 = Path(__file__).resolve().parents[1] / "examples" / "09_rtl2gds_digital.py"


# Canonical per-PDK fixtures: (pdk_name, PdkConfig, template, defaults,
# expected_librelane_pdk, expected_rt_max_layer, expected_display_fragment)
_PDK_CASES = [
    pytest.param(
        "gf180mcu", GF180MCU_D, GF180_CONFIG_TEMPLATE, GF180_DEFAULTS,
        "gf180mcuD", "Metal4", "GF180MCU",
        id="gf180mcu",
    ),
    pytest.param(
        "ihp_sg13g2", IHP_SG13G2, IHP_SG13G2_CONFIG_TEMPLATE, IHP_SG13G2_DEFAULTS,
        "ihp-sg13g2", "TopMetal2", "IHP SG13G2",
        id="ihp_sg13g2",
    ),
]


class TestConfigTemplate:
    @pytest.mark.parametrize(
        "pdk_name,pdk_cfg,template,defaults,librelane_pdk,rt_max,display",
        _PDK_CASES,
    )
    def test_template_is_valid_yaml(
        self, pdk_name, pdk_cfg, template, defaults, librelane_pdk, rt_max, display,
    ):
        filled = template.format(
            design_name="test_counter",
            verilog_file="../src/counter.v",
            clock_port="clk",
            clock_period=25,
            die_width=300.0,
            die_height=300.0,
        )
        data = yaml.safe_load(filled)
        assert data["DESIGN_NAME"] == "test_counter"
        assert data["CLOCK_PERIOD"] == 25
        assert data["RT_MAX_LAYER"] == rt_max
        assert "VDD" in data["VDD_NETS"]
        assert "VSS" in data["GND_NETS"]

    @pytest.mark.parametrize(
        "pdk_name,pdk_cfg,template,defaults,librelane_pdk,rt_max,display",
        _PDK_CASES,
    )
    def test_get_config_template_matches(
        self, pdk_name, pdk_cfg, template, defaults, librelane_pdk, rt_max, display,
    ):
        tpl, defs = get_config_template(pdk_cfg)
        assert tpl is template
        assert defs is defaults

    @pytest.mark.parametrize(
        "pdk_name,pdk_cfg,template,defaults,librelane_pdk,rt_max,display",
        _PDK_CASES,
    )
    def test_defaults_are_reasonable(
        self, pdk_name, pdk_cfg, template, defaults, librelane_pdk, rt_max, display,
    ):
        assert defaults["clock_period"] >= 5
        assert defaults["die_width"] > 0
        assert defaults["clock_port"] == "clk"

    def test_gf180_boilerplate_preserved(self):
        filled = GF180_CONFIG_TEMPLATE.format(
            design_name="x", verilog_file="x.v",
            clock_port="clk", clock_period=25,
            die_width=100, die_height=100,
        )
        data = yaml.safe_load(filled)
        assert data["DIODE_ON_PORTS"] == "in"
        assert data["PDN_MULTILAYER"] is False
        assert data["PDN_VWIDTH"] == 5


class TestFromSpecPrompt:
    @pytest.mark.parametrize(
        "pdk_name,pdk_cfg,template,defaults,librelane_pdk,rt_max,display",
        _PDK_CASES,
    )
    def test_prompt_routes_pdk(
        self, pdk_name, pdk_cfg, template, defaults, librelane_pdk, rt_max, display,
    ):
        prompt = build_from_spec_prompt(
            spec="UART transmitter, 9600 baud",
            work_dir="/tmp/uart",
            pdk_root=f"/pdk/{pdk_name}",
            pdk_config=pdk_name,
        )
        assert "UART transmitter" in prompt
        assert f"PDK={librelane_pdk}" in prompt
        assert display in prompt

    def test_prompt_has_rtl_phase(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
            pdk_config="gf180mcu",
        )
        assert "Phase 1 - WRITE RTL" in prompt

    def test_prompt_has_lint_phase(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
            pdk_config="gf180mcu",
        )
        assert "verilator" in prompt

    def test_prompt_has_testbench_phase(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
            pdk_config="ihp_sg13g2",
        )
        # Testbench generation must be mandatory for both PDKs
        assert "Phase 2.5 - WRITE TESTBENCH AND SIMULATE" in prompt
        assert "iverilog" in prompt

    def test_prompt_has_config_template(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
            pdk_config="gf180mcu",
        )
        assert "DESIGN_NAME" in prompt
        assert "PDN_VWIDTH" in prompt  # GF180 still has PDN straps

    def test_prompt_has_flow_command(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
            pdk_config="gf180mcu",
        )
        assert "python3 -m librelane" in prompt

    def test_prompt_has_pdk_root(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/my/pdk",
            pdk_config="gf180mcu",
        )
        assert "PDK_ROOT=/my/pdk" in prompt

    def test_prompt_has_env_scrub_instruction(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
            pdk_config="ihp_sg13g2",
        )
        # The F5 rule about not inheriting PDK env vars must be present
        assert "CRITICAL ENVIRONMENT RULE" in prompt
        assert "Never rely on inherited" in prompt or "explicit PDK" in prompt

    def test_prompt_has_done_sentinel(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
            pdk_config="gf180mcu",
        )
        assert "DONE" in prompt

    def test_prompt_has_final_report(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
            pdk_config="gf180mcu",
        )
        assert "SIGNOFF CLEAN" in prompt


class TestFromSpecDryRun:
    @pytest.mark.parametrize("pdk", ["gf180mcu", "ihp_sg13g2"])
    def test_example09_spec_dry_run(self, pdk):
        result = subprocess.run(
            [PYTHON, str(EXAMPLE_09),
             "--dry-run", "--spec", "4-bit counter",
             "--pdk", pdk,
             "--pdk-root", "/tmp/fake_pdk"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "PASS" in result.stdout
        assert "From Spec" in result.stdout

    def test_spec_forces_cc_cli(self):
        result = subprocess.run(
            [PYTHON, str(EXAMPLE_09),
             "--dry-run", "--spec", "counter",
             "--pdk-root", "/tmp/fake",
             "--backend", "adk"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "cc_cli" in result.stdout
