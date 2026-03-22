"""Tests for PDK abstraction layer."""

import os

import pytest

from eda_agents.core.pdk import (
    GF180MCU_D,
    IHP_SG13G2,
    PdkConfig,
    get_pdk,
    list_pdks,
    register_pdk,
    resolve_pdk,
    resolve_pdk_root,
)


class TestPdkConfig:
    def test_ihp_config_identity(self):
        assert IHP_SG13G2.name == "ihp_sg13g2"
        assert IHP_SG13G2.technology_nm == 130
        assert IHP_SG13G2.VDD == 1.2
        assert IHP_SG13G2.Lmin_m == 130e-9
        assert IHP_SG13G2.nmos_symbol == "sg13_lv_nmos"
        assert IHP_SG13G2.pmos_symbol == "sg13_lv_pmos"
        assert IHP_SG13G2.instance_prefix == "X"

    def test_gf180_config_identity(self):
        assert GF180MCU_D.name == "gf180mcu"
        assert GF180MCU_D.technology_nm == 180
        assert GF180MCU_D.VDD == 3.3
        assert GF180MCU_D.Lmin_m == 280e-9
        assert GF180MCU_D.nmos_symbol == "nfet_03v3"
        assert GF180MCU_D.pmos_symbol == "pfet_03v3"
        assert GF180MCU_D.instance_prefix == "X"

    def test_ihp_has_osdi(self):
        assert IHP_SG13G2.has_osdi() is True
        assert len(IHP_SG13G2.osdi_files) == 3

    def test_gf180_no_osdi(self):
        assert GF180MCU_D.has_osdi() is False
        assert GF180MCU_D.osdi_files == ()

    def test_model_lib_path(self):
        path = IHP_SG13G2.model_lib_path("/opt/pdk")
        assert path == "/opt/pdk/ihp-sg13g2/libs.tech/ngspice/models/cornerMOSlv.lib"

    def test_osdi_dir_path(self):
        path = IHP_SG13G2.osdi_dir_path("/opt/pdk")
        assert path == "/opt/pdk/ihp-sg13g2/libs.tech/ngspice/osdi"

    def test_gf180_osdi_dir_none(self):
        assert GF180MCU_D.osdi_dir_path("/opt/pdk") is None

    def test_cap_lib_path(self):
        path = IHP_SG13G2.cap_lib_path("/opt/pdk")
        assert "cornerCAP" in path

    def test_gf180_has_cap_lib(self):
        path = GF180MCU_D.cap_lib_path("/opt/pdk")
        assert path is not None
        assert "sm141064_mim" in path

    def test_frozen(self):
        with pytest.raises(AttributeError):
            IHP_SG13G2.VDD = 3.3  # type: ignore


class TestPdkRegistry:
    def test_list_pdks(self):
        names = list_pdks()
        assert "ihp_sg13g2" in names
        assert "gf180mcu" in names

    def test_get_pdk(self):
        ihp = get_pdk("ihp_sg13g2")
        assert ihp is IHP_SG13G2
        gf = get_pdk("gf180mcu")
        assert gf is GF180MCU_D

    def test_get_pdk_unknown(self):
        with pytest.raises(KeyError, match="Unknown PDK"):
            get_pdk("sky130")

    def test_register_custom(self):
        custom = PdkConfig(
            name="test_pdk",
            display_name="Test PDK",
            technology_nm=65,
            VDD=1.0,
            Lmin_m=65e-9,
            Wmin_m=100e-9,
            z1_m=200e-9,
            model_lib_rel="test/models.lib",
            model_corner="tt",
        )
        register_pdk(custom)
        assert get_pdk("test_pdk") is custom
        assert "test_pdk" in list_pdks()


class TestResolvePdk:
    def test_none_defaults_to_ihp(self):
        pdk = resolve_pdk(None)
        assert pdk is IHP_SG13G2

    def test_passthrough_config(self):
        pdk = resolve_pdk(GF180MCU_D)
        assert pdk is GF180MCU_D

    def test_string_name(self):
        pdk = resolve_pdk("gf180mcu")
        assert pdk is GF180MCU_D

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("EDA_AGENTS_PDK", "gf180mcu")
        pdk = resolve_pdk(None)
        assert pdk is GF180MCU_D

    def test_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("EDA_AGENTS_PDK", "gf180mcu")
        pdk = resolve_pdk("ihp_sg13g2")
        assert pdk is IHP_SG13G2


class TestResolvePdkRoot:
    def test_explicit_root(self):
        root = resolve_pdk_root(IHP_SG13G2, "/custom/path")
        assert root == "/custom/path"

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("PDK_ROOT", "/env/pdk")
        root = resolve_pdk_root(IHP_SG13G2)
        assert root == "/env/pdk"

    def test_default_fallback(self, monkeypatch):
        monkeypatch.delenv("PDK_ROOT", raising=False)
        root = resolve_pdk_root(IHP_SG13G2)
        assert root == IHP_SG13G2.default_pdk_root

    def test_no_root_raises(self, monkeypatch):
        monkeypatch.delenv("PDK_ROOT", raising=False)
        # Create a PDK with no default_pdk_root to test the error
        no_root_pdk = PdkConfig(
            name="test_no_root", display_name="Test", technology_nm=65,
            VDD=1.0, Lmin_m=65e-9, Wmin_m=100e-9, z1_m=200e-9,
            model_lib_rel="test.lib", model_corner="tt",
            default_pdk_root="",
        )
        with pytest.raises(ValueError, match="No PDK_ROOT"):
            resolve_pdk_root(no_root_pdk)


