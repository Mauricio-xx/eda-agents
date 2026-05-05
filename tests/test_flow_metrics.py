"""Tests for FlowMetrics dataclass and FlowStage enum."""

import json

import pytest

from eda_agents.core.flow_stage import FlowStage, StageResult
from eda_agents.core.flow_metrics import FlowMetrics


class TestFlowStage:
    def test_stage_ordering(self):
        assert FlowStage.RTL_LINT.value < FlowStage.SYNTH.value
        assert FlowStage.SYNTH.value < FlowStage.ROUTE.value
        assert FlowStage.ROUTE.value < FlowStage.PRECHECK.value

    def test_all_stages_present(self):
        names = [s.name for s in FlowStage]
        assert "RTL_LINT" in names
        assert "SYNTH" in names
        assert "ROUTE" in names
        assert "SIGNOFF_DRC" in names
        assert "PRECHECK" in names
        assert "GL_SIM_POST_PNR" in names
        assert "VERILOGA_COMPILE" in names
        assert "XSPICE_COMPILE" in names
        assert len(names) == 17


class TestStageResult:
    def test_success(self):
        r = StageResult(stage=FlowStage.SYNTH, success=True, run_time_s=10.5)
        assert "OK" in r.summary
        assert "10.5" in r.summary

    def test_failure(self):
        r = StageResult(
            stage=FlowStage.SIGNOFF_DRC,
            success=False,
            error="3 violations",
        )
        assert "FAIL" in r.summary
        assert "3 violations" in r.summary

    def test_metrics_delta(self):
        r = StageResult(
            stage=FlowStage.SYNTH,
            success=True,
            metrics_delta={"design__instance__count": 12201},
        )
        assert r.metrics_delta["design__instance__count"] == 12201


class TestFlowMetrics:
    def test_construction(self):
        m = FlowMetrics(
            synth_cell_count=12201,
            wns_worst_ns=1.407,
            power_total_w=0.05185,
            die_area_um2=256175.0,
        )
        assert m.synth_cell_count == 12201
        assert m.wns_worst_ns == 1.407

    def test_power_mw(self):
        m = FlowMetrics(power_total_w=0.05185)
        assert abs(m.power_total_mw - 51.85) < 0.01

    def test_power_mw_none(self):
        m = FlowMetrics()
        assert m.power_total_mw is None

    def test_drc_total(self):
        m = FlowMetrics(
            klayout_drc_count=2,
            magic_drc_count=1,
            route_drc_errors=0,
        )
        assert m.drc_total == 3

    def test_drc_total_all_none(self):
        m = FlowMetrics()
        assert m.drc_total == 0

    def test_weighted_fom_valid(self):
        m = FlowMetrics(
            wns_worst_ns=1.407,
            die_area_um2=256175.0,
            power_total_w=0.05185,
            clock_period_ns=40.0,
        )
        fom = m.weighted_fom()
        assert fom > 0
        # Timing-met component is binary (1.0 when WNS >= 0); set
        # the other weights to zero to isolate it.
        fom_timing_only = m.weighted_fom(
            timing_w=1.0, perf_w=0.0, area_w=0.0, power_w=0.0,
        )
        assert abs(fom_timing_only - 1.0) < 0.001

    def test_weighted_fom_perf_uses_clock(self):
        """Performance term must scale with achievable frequency. The
        regression here is the bug that let the LLM win by relaxing
        clock to 5000 ns: with the old FoM, slow clocks produced
        a higher score; with the new PPA FoM, a slow clock produces
        a low Performance contribution.
        """
        baseline = FlowMetrics(
            wns_worst_ns=0.28,
            die_area_um2=640000.0,
            power_total_w=0.021,
            clock_period_ns=55.0,
        )
        relaxed = FlowMetrics(
            wns_worst_ns=4900.0,    # huge slack from clock relax
            die_area_um2=640000.0,  # same RTL
            power_total_w=0.0002,   # P scales with f
            clock_period_ns=5000.0,
        )
        baseline_fom = baseline.weighted_fom()
        relaxed_fom = relaxed.weighted_fom()
        # With proper PPA, baseline must beat trivial relax.
        assert baseline_fom > relaxed_fom, (
            f"Expected baseline FoM > relax FoM, got "
            f"baseline={baseline_fom:.2f} vs relaxed={relaxed_fom:.2f}. "
            f"This means the FoM is gameable by clock relaxation, the "
            f"exact regression we introduced the Performance term to "
            f"prevent."
        )

    def test_weighted_fom_missing_wns(self):
        m = FlowMetrics(die_area_um2=256175.0)
        assert m.weighted_fom() == 0.0

    def test_weighted_fom_no_clock_period_skips_perf_and_energy(self):
        """When clock_period_ns is missing the Performance and energy/
        cycle terms drop out; the FoM is still callable and returns a
        positive value driven by area + timing-met."""
        m = FlowMetrics(
            wns_worst_ns=1.0,
            die_area_um2=640000.0,
            power_total_w=0.021,
        )
        fom = m.weighted_fom()
        assert fom > 0
        # Without clock period, perf_score and energy_eff_score are 0.
        # FoM = timing_w*1 + area_w*(1e6/640000) = 0.5 + 1.0*1.5625
        assert abs(fom - (0.5 + 1.0 * 1.5625)) < 1e-6

    def test_validity_check_pass(self):
        m = FlowMetrics(
            wns_worst_ns=1.407,
            drc_clean=True,
            lvs_match=True,
        )
        valid, violations = m.validity_check()
        assert valid
        assert violations == []

    def test_validity_check_fail_timing(self):
        m = FlowMetrics(wns_worst_ns=-0.366)
        valid, violations = m.validity_check()
        assert not valid
        assert len(violations) == 1
        assert "Timing" in violations[0]

    def test_validity_check_fail_drc(self):
        m = FlowMetrics(
            wns_worst_ns=5.0,
            drc_clean=False,
            klayout_drc_count=3,
        )
        valid, violations = m.validity_check()
        assert not valid
        assert any("DRC" in v for v in violations)

    def test_validity_check_fail_lvs(self):
        m = FlowMetrics(wns_worst_ns=5.0, lvs_match=False)
        valid, violations = m.validity_check()
        assert not valid
        assert any("LVS" in v for v in violations)

    def test_validity_check_unknown_is_valid(self):
        # None fields (unknown) should not trigger violations
        m = FlowMetrics()
        valid, violations = m.validity_check()
        assert valid
        assert violations == []

    def test_wns_per_corner(self):
        m = FlowMetrics(
            wns_per_corner={
                "nom_tt_025C_5v00": 19.566,
                "nom_ss_125C_4v50": 2.017,
                "max_ss_125C_4v50": 1.407,
            },
        )
        assert m.wns_per_corner["nom_tt_025C_5v00"] == 19.566
        assert len(m.wns_per_corner) == 3


