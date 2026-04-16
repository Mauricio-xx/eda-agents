"""Flow stage definitions for digital RTL-to-GDS pipelines.

Enumerates the discrete stages of a hardening flow and provides a
typed result container for per-stage outcomes.  Used by stage runners
(Phase 2), the DigitalAutoresearchRunner (Phase 3), and ADK sub-agents
(Phase 4) to communicate progress and metrics through the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path


class FlowStage(Enum):
    """Ordered stages of a digital RTL-to-GDS flow.

    The integer values reflect typical execution order but are not
    guaranteed to be contiguous — stages may be skipped depending on
    the flow configuration.
    """

    RTL_LINT = auto()
    RTL_SIM = auto()
    RTL_FORMAL = auto()         # no runner in milestone 1
    SYNTH = auto()
    POST_SYNTH_SIM = auto()
    POST_SYNTH_STA = auto()
    FLOORPLAN = auto()
    PLACE = auto()
    CTS = auto()
    ROUTE = auto()
    SIGNOFF_DRC = auto()
    SIGNOFF_LVS = auto()
    SIGNOFF_STA = auto()
    GL_SIM_POST_PNR = auto()    # post-route GL sim with SDF annotation
    PRECHECK = auto()
    VERILOGA_COMPILE = auto()   # Verilog-A -> OSDI compilation (openvaf)
    XSPICE_COMPILE = auto()     # XSPICE code model (.cm) compilation (cmpp + cc)


@dataclass
class StageResult:
    """Outcome of running a single flow stage.

    Parameters
    ----------
    stage : FlowStage
        Which stage produced this result.
    success : bool
        Whether the stage completed without fatal errors.
    metrics_delta : dict[str, float]
        Metrics produced or updated by this stage (e.g. cell count
        after synthesis, WNS after STA).  Keys follow LibreLane
        ``state_in.json`` naming where applicable.
    artifacts : dict[str, Path]
        Named output files (e.g. ``{"gds": Path("..."), "def": ...}``).
    log_tail : str
        Last ~50 lines of the stage log for diagnostics.
    run_time_s : float
        Wall-clock seconds for this stage.
    error : str or None
        Human-readable error description if ``success`` is False.
    """

    stage: FlowStage
    success: bool
    metrics_delta: dict[str, float] = field(default_factory=dict)
    artifacts: dict[str, Path] = field(default_factory=dict)
    log_tail: str = ""
    run_time_s: float = 0.0
    error: str | None = None

    @property
    def summary(self) -> str:
        status = "OK" if self.success else f"FAIL: {self.error or 'unknown'}"
        return f"{self.stage.name}: {status} ({self.run_time_s:.1f}s)"
