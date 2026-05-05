"""Default ``DigitalDesign.extract_measurements`` contract tests.

The ABC default reproduces the five PPA columns the runner emitted
before the dict-based API switch. Designs that don't override
``measurement_columns`` / ``extract_measurements`` keep producing the
same TSV schema as before, which is the zero-regression guarantee for
``GenericDesign`` / ``FazyRvHachureDesign`` / ``SystolicMacDftDesign``.
"""

from __future__ import annotations

from pathlib import Path

from eda_agents.core.digital_design import (
    DEFAULT_PPA_COLUMNS,
    DigitalDesign,
    TestbenchSpec,
)
from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.stage_results import StageResults


class _MinimalDesign(DigitalDesign):
    """Concrete subclass that exercises ABC defaults only."""

    def project_name(self):
        return "minimal"

    def specification(self):
        return ""

    def design_space(self):
        return {}

    def flow_config_overrides(self):
        return {}

    def project_dir(self):
        return Path("/tmp")

    def librelane_config(self):
        return Path("/tmp/config.yaml")

    def compute_fom(self, measurements):
        return 0.0

    def check_validity(self, measurements):
        return True, []

    def prompt_description(self):
        return ""

    def design_vars_description(self):
        return ""

    def specs_description(self):
        return ""

    def fom_description(self):
        return ""

    def reference_description(self):
        return ""

    def testbench(self):
        return TestbenchSpec(driver="cocotb", target="sim")


def test_default_columns_match_canonical_tuple():
    d = _MinimalDesign()
    cols = d.measurement_columns()
    assert cols == list(DEFAULT_PPA_COLUMNS)
    assert len(cols) == 5


def test_default_columns_returns_list_not_tuple():
    """``measurement_columns`` returns ``list[str]`` for downstream
    consumers (TSV header builder, runner) that mutate the result."""
    d = _MinimalDesign()
    cols = d.measurement_columns()
    assert isinstance(cols, list)


def test_extract_measurements_from_flow_metrics():
    d = _MinimalDesign()
    metrics = FlowMetrics(
        wns_worst_ns=1.5,
        synth_cell_count=12_000,
        die_area_um2=250_000.0,
        power_total_w=0.052,
        wire_length_um=180_000.0,
    )
    sr = StageResults(
        eval_idx=1, params={}, work_dir=Path("/tmp"),
        flow_metrics=metrics,
    )
    measurements = d.extract_measurements(sr)
    assert measurements == {
        "wns_worst_ns": 1.5,
        "cell_count": 12_000,
        "die_area_um2": 250_000.0,
        "power_mw": 52.0,            # converted W -> mW
        "wire_length_um": 180_000.0,
    }


def test_extract_measurements_handles_missing_flow_metrics():
    d = _MinimalDesign()
    sr = StageResults(eval_idx=1, params={}, work_dir=Path("/tmp"))
    measurements = d.extract_measurements(sr)
    # When LibreLane didn't produce metrics, every PPA key maps to
    # ``None`` so the TSV row stays the same shape.
    assert measurements == {col: None for col in DEFAULT_PPA_COLUMNS}


def test_extract_measurements_handles_partial_flow_metrics():
    """A flow that only ran through synthesis still yields cell_count;
    the rest of the columns map to ``None`` cleanly."""
    d = _MinimalDesign()
    metrics = FlowMetrics(synth_cell_count=12_000)
    sr = StageResults(
        eval_idx=1, params={}, work_dir=Path("/tmp"),
        flow_metrics=metrics,
    )
    measurements = d.extract_measurements(sr)
    assert measurements["cell_count"] == 12_000
    assert measurements["wns_worst_ns"] is None
    assert measurements["power_mw"] is None
