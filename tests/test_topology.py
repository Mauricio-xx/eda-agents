"""Tests for CircuitTopology abstract base class."""
from eda_agents.core.topology import CircuitTopology
from eda_agents.core.spice_runner import SpiceResult


class _DummyTopology(CircuitTopology):
    def topology_name(self): return "dummy"
    def design_space(self): return {"x": (0.0, 1.0), "y": (0.0, 10.0)}
    def params_to_sizing(self, params): return {"M1": {"W": params["x"], "L": params["y"]}}
    def generate_netlist(self, sizing, work_dir):
        p = work_dir / "test.cir"
        p.write_text("* test\n.end\n")
        return p
    def compute_fom(self, spice_result, sizing): return 1.0 if spice_result.success else 0.0
    def check_validity(self, spice_result, sizing=None):
        return (spice_result.success, [] if spice_result.success else ["failed"])
    def prompt_description(self): return "Dummy topology for testing"
    def design_vars_description(self): return "- x: [0-1]\n- y: [0-10]"
    def specs_description(self): return "x > 0.5"
    def fom_description(self): return "FoM = 1.0 for success"
    def reference_description(self): return "x=0.5, y=5.0"


class TestCircuitTopologyABC:
    def test_default_params(self):
        t = _DummyTopology()
        dp = t.default_params()
        assert dp == {"x": 0.5, "y": 5.0}

    def test_design_space(self):
        t = _DummyTopology()
        space = t.design_space()
        assert "x" in space
        assert "y" in space
        assert len(space) == 2

    def test_tool_spec(self):
        t = _DummyTopology()
        spec = t.tool_spec()
        assert spec["type"] == "function"
        assert "simulate_circuit" in spec["function"]["name"]
        params = spec["function"]["parameters"]
        assert "x" in params["properties"]
        assert "y" in params["properties"]

    def test_compute_fom(self):
        t = _DummyTopology()
        ok = SpiceResult(success=True, Adc_dB=50.0)
        fail = SpiceResult(success=False)
        assert t.compute_fom(ok, {}) == 1.0
        assert t.compute_fom(fail, {}) == 0.0

    def test_check_validity(self):
        t = _DummyTopology()
        ok = SpiceResult(success=True)
        valid, violations = t.check_validity(ok)
        assert valid
        assert violations == []

    def test_exploration_hints_default(self):
        t = _DummyTopology()
        assert t.exploration_hints() == {}

    def test_auxiliary_tools_description(self):
        t = _DummyTopology()
        desc = t.auxiliary_tools_description()
        assert "gmid_lookup" in desc
