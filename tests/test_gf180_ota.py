"""Tests for GF180 OTA topology."""

import pytest

from eda_agents.topologies.ota_gf180 import GF180OTATopology
from eda_agents.core.pdk import GF180MCU_D


class TestGF180OTATopology:
    def test_topology_name(self):
        topo = GF180OTATopology()
        assert topo.topology_name() == "gf180_ota"

    def test_pdk_is_gf180(self):
        topo = GF180OTATopology()
        assert topo.pdk is GF180MCU_D
        assert topo.pdk.VDD == 3.3

    def test_design_space(self):
        topo = GF180OTATopology()
        space = topo.design_space()
        assert "Ibias_uA" in space
        assert "L_dp_um" in space
        assert "L_load_um" in space
        assert "Cc_pF" in space
        assert "W_dp_um" in space
        assert len(space) == 5

    def test_default_params_in_range(self):
        topo = GF180OTATopology()
        defaults = topo.default_params()
        space = topo.design_space()
        for key, val in defaults.items():
            lo, hi = space[key]
            assert lo <= val <= hi, f"{key}={val} outside [{lo}, {hi}]"

    def test_params_to_sizing(self):
        topo = GF180OTATopology()
        sizing = topo.params_to_sizing(topo.default_params())
        assert "M1" in sizing
        assert "M6" in sizing
        assert sizing["_VDD"] == 3.3
        # All transistor W >= Wmin
        for name, dev in sizing.items():
            if name.startswith("_"):
                continue
            assert dev["W"] >= GF180MCU_D.Wmin_m

    def test_netlist_generation(self, tmp_path):
        topo = GF180OTATopology()
        sizing = topo.params_to_sizing(topo.default_params())
        cir = topo.generate_netlist(sizing, tmp_path)
        assert cir.exists()

        # Read all generated files
        all_content = ""
        for f in tmp_path.iterdir():
            if f.suffix in (".cir", ".net"):
                all_content += f.read_text()

        # GF180 device names
        assert "nfet_03v3" in all_content
        assert "pfet_03v3" in all_content
        # No IHP references
        assert "sg13" not in all_content
        # GF180 model paths
        assert "design.ngspice" in all_content
        assert "sm141064" in all_content
        # No OSDI
        assert "osdi" not in all_content
        # VDD = 3.3
        assert "3.3" in all_content

    def test_prompt_description(self):
        topo = GF180OTATopology()
        desc = topo.prompt_description()
        assert "GF180" in desc
        assert "3.3" in desc

    def test_check_validity_pass(self):
        from eda_agents.core.spice_runner import SpiceResult
        topo = GF180OTATopology()
        result = SpiceResult(
            success=True,
            Adc_dB=50.0,
            GBW_Hz=1e6,
            PM_deg=60.0,
        )
        valid, violations = topo.check_validity(result)
        assert valid
        assert violations == []

    def test_check_validity_fail(self):
        from eda_agents.core.spice_runner import SpiceResult
        topo = GF180OTATopology()
        result = SpiceResult(
            success=True,
            Adc_dB=30.0,  # below 40dB spec
            GBW_Hz=100e3,  # below 500kHz spec
            PM_deg=30.0,   # below 45deg spec
        )
        valid, violations = topo.check_validity(result)
        assert not valid
        assert len(violations) == 3


@pytest.mark.spice
class TestGF180OTASpice:
    """Integration tests requiring ngspice + GF180MCU PDK."""

    @pytest.fixture(autouse=True)
    def check_pdk(self):
        from eda_agents.core.spice_runner import SpiceRunner
        runner = SpiceRunner(pdk=GF180MCU_D)
        missing = runner.validate_pdk()
        if missing:
            pytest.skip(f"GF180 PDK not available: {missing}")

    def test_spice_simulation(self, tmp_path):
        from eda_agents.core.spice_runner import SpiceRunner
        topo = GF180OTATopology()
        runner = SpiceRunner(pdk=GF180MCU_D)

        sizing = topo.params_to_sizing(topo.default_params())
        cir = topo.generate_netlist(sizing, tmp_path)
        result = runner.run(cir, tmp_path)

        assert result.success, f"Simulation failed: {result.error}"
        assert result.Adc_dB is not None
        assert result.GBW_Hz is not None
        assert result.PM_deg is not None

    def test_default_spice_meets_specs(self, tmp_path):
        """Run default GF180OTA params through ngspice, verify specs."""
        from eda_agents.core.spice_runner import SpiceRunner
        topo = GF180OTATopology()
        runner = SpiceRunner(pdk=GF180MCU_D)

        params = topo.default_params()
        sizing = topo.params_to_sizing(params)
        cir = topo.generate_netlist(sizing, tmp_path)
        result = runner.run(cir, tmp_path)

        assert result.success, f"Simulation failed: {result.error}"
        fom = topo.compute_fom(result, sizing)
        valid, violations = topo.check_validity(result, sizing)

        # Record actual values for diagnostics
        print(f"Default design SPICE results:")
        print(f"  Adc = {result.Adc_dB:.1f} dB")
        print(f"  GBW = {result.GBW_Hz:.0f} Hz ({result.GBW_Hz/1e3:.1f} kHz)")
        print(f"  PM  = {result.PM_deg:.1f} deg")
        print(f"  FoM = {fom:.2e}")
        print(f"  Valid: {valid}, Violations: {violations}")

        assert fom > 0, "FoM should be positive for a working design"
