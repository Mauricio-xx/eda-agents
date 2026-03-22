"""Tests for Miller OTA analytical designer."""
import pytest
from eda_agents.topologies.miller_ota import (
    MillerOTADesigner, ProcessParams, _ic_gmsid, _gmsid_ic, _vdssat_ic,
)


class TestEKVFunctions:
    def test_roundtrip_ic_gmsid(self):
        for ic in [0.01, 0.1, 1.0, 10.0, 100.0]:
            gmsid = _gmsid_ic(ic)
            ic_back = _ic_gmsid(gmsid)
            assert abs(ic - ic_back) / ic < 0.01

    def test_weak_inversion_limit(self):
        # In weak inversion, gmsid_ic -> gms_ic(ic)/ic -> q/(q*(q+1)) -> 1/(q+1)
        # For very small ic, q ~ sqrt(ic) -> 0, so gmsid -> 1.0 (normalized)
        # The value 2.0 is only returned for ic <= 0 as a guard.
        assert _gmsid_ic(1e-6) == pytest.approx(1.0, rel=0.01)

    def test_vdssat_increases_with_ic(self):
        assert _vdssat_ic(100) > _vdssat_ic(1) > _vdssat_ic(0.01)


class TestProcessParams:
    def test_thermal_voltage(self):
        p = ProcessParams()
        assert 0.025 < p.UT < 0.027

    def test_nmos_stronger(self):
        p = ProcessParams()
        assert p.Ispecsqn > p.Ispecsqp


class TestMillerOTADesigner:
    def test_nominal_design(self):
        d = MillerOTADesigner()
        r = d.analytical_design(12.0, 10.0, 0.5e-6, 0.5e-6, 0.5e-12, 10e-6)
        assert r.Adc_dB > 0
        assert r.GBW > 0
        assert r.PM > 0
        assert len(r.transistors) == 9
        assert r.power_uW > 0
        assert r.area_um2 > 0

    def test_longer_L_more_gain(self):
        d = MillerOTADesigner()
        r1 = d.analytical_design(12, 10, 0.5e-6, 0.5e-6, 0.5e-12, 10e-6)
        r2 = d.analytical_design(12, 10, 2.0e-6, 2.0e-6, 0.5e-12, 10e-6)
        assert r2.Adc_dB > r1.Adc_dB

    def test_fom_nonzero(self):
        d = MillerOTADesigner()
        r = d.analytical_design(12, 10, 1e-6, 1e-6, 1e-12, 10e-6)
        assert r.FoM > 0
        assert r.raw_FoM >= r.FoM

    def test_netlist_generation(self, tmp_path):
        d = MillerOTADesigner()
        r = d.analytical_design(12, 10, 0.5e-6, 0.5e-6, 0.5e-12, 10e-6)
        cir = d.generate_netlist(r, tmp_path)
        assert cir.exists()
        assert (tmp_path / "miller_ota.net").exists()
        assert (tmp_path / "miller_ota.par").exists()
        content = cir.read_text()
        assert "cornerMOSlv.lib" in content

    def test_sweep(self):
        d = MillerOTADesigner()
        results = d.sweep_design_space(
            gmid_input_range=(10, 15, 2),
            gmid_load_range=(8, 12, 2),
            L_input_range=(0.5e-6, 1e-6, 2),
            L_load_range=(0.5e-6, 1e-6, 2),
            Cc_range=(0.5e-12, 1e-12, 2),
        )
        assert len(results) == 32  # 2^5
        assert all(r.Adc_dB > 0 for r in results)
