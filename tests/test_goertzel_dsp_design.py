"""Contract tests for ``GoertzelDspDesign``.

Validates the domain-agnostic ``DigitalDesign`` API by exercising a
real subclass that extends ``DEFAULT_PPA_COLUMNS`` with two DSP
columns, computes throughput from a cocotb sidecar JSON, and rejects
evals below the Nyquist floor. No LibreLane, no cocotb runtime —
fixture-driven.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from eda_agents.core.designs.goertzel_dsp import GoertzelDspDesign
from eda_agents.core.digital_design import DEFAULT_PPA_COLUMNS
from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.flow_stage import FlowStage, StageResult
from eda_agents.core.stage_results import StageResults

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "goertzel_dsp"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stage_results(
    *,
    cycles_per_sample: int | None = 100,
    clock_period_ns: float | None = 10.0,
    sidecar_dir: Path | None = None,
    eval_idx: int = 1,
) -> StageResults:
    """Build a synthetic StageResults bag with an optional rtl_sim sidecar.

    When ``cycles_per_sample`` is None, no sidecar is emitted, mirroring
    the case where the cocotb tb didn't run or didn't produce JSON.
    """
    artifacts: dict[str, Path] = {}
    if cycles_per_sample is not None and sidecar_dir is not None:
        sidecar = sidecar_dir / "meas.json"
        sidecar.write_text(
            f'{{"cycles_per_sample": {cycles_per_sample}}}'
        )
        artifacts["meas.json"] = sidecar

    rtl_sim = StageResult(
        stage=FlowStage.RTL_SIM,
        success=True,
        artifacts=artifacts,
    )

    flow_metrics = FlowMetrics(
        wns_worst_ns=2.5,
        synth_cell_count=20_000,
        die_area_um2=400_000.0,
        power_total_w=0.05,
        wire_length_um=200_000.0,
        clock_period_ns=clock_period_ns,
        drc_clean=True,
        lvs_match=True,
    )

    return StageResults(
        eval_idx=eval_idx,
        params={"CLOCK_PERIOD": clock_period_ns or 10.0},
        work_dir=sidecar_dir or Path("/tmp"),
        flow_metrics=flow_metrics,
        rtl_sim=rtl_sim,
    )


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_defaults(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path)
        assert d.project_name() == "goertzel-dsp"
        assert d._fs_target == 8000.0
        assert d._dsp_w == 2.0

    def test_custom_fs_target(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path, fs_target=44_100.0)
        assert d._fs_target == 44_100.0

    def test_rejects_zero_fs_target(self, tmp_path):
        with pytest.raises(ValueError, match="fs_target"):
            GoertzelDspDesign(project_dir=tmp_path, fs_target=0.0)

    def test_rejects_negative_dsp_w(self, tmp_path):
        with pytest.raises(ValueError, match="dsp_w"):
            GoertzelDspDesign(project_dir=tmp_path, dsp_w=-1.0)


# ---------------------------------------------------------------------------
# measurement_columns extends PPA
# ---------------------------------------------------------------------------


class TestMeasurementColumns:
    def test_extends_ppa_with_dsp_columns(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path)
        cols = d.measurement_columns()
        # PPA columns come first to keep the TSV a strict superset.
        assert cols[: len(DEFAULT_PPA_COLUMNS)] == list(DEFAULT_PPA_COLUMNS)
        # Two DSP columns appended.
        assert cols[len(DEFAULT_PPA_COLUMNS):] == [
            "cycles_per_sample",
            "throughput_sps",
        ]


# ---------------------------------------------------------------------------
# extract_measurements: sidecar parsing + throughput math
# ---------------------------------------------------------------------------


class TestExtractMeasurements:
    def test_high_throughput_sidecar(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path)
        sr = _make_stage_results(
            cycles_per_sample=100,
            clock_period_ns=10.0,
            sidecar_dir=tmp_path,
        )
        m = d.extract_measurements(sr)
        # PPA columns from default extraction.
        assert m["wns_worst_ns"] == 2.5
        assert m["cell_count"] == 20_000
        # DSP measurement: 1e9 / (10 * 100) = 1 MSPS.
        assert m["cycles_per_sample"] == 100
        assert m["throughput_sps"] == pytest.approx(1.0e6, rel=1e-9)

    def test_low_throughput_sidecar(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path)
        sr = _make_stage_results(
            cycles_per_sample=200,
            clock_period_ns=5000.0,
            sidecar_dir=tmp_path,
        )
        m = d.extract_measurements(sr)
        # 1e9 / (5000 * 200) = 1000 sps. Below 8 kHz target.
        assert m["throughput_sps"] == pytest.approx(1000.0, rel=1e-9)

    def test_missing_sidecar_yields_none(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path)
        # No sidecar emitted (cycles_per_sample=None).
        sr = _make_stage_results(
            cycles_per_sample=None, clock_period_ns=10.0,
            sidecar_dir=tmp_path,
        )
        m = d.extract_measurements(sr)
        assert m["cycles_per_sample"] is None
        assert m["throughput_sps"] is None

    def test_period_falls_back_to_params(self, tmp_path):
        """When flow_metrics is absent (mock path), throughput is
        still computed from the eval's CLOCK_PERIOD parameter."""
        d = GoertzelDspDesign(project_dir=tmp_path)
        sidecar = tmp_path / "meas.json"
        sidecar.write_text('{"cycles_per_sample": 100}')
        sr = StageResults(
            eval_idx=1,
            params={"CLOCK_PERIOD": 10.0},
            work_dir=tmp_path,
            flow_metrics=None,
            rtl_sim=StageResult(
                stage=FlowStage.RTL_SIM,
                success=True,
                artifacts={"meas.json": sidecar},
            ),
        )
        m = d.extract_measurements(sr)
        assert m["throughput_sps"] == pytest.approx(1.0e6, rel=1e-9)

    def test_uses_canned_fixtures(self, tmp_path):
        """Fixture JSONs in tests/fixtures/goertzel_dsp/ produce the
        expected throughput when paired with their documented clock
        period. Guards against fixture drift."""
        d = GoertzelDspDesign(project_dir=tmp_path)

        # High-throughput fixture: 100 cycles per sample @ 10 ns clock
        # = 1 MSPS.
        high_sidecar = tmp_path / "meas_high.json"
        high_sidecar.write_text(
            (FIXTURES_DIR / "meas_8k_high_throughput.json").read_text()
        )
        sr_high = StageResults(
            eval_idx=1,
            params={"CLOCK_PERIOD": 10.0},
            work_dir=tmp_path,
            rtl_sim=StageResult(
                stage=FlowStage.RTL_SIM, success=True,
                artifacts={"meas.json": high_sidecar},
            ),
        )
        m_high = d.extract_measurements(sr_high)
        assert m_high["throughput_sps"] == pytest.approx(1.0e6, rel=1e-9)

        # Low-throughput fixture: 200 cycles per sample @ 5000 ns
        # clock = 1 kSPS, below 8 kHz target.
        low_sidecar = tmp_path / "meas_low.json"
        low_sidecar.write_text(
            (FIXTURES_DIR / "meas_8k_low_throughput.json").read_text()
        )
        sr_low = StageResults(
            eval_idx=2,
            params={"CLOCK_PERIOD": 5000.0},
            work_dir=tmp_path,
            rtl_sim=StageResult(
                stage=FlowStage.RTL_SIM, success=True,
                artifacts={"meas.json": low_sidecar},
            ),
        )
        m_low = d.extract_measurements(sr_low)
        assert m_low["throughput_sps"] == pytest.approx(1000.0, rel=1e-9)


