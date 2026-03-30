"""Structured result dataclasses for multi-phase Track D flow.

Each phase of the Track D orchestrator produces a typed result
that downstream phases and the final report can consume without
parsing LLM text output.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExplorationResult:
    """Result from a design exploration phase (sizing optimization)."""

    best_params: dict[str, float]
    best_fom: float
    best_valid: bool
    all_evals: list[dict] = field(default_factory=list)
    agent_summary: str = ""

    @property
    def n_evals(self) -> int:
        return len(self.all_evals)

    @property
    def n_valid(self) -> int:
        return sum(1 for e in self.all_evals if e.get("valid"))

    @property
    def validity_rate(self) -> float:
        return self.n_valid / self.n_evals if self.n_evals else 0.0


@dataclass
class FlowResult:
    """Result from an RTL-to-GDS hardening flow (LibreLane/ORFS)."""

    success: bool
    gds_path: str | None = None
    def_path: str | None = None
    netlist_path: str | None = None
    timing_met: bool | None = None
    drc_clean: bool | None = None
    run_dir: str = ""
    run_time_s: float = 0.0
    error: str | None = None
    log_tail: str = ""

    @property
    def summary(self) -> str:
        if self.error:
            return f"Flow failed: {self.error}"
        parts = []
        if self.gds_path:
            parts.append("GDS generated")
        if self.timing_met is not None:
            parts.append(f"timing {'met' if self.timing_met else 'VIOLATED'}")
        if self.drc_clean is not None:
            parts.append(f"DRC {'clean' if self.drc_clean else 'dirty'}")
        return f"Flow: {', '.join(parts) or 'completed'} ({self.run_time_s:.0f}s)"


@dataclass
class DRCResult:
    """Result from DRC analysis and fix loop."""

    total_violations: int
    violated_rules: dict[str, int] = field(default_factory=dict)
    clean: bool = False
    report_path: str | None = None
    fixes_applied: list[dict] = field(default_factory=list)
    iterations: int = 0

    @property
    def summary(self) -> str:
        if self.clean:
            return f"DRC clean after {self.iterations} iteration(s)"
        top = sorted(self.violated_rules.items(), key=lambda x: x[1], reverse=True)[:5]
        rules_str = ", ".join(f"{r}({c})" for r, c in top)
        return (
            f"DRC: {self.total_violations} violations across "
            f"{len(self.violated_rules)} rules after {self.iterations} "
            f"iteration(s). Top: {rules_str}"
        )


@dataclass
class AutoresearchResult:
    """Result from an autoresearch autonomous exploration loop."""

    best_params: dict[str, float]
    best_fom: float
    best_valid: bool
    total_evals: int
    kept: int
    discarded: int
    top_n: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    tsv_path: str = ""

    @property
    def improvement_rate(self) -> float:
        return self.kept / self.total_evals if self.total_evals else 0.0

    @property
    def validity_rate(self) -> float:
        n_valid = sum(1 for h in self.history if h.get("valid"))
        return n_valid / self.total_evals if self.total_evals else 0.0

    @property
    def summary(self) -> str:
        return (
            f"Autoresearch: {self.total_evals} evals, "
            f"{self.kept} kept, best FoM={self.best_fom:.2e}, "
            f"valid={self.best_valid}"
        )


@dataclass
class LVSResult:
    """Result from layout-vs-schematic comparison."""

    match: bool
    mismatches: int = 0
    report_path: str | None = None
    extracted_netlist_path: str | None = None

    @property
    def summary(self) -> str:
        if self.match:
            return "LVS: match"
        return f"LVS: MISMATCH ({self.mismatches} differences)"
