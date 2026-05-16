"""Structured result dataclasses for multi-phase Track D flow.

Each phase of the Track D orchestrator produces a typed result
that downstream phases and the final report can consume without
parsing LLM text output.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Manifest schema version emitted by ``PostLayoutResult.to_package``.
# Bump when an incompatible change ships (renamed/removed fields).
_PACKAGE_SCHEMA_VERSION = "1.0"


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
    # Sum of ``total_tokens`` reported by the LLM backend across every
    # proposal call made during the run. Stays 0 when no LLM is
    # involved (mock metrics, analytical callables) or when the
    # backend does not populate ``response.usage`` — callers should
    # treat a 0 as "not measured" rather than "zero tokens used".
    total_tokens: int = 0

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
class PostLayoutResult:
    """Result from the full post-layout validation pipeline.

    Tracks pre-layout vs post-layout performance deltas to quantify
    the impact of parasitics on circuit performance.
    """

    params: dict[str, float] = field(default_factory=dict)
    pre_layout_fom: float = 0.0

    # Provenance (filled by the validator from its topology binding)
    pdk: str | None = None
    topology: str | None = None

    # Layout
    gds_path: str | None = None
    netlist_path: str | None = None

    # DRC
    drc_clean: bool = False
    drc_violations: int = 0
    drc_report_path: str | None = None

    # LVS
    lvs_match: bool = False
    lvs_report_path: str | None = None

    # PEX
    extracted_netlist_path: str | None = None
    pex_corner: str = "ngspice()"

    # Post-layout SPICE results
    post_Adc_dB: float | None = None
    post_GBW_Hz: float | None = None
    post_PM_deg: float | None = None
    post_fom: float = 0.0
    post_valid: bool = False
    post_sim_dir: str | None = None

    # Pre-layout artifacts (caller-provided pointer, not always populated)
    pre_sim_dir: str | None = None

    # Baseline (gLayout schematic, no parasitics) -- overlay path only
    baseline_Adc_dB: float | None = None
    baseline_GBW_Hz: float | None = None
    baseline_PM_deg: float | None = None
    baseline_fom: float = 0.0
    baseline_valid: bool = False

    # Deltas (post - pre)
    gain_delta_dB: float = 0.0
    gbw_delta_pct: float = 0.0
    pm_delta_deg: float = 0.0
    fom_delta_pct: float = 0.0

    # Timing
    total_time_s: float = 0.0
    error: str | None = None

    @property
    def summary(self) -> str:
        if self.error:
            return f"Post-layout error: {self.error}"
        parts = []
        if self.gds_path:
            parts.append(f"DRC={'clean' if self.drc_clean else f'{self.drc_violations} viols'}")
            parts.append(f"LVS={'match' if self.lvs_match else 'MISMATCH'}")
        if self.baseline_Adc_dB is not None and self.post_Adc_dB is not None:
            delta = self.post_Adc_dB - self.baseline_Adc_dB
            parts.append(
                f"Adc={self.post_Adc_dB:.1f}dB(base={self.baseline_Adc_dB:.1f},d={delta:+.1f})"
            )
        elif self.post_Adc_dB is not None:
            parts.append(f"Adc={self.post_Adc_dB:.1f}dB(d={self.gain_delta_dB:+.1f})")
        if self.post_GBW_Hz is not None:
            parts.append(f"GBW={self.post_GBW_Hz/1e6:.2f}MHz(d={self.gbw_delta_pct:+.1f}%)")
        if self.post_PM_deg is not None:
            parts.append(f"PM={self.post_PM_deg:.1f}deg(d={self.pm_delta_deg:+.1f})")
        parts.append(f"FoM d={self.fom_delta_pct:+.1f}%")
        return f"Post-layout: {', '.join(parts)}"

    def to_package(
        self,
        dst: Path | str,
        *,
        copy_artifacts: bool = True,
        sim_artifact_globs: tuple[str, ...] = ("*.cir", "*.spice", "*.meas", "*.log"),
    ) -> Path:
        """Bundle this result into a standardised flat package directory.

        Layout produced under ``dst``::

            manifest.json
            layout.gds              (when self.gds_path is set)
            schematic.spice         (when self.netlist_path is set)
            extracted.spice         (when self.extracted_netlist_path is set)
            drc_report.lyrdb        (when self.drc_report_path is set)
            lvs_report.lvsdb        (when self.lvs_report_path is set)
            pre_sim/<glob matches>  (when self.pre_sim_dir is set)
            post_sim/<glob matches> (when self.post_sim_dir is set)

        Manifest schema mirrors CABAgent ``bench_gen.create_pkg``
        (``param``, ``layout``, ``extract``, ``drc``, ``lvs``,
        ``pre-sim``, ``post-sim``) for cross-compatibility and adds
        eda-agents fields: ``pdk``, ``topology``, ``params``,
        ``pre_layout_fom``, ``post_layout_fom``, ``deltas``,
        ``pex_corner``, ``drc_clean``, ``lvs_match``, ``drc_violations``,
        ``total_time_s``, ``error``.

        Parameters
        ----------
        dst : Path or str
            Destination directory. Created if missing. Existing files
            with colliding names are overwritten.
        copy_artifacts : bool
            ``True`` (default) copies source artifacts; ``False`` moves
            them. Default is copy because move cannibalises the
            validator work_dir; only flip to move when packaging is the
            final step of a pipeline whose work_dir is disposable.
        sim_artifact_globs : tuple of str
            Glob patterns harvested from ``pre_sim_dir`` /
            ``post_sim_dir`` when those are set. Default keeps cir, raw
            spice, .meas, and .log files; non-matching files (huge raw
            wave dumps, scratch dirs) are skipped.

        Returns
        -------
        Path
            The destination directory.
        """
        dst = Path(dst)
        dst.mkdir(parents=True, exist_ok=True)

        action = shutil.copy2 if copy_artifacts else shutil.move

        pkg: dict = {
            "schema_version": _PACKAGE_SCHEMA_VERSION,
            "pdk": self.pdk,
            "topology": self.topology,
            "params": dict(self.params),
            "param": [],
            "const": [],
            "pre-sim": [],
            "layout": [],
            "extract": [],
            "drc": [],
            "lvs": [],
            "post-sim": [],
            "pre_layout_fom": self.pre_layout_fom,
            "post_layout_fom": self.post_fom,
            "deltas": {
                "fom_pct": self.fom_delta_pct,
                "gain_dB": self.gain_delta_dB,
                "gbw_pct": self.gbw_delta_pct,
                "pm_deg": self.pm_delta_deg,
            },
            "post_layout_metrics": {
                "Adc_dB": self.post_Adc_dB,
                "GBW_Hz": self.post_GBW_Hz,
                "PM_deg": self.post_PM_deg,
                "valid": self.post_valid,
            },
            "baseline_metrics": {
                "Adc_dB": self.baseline_Adc_dB,
                "GBW_Hz": self.baseline_GBW_Hz,
                "PM_deg": self.baseline_PM_deg,
                "fom": self.baseline_fom,
                "valid": self.baseline_valid,
            },
            "pex_corner": self.pex_corner,
            "drc_clean": self.drc_clean,
            "drc_violations": self.drc_violations,
            "lvs_match": self.lvs_match,
            "total_time_s": self.total_time_s,
            "error": self.error,
        }

        def _drop_into_dst(src: str | None, name: str, key: str) -> None:
            if not src:
                return
            src_path = Path(src)
            if not src_path.is_file():
                logger.warning("Package: %s missing at %s, skipping", key, src)
                return
            out = dst / name
            action(str(src_path), str(out))
            pkg[key].append(name)

        _drop_into_dst(self.gds_path, "layout.gds", "layout")
        _drop_into_dst(self.netlist_path, "schematic.spice", "extract")
        _drop_into_dst(self.extracted_netlist_path, "extracted.spice", "extract")
        if self.drc_report_path:
            _drop_into_dst(self.drc_report_path, Path(self.drc_report_path).name, "drc")
        if self.lvs_report_path:
            _drop_into_dst(self.lvs_report_path, Path(self.lvs_report_path).name, "lvs")

        def _harvest_sim_dir(src: str | None, sub: str, key: str) -> None:
            if not src:
                return
            src_dir = Path(src)
            if not src_dir.is_dir():
                logger.warning("Package: %s missing at %s, skipping", key, src)
                return
            out_dir = dst / sub
            out_dir.mkdir(parents=True, exist_ok=True)
            for pattern in sim_artifact_globs:
                for match in sorted(src_dir.glob(pattern)):
                    if not match.is_file():
                        continue
                    out = out_dir / match.name
                    action(str(match), str(out))
                    pkg[key].append(f"{sub}/{match.name}")

        _harvest_sim_dir(self.pre_sim_dir, "pre_sim", "pre-sim")
        _harvest_sim_dir(self.post_sim_dir, "post_sim", "post-sim")

        # Drop empty fallback lists per CABAgent contract (string sentinel).
        for key in ("param", "const", "pre-sim", "layout", "extract", "drc", "lvs", "post-sim"):
            if pkg[key] == []:
                pkg[key] = f"{key} artifacts not found"

        manifest_path = dst / "manifest.json"
        manifest_path.write_text(json.dumps(pkg, indent=2, default=str) + "\n")
        logger.info("Wrote benchmark package manifest to %s", manifest_path)
        return dst


def package_postlayout_results(
    results: list[PostLayoutResult],
    dst_root: Path | str,
    *,
    copy_artifacts: bool = True,
) -> list[Path]:
    """Package a list of ``PostLayoutResult`` into one directory per design.

    Convenience pair for ``PostLayoutValidator.validate_top_n`` output.
    Sub-directories are named ``design_000``, ``design_001``, ... in
    list order. Returns the list of created package directories.
    """
    dst_root = Path(dst_root)
    dst_root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, r in enumerate(results):
        sub = dst_root / f"design_{i:03d}"
        paths.append(r.to_package(sub, copy_artifacts=copy_artifacts))
    return paths


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