# ---------------------------------------------------------------------------
# check_validity Nyquist gate
# ---------------------------------------------------------------------------


class TestCheckValidity:
    def test_throughput_above_floor_passes(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path, fs_target=8000.0)
        m = {
            "wns_worst_ns": 2.5,
            "die_area_um2": 400_000.0,
            "power_mw": 50.0,
            "drc_clean": True,
            "lvs_match": True,
            "cycles_per_sample": 100,
            "throughput_sps": 1.0e6,
        }
        valid, violations = d.check_validity(m)
        assert valid, violations
        assert violations == []

    def test_throughput_below_floor_fails(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path, fs_target=8000.0)
        m = {
            "wns_worst_ns": 2.5,
            "die_area_um2": 400_000.0,
            "power_mw": 50.0,
            "drc_clean": True,
            "lvs_match": True,
            "cycles_per_sample": 200,
            "throughput_sps": 1000.0,
        }
        valid, violations = d.check_validity(m)
        assert not valid
        assert any("Throughput" in v and "below floor" in v for v in violations)

    def test_missing_throughput_fails(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path)
        m = {
            "wns_worst_ns": 2.5,
            "die_area_um2": 400_000.0,
            "drc_clean": True,
            "lvs_match": True,
            "cycles_per_sample": None,
            "throughput_sps": None,
        }
        valid, violations = d.check_validity(m)
        assert not valid
        assert any("DSP measurement missing" in v for v in violations)

    def test_throughput_pass_but_timing_fails(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path)
        m = {
            "wns_worst_ns": -0.5,
            "die_area_um2": 400_000.0,
            "drc_clean": True,
            "lvs_match": True,
            "cycles_per_sample": 100,
            "throughput_sps": 1.0e6,
        }
        valid, violations = d.check_validity(m)
        assert not valid
        assert any("Timing" in v for v in violations)


# ---------------------------------------------------------------------------
# compute_fom: Nyquist-aware reward
# ---------------------------------------------------------------------------