class TestSpiceRunnerPdk:
    """Test SpiceRunner PDK integration."""

    def test_default_pdk_is_ihp(self):
        from eda_agents.core.spice_runner import SpiceRunner
        runner = SpiceRunner()
        assert runner.pdk is IHP_SG13G2
        assert "cornerMOSlv" in str(runner.model_lib)

    def test_gf180_pdk(self, monkeypatch):
        monkeypatch.setenv("PDK_ROOT", "/tmp/fake_pdk")
        from eda_agents.core.spice_runner import SpiceRunner
        runner = SpiceRunner(pdk=GF180MCU_D, pdk_root="/tmp/fake_pdk")
        assert runner.pdk is GF180MCU_D
        assert "sm141064.ngspice" in str(runner.model_lib)
        assert runner.osdi_dir is None
        assert runner.osdi_paths == []

    def test_ihp_has_osdi_paths(self):
        from eda_agents.core.spice_runner import SpiceRunner
        runner = SpiceRunner()
        assert len(runner.osdi_paths) == 3


class TestTopologyPdk:
    """Test topology PDK propagation."""

    def test_aa_ota_default_ihp(self):
        from eda_agents.topologies.ota_analogacademy import AnalogAcademyOTATopology
        topo = AnalogAcademyOTATopology()
        assert topo.pdk is IHP_SG13G2
        assert "IHP" in topo.prompt_description()

    def test_aa_ota_gf180(self):
        from eda_agents.topologies.ota_analogacademy import AnalogAcademyOTATopology
        topo = AnalogAcademyOTATopology(pdk="gf180mcu")
        assert topo.pdk is GF180MCU_D
        assert "GF180" in topo.prompt_description()

    def test_strongarm_default_ihp(self):
        from eda_agents.topologies.comparator_strongarm import StrongARMComparatorTopology
        topo = StrongARMComparatorTopology()
        assert topo.pdk is IHP_SG13G2

    def test_miller_ota_default_ihp(self):
        from eda_agents.topologies.ota_miller import MillerOTATopology
        topo = MillerOTATopology()
        assert topo.pdk is IHP_SG13G2
        assert "IHP" in topo.prompt_description()

    def test_miller_ota_gf180(self):
        from eda_agents.topologies.ota_miller import MillerOTATopology
        topo = MillerOTATopology(pdk="gf180mcu")
        assert topo.pdk is GF180MCU_D
        assert "GF180" in topo.prompt_description()


class TestNetlistPdkDeviceNames:
    """Verify netlists use PDK-correct device names.

    OTA topologies split into .net (circuit) and .ac.cir (control) files.
    Device names are in the .net file; model paths and OSDI are in .ac.cir.
    StrongARM is a single .cir file with everything inline.
    """

    @staticmethod
    def _all_content(tmp_path) -> str:
        """Read all generated files in the work dir."""
        parts = []
        for f in sorted(tmp_path.iterdir()):
            if f.suffix in (".cir", ".net", ".par"):
                parts.append(f.read_text())
        return "\n".join(parts)

    def test_aa_ota_ihp_netlist_has_sg13(self, tmp_path):
        from eda_agents.topologies.ota_analogacademy import AnalogAcademyOTATopology
        topo = AnalogAcademyOTATopology()
        sizing = topo.params_to_sizing(topo.default_params())
        topo.generate_netlist(sizing, tmp_path)
        content = self._all_content(tmp_path)
        assert "sg13_lv_nmos" in content
        assert "sg13_lv_pmos" in content
        assert "osdi" in content  # IHP needs OSDI

    def test_aa_ota_gf180_netlist_has_nfet(self, tmp_path):
        from eda_agents.topologies.ota_analogacademy import AnalogAcademyOTATopology
        topo = AnalogAcademyOTATopology(pdk="gf180mcu")
        sizing = topo.params_to_sizing(topo.default_params())
        topo.generate_netlist(sizing, tmp_path)
        content = self._all_content(tmp_path)
        assert "nfet_03v3" in content
        assert "pfet_03v3" in content
        assert "osdi" not in content  # GF180 has no OSDI

    def test_gf180_netlist_includes_design_ngspice(self, tmp_path):
        """GF180 netlists must include design.ngspice for global params."""
        from eda_agents.topologies.ota_analogacademy import AnalogAcademyOTATopology
        topo = AnalogAcademyOTATopology(pdk="gf180mcu")
        sizing = topo.params_to_sizing(topo.default_params())
        topo.generate_netlist(sizing, tmp_path)
        content = self._all_content(tmp_path)
        assert "design.ngspice" in content

    def test_strongarm_gf180_netlist(self, tmp_path):
        from eda_agents.topologies.comparator_strongarm import StrongARMComparatorTopology
        topo = StrongARMComparatorTopology(pdk="gf180mcu")
        sizing = topo.params_to_sizing(topo.default_params())
        cir = topo.generate_netlist(sizing, tmp_path)
        content = cir.read_text()
        assert "nfet_03v3" in content
        assert "pfet_03v3" in content
        assert "sg13" not in content

    def test_miller_ota_ihp_netlist(self, tmp_path):
        from eda_agents.topologies.ota_miller import MillerOTATopology
        topo = MillerOTATopology()
        sizing = topo.params_to_sizing(topo.default_params())
        topo.generate_netlist(sizing, tmp_path)
        content = self._all_content(tmp_path)
        assert "sg13_lv_nmos" in content
        assert "sg13_lv_pmos" in content
