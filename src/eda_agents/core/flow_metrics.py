"""Typed flow metrics for digital RTL-to-GDS results.

Wraps the flat metric dict produced by LibreLaneMetricsParser into a
typed dataclass with named fields, a weighted FoM computation, and a
validity gate. Field names and semantics are grounded in Phase 0
observations from the bring-up educational designs that calibrated
the digital pipeline (see ``docs/digital_flow_field_notes.md``
sections 4.3 and 5.2 and :data:`GF180_EDUCATIONAL` for the canonical
calibration assumptions). New design classes whose normalisation
constants differ materially (e.g. kHz IoT, large GPUs) should declare
their own :class:`PpaProfile` rather than re-tuning the shipped
defaults.

The canonical data source is ``final/metrics.json`` or the accumulated
``state_in.json`` chain from a LibreLane run directory. Per-corner
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


@dataclass(frozen=True)
class PpaProfile:
    """Named PPA weight profile for :meth:`FlowMetrics.weighted_fom`.

    A profile bundles the four PPA pulls (timing, performance, area,
    power) so the call site declares which calibration class the FoM
    is using rather than carrying loose magic numbers. Define a new
    profile when a design class with materially different scaling
    (clock target, area target, power budget) is introduced; the
    discoverable profile name is the entry point for understanding
    what the FoM is optimising for.
    """

    timing_w: float
    perf_w: float
    area_w: float
    power_w: float
    name: str = ""


GF180_EDUCATIONAL = PpaProfile(
    timing_w=0.5,
    perf_w=1.0,
    area_w=1.0,
    power_w=1.0,
    name="GF180_EDUCATIONAL",
)
"""Profile carried by the bring-up educational designs that brought up
the digital pipeline. Roughly equal pull on timing-met (0.5), MHz
performance (1.0), inverse die area (1.0), and inverse energy-per-cycle
(1.0); see ``PpaProfile`` for the formula. Suitable for designs whose
performance signal is dominated by raw MHz throughput within the few-MHz
to tens-of-MHz range.

Calibration notes (GF180MCU 5V0 stdcells, the bring-up corner):
  - Baseline designs land at 5-50 MHz, so the ``1000 / period_ns``
    perf score sits in [20, 200].
  - The ``1e6 / area_um2`` area normalisation puts a 640k-um2 design
    at ~1.5, comparable to the timing-met bonus.
  - Energy per cycle for a 25k-cell GF180 design is ~1 nJ, so the
    ``1 / (P * period)`` term is ~1 - roughly clock-invariant by
    construction.

