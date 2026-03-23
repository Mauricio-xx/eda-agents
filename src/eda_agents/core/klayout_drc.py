"""KLayout DRC runner for GF180MCU PDK.

Invokes the PDK's run_drc.py as a subprocess, captures exit code,
and parses the resulting .lyrdb XML report files.

The run_drc.py script exits 1 when violations are found -- that is
NOT an error, it is the expected result for a dirty design.
"""

from __future__ import annotations

import logging
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

# Default PDK variant for GF180MCU wafer-space template
DEFAULT_VARIANT = "C"

# Relative path from PDK root to the DRC script
_DRC_SCRIPT_REL = "gf180mcuD/libs.tech/klayout/tech/drc/run_drc.py"


@dataclass
class KLayoutDrcResult:
    """Result of a KLayout DRC run."""

    success: bool
    """True if klayout executed without crashing (exit 0 or 1)."""
    total_violations: int
    clean: bool
    """True when total_violations == 0."""
    violated_rules: dict[str, int] = field(default_factory=dict)
    """rule_name -> violation count."""
    report_path: str | None = None
    """Path to the primary .lyrdb report file."""
    report_paths: list[str] = field(default_factory=list)
    """All .lyrdb files generated (one per table)."""
    run_time_s: float = 0.0
    error: str | None = None

    @property
    def summary(self) -> str:
        if self.error:
            return f"KLayout DRC error: {self.error}"
        if self.clean:
            return "KLayout DRC: clean (0 violations)"
        top_rules = sorted(
            self.violated_rules.items(), key=lambda x: x[1], reverse=True
        )[:5]
        rule_str = ", ".join(f"{r}({c})" for r, c in top_rules)
        return (
            f"KLayout DRC: {self.total_violations} violations "
            f"across {len(self.violated_rules)} rules. "
            f"Top: {rule_str}"
        )


def _find_klayout_python() -> str:
    """Find a Python interpreter that can import klayout.db and docopt.

    The venv python typically lacks klayout.db (system-installed),
    so we try /usr/bin/python3 and other common locations.
    """
    import subprocess as _sp

    candidates = ["/usr/bin/python3", "python3"]
    for python in candidates:
        try:
            proc = _sp.run(
                [python, "-c", "import klayout.db; import docopt"],
                capture_output=True,
                timeout=10,
            )
            if proc.returncode == 0:
                return python
        except (FileNotFoundError, _sp.TimeoutExpired):
            continue
    return "python3"  # fallback


