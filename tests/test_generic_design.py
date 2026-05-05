"""Tests for GenericDesign — auto-derived DigitalDesign from LibreLane config."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from eda_agents.core.designs.generic import GenericDesign

SAMPLE_CONFIG = {
    "DESIGN_NAME": "test_counter",
    "VERILOG_FILES": ["dir::../src/counter.v"],
    "CLOCK_PORT": "clk",
    "CLOCK_PERIOD": 40,
    "PL_TARGET_DENSITY_PCT": 65,
    "FP_SIZING": "absolute",
    "DIE_AREA": [0.0, 0.0, 300.0, 250.0],
    "VDD_NETS": ["VDD"],
    "GND_NETS": ["VSS"],
}


@pytest.fixture
def yaml_config(tmp_path):
    """Create a temporary YAML config file."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(SAMPLE_CONFIG, default_flow_style=False))
    return config_path


@pytest.fixture
def json_config(tmp_path):
    """Create a temporary JSON config file."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(SAMPLE_CONFIG, indent=2))
    return config_path


# ---------------------------------------------------------------------------
# Auto-derivation tests
# ---------------------------------------------------------------------------


class TestAutoDerivation:
    def test_project_name_from_config(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert d.project_name() == "test-counter"

    def test_project_dir_is_config_parent(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert d.project_dir() == yaml_config.parent

    def test_librelane_config_is_config_path(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert d.librelane_config() == yaml_config

    def test_design_space_has_density(self, yaml_config):
        """Density is exposed as an open ``(lo, hi)`` tuple. The
        baseline value (65) must sit inside the range so the LLM can
        actually propose it."""
        d = GenericDesign(yaml_config)
        ds = d.design_space()
        assert "PL_TARGET_DENSITY_PCT" in ds
        rng = ds["PL_TARGET_DENSITY_PCT"]
        assert isinstance(rng, tuple) and len(rng) == 2
        lo, hi = rng
        assert lo < 65 < hi

    def test_design_space_has_clock(self, yaml_config):
        """Clock period is exposed as an open ``(lo, hi)`` tuple. The
        config baseline (40 ns) must sit inside the range, and the
        upper bound must be far above 1.25x baseline so the LLM can
        relax timing past the prior policy cap."""
        d = GenericDesign(yaml_config)
        ds = d.design_space()
        assert "CLOCK_PERIOD" in ds
        rng = ds["CLOCK_PERIOD"]
        assert isinstance(rng, tuple) and len(rng) == 2
        lo, hi = rng
        assert lo < 40.0 < hi
        assert hi >= 40.0 * 5, (
            "Clock upper bound must allow >5x relaxation; the prior "
            "1.25x cap is the policy we explicitly removed (LLM should "
            "have full freedom to experiment with timing)."
        )

    def test_design_space_density_range_is_libelane_valid(self, yaml_config):
        """The density bound is the LibreLane-valid window, not a
        narrow grid. ``(1.0, 99.0)`` is the floor/ceiling LibreLane
        accepts; values outside crash floorplan but neither endpoint
        crashes the tool."""
        d = GenericDesign(yaml_config)
        ds = d.design_space()
        lo, hi = ds["PL_TARGET_DENSITY_PCT"]
        assert lo >= 0.0 and hi <= 100.0
        assert hi - lo > 90.0, (
            "Density window must span almost the full LibreLane-valid "
            "range, not the prior baseline +/-20 strip."
        )

    def test_design_space_overrides(self, yaml_config):
        d = GenericDesign(
            yaml_config,
            design_space_overrides={"CLOCK_PERIOD": [20, 30, 40, 50]},
        )
        ds = d.design_space()
        assert ds["CLOCK_PERIOD"] == [20, 30, 40, 50]

    def test_flow_config_overrides_empty(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert d.flow_config_overrides() == {}

    def test_rtl_sources_parsed(self, yaml_config):
        d = GenericDesign(yaml_config)
        sources = d.rtl_sources()
        assert len(sources) == 1
        assert "counter.v" in str(sources[0])

    def test_specification_has_design_name(self, yaml_config):
        d = GenericDesign(yaml_config)
        spec = d.specification()
        assert "test_counter" in spec

    def test_specification_has_clock(self, yaml_config):
        d = GenericDesign(yaml_config)
        spec = d.specification()
        assert "40" in spec


# ---------------------------------------------------------------------------
# FoM and validity
# ---------------------------------------------------------------------------


class TestFomAndValidity:
    def test_compute_fom_valid(self, yaml_config):
        d = GenericDesign(yaml_config)
        measurements = {
            "wns_worst_ns": 5.0,
            "die_area_um2": 100000,
            "power_mw": 10.0,
            "drc_clean": True,
            "lvs_match": True,
        }
        fom = d.compute_fom(measurements)
        assert fom > 0

    def test_compute_fom_invalid_timing(self, yaml_config):
        d = GenericDesign(yaml_config)
        measurements = {
            "wns_worst_ns": -1.0,
            "drc_clean": True,
            "lvs_match": True,
        }
        fom = d.compute_fom(measurements)
        assert fom == 0.0

    def test_custom_fom_weights(self, yaml_config):
        # Drop every weight to zero except timing_met to verify the
        # weight dict is honoured; the remaining FoM should be exactly
        # the binary timing-met indicator.
        d = GenericDesign(
            yaml_config,
            fom_weights={
                "timing_w": 2.0,
                "perf_w": 0.0,
                "area_w": 0.0,
                "power_w": 0.0,
            },
        )
        measurements = {
            "wns_worst_ns": 10.0,
            "die_area_um2": 100000,
            "power_mw": 10.0,
            "clock_period_ns": 40.0,
            "drc_clean": True,
            "lvs_match": True,
        }
        fom = d.compute_fom(measurements)
        assert abs(fom - 2.0) < 1e-6

    def test_check_validity_delegates(self, yaml_config):
        d = GenericDesign(yaml_config)
        measurements = {
            "wns_worst_ns": 5.0,
            "drc_clean": True,
            "lvs_match": True,
        }
        valid, violations = d.check_validity(measurements)
        assert valid
        assert violations == []


# ---------------------------------------------------------------------------
# PDK and shell wrapper
# ---------------------------------------------------------------------------


class TestPdkAndShellWrapper:
    def test_pdk_root_explicit(self, yaml_config):
        d = GenericDesign(yaml_config, pdk_root="/some/pdk")
        assert d.pdk_root() == Path("/some/pdk")

    def test_pdk_root_default_none(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert d.pdk_root() is None

    def test_no_nix_shell_in_tmp(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert d.shell_wrapper() is None

    def test_nix_shell_detected(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(yaml.dump(SAMPLE_CONFIG))
        (tmp_path / "shell.nix").write_text("# nix shell")
        d = GenericDesign(config)
        wrapper = d.shell_wrapper()
        assert wrapper is not None
        assert "nix-shell" in wrapper

    def test_shell_wrapper_explicit(self, yaml_config):
        d = GenericDesign(yaml_config, shell_wrapper="nix-shell /my/dir --run")
        assert d.shell_wrapper() == "nix-shell /my/dir --run"

    def test_shell_wrapper_none_explicit(self, yaml_config):
        d = GenericDesign(yaml_config, shell_wrapper=None)
        assert d.shell_wrapper() is None


# ---------------------------------------------------------------------------
# JSON config
# ---------------------------------------------------------------------------


class TestJsonConfig:
    def test_project_name_from_json(self, json_config):
        d = GenericDesign(json_config)
        assert d.project_name() == "test-counter"

    def test_design_space_from_json(self, json_config):
        d = GenericDesign(json_config)
        ds = d.design_space()
        assert "PL_TARGET_DENSITY_PCT" in ds


# ---------------------------------------------------------------------------
# Prompt metadata
# ---------------------------------------------------------------------------


class TestPromptMetadata:
    def test_prompt_description_nonempty(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert len(d.prompt_description()) > 20

    def test_design_vars_description(self, yaml_config):
        d = GenericDesign(yaml_config)
        desc = d.design_vars_description()
        assert "PL_TARGET_DENSITY_PCT" in desc

    def test_specs_description(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert "WNS" in d.specs_description()

    def test_fom_description(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert "FoM" in d.fom_description()

    def test_reference_description(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert "baseline" in d.reference_description().lower()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validate_clone_ok(self, yaml_config):
        d = GenericDesign(yaml_config)
        assert d.validate_clone() == []

    def test_validate_clone_missing_config(self, tmp_path):
        d = GenericDesign(tmp_path / "nonexistent.yaml")
        problems = d.validate_clone()
        assert any("not found" in p.lower() for p in problems)


# ---------------------------------------------------------------------------
# Backend compatibility
# ---------------------------------------------------------------------------


class TestBackendCompat:
    def test_prompt_builder_works(self, yaml_config):
        from eda_agents.agents.tool_defs import build_digital_rtl2gds_prompt

        d = GenericDesign(yaml_config)
        prompt = build_digital_rtl2gds_prompt(d)
        assert len(prompt) > 100
        assert "test-counter" in prompt

    def test_cc_cli_dry_run(self, yaml_config):
        from eda_agents.agents.digital_adk_agents import ProjectManager

        d = GenericDesign(yaml_config)
        pm = ProjectManager(design=d, backend="cc_cli")
        result = asyncio.run(
            pm.run(Path("/tmp/generic_dry"), dry_run=True)
        )
        assert "prompt" in result
        assert len(result["prompt"]) > 100


# ---------------------------------------------------------------------------
# Autoresearch chain compatibility
# ---------------------------------------------------------------------------


class TestAutoresearchChain:
    """Verify GenericDesign is compatible with DigitalAutoresearchRunner."""

    def test_runner_accepts_generic_design(self, yaml_config, tmp_path):
        from eda_agents.agents.digital_autoresearch import (
            DigitalAutoresearchRunner,
        )

        mock_metrics = Path(__file__).resolve().parents[1] / "fixtures" / "fake_flow_metrics.json"
        d = GenericDesign(yaml_config)
        runner = DigitalAutoresearchRunner(
            design=d,
            model="test-model",
            budget=1,
            use_mock_metrics=mock_metrics,
        )
        # Runner should initialize without error
        assert runner.design is d
        assert runner.budget == 1

    def test_runner_generates_program(self, yaml_config):
        from eda_agents.agents.digital_autoresearch import (
            DigitalAutoresearchRunner,
        )

        d = GenericDesign(yaml_config)
        runner = DigitalAutoresearchRunner(
            design=d, model="test", budget=1,
        )
        program = runner._generate_program()
        assert "test-counter" in program
        assert "PL_TARGET_DENSITY_PCT" in program

    def test_custom_fom_weights_propagate(self, yaml_config, tmp_path):
        """FoM weights set on GenericDesign propagate to compute_fom."""
        d_default = GenericDesign(yaml_config)
        d_custom = GenericDesign(yaml_config, fom_weights={"area_w": 5.0})

        measurements = {
            "wns_worst_ns": 0.5,
            "die_area_um2": 90000.0,
            "power_mw": 10.0,
        }

        fom_default = d_default.compute_fom(measurements)
        fom_custom = d_custom.compute_fom(measurements)
        # Higher area_w should change the result
        assert fom_custom != fom_default
        assert fom_custom > fom_default  # area_w 5.0 > 0.5