A new profile should redo this calibration so all four terms are
commensurate at the new design's operating point; otherwise one
component swamps the FoM."""

LOW_POWER_KHZ = PpaProfile(
    timing_w=1.0,
    perf_w=0.0,
    area_w=0.5,
    power_w=2.0,
    name="LOW_POWER_KHZ",
)
"""Profile for kHz-class IoT controllers where MHz frequency is irrelevant
once timing is met. ``perf_w`` is zero so the formula does not reward
clock speed beyond the timing-met binary; ``power_w`` is doubled so
energy-per-cycle is the dominant FoM driver."""


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
        profile: PpaProfile,
        *,
        timing_w: float | None = None,
        perf_w: float | None = None,
        area_w: float | None = None,
        power_w: float | None = None,
    ) -> float:
        """Compute a weighted figure of merit (PPA: Performance, Power, Area).

        Higher is better. Returns 0.0 if essential metrics are missing.

        The four PPA pulls are taken from ``profile`` unless the caller
        overrides any of them via the keyword arguments. Overrides act
        per-key: passing ``timing_w`` only replaces ``profile.timing_w``;
        the other three weights still come from the profile. Designs
        whose FoM target differs materially from any shipped profile
        should declare a new :class:`PpaProfile` rather than re-tuning
        weights ad-hoc, so the calibration intent stays discoverable.

        Components (all normalized to roughly the same magnitude so that
        none dominates by accident):

        * **Timing-met**: binary 1.0 / 0.0. ``check_validity`` already
          rejects WNS < 0 designs, so by the time the FoM is computed
          the timing is met. Excessive slack is NOT rewarded because
          that would reduce to "relax clock to win", which gamed an
          earlier formula. The Performance term carries the actual
          frequency.
        * **Performance**: achievable frequency in MHz at the synth
          clock period. ``1000 / clock_period_ns``; this term carries
          the throughput signal that a relax-everything optimizer
          used to evade.
        * **Area**: inverse die area, ``1e6 / area_um2``. Roughly
          unitless.
        * **Power**: inverse energy-per-cycle, ``1 / (power_W * period_ns)``
          (units: 1 / nJ-per-cycle). Power scales linearly with
          frequency in synchronous logic, so plain ``1/power`` rewarded
          slow clocks trivially. Energy per cycle is roughly clock-
          invariant for a fixed RTL, which is what we want: the FoM
          rewards genuine power improvements (smaller cells, lower-
          leakage VTs, clock gating), not clock relaxation.

            FoM = timing_w * timing_met + perf_w * freq_MHz
                + area_w * area_score + power_w * energy_eff_score
        """
        if self.wns_worst_ns is None:
            return 0.0

        timing_w_used = timing_w if timing_w is not None else profile.timing_w
        perf_w_used = perf_w if perf_w is not None else profile.perf_w
        area_w_used = area_w if area_w is not None else profile.area_w
        power_w_used = power_w if power_w is not None else profile.power_w

        # Timing met (binary): 1.0 if WNS >= 0, 0.0 otherwise.
        # ``check_validity`` already gates WNS < 0 -> FoM=0 in
        # ``GenericDesign.compute_fom``, so this is mostly a defensive
        # echo of the same gate when the FoM is called directly.
        timing_score = 1.0 if self.wns_worst_ns >= 0 else 0.0

        # Performance: achievable frequency in MHz. The synth clock
        # period IS the achievable period (positive WNS means there is
        # additional headroom we are not exploiting yet).
        perf_score = 0.0
        if self.clock_period_ns and self.clock_period_ns > 0:
            perf_score = 1000.0 / self.clock_period_ns

        # Area: inverse area, scaled.
        area_score = 0.0
        if self.die_area_um2 and self.die_area_um2 > 0:
            area_score = 1e6 / self.die_area_um2

        # Energy efficiency: 1 / energy-per-cycle (in nJ).
        # nJ_per_cycle = power_W * period_ns. That product is
        # roughly constant for a fixed RTL across clock periods
        # (P scales linearly with f in CMOS), so this term is NOT
        # gameable by clock relaxation alone.
        energy_eff_score = 0.0
        if (
            self.power_total_w
            and self.power_total_w > 0
            and self.clock_period_ns
            and self.clock_period_ns > 0
        ):
            nj_per_cycle = self.power_total_w * self.clock_period_ns
            energy_eff_score = 1.0 / nj_per_cycle

        return (
            timing_w_used * timing_score
            + perf_w_used * perf_score
            + area_w_used * area_score
            + power_w_used * energy_eff_score
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
    def from_measurements(
        cls, measurements: dict[str, float | int | bool | None]
    ) -> FlowMetrics:
        """Build a partial :class:`FlowMetrics` from a measurements dict.

        Inverse of the default :meth:`DigitalDesign.extract_measurements`:
        accepts the five PPA keys (and a small set of optional ones)
        and returns a populated dataclass that
        :meth:`weighted_fom` and :meth:`validity_check` can consume.

        Used by digital design subclasses so the dict-based
        ``compute_fom`` signature can stay a one-liner that delegates
        to the existing PPA helper without re-implementing the formula.

        Recognized keys (others are silently ignored):

        * ``wns_worst_ns`` -> ``wns_worst_ns``
        * ``cell_count`` -> ``synth_cell_count``
        * ``die_area_um2`` -> ``die_area_um2``
        * ``power_mw`` -> ``power_total_w`` (converted mW -> W)
        * ``wire_length_um`` -> ``wire_length_um``
        * ``clock_period_ns`` -> ``clock_period_ns``
        * ``drc_clean`` -> ``drc_clean``
        * ``lvs_match`` -> ``lvs_match``
        """
        kwargs: dict = {}
        for key, target in (
            ("wns_worst_ns", "wns_worst_ns"),
            ("cell_count", "synth_cell_count"),
            ("die_area_um2", "die_area_um2"),
            ("wire_length_um", "wire_length_um"),
            ("clock_period_ns", "clock_period_ns"),
            ("drc_clean", "drc_clean"),
            ("lvs_match", "lvs_match"),
        ):
            if key in measurements and measurements[key] is not None:
                kwargs[target] = measurements[key]
        # power_mw is the runner-facing key; FlowMetrics stores watts.
        if measurements.get("power_mw") is not None:
            kwargs["power_total_w"] = float(measurements["power_mw"]) / 1000.0
        return cls(**kwargs)

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

        # ``resolved.json`` holds the LibreLane-resolved config (the
        # config that the run actually used after merging defaults +
        # template + overrides). LibreLane does not echo
        # ``CLOCK_PERIOD`` (or other config values) into the run-time
        # ``metrics.json``, so we have to source them from
        # ``resolved.json`` to get the achievable-frequency signal in
        # ``FlowMetrics.clock_period_ns``. Without this, the
        # PPA-style FoM falls back to ``area + timing-met`` and the
        # LLM can game it by relaxing clock without paying any
        # Performance penalty.
        resolved = run_dir / "resolved.json"
        if resolved.is_file():
            try:
                cfg = json.loads(resolved.read_text())
                if isinstance(cfg, dict) and "CLOCK_PERIOD" in cfg:
                    all_metrics.setdefault("CLOCK_PERIOD", cfg["CLOCK_PERIOD"])
            except (json.JSONDecodeError, OSError):
                pass

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
