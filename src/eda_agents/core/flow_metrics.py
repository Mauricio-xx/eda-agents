"""Typed flow metrics for digital RTL-to-GDS results.

Wraps the flat metric dict produced by LibreLaneMetricsParser into a
typed dataclass with named fields, a weighted FoM computation, and a
validity gate.  Field names and semantics are grounded in Phase 0
observations on GF180MCU / fazyrv-hachure (see
``docs/digital_flow_field_notes.md`` sections 4.3 and 5.2).

The canonical data source is ``final/metrics.json`` or the accumulated
``state_in.json`` chain from a LibreLane run directory.  Per-corner
``.rpt`` files may differ for power (pre- vs post-RCX); this class
uses the RCX-corrected final values.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from eda_agents.core.flow_stage import FlowStage

logger = logging.getLogger(__name__)

# Mapping from FlowMetrics field -> LibreLane metric key(s).
# Used by from_librelane_run_dir to populate typed fields from the
# flat metrics dict.  When a field maps to multiple candidate keys,
# the first found wins.
_KEY_MAP: dict[str, list[str]] = {
    "synth_cell_count": ["design__instance__count"],
    "stdcell_count": ["design__instance__count__stdcell"],
    "fill_cell_count": ["design__instance__count__class:fill_cell"],
    "die_area_um2": ["design__die__area"],
    "stdcell_area_um2": ["design__instance__area__stdcell"],
    "utilization_pct": ["design__instance__utilization"],
    "wns_worst_ns": ["timing__setup__ws"],
    "tns_worst_ns": ["timing__setup__tns"],
    "hold_wns_worst_ns": ["timing__hold__ws"],
    "power_total_w": ["power__total"],
    "power_internal_w": ["power__internal__total"],
    "power_switching_w": ["power__switching__total"],
    "wire_length_um": ["route__wirelength"],
    "gr_wire_length_um": ["global_route__wirelength"],
    "route_drc_errors": ["route__drc_errors"],
    "klayout_drc_count": ["klayout__drc_error__count"],
    "magic_drc_count": ["magic__drc_error__count"],
    "antenna_violations": ["antenna__violating__nets"],
    "clock_period_ns": ["CLOCK_PERIOD"],
}


@dataclass
class FlowMetrics:
    """Typed container for digital flow metrics.

    All numeric fields default to ``None`` (unknown / not extracted).
    Boolean fields default to ``None`` as well, indicating the check
    was not run.
    """

    # Synthesis / instance counts
    synth_cell_count: int | None = None
    stdcell_count: int | None = None
    fill_cell_count: int | None = None

    # Area
    die_area_um2: float | None = None
    stdcell_area_um2: float | None = None
    utilization_pct: float | None = None

    # Timing (setup)
    wns_worst_ns: float | None = None
    tns_worst_ns: float | None = None
    # Per-corner WNS: {"nom_tt_025C_5v00": 19.566, "max_ss_125C_4v50": 1.407, ...}
    wns_per_corner: dict[str, float] = field(default_factory=dict)

    # Timing (hold)
    hold_wns_worst_ns: float | None = None

    # Power (post-RCX final values, in watts)
    power_total_w: float | None = None
    power_internal_w: float | None = None
    power_switching_w: float | None = None

    # Routing
    wire_length_um: float | None = None
    gr_wire_length_um: float | None = None

    # DRC
    route_drc_errors: int | None = None
    klayout_drc_count: int | None = None
    magic_drc_count: int | None = None
    drc_clean: bool | None = None

    # LVS
    lvs_match: bool | None = None

    # Antenna
    antenna_violations: int | None = None

    # Clock
    clock_period_ns: float | None = None

    # Stage completion status
    stage_status: dict[FlowStage, bool] = field(default_factory=dict)

    # Raw metrics dict (for debugging / full access)
    raw_metrics: dict[str, float | int] = field(
        default_factory=dict, repr=False
    )

    @property
    def power_total_mw(self) -> float | None:
        """Total power in milliwatts (convenience)."""
        if self.power_total_w is None:
            return None
        return self.power_total_w * 1000.0

    @property
    def drc_total(self) -> int:
        """Sum of all DRC error counts (0 if all are None)."""
        total = 0
        for v in (self.route_drc_errors, self.klayout_drc_count,
                  self.magic_drc_count):
            if v is not None:
                total += v
        return total

    def weighted_fom(
        self,
        timing_w: float = 1.0,
        area_w: float = 0.5,
        power_w: float = 0.3,
    ) -> float:
        """Compute a weighted figure of merit.

        Higher is better.  Returns 0.0 if essential metrics are missing.

        Components:
        - Timing: WNS in ns (worst corner).  Negative = penalty.
        - Area: inverse of die area (smaller is better).
        - Power: inverse of total power (lower is better).

        Each component is normalized and weighted.  The formula is
        intentionally simple — ``DigitalDesign.compute_fom()`` can
        override with a design-specific formula.
        """
        if self.wns_worst_ns is None:
            return 0.0

        # Timing component: positive WNS is good, negative is bad
        timing_score = self.wns_worst_ns

        # Area component: normalized inverse (1e6 / area).
        # 256k um2 -> ~3.9, smaller area -> higher score
        area_score = 0.0
        if self.die_area_um2 and self.die_area_um2 > 0:
            area_score = 1e6 / self.die_area_um2

        # Power component: normalized inverse (1 / power_w).
        # 0.052 W -> ~19.2, lower power -> higher score
        power_score = 0.0
        if self.power_total_w and self.power_total_w > 0:
            power_score = 1.0 / self.power_total_w

        return (
            timing_w * timing_score
            + area_w * area_score
            + power_w * power_score
        )

    def validity_check(self) -> tuple[bool, list[str]]:
        """Check whether these metrics represent a valid design.

        Returns (valid, list_of_violations).  A design with negative
        WNS at the worst corner is invalid (timing not closed).
        """
        violations: list[str] = []

        if self.wns_worst_ns is not None and self.wns_worst_ns < 0:
            violations.append(
                f"Timing not closed: WNS worst corner = {self.wns_worst_ns:.3f} ns"
            )

        if self.drc_clean is False:
            violations.append(
                f"DRC not clean: {self.drc_total} total errors"
            )

        if self.lvs_match is False:
            violations.append("LVS mismatch")

        return (len(violations) == 0, violations)

    @classmethod
    def from_librelane_run_dir(cls, run_dir: Path | str) -> FlowMetrics:
        """Build FlowMetrics from a LibreLane run directory.

        Scans ``state_in.json`` files in the run directory, merges all
        metrics, and maps them to typed fields.  This delegates the
        file-scanning logic to the same pattern used by
        ``LibreLaneMetricsParser`` but produces a typed object instead
        of markdown.

        Parameters
        ----------
        run_dir : Path
            Path to a LibreLane run directory (e.g.
            ``macros/frv_1/runs/RUN_2026-04-11_23-15-24``).
        """
        run_dir = Path(run_dir)
        all_metrics = _collect_metrics(run_dir)

        kwargs: dict = {"raw_metrics": dict(all_metrics)}

        # Map known keys to typed fields
        for field_name, candidate_keys in _KEY_MAP.items():
            for key in candidate_keys:
                if key in all_metrics:
                    kwargs[field_name] = all_metrics[key]
                    break

        # Per-corner WNS
        wns_corners: dict[str, float] = {}
        for k, v in all_metrics.items():
            if k.startswith("timing__setup__ws__corner:"):
                corner = k.split(":", 1)[1]
                wns_corners[corner] = float(v)
        if wns_corners:
            kwargs["wns_per_corner"] = wns_corners

        # Derive drc_clean from counts
        klayout = all_metrics.get("klayout__drc_error__count")
        magic = all_metrics.get("magic__drc_error__count")
        route_drc = all_metrics.get("route__drc_errors")
        if klayout is not None or magic is not None:
            total = (klayout or 0) + (magic or 0) + (route_drc or 0)
            kwargs["drc_clean"] = total == 0

        return cls(**kwargs)


def _collect_metrics(run_dir: Path) -> dict[str, float | int]:
    """Merge all metrics from state_in.json files in a run directory."""
    all_metrics: dict[str, float | int] = {}

    # Try final/metrics.json first (most complete, post-RCX)
    final_metrics = run_dir / "final" / "metrics.json"
    if final_metrics.is_file():
        try:
            data = json.loads(final_metrics.read_text())
            if isinstance(data, dict):
                # final/metrics.json is a flat dict of metrics
                all_metrics.update(data)
                return all_metrics
        except (json.JSONDecodeError, OSError):
            pass

    # Fall back to scanning state_in.json files
    for f in sorted(run_dir.rglob("state_in.json")):
        try:
            data = json.loads(f.read_text())
            metrics = data.get("metrics")
            if isinstance(metrics, dict):
                all_metrics.update(metrics)
        except (json.JSONDecodeError, OSError):
            continue

    return all_metrics
