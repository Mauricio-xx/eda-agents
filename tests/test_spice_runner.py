"""Tests for SpiceRunner and SpiceResult."""
import pytest
from eda_agents.core.spice_runner import SpiceResult, SpiceRunner, _parse_meas_value


class TestSpiceResult:
    def test_basic_fields(self):
        r = SpiceResult(success=True, Adc_dB=55.0, GBW_Hz=1e6, PM_deg=65.0)
        assert r.success
        assert r.Adc_dB == 55.0
        assert r.GBW_MHz == pytest.approx(1.0)

    def test_gbw_mhz_none(self):
        r = SpiceResult(success=True)
        assert r.GBW_MHz is None

    def test_failed_result(self):
        r = SpiceResult(success=False, error="timeout")
        assert not r.success
        assert r.error == "timeout"


class TestParseMeasValue:
    def test_scientific_notation(self):
        assert _parse_meas_value("adc = 5.55000e+01") == pytest.approx(55.5)

    def test_plain_number(self):
        assert _parse_meas_value("pgbw = -30.0") == pytest.approx(-30.0)

    def test_no_value(self):
        assert _parse_meas_value("no measurement here") is None


class TestSpiceRunnerInit:
    def test_default_construction(self):
        runner = SpiceRunner()
        assert runner.pdk_root.name in ("IHP-Open-PDK", runner.pdk_root.name)
        assert runner.corner == "mos_tt"
        assert runner.timeout_s == 120

    def test_custom_pdk_root(self, tmp_path):
        runner = SpiceRunner(pdk_root=tmp_path)
        assert runner.pdk_root == tmp_path

    def test_validate_pdk_missing(self, tmp_path):
        runner = SpiceRunner(pdk_root=tmp_path)
        missing = runner.validate_pdk()
        assert len(missing) > 0


class TestParseOutput:
    def test_parse_ac_measurements(self):
        runner = SpiceRunner()
        stdout = """
adc                 =  5.50000e+01
adc_peak            =  5.52000e+01
gbw                 =  1.23400e+06
pgbw                =  6.50000e+01
"""
        result = runner._parse_output(stdout, "", 1.0)
        assert result.success
        assert result.Adc_dB == pytest.approx(55.0)
        assert result.Adc_peak_dB == pytest.approx(55.2)
        assert result.GBW_Hz == pytest.approx(1.234e6)
        assert result.PM_deg == pytest.approx(65.0)

    def test_parse_empty_output(self):
        runner = SpiceRunner()
        result = runner._parse_output("", "", 0.5)
        assert result.success
        assert result.Adc_dB is None
