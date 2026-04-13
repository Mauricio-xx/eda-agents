"""Tests for the from-spec RTL-to-GDS path (Mode 3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from eda_agents.agents.gf180_config_template import (
    GF180_CONFIG_TEMPLATE,
    GF180_DEFAULTS,
)
from eda_agents.agents.tool_defs import build_from_spec_prompt

PYTHON = sys.executable
EXAMPLE_09 = Path(__file__).resolve().parents[1] / "examples" / "09_rtl2gds_gf180.py"


class TestConfigTemplate:
    def test_template_is_valid_yaml(self):
        filled = GF180_CONFIG_TEMPLATE.format(
            design_name="test_counter",
            verilog_file="../src/counter.v",
            clock_port="clk",
            clock_period=50,
            die_width=300.0,
            die_height=300.0,
        )
        data = yaml.safe_load(filled)
        assert data["DESIGN_NAME"] == "test_counter"
        assert data["CLOCK_PERIOD"] == 50
        assert data["RT_MAX_LAYER"] == "Metal4"

    def test_template_has_gf180_boilerplate(self):
        filled = GF180_CONFIG_TEMPLATE.format(
            design_name="x", verilog_file="x.v",
            clock_port="clk", clock_period=25,
            die_width=100, die_height=100,
        )
        data = yaml.safe_load(filled)
        assert data["DIODE_ON_PORTS"] == "in"
        assert data["PDN_MULTILAYER"] is False
        assert data["PDN_VWIDTH"] == 5
        assert "VDD" in data["VDD_NETS"]

    def test_defaults_are_reasonable(self):
        assert GF180_DEFAULTS["clock_period"] >= 10
        assert GF180_DEFAULTS["die_width"] > 0
        assert GF180_DEFAULTS["clock_port"] == "clk"


class TestFromSpecPrompt:
    def test_prompt_contains_spec(self):
        prompt = build_from_spec_prompt(
            spec="UART transmitter, 9600 baud",
            work_dir="/tmp/uart",
            pdk_root="/pdk/gf180",
        )
        assert "UART transmitter" in prompt

    def test_prompt_has_rtl_phase(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
        )
        assert "Phase 1 - WRITE RTL" in prompt

    def test_prompt_has_lint_phase(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
        )
        assert "verilator" in prompt

    def test_prompt_has_config_template(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
        )
        assert "DESIGN_NAME" in prompt
        assert "PDN_VWIDTH" in prompt

    def test_prompt_has_flow_command(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
        )
        assert "python3 -m librelane" in prompt

    def test_prompt_has_pdk_root(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/my/pdk",
        )
        assert "PDK_ROOT=/my/pdk" in prompt

    def test_prompt_has_done_sentinel(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
        )
        assert "DONE" in prompt

    def test_prompt_has_final_report(self):
        prompt = build_from_spec_prompt(
            spec="counter", work_dir="/tmp/x", pdk_root="/pdk",
        )
        assert "SIGNOFF CLEAN" in prompt


class TestFromSpecDryRun:
    def test_example09_spec_dry_run(self):
        result = subprocess.run(
            [PYTHON, str(EXAMPLE_09),
             "--dry-run", "--spec", "4-bit counter",
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
