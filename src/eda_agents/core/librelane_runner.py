"""LibreLane flow runner for RTL-to-GDS hardening.

Wraps the LibreLane CLI as a subprocess, providing structured
execution and result parsing. Supports config modification for
iterative DRC-fix loops.

LibreLane CLI:
    librelane <config.json> [--run-tag <tag>] [--pdk-root <path>]

The project directory must contain a config file (JSON or YAML)
with the design configuration (meta.version >= 2). Both
``config.json`` and ``config.yaml`` are supported transparently.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import yaml

from eda_agents.agents.phase_results import DRCResult, FlowResult

logger = logging.getLogger(__name__)

# Keys that are safe to modify in the flow config without breaking
# the design intent. Other keys require explicit opt-in.
SAFE_CONFIG_KEYS = frozenset({
    # Timing
    "CLOCK_PERIOD",
    # Placement
    "PL_TARGET_DENSITY_PCT",
    "GPL_CELL_PADDING",
    "DPL_CELL_PADDING",
    # PDN (v3 naming: PDN_*, not FP_PDN_*)
    "PDN_VPITCH",
    "PDN_HPITCH",
    "PDN_VOFFSET",
    "PDN_HOFFSET",
    "PDN_VWIDTH",
    "PDN_HWIDTH",
    # Floorplan
    "FP_MACRO_HORIZONTAL_HALO",
    "FP_MACRO_VERTICAL_HALO",
    "DIE_AREA",
    "FP_SIZING",
    # Global routing
    "GRT_ALLOW_CONGESTION",
    "GRT_OVERFLOW_ITERS",
    "GRT_ANTENNA_REPAIR_ITERS",
    # Detailed routing
    "DRT_OPT_ITERS",
    # Resizer
    "RSZ_DONT_TOUCH_RX",
    # Parasitic extraction
    "RCX_RULESETS",
})


def _find_librelane_python() -> str | None:
    """Find a Python that can import librelane."""
    candidates = [
        "/home/montanares/git/librelane/.venv/bin/python",
        "python3",
    ]
    for py in candidates:
        try:
            proc = subprocess.run(
                [py, "-c", "import librelane"],
                capture_output=True,
                timeout=10,
            )
            if proc.returncode == 0:
                return py
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


class LibreLaneRunner:
    """Runs LibreLane RTL-to-GDS flows via subprocess.

    Parameters
    ----------
    project_dir : Path
        Directory containing the design (Verilog sources, config file).
    config_file : str
        Config filename relative to project_dir. Default: "config.json".
    pdk_root : str or None
        Override for PDK_ROOT environment variable.
    timeout_s : int
        Maximum flow runtime in seconds. Default: 1800 (30 min).
    python_cmd : str or None
        Python interpreter with librelane installed. Auto-detected if None.
        Ignored when ``shell_wrapper`` is set (the wrapper provides Python).
    shell_wrapper : str or None
        Shell command prefix for environments like nix-shell.
        When set, the flow command is run as:
        ``<shell_wrapper> '<python> -m librelane ...'``
        For nix-shell: ``"nix-shell /path/to/project --run"``
    """

    def __init__(
        self,
        project_dir: Path | str,
        config_file: str = "config.json",
        pdk_root: str | None = None,
        timeout_s: int = 1800,
        python_cmd: str | None = None,
        shell_wrapper: str | None = None,
        env_extra: dict[str, str] | None = None,
        extra_flags: tuple[str, ...] | list[str] | None = None,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.config_file = config_file
        self.config_path = self.project_dir / config_file
        self.pdk_root = pdk_root
        self.env_extra = env_extra or {}
        self.timeout_s = timeout_s
        self.shell_wrapper = shell_wrapper
        self.extra_flags = list(extra_flags) if extra_flags else []
        if shell_wrapper:
            # When using a shell wrapper, default python to "python3"
            self.python_cmd = python_cmd or "python3"
        else:
            self.python_cmd = python_cmd or _find_librelane_python()

    def validate_setup(self) -> list[str]:
        """Check prerequisites. Returns list of problems (empty = OK)."""
        problems = []

        if not self.project_dir.is_dir():
            problems.append(f"Project directory not found: {self.project_dir}")

        if not self.config_path.is_file():
            problems.append(f"Config not found: {self.config_path}")

        if not self.python_cmd:
            problems.append(
                "No Python interpreter with librelane found. "
                "Install librelane or set python_cmd explicitly."
            )

        return problems

    @property
    def _is_yaml(self) -> bool:
        """True if the config file uses YAML format."""
        return self.config_path.suffix in (".yaml", ".yml")

    def _read_config(self) -> dict:
        """Read the current config file (JSON or YAML)."""
        if not self.config_path.is_file():
            raise FileNotFoundError(f"Config not found: {self.config_path}")
        text = self.config_path.read_text()
        if self._is_yaml:
            return yaml.safe_load(text) or {}
        return json.loads(text)

    def _write_config(self, config: dict) -> None:
        """Write config back to disk (JSON or YAML)."""
        if self._is_yaml:
            self.config_path.write_text(
                yaml.dump(config, default_flow_style=False, sort_keys=False)
            )
        else:
            self.config_path.write_text(json.dumps(config, indent=4) + "\n")

    def modify_config(self, key: str, value, force: bool = False) -> dict:
        """Safely modify a config parameter.

        Only allows known-safe keys unless force=True.

        Parameters
        ----------
        key : str
            Config key to modify.
        value : any
            New value.
        force : bool
            If True, skip safe-key validation.

        Returns
        -------
        dict
            {"key": key, "old_value": old, "new_value": value}

        Raises
        ------
        ValueError
            If key is not in the safe list and force=False.
        """
        if not force and key not in SAFE_CONFIG_KEYS:
            raise ValueError(
                f"Key '{key}' is not in the safe modification list. "
                f"Use force=True to override. Safe keys: {sorted(SAFE_CONFIG_KEYS)}"
            )

        config = self._read_config()
        old_value = config.get(key)
        config[key] = value
        self._write_config(config)

        logger.info("Config: %s: %s -> %s", key, old_value, value)
        return {"key": key, "old_value": old_value, "new_value": value}

    def run_flow(
        self,
        tag: str = "",
        frm: str | None = None,
        to: str | None = None,
        overwrite: bool = True,
    ) -> FlowResult:
        """Run the LibreLane flow.

        Parameters
        ----------
        tag : str
            Tag for this run (creates runs/<tag> subdirectory).
        frm : str or None
            Start from this step (e.g., "OpenROAD.DetailedRouting").
        to : str or None
            Stop after this step.
        overwrite : bool
            Overwrite existing run directory.

        Returns
        -------
        FlowResult
        """
        if not self.python_cmd:
            return FlowResult(
                success=False,
                error="No Python interpreter with librelane found",
            )

        if not self.config_path.is_file():
            return FlowResult(
                success=False,
                error=f"Config not found: {self.config_path}",
            )

        # Build the inner librelane command parts
        inner_parts = [
            self.python_cmd,
            "-m", "librelane",
            str(self.config_path),
        ]

        if tag:
            inner_parts.extend(["--run-tag", tag])
        if frm:
            inner_parts.extend(["--frm", frm])
        if to:
            inner_parts.extend(["--to", to])
        if overwrite:
            inner_parts.append("--overwrite")
        if self.extra_flags:
            inner_parts.extend(self.extra_flags)

        env = os.environ.copy()
        if self.pdk_root:
            env["PDK_ROOT"] = self.pdk_root
        env.update(self.env_extra)

        # Build the actual subprocess command
        if self.shell_wrapper:
            # Wrap: e.g. nix-shell /path --run 'python3 -m librelane ...'
            inner_str = " ".join(inner_parts)
            wrapper_parts = self.shell_wrapper.split()
            cmd = [*wrapper_parts, inner_str]
        else:
            cmd = inner_parts

        logger.info("Running LibreLane: %s", " ".join(cmd))
        t0 = time.monotonic()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=str(self.project_dir),
                env=env,
            )
        except FileNotFoundError:
            return FlowResult(
                success=False,
                error=f"Python interpreter not found: {self.python_cmd}",
            )
        except subprocess.TimeoutExpired:
            return FlowResult(
                success=False,
                error=f"Flow timed out after {self.timeout_s}s",
                run_time_s=time.monotonic() - t0,
            )

        elapsed = time.monotonic() - t0
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # LibreLane exit codes: 0 = success, 2 = flow error (DRC fail etc)
        success = proc.returncode == 0

        # Find the run directory
        run_dir = self._find_run_dir(tag)

        # Find output artifacts
        gds_path = None
        def_path = None
        netlist_path = None
        if run_dir:
            gds_path = self._find_artifact(run_dir, "*.gds")
            def_path = self._find_artifact(run_dir, "*.def")
            netlist_path = self._find_artifact(run_dir, "*.nl.v")

        # Check DRC/timing from flow output
        timing_met = None
        drc_clean = None
        if "Timing violations" in stdout or "VIOLATED" in stdout:
            timing_met = False
        elif "No timing violations" in stdout or proc.returncode == 0:
            timing_met = True if run_dir else None

        error_msg = None
        if not success:
            # Extract last meaningful error from stderr or stdout
            for line in reversed((stderr or stdout).strip().splitlines()):
                line = line.strip()
                if line and not line.startswith("["):
                    error_msg = line[:500]
                    break
            if not error_msg:
                error_msg = f"Flow failed with exit code {proc.returncode}"

        return FlowResult(
            success=success,
            gds_path=str(gds_path) if gds_path else None,
            def_path=str(def_path) if def_path else None,
            netlist_path=str(netlist_path) if netlist_path else None,
            timing_met=timing_met,
            drc_clean=drc_clean,
            run_dir=str(run_dir) if run_dir else "",
            run_time_s=elapsed,
            error=error_msg,
            log_tail=(stdout + "\n" + stderr)[-3000:],
        )

    def _find_run_dir(self, tag: str = "") -> Path | None:
        """Find the most recent run directory."""
        runs_dir = self.project_dir / "runs"
        if not runs_dir.is_dir():
            return None

        if tag:
            tagged = runs_dir / tag
            if tagged.is_dir():
                return tagged

        # Find most recent by modification time
        subdirs = [d for d in runs_dir.iterdir() if d.is_dir()]
        if not subdirs:
            return None
        return max(subdirs, key=lambda d: d.stat().st_mtime)

    def _find_artifact(self, run_dir: Path, pattern: str) -> Path | None:
        """Find an artifact in the run directory tree."""
        # Check final/ directory first (LibreLane convention)
        final_dir = run_dir / "final"
        if final_dir.is_dir():
            matches = sorted(final_dir.rglob(pattern))
            if matches:
                return matches[0]

        # Fall back to searching the whole run dir
        matches = sorted(run_dir.rglob(pattern))
        return matches[0] if matches else None

    def read_drc(self, run_dir: str | Path | None = None) -> DRCResult:
        """Parse DRC results from a run directory.

        Looks for KLayout .lyrdb files or Magic DRC output.
        """
        if run_dir is None:
            run_dir = self._find_run_dir()
        if run_dir is None:
            return DRCResult(total_violations=-1, clean=False)

        run_dir = Path(run_dir)

        # Look for KLayout .lyrdb files
        lyrdb_files = sorted(run_dir.rglob("*.lyrdb"))
        if lyrdb_files:
            from eda_agents.core.klayout_drc import parse_lyrdb

            all_rules: dict[str, int] = {}
            for f in lyrdb_files:
                try:
                    rules = parse_lyrdb(f)
                    for rule, count in rules.items():
                        all_rules[rule] = all_rules.get(rule, 0) + count
                except Exception as e:
                    logger.warning("Failed to parse %s: %s", f, e)

            total = sum(all_rules.values())
            return DRCResult(
                total_violations=total,
                violated_rules=all_rules,
                clean=total == 0,
                report_path=str(lyrdb_files[0]),
            )

        # Look for Magic DRC output
        drc_files = sorted(run_dir.rglob("*.magic.drc"))
        if drc_files:
            content = drc_files[0].read_text()
            total = content.count("\n") // 3  # rough estimate
            return DRCResult(
                total_violations=total,
                clean=total == 0,
                report_path=str(drc_files[0]),
            )

        return DRCResult(total_violations=-1, clean=False)

    def read_timing(self, run_dir: str | Path | None = None) -> dict:
        """Parse timing reports from a run directory.

        Returns
        -------
        dict
            Keys: wns (worst negative slack), tns (total negative slack),
            met (bool), report_path.
        """
        if run_dir is None:
            run_dir = self._find_run_dir()
        if run_dir is None:
            return {"error": "No run directory found"}

        run_dir = Path(run_dir)

        # Look for OpenROAD timing reports
        timing_files = sorted(run_dir.rglob("*sta*.rpt")) + sorted(
            run_dir.rglob("*timing*.rpt")
        )
        if not timing_files:
            return {"error": "No timing report found"}

        content = timing_files[0].read_text()
        wns = None
        tns = None

        for line in content.splitlines():
            if "wns" in line.lower():
                try:
                    wns = float(line.split()[-1])
                except (ValueError, IndexError):
                    pass
            if "tns" in line.lower():
                try:
                    tns = float(line.split()[-1])
                except (ValueError, IndexError):
                    pass

        met = wns is not None and wns >= 0

        return {
            "wns": wns,
            "tns": tns,
            "met": met,
            "report_path": str(timing_files[0]),
        }

    def latest_gds(self) -> Path | None:
        """GDS from the most recent run."""
        run_dir = self._find_run_dir()
        if run_dir:
            return self._find_artifact(run_dir, "*.gds")
        return None

    def latest_run_dir(self) -> Path | None:
        """Path to latest run artifacts."""
        return self._find_run_dir()

    def design_name(self) -> str | None:
        """Extract DESIGN_NAME from config."""
        try:
            config = self._read_config()
            return config.get("DESIGN_NAME")
        except Exception:
            return None
