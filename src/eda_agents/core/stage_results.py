"""Per-eval stage outputs aggregated for ``DigitalDesign`` consumption.

The autoresearch runner builds a :class:`StageResults` instance from
the artefacts produced by one evaluation (LibreLane run, optional RTL
lint/sim gates, optional GL sim) and hands it to
:meth:`DigitalDesign.extract_measurements`. Designs read whatever they
need from this bag — LibreLane metrics, cocotb sidecar artefacts, lint
warnings, the RTL diff actually applied — without forcing the runner
to know which fields the FoM cares about.

Every per-stage field defaults to ``None`` so a design that only looks
at ``flow_metrics`` keeps working even when a hybrid eval skipped one
of the gates. ``extras`` is the open-ended slot a design can populate
from a custom post-flow stage (e.g. SPICE post-PEX) without forcing a
runner edit.

The class is :class:`frozen <dataclasses.dataclass>` to make it clear
the bag is a read-only view from the design's perspective; the runner
constructs a fresh one per eval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eda_agents.core.flow_metrics import FlowMetrics
    from eda_agents.core.flow_stage import StageResult


@dataclass(frozen=True)
class StageResults:
    """Bag of per-eval stage outputs handed to a digital design.

    Parameters
    ----------
    eval_idx
        1-based index of the evaluation in the autoresearch budget.
    params
        The flow-config / design-space parameters proposed for this
        eval. Useful when a design needs the input to compute a
        derived measurement (e.g. throughput from CLOCK_PERIOD plus
        a measured cycles-per-sample).
    work_dir
        Per-eval scratch directory. Designs that need to drop temp
        files for their own measurement steps should write under this
        path.
    flow_metrics
        Typed LibreLane metrics for the eval (post-RCX values), or
        ``None`` when LibreLane did not produce a usable run dir
        (early failure, mock path with insufficient data).
    run_dir
        Absolute path to the LibreLane run directory (e.g.
        ``.../macros/frv_1/runs/RUN_2026-04-...``), or ``None`` if no
        run was produced.
    rtl_lint
        Result of the pre-LibreLane RTL lint gate (hybrid / RTL
        strategies). ``None`` when not run.
    rtl_sim
        Result of the pre-LibreLane RTL sim gate. ``None`` when the
        design has no testbench or the gate is disabled. The
        ``StageResult.artifacts`` slot is the canonical place for
        cocotb sidecar files like ``meas.json``.
    gl_sim_post_synth, gl_sim_post_pnr
        Optional post-LibreLane gate-level sim outcomes
        (``{"success", "error", "log_tail", "run_time_s",
        "sdf_warnings"?}``). ``None`` when GL sim is not applicable
        for the PDK / design.
    rtl_changes
        For the hybrid path, the RTL diff actually applied this
        eval, keyed by repo-relative path. ``None`` for flow-only
        evals.
    extras
        Free-form bucket for design-specific post-flow measurements
        the runner does not know about. Mutable on construction
        (the runner can accumulate into this dict before freezing
        the dataclass; in practice it is populated at construction
        time and then the bag is treated as read-only).
    """

    eval_idx: int
    params: dict[str, float | int | str]
    work_dir: Path

    flow_metrics: "FlowMetrics | None" = None
    run_dir: Path | None = None

    rtl_lint: "StageResult | None" = None
    rtl_sim: "StageResult | None" = None

    gl_sim_post_synth: dict | None = None
    gl_sim_post_pnr: dict | None = None

    rtl_changes: dict[str, str] | None = None

    extras: dict[str, object] = field(default_factory=dict)
