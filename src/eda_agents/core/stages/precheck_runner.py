"""Precheck stage runner.

Wraps the wafer-space ``precheck.py`` script with correct CLI
arguments (``--input``, ``--top``, ``--slot``).  Does NOT use the
broken ``run_precheck`` from ``tools/eda_tools.py`` which has wrong
argument names.

Validates that ``final/gds/<design>.gds`` exists before invoking
precheck (lesson F7: step-level GDS files are not valid substitutes).
Always passes explicit ``PDK_ROOT`` and ``PDK`` to prevent env bleed
(lesson F5).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from eda_agents.core.flow_stage import FlowStage, StageResult
from eda_agents.core.tool_environment import ToolEnvironment

logger = logging.getLogger(__name__)


class PrecheckRunner:
    """Runs wafer-space precheck and returns a ``StageResult``.

    Parameters
    ----------
    precheck_dir : Path
        Path to the ``gf180mcu-precheck`` repository clone.
    env : ToolEnvironment
        Execution environment.
    pdk_root : str or None
        Explicit PDK_ROOT for precheck.  If None, uses
        ``<precheck_dir>/gf180mcu`` (precheck's own PDK clone).
    slot : str
        Slot size.  One of ``"1x1"``, ``"0p5x1"``, ``"1x0p5"``,
        ``"0p5x0p5"``.
    timeout_s : int
        Maximum precheck runtime in seconds.
    """

    def __init__(
        self,
        precheck_dir: Path | str,
        env: ToolEnvironment,
        *,
        pdk_root: str | None = None,
        slot: str = "1x1",
        timeout_s: int = 7200,
    ):
        self.precheck_dir = Path(precheck_dir)
        self.env = env
        self.pdk_root = pdk_root or str(self.precheck_dir / "gf180mcu")
        self.slot = slot
        self.timeout_s = timeout_s

    def run(
        self,
        gds_path: Path | str,
        *,
        top_cell: str = "",
        die_id: str = "",
        output_gds: Path | str | None = None,
        run_tag: str = "",
        work_dir: Path | str | None = None,
    ) -> StageResult:
        """Run precheck against a final GDS.

        Parameters
        ----------
        gds_path : Path or str
            Path to ``final/gds/<design>.gds``.
        top_cell : str
            Top cell name.  If empty, derived from GDS filename.
        die_id : str
            Die ID for QR code.  If empty, precheck uses default.
        output_gds : Path or str, optional
            Path for the modified output GDS with QR code.
        run_tag : str
            LibreLane run tag for precheck's internal run dir.
        work_dir : Path or str, optional
            Working directory for precheck.  Defaults to precheck_dir.
        """
        t0 = time.monotonic()

        gds = Path(gds_path)
        if not gds.is_file():
            return StageResult(
                stage=FlowStage.PRECHECK,
                success=False,
                error=(
                    f"GDS file not found: {gds}. "
                    "Precheck requires final/gds/<design>.gds, not step-level GDS (F7)."
                ),
                run_time_s=time.monotonic() - t0,
            )

        if not self.precheck_dir.is_dir():
            return StageResult(
                stage=FlowStage.PRECHECK,
                success=False,
                error=f"Precheck directory not found: {self.precheck_dir}",
                run_time_s=time.monotonic() - t0,
            )

        precheck_script = self.precheck_dir / "precheck.py"
        if not precheck_script.is_file():
            return StageResult(
                stage=FlowStage.PRECHECK,
                success=False,
                error=f"precheck.py not found in {self.precheck_dir}",
                run_time_s=time.monotonic() - t0,
            )

        # Build command with correct CLI args (not the broken eda_tools.py version)
        cmd = [
            "python3",
            str(precheck_script),
            "--input", str(gds),
            "--slot", self.slot,
        ]

        cell = top_cell or gds.stem
        cmd.extend(["--top", cell])

        if die_id:
            cmd.extend(["--id", die_id])
        if output_gds:
            cmd.extend(["--output", str(output_gds)])
        if run_tag:
            cmd.extend(["--run-tag", run_tag])

        effective_work_dir = Path(work_dir) if work_dir else self.precheck_dir

        if work_dir and work_dir != self.precheck_dir:
            cmd.extend(["--dir", str(work_dir)])

        # Explicit PDK environment (F5 pattern)
        run_env = os.environ.copy()
        run_env["PDK_ROOT"] = self.pdk_root
        run_env["PDK"] = "gf180mcuD"

        logger.info("PrecheckRunner: %s", " ".join(cmd))

        try:
            proc = self.env.run(
                cmd,
                cwd=effective_work_dir,
                env=run_env,
                timeout_s=self.timeout_s,
            )
        except FileNotFoundError:
            return StageResult(
                stage=FlowStage.PRECHECK,
                success=False,
                error="python3 not found on PATH",
                run_time_s=time.monotonic() - t0,
            )

        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        elapsed = time.monotonic() - t0

        # Parse results: exit code 0 = pass, 1 = FlowError from fatal checker
        success = proc.returncode == 0

        # Try to extract error counts from per-step state_out.json files
        precheck_errors = self._count_precheck_errors(effective_work_dir, run_tag)

        metrics_delta: dict[str, float] = {
            "precheck_errors": precheck_errors,
        }

        artifacts: dict[str, Path] = {}
        if output_gds and Path(output_gds).is_file():
            artifacts["output_gds"] = Path(output_gds)

        return StageResult(
            stage=FlowStage.PRECHECK,
            success=success,
            metrics_delta=metrics_delta,
            artifacts=artifacts,
            log_tail=combined[-2000:],
            run_time_s=elapsed,
            error=f"Precheck failed (exit {proc.returncode})" if not success else None,
        )

    def _count_precheck_errors(
        self, work_dir: Path, run_tag: str
    ) -> int:
        """Count total errors from precheck's per-step state_out.json files."""
        runs_dir = work_dir / "librelane" / "runs"
        if not runs_dir.is_dir():
            # Try precheck_dir as fallback
            runs_dir = self.precheck_dir / "librelane" / "runs"
            if not runs_dir.is_dir():
                return 0

        # Find the run directory
        if run_tag:
            run_dir = runs_dir / run_tag
        else:
            # Most recent
            subdirs = [d for d in runs_dir.iterdir() if d.is_dir()]
            if not subdirs:
                return 0
            run_dir = max(subdirs, key=lambda d: d.stat().st_mtime)

        if not run_dir.is_dir():
            return 0

        # Sum error counts from state_out.json files
        total_errors = 0
        error_keys = [
            "klayout__drc_error__count",
            "klayout__zero_area_polygons__count",
            "magic__drc_error__count",
            "antenna__violating__nets",
        ]

        for state_file in sorted(run_dir.rglob("state_out.json")):
            try:
                data = json.loads(state_file.read_text())
                metrics = data.get("metrics", {})
                for key in error_keys:
                    val = metrics.get(key)
                    if val is not None and isinstance(val, (int, float)):
                        total_errors += int(val)
            except (json.JSONDecodeError, OSError):
                continue

        return total_errors
