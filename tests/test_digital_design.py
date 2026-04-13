"""Tests for DigitalDesign ABC and concrete design wrappers."""

from pathlib import Path

from eda_agents.core.digital_design import DigitalDesign, TestbenchSpec
from eda_agents.core.flow_metrics import FlowMetrics


class _DummyDesign(DigitalDesign):
    """Minimal concrete subclass for testing the ABC."""

    def project_name(self):
        return "dummy-design"

    def specification(self):
        return "A dummy design for testing."

    def design_space(self):
        return {
            "DENSITY": [40, 50, 60],
            "CLOCK": (10.0, 50.0),
        }

    def flow_config_overrides(self):
        return {"DESIGN_NAME": "dummy"}

    def project_dir(self):
        return Path("/tmp/dummy")

    def librelane_config(self):
        return Path("/tmp/dummy/config.yaml")

    def compute_fom(self, metrics):
        if metrics.wns_worst_ns is None or metrics.wns_worst_ns < 0:
            return 0.0
        return metrics.weighted_fom()

    def check_validity(self, metrics):
        return metrics.validity_check()

    def prompt_description(self):
        return "Dummy design for unit tests."

    def design_vars_description(self):
        return "- DENSITY: [40, 50, 60]\n- CLOCK: [10-50] ns"

    def specs_description(self):
        return "WNS >= 0, DRC clean"

    def fom_description(self):
        return "FoM = weighted timing + area + power"

    def reference_description(self):
        return "DENSITY=50, CLOCK=30: WNS=+5ns"


class TestDigitalDesignABC:
    def test_instantiate_dummy(self):
        d = _DummyDesign()
        assert d.project_name() == "dummy-design"

    def test_design_space_discrete(self):
        d = _DummyDesign()
        space = d.design_space()
        assert isinstance(space["DENSITY"], list)
        assert 50 in space["DENSITY"]

    def test_design_space_continuous(self):
        d = _DummyDesign()
        space = d.design_space()
        assert isinstance(space["CLOCK"], tuple)
        assert space["CLOCK"] == (10.0, 50.0)

    def test_default_config_discrete(self):
        d = _DummyDesign()
        cfg = d.default_config()
        # Middle element of [40, 50, 60] -> index 1 -> 50
        assert cfg["DENSITY"] == 50

    def test_default_config_continuous(self):
        d = _DummyDesign()
        cfg = d.default_config()
        assert cfg["CLOCK"] == 30.0

    def test_flow_type_default(self):
        d = _DummyDesign()
        assert d.flow_type() == "Classic"

    def test_pdk_root_default_none(self):
        d = _DummyDesign()
        assert d.pdk_root() is None

    def test_rtl_sources_default_empty(self):
        d = _DummyDesign()
        assert d.rtl_sources() == []

    def test_testbench_default_none(self):
        d = _DummyDesign()
        assert d.testbench() is None

    def test_compute_fom_valid(self):
        d = _DummyDesign()
        m = FlowMetrics(
            wns_worst_ns=5.0,
            die_area_um2=256_175,
            power_total_w=0.052,
        )
        fom = d.compute_fom(m)
        assert fom > 0

    def test_compute_fom_invalid(self):
        d = _DummyDesign()
        m = FlowMetrics(wns_worst_ns=-1.0)
        assert d.compute_fom(m) == 0.0

    def test_check_validity_pass(self):
        d = _DummyDesign()
        m = FlowMetrics(wns_worst_ns=5.0, drc_clean=True, lvs_match=True)
        valid, violations = d.check_validity(m)
        assert valid
        assert violations == []

    def test_check_validity_fail_timing(self):
        d = _DummyDesign()
        m = FlowMetrics(wns_worst_ns=-0.5)
        valid, violations = d.check_validity(m)
        assert not valid
        assert any("Timing" in v for v in violations)

    def test_prompt_methods_nonempty(self):
        d = _DummyDesign()
        assert len(d.prompt_description()) > 10
        assert len(d.design_vars_description()) > 10
        assert len(d.specs_description()) > 5
        assert len(d.fom_description()) > 5
        assert len(d.reference_description()) > 5

    def test_tool_spec_discrete(self):
        d = _DummyDesign()
        spec = d.tool_spec()
        assert spec["type"] == "function"
        params = spec["function"]["parameters"]
        assert "DENSITY" in params["properties"]
        assert "enum" in params["properties"]["DENSITY"]
        assert params["properties"]["DENSITY"]["enum"] == [40, 50, 60]

    def test_tool_spec_continuous(self):
        d = _DummyDesign()
        spec = d.tool_spec()
        params = spec["function"]["parameters"]
        assert "CLOCK" in params["properties"]
        assert "enum" not in params["properties"]["CLOCK"]

    def test_exploration_hints_default(self):
        d = _DummyDesign()
        assert d.exploration_hints() == {}