class TestComputeFom:
    def test_invalid_returns_zero(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path, fs_target=8000.0)
        m = {
            "wns_worst_ns": 2.5,
            "die_area_um2": 400_000.0,
            "power_mw": 50.0,
            "drc_clean": True,
            "lvs_match": True,
            # Below floor.
            "cycles_per_sample": 200,
            "throughput_sps": 1000.0,
        }
        assert d.compute_fom(m) == 0.0

    def test_dsp_term_log10_of_margin(self, tmp_path):
        """The DSP contribution is exactly ``dsp_w * log10(throughput /
        fs_target)``; 10x margin -> +2.0, 100x margin -> +4.0 with
        the default weight."""
        d = GoertzelDspDesign(
            project_dir=tmp_path, fs_target=8000.0, dsp_w=2.0,
        )
        # Set PPA fields to known values, vary only throughput.
        common = {
            "wns_worst_ns": 2.5,
            "die_area_um2": 1e6,        # area term = 1.0
            "power_mw": 100.0,           # power 0.1 W
            "clock_period_ns": 10.0,     # nJ_per_cycle = 1.0 -> energy term = 1.0
            "drc_clean": True,
            "lvs_match": True,
            "cycles_per_sample": 1,
        }
        m_low = {**common, "throughput_sps": 8000.0 * 10}    # 10x margin
        m_high = {**common, "throughput_sps": 8000.0 * 100}  # 100x margin

        fom_low = d.compute_fom(m_low)
        fom_high = d.compute_fom(m_high)

        # Difference is exactly dsp_w * log10(100/10) = dsp_w * 1 = 2.0
        assert fom_high - fom_low == pytest.approx(2.0, rel=1e-9)

    def test_monotonic_in_throughput(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path, fs_target=8000.0)
        common = {
            "wns_worst_ns": 2.5,
            "die_area_um2": 400_000.0,
            "power_mw": 50.0,
            "clock_period_ns": 10.0,
            "drc_clean": True,
            "lvs_match": True,
            "cycles_per_sample": 1,
        }
        foms = []
        for tps in (8001.0, 80_000.0, 800_000.0, 8_000_000.0):
            m = {**common, "throughput_sps": tps}
            foms.append(d.compute_fom(m))
        # Strictly increasing.
        assert foms == sorted(foms)
        assert all(b > a for a, b in zip(foms, foms[1:]))

    def test_no_perf_term_no_clock_relaxation_attack(self, tmp_path):
        """Clock relaxation alone (slower clock, same RTL,
        proportionally lower throughput) must not raise FoM. With the
        bare-frequency term dropped and throughput-driven scoring,
        slowing the clock loses both performance and the DSP reward
        at the same time."""
        d = GoertzelDspDesign(project_dir=tmp_path, fs_target=8000.0)
        # Same RTL -> cycles_per_sample fixed; period varies.
        # throughput_sps = 1e9 / (period_ns * cycles_per_sample), so
        # slowing the clock proportionally lowers throughput.
        cycles = 100

        m_fast = {
            "wns_worst_ns": 2.5,
            "die_area_um2": 400_000.0,
            "power_mw": 50.0,
            "clock_period_ns": 10.0,
            "drc_clean": True,
            "lvs_match": True,
            "cycles_per_sample": cycles,
            "throughput_sps": 1.0e9 / (10.0 * cycles),  # 1 MSPS
        }
        m_slow = {
            **m_fast,
            "clock_period_ns": 100.0,
            "throughput_sps": 1.0e9 / (100.0 * cycles),  # 100 kSPS
        }
        # Slow clock has higher energy_eff_score (energy-per-cycle is
        # the same product P*period only when P scales with f; with
        # power_mw fixed across both rows the energy-per-cycle goes
        # UP for the slow clock and ENERGY_EFF goes DOWN, but
        # throughput drops by exactly the same factor 10 so the DSP
        # term loses 2.0). Net: slow clock is strictly worse.
        assert d.compute_fom(m_fast) > d.compute_fom(m_slow)


# ---------------------------------------------------------------------------
# Integration: full extract_measurements -> compute_fom roundtrip
# ---------------------------------------------------------------------------


class TestEndToEndFromStageResults:
    def test_high_throughput_eval_is_valid(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path, fs_target=8000.0)
        sr = _make_stage_results(
            cycles_per_sample=100,
            clock_period_ns=10.0,
            sidecar_dir=tmp_path,
        )
        m = d.extract_measurements(sr)
        valid, violations = d.check_validity(m)
        assert valid, violations
        fom = d.compute_fom(m)
        assert fom > 0
        # log10(1e6 / 8000) ~= 2.0969 -> dsp term ~= 4.19
        expected_dsp = 2.0 * math.log10(1.0e6 / 8000.0)
        assert fom > expected_dsp  # PPA term contributes positively too

    def test_low_throughput_eval_is_invalid(self, tmp_path):
        d = GoertzelDspDesign(project_dir=tmp_path, fs_target=8000.0)
        sr = _make_stage_results(
            cycles_per_sample=200,
            clock_period_ns=5000.0,
            sidecar_dir=tmp_path,
        )
        m = d.extract_measurements(sr)
        valid, violations = d.check_validity(m)
        assert not valid
        assert any("Throughput" in v for v in violations)
        assert d.compute_fom(m) == 0.0