class KLayoutDrcRunner:
    """Runs GF180MCU KLayout DRC via the PDK's run_drc.py script.

    Parameters
    ----------
    pdk_root : str or None
        Path to PDK root. Falls back to PDK_ROOT env var, then
        the GF180MCU_D default.
    variant : str
        PDK variant (A-F). Default "C" = 5LM, 9K top metal, MIM-B.
    timeout_s : int
        Maximum runtime in seconds.
    python_cmd : str or None
        Python interpreter that has klayout.db and docopt installed.
        Auto-detected if None.
    """

    def __init__(
        self,
        pdk_root: str | None = None,
        variant: str = DEFAULT_VARIANT,
        timeout_s: int = 600,
        python_cmd: str | None = None,
    ):
        self.variant = variant
        self.timeout_s = timeout_s
        self.python_cmd = python_cmd or _find_klayout_python()

        if pdk_root:
            self.pdk_root = Path(pdk_root)
        else:
            env_root = os.environ.get("PDK_ROOT")
            if env_root:
                self.pdk_root = Path(env_root)
            else:
                from eda_agents.core.pdk import GF180MCU_D
                self.pdk_root = Path(GF180MCU_D.default_pdk_root)

        self._drc_script = self.pdk_root / _DRC_SCRIPT_REL

    def validate_setup(self) -> list[str]:
        """Check prerequisites. Returns list of problems (empty = OK)."""
        import shutil
        import subprocess

        problems = []

        if not self.pdk_root.is_dir():
            problems.append(f"PDK root not found: {self.pdk_root}")

        if not self._drc_script.is_file():
            problems.append(f"run_drc.py not found: {self._drc_script}")

        if not shutil.which("klayout"):
            problems.append("klayout not found in PATH")

        # Check that python_cmd can import required modules
        try:
            proc = subprocess.run(
                [self.python_cmd, "-c", "import klayout.db; import docopt"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                problems.append(
                    f"{self.python_cmd} missing klayout.db or docopt: "
                    f"{proc.stderr.strip()}"
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            problems.append(f"Cannot run {self.python_cmd}: {e}")

        return problems

    def run(
        self,
        gds_path: str | Path,
        run_dir: str | Path,
        top_cell: str | None = None,
        table: str | Sequence[str] | None = None,
        mp: int = 1,
    ) -> KLayoutDrcResult:
        """Run DRC on a GDS file.

        Parameters
        ----------
        gds_path : path
            Input GDS file.
        run_dir : path
            Output directory for reports and logs.
        top_cell : str or None
            Top cell name. Auto-detected if None.
        table : str, list of str, or None
            Specific rule table(s) to run. None = all tables.
        mp : int
            Number of parallel cores for rule deck execution.

        Returns
        -------
        KLayoutDrcResult
        """
        import subprocess

        gds_path = Path(gds_path).resolve()
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        if not gds_path.is_file():
            return KLayoutDrcResult(
                success=False,
                total_violations=0,
                clean=False,
                error=f"GDS file not found: {gds_path}",
            )

        if not self._drc_script.is_file():
            return KLayoutDrcResult(
                success=False,
                total_violations=0,
                clean=False,
                error=f"run_drc.py not found: {self._drc_script}",
            )

        # Build command
        cmd = [
            self.python_cmd,
            str(self._drc_script),
            f"--path={gds_path}",
            f"--variant={self.variant}",
            f"--run_dir={run_dir}",
            f"--mp={mp}",
        ]

        if top_cell:
            cmd.append(f"--topcell={top_cell}")

        if table:
            tables = [table] if isinstance(table, str) else list(table)
            for t in tables:
                cmd.append(f"--table={t}")

        logger.info("Running KLayout DRC: %s", " ".join(cmd))
        t0 = time.monotonic()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=str(run_dir),
            )
        except FileNotFoundError:
            return KLayoutDrcResult(
                success=False,
                total_violations=0,
                clean=False,
                error=f"Python interpreter not found: {self.python_cmd}",
            )
        except subprocess.TimeoutExpired:
            return KLayoutDrcResult(
                success=False,
                total_violations=0,
                clean=False,
                error=f"DRC timed out after {self.timeout_s}s",
                run_time_s=time.monotonic() - t0,
            )

        elapsed = time.monotonic() - t0

        # Collect all .lyrdb files generated in run_dir
        lyrdb_files = sorted(run_dir.glob("*.lyrdb"))
        report_paths = [str(f) for f in lyrdb_files]

        # Aggregate violations across all reports
        all_rules: dict[str, int] = {}
        for lyrdb in lyrdb_files:
            try:
                rules = parse_lyrdb(lyrdb)
                for rule, count in rules.items():
                    all_rules[rule] = all_rules.get(rule, 0) + count
            except ET.ParseError as e:
                logger.warning("Failed to parse %s: %s", lyrdb, e)

        total = sum(all_rules.values())

        # exit 0 = clean, exit 1 = violations found OR script crash.
        # Distinguish: if .lyrdb files exist, it ran successfully.
        # If exit 1 but no .lyrdb and stderr has traceback, it crashed.
        stderr = proc.stderr or ""
        if proc.returncode != 0 and not lyrdb_files:
            # No reports generated -- likely a crash
            error_msg = stderr.strip()[-500:] if stderr else None
            return KLayoutDrcResult(
                success=False,
                total_violations=0,
                clean=False,
                run_time_s=elapsed,
                error=error_msg or f"DRC failed with exit code {proc.returncode}",
            )

        if proc.returncode not in (0, 1):
            error_msg = stderr.strip()[-500:] if stderr else None
            return KLayoutDrcResult(
                success=False,
                total_violations=total,
                clean=total == 0,
                violated_rules=all_rules,
                report_path=report_paths[0] if report_paths else None,
                report_paths=report_paths,
                run_time_s=elapsed,
                error=error_msg or f"DRC crashed with exit code {proc.returncode}",
            )

        return KLayoutDrcResult(
            success=True,
            total_violations=total,
            clean=total == 0,
            violated_rules=all_rules,
            report_path=report_paths[0] if report_paths else None,
            report_paths=report_paths,
            run_time_s=elapsed,
        )


def parse_lyrdb(path: str | Path) -> dict[str, int]:
    """Parse a KLayout .lyrdb XML report and count violations per rule.

    Parameters
    ----------
    path : str or Path
        Path to the .lyrdb file.

    Returns
    -------
    dict[str, int]
        Mapping of rule_name -> violation count.
    """
    tree = ET.parse(str(path))
    root = tree.getroot()

    rules: dict[str, int] = {}

    # Find <items> element (contains individual violations)
    items = root.find("items")
    if items is None:
        # Try index-based fallback (myroot[7] in PDK code)
        children = list(root)
        if len(children) > 7:
            items = children[7]
        else:
            return rules

    for item in items:
        if item.tag != "item":
            continue

        # Find <category> child -- contains rule name in quotes
        cat_elem = item.find("category")
        if cat_elem is not None and cat_elem.text:
            rule = cat_elem.text.strip().replace("'", "")
            if rule:
                rules[rule] = rules.get(rule, 0) + 1

    return rules