class TestTestbenchSpec:
    def test_defaults(self):
        tb = TestbenchSpec(driver="cocotb", target="sim")
        assert tb.driver == "cocotb"
        assert tb.env_overrides == {}
        assert tb.work_dir_relative == "."

    def test_with_overrides(self):
        tb = TestbenchSpec(
            driver="iverilog",
            target="run_tests.sh",
            env_overrides={"GL": "1"},
        )
        assert tb.env_overrides["GL"] == "1"


class TestFazyRvHachureDesign:
    def test_import(self):
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign
        d = FazyRvHachureDesign(designs_dir="/tmp/nonexistent")
        assert d.project_name() == "fazyrv-hachure-frv_1"

    def test_chip_top_variant(self):
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign
        d = FazyRvHachureDesign(designs_dir="/tmp/nonexistent", macro="")
        assert d.project_name() == "fazyrv-hachure-chip"
        assert d.flow_type() == "Chip"

    def test_design_space_has_observed_knobs(self):
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign
        d = FazyRvHachureDesign(designs_dir="/tmp/nonexistent")
        space = d.design_space()
        assert "PL_TARGET_DENSITY_PCT" in space
        assert "CLOCK_PERIOD" in space
        # Phase 0: density values are discrete
        assert isinstance(space["PL_TARGET_DENSITY_PCT"], list)
        assert 65 in space["PL_TARGET_DENSITY_PCT"]
        # Phase 0: clock >= 35 (safe lower bound)
        assert min(space["CLOCK_PERIOD"]) >= 35

    def test_prompt_description_mentions_gf180(self):
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign
        d = FazyRvHachureDesign(designs_dir="/tmp/nonexistent")
        assert "GF180" in d.prompt_description()

    def test_validate_clone_missing(self):
        from eda_agents.core.designs.fazyrv_hachure import FazyRvHachureDesign
        d = FazyRvHachureDesign(designs_dir="/tmp/nonexistent")
        problems = d.validate_clone()
        assert len(problems) > 0
        assert "not found" in problems[0].lower()


class TestSystolicMacDftDesign:
    def test_import(self):
        from eda_agents.core.designs.systolic_mac_dft import SystolicMacDftDesign
        d = SystolicMacDftDesign(designs_dir="/tmp/nonexistent")
        assert d.project_name() == "systolic-mac-dft"

    def test_design_space(self):
        from eda_agents.core.designs.systolic_mac_dft import SystolicMacDftDesign
        d = SystolicMacDftDesign(designs_dir="/tmp/nonexistent")
        space = d.design_space()
        assert "PL_TARGET_DENSITY_PCT" in space
        assert "CLOCK_PERIOD" in space

    def test_reference_mentions_deferred(self):
        from eda_agents.core.designs.systolic_mac_dft import SystolicMacDftDesign
        d = SystolicMacDftDesign(designs_dir="/tmp/nonexistent")
        assert "deferred" in d.reference_description().lower()