class TestFlowMetricsFromRunDir:
    @pytest.fixture
    def fake_run_dir(self, tmp_path):
        """Create a minimal LibreLane run dir with state_in.json files."""
        run_dir = tmp_path / "RUN_test"
        run_dir.mkdir()

        # Simulate a few steps with metrics
        synth_step = run_dir / "06-yosys-synthesis"
        synth_step.mkdir()
        (synth_step / "state_in.json").write_text(json.dumps({
            "metrics": {
                "design__instance__count": 12201,
                "design__instance__count__stdcell": 5806,
                "design__die__area": 256175.0,
            }
        }))

        sta_step = run_dir / "56-openroad-stapostpnr"
        sta_step.mkdir()
        (sta_step / "state_in.json").write_text(json.dumps({
            "metrics": {
                "timing__setup__ws": 1.407,
                "timing__setup__ws__corner:nom_tt_025C_5v00": 19.566,
                "timing__setup__ws__corner:max_ss_125C_4v50": 1.407,
                "timing__hold__ws": 0.268,
                "power__total": 0.05185,
                "power__internal__total": 0.03762,
                "power__switching__total": 0.01424,
            }
        }))

        route_step = run_dir / "45-openroad-detailedrouting"
        route_step.mkdir()
        (route_step / "state_in.json").write_text(json.dumps({
            "metrics": {
                "route__wirelength": 155900,
                "route__drc_errors": 0,
                "global_route__wirelength": 245926,
            }
        }))

        drc_step = run_dir / "64-magic-drc"
        drc_step.mkdir()
        (drc_step / "state_in.json").write_text(json.dumps({
            "metrics": {
                "magic__drc_error__count": 0,
                "klayout__drc_error__count": 0,
                "antenna__violating__nets": 0,
            }
        }))

        return run_dir

    def test_from_run_dir(self, fake_run_dir):
        m = FlowMetrics.from_librelane_run_dir(fake_run_dir)
        assert m.synth_cell_count == 12201
        assert m.stdcell_count == 5806
        assert m.die_area_um2 == 256175.0
        assert m.wns_worst_ns == 1.407
        assert m.hold_wns_worst_ns == 0.268
        assert m.power_total_w == 0.05185
        assert m.wire_length_um == 155900
        assert m.klayout_drc_count == 0
        assert m.magic_drc_count == 0
        assert m.antenna_violations == 0

    def test_drc_clean_derived(self, fake_run_dir):
        m = FlowMetrics.from_librelane_run_dir(fake_run_dir)
        assert m.drc_clean is True

    def test_per_corner_wns(self, fake_run_dir):
        m = FlowMetrics.from_librelane_run_dir(fake_run_dir)
        assert "nom_tt_025C_5v00" in m.wns_per_corner
        assert m.wns_per_corner["nom_tt_025C_5v00"] == 19.566
        assert m.wns_per_corner["max_ss_125C_4v50"] == 1.407

    def test_raw_metrics_preserved(self, fake_run_dir):
        m = FlowMetrics.from_librelane_run_dir(fake_run_dir)
        # All metrics from all files should be in raw_metrics
        assert "design__instance__count" in m.raw_metrics
        assert "timing__setup__ws" in m.raw_metrics
        assert "route__wirelength" in m.raw_metrics

    def test_validity_on_parsed(self, fake_run_dir):
        m = FlowMetrics.from_librelane_run_dir(fake_run_dir)
        valid, violations = m.validity_check()
        assert valid
        assert violations == []

    def test_fom_on_parsed(self, fake_run_dir):
        m = FlowMetrics.from_librelane_run_dir(fake_run_dir)
        fom = m.weighted_fom()
        assert fom > 0

    def test_empty_run_dir(self, tmp_path):
        empty = tmp_path / "empty_run"
        empty.mkdir()
        m = FlowMetrics.from_librelane_run_dir(empty)
        assert m.synth_cell_count is None
        assert m.wns_worst_ns is None
        assert m.raw_metrics == {}

    def test_final_metrics_json_preferred(self, tmp_path):
        """If final/metrics.json exists, use it over state_in.json files."""
        run_dir = tmp_path / "RUN_final"
        run_dir.mkdir()
        final_dir = run_dir / "final"
        final_dir.mkdir()

        # final/metrics.json has the RCX-corrected values
        (final_dir / "metrics.json").write_text(json.dumps({
            "design__instance__count": 99999,
            "power__total": 0.060,
        }))

        # This state_in.json should be ignored
        step = run_dir / "06-yosys-synthesis"
        step.mkdir()
        (step / "state_in.json").write_text(json.dumps({
            "metrics": {"design__instance__count": 11111}
        }))

        m = FlowMetrics.from_librelane_run_dir(run_dir)
        # Should use final/metrics.json value, not state_in.json
        assert m.synth_cell_count == 99999
        assert m.power_total_w == 0.060

    def test_clock_period_from_resolved_json(self, tmp_path):
        """``CLOCK_PERIOD`` comes from ``resolved.json``, not from
        ``metrics.json``. LibreLane writes the resolved config (after
        defaults + template merge) to ``resolved.json`` at the run-dir
        root; the per-step ``state_in.json`` files only carry runtime
        metrics, not config values.

        Without this fallback, the PPA FoM degenerates to area +
        timing-met for any run because ``clock_period_ns`` stays
        ``None``. That bug masked the Performance term and let the
        LLM trivially win by relaxing the clock period.
        """
        run_dir = tmp_path / "RUN_clk"
        run_dir.mkdir()
        # resolved.json carries CLOCK_PERIOD
        (run_dir / "resolved.json").write_text(json.dumps({
            "DESIGN_NAME": "demo",
            "CLOCK_PERIOD": 55.0,
            "PL_TARGET_DENSITY_PCT": 50,
        }))
        # final/metrics.json carries everything else but NOT clock period
        final_dir = run_dir / "final"
        final_dir.mkdir()
        (final_dir / "metrics.json").write_text(json.dumps({
            "design__instance__count": 25000,
            "design__die__area": 640000,
            "timing__setup__ws": 0.28,
            "power__total": 0.021,
        }))

        m = FlowMetrics.from_librelane_run_dir(run_dir)
        assert m.clock_period_ns == 55.0
        # PPA FoM components must all be active when clock is known.
        # baseline FoM at 25 MHz, 21 mW, 640k um2 with default weights
        # is ~21 (timing 0.5 + perf 18.18 + area 1.56 + energy 0.866).
        assert m.weighted_fom() > 15.0, (
            f"PPA FoM should be ~21 with clock present, got "
            f"{m.weighted_fom():.2f}. If <5, clock_period_ns is "
            f"likely None and the perf/energy terms are zeroed out, "
            f"which is the regression we are guarding against."
        )

    def test_clock_period_explicit_metric_overrides_resolved(self, tmp_path):
        """If both ``resolved.json`` and ``metrics.json`` carry
        ``CLOCK_PERIOD``, the metrics value wins. Defensive: in
        practice LibreLane only writes it in resolved, but if that
        ever changes we want the runtime-emitted value (closer to
        what the synth actually saw) to take precedence."""
        run_dir = tmp_path / "RUN_clk_both"
        run_dir.mkdir()
        (run_dir / "resolved.json").write_text(json.dumps({
            "CLOCK_PERIOD": 100.0,  # config value
        }))
        final_dir = run_dir / "final"
        final_dir.mkdir()
        (final_dir / "metrics.json").write_text(json.dumps({
            "CLOCK_PERIOD": 55.0,  # actual run-time value
        }))

        m = FlowMetrics.from_librelane_run_dir(run_dir)
        assert m.clock_period_ns == 55.0  # metrics wins
