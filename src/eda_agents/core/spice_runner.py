"""Reusable ngspice execution layer for circuit simulation.

Provides SpiceRunner for synchronous and async ngspice invocations,
with standardized measurement parsing and PDK path validation.
Supports any PDK via PdkConfig (defaults to IHP SG13G2).
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from eda_agents.core.pdk import PdkConfig, resolve_pdk, resolve_pdk_root

# Regex for parsing .meas output lines: "name = 1.234e+05"
_MEAS_RE = re.compile(r"=\s*([-+]?\d+\.?\d*(?:e[-+]?\d+)?)", re.IGNORECASE)


@dataclass
class SpiceResult:
    """Parsed results from an ngspice AC analysis simulation."""

    success: bool
    Adc_dB: float | None = None
    Adc_peak_dB: float | None = None
    GBW_Hz: float | None = None
    PM_deg: float | None = None
    power_uW: float | None = None
    error: str | None = None
    sim_time_s: float = 0.0
    measurements: dict[str, float] = field(default_factory=dict)
    stdout_tail: str = ""
    stderr_tail: str = ""

    @property
    def GBW_MHz(self) -> float | None:
        """GBW in MHz for convenience."""
        return self.GBW_Hz / 1e6 if self.GBW_Hz is not None else None


class SpiceRunner:
    """Execute ngspice simulations with PDK-aware environment setup.

    Validates PDK_ROOT and model files on construction. Provides both
    synchronous (run) and async (run_async) execution methods.

    Parameters
    ----------
    pdk : PdkConfig or str, optional
        PDK configuration. String names are resolved via the registry.
        Defaults to resolve_pdk() (EDA_AGENTS_PDK env or IHP SG13G2).
    pdk_root : str or Path, optional
        Path to PDK installation root. Defaults to PDK_ROOT env var
        or the PDK's default_pdk_root.
    corner : str, optional
        Model corner override. If None, uses pdk.model_corner.
    timeout_s : int
        Maximum simulation time in seconds. Default 120.
    extra_osdi : iterable of str or Path, optional
        Extra OSDI shared libraries to load alongside the PDK ones
        (e.g. user models compiled from Verilog-A via openvaf). Pass
        the same list through to ``netlist_osdi_lines(pdk,
        extra_osdi=runner.extra_osdi)`` when building the netlist.
    """

    def __init__(
        self,
        pdk: PdkConfig | str | None = None,
        pdk_root: str | Path | None = None,
        corner: str | None = None,
        timeout_s: int = 120,
        extra_osdi: "list[str | Path] | tuple[str | Path, ...] | None" = None,
    ):
        self.pdk = resolve_pdk(pdk)
        root_str = resolve_pdk_root(
            self.pdk, str(pdk_root) if pdk_root else None
        )
        self.pdk_root = Path(root_str)
        self.corner = corner if corner is not None else self.pdk.model_corner
        self.timeout_s = timeout_s

        # Derive paths from PDK config
        self._model_lib = Path(self.pdk.model_lib_path(root_str))
        osdi_path = self.pdk.osdi_dir_path(root_str)
        self._osdi_dir = Path(osdi_path) if osdi_path else None
        self._extra_osdi: tuple[Path, ...] = tuple(
            Path(p).resolve() for p in (extra_osdi or ())
        )

    @property
    def model_lib(self) -> Path:
        return self._model_lib

    @property
    def osdi_dir(self) -> Path | None:
        return self._osdi_dir

    @property
    def osdi_paths(self) -> list[Path]:
        if self._osdi_dir is None:
            return list(self._extra_osdi)
        return [self._osdi_dir / f for f in self.pdk.osdi_files] + list(
            self._extra_osdi
        )

    @property
    def extra_osdi(self) -> tuple[Path, ...]:
        """User-supplied OSDI libraries loaded on top of the PDK set."""
        return self._extra_osdi

    def validate_pdk(self) -> list[str]:
        """Check that PDK files exist. Returns list of missing paths."""
        missing = []
        if not self.pdk_root.is_dir():
            missing.append(f"PDK_ROOT: {self.pdk_root}")
            return missing
        if not self._model_lib.is_file():
            missing.append(f"Model lib: {self._model_lib}")
        for p in self.osdi_paths:
            if not p.is_file():
                missing.append(f"OSDI: {p}")
        return missing

    def _build_env(self) -> dict[str, str]:
        """Build environment dict with PDK_ROOT set."""
        env = os.environ.copy()
        env["PDK_ROOT"] = str(self.pdk_root)
        return env

    def _install_extra_osdi_spiceinit(self, work_dir: Path) -> Path | None:
        """Write a cwd ``.spiceinit`` that pre-loads extra OSDI files.

        ngspice sources ``./.spiceinit`` before parsing the deck, which
        is the only reliable hook for registering model types defined
        in user-compiled Verilog-A (``osdi`` inside ``.control`` fires
        after ``.model`` lines are parsed and therefore too late).

        Returns the written path (caller is responsible for cleanup)
        or ``None`` when there are no extras.
        """
        if not self._extra_osdi:
            return None
        target = work_dir / ".spiceinit"
        if target.exists():
            raise RuntimeError(
                f"Cannot install extra-OSDI spiceinit: {target} already exists. "
                "Use a dedicated working directory for SpiceRunner with extra_osdi."
            )
        lines = ["* eda-agents extra OSDI pre-load (auto-generated)"]
        lines.extend(f"osdi '{p}'" for p in self._extra_osdi)
        target.write_text("\n".join(lines) + "\n")
        return target

    def run(self, cir_path: Path, work_dir: Path | None = None) -> SpiceResult:
        """Run ngspice in batch mode synchronously.

        Parameters
        ----------
        cir_path : Path
            Path to the .cir control file.
        work_dir : Path, optional
            Working directory for ngspice. Defaults to cir_path's parent.

        Returns
        -------
        SpiceResult
            Parsed simulation results.
        """
        cir_path = Path(cir_path).resolve()
        if work_dir is None:
            work_dir = cir_path.parent
        else:
            work_dir = Path(work_dir).resolve()

        env = self._build_env()
        t0 = time.monotonic()

        spiceinit_path = self._install_extra_osdi_spiceinit(work_dir)
        try:
            try:
                proc = subprocess.run(
                    ["ngspice", "-b", str(cir_path)],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    cwd=str(work_dir),
                    env=env,
                )
            except FileNotFoundError:
                return SpiceResult(success=False, error="ngspice not found in PATH")
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - t0
                return SpiceResult(
                    success=False,
                    error=f"ngspice timed out ({self.timeout_s}s)",
                    sim_time_s=elapsed,
                )
        finally:
            if spiceinit_path is not None and spiceinit_path.exists():
                spiceinit_path.unlink()

        elapsed = time.monotonic() - t0
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # ngspice sometimes exits with code 1 on warnings (e.g., gmin stepping)
        # but still produces valid results. Only fail for code > 1 or missing output.
        if proc.returncode != 0 and proc.returncode != 1:
            return SpiceResult(
                success=False,
                error=f"ngspice exited with code {proc.returncode}",
                sim_time_s=elapsed,
                stdout_tail=stdout[-3000:],
                stderr_tail=stderr[-2000:],
            )

        if proc.returncode == 1 and not _has_measurements(stdout):
            return SpiceResult(
                success=False,
                error="ngspice exited with code 1 (no measurements)",
                sim_time_s=elapsed,
                stdout_tail=stdout[-3000:],
                stderr_tail=stderr[-2000:],
            )

        return self._parse_output(stdout, stderr, elapsed)

    async def run_async(
        self, cir_path: Path, work_dir: Path | None = None
    ) -> SpiceResult:
        """Run ngspice asynchronously for concurrent simulations.

        Same interface as run() but uses asyncio subprocess.
        """
        # Resolve to absolute paths -- relative paths break when the
        # process cwd changes (e.g., MCP server init, concurrent sessions).
        cir_path = Path(cir_path).resolve()
        if work_dir is None:
            work_dir = cir_path.parent
        else:
            work_dir = Path(work_dir).resolve()

        env = self._build_env()
        t0 = time.monotonic()

        import shutil
        ngspice_path = shutil.which("ngspice")
        if not ngspice_path:
            return SpiceResult(success=False, error="ngspice not found in PATH")

        spiceinit_path = self._install_extra_osdi_spiceinit(work_dir)
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    ngspice_path, "-b", str(cir_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(work_dir),
                    env=env,
                )

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=self.timeout_s
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    elapsed = time.monotonic() - t0
                    return SpiceResult(
                        success=False,
                        error=f"ngspice timed out ({self.timeout_s}s)",
                        sim_time_s=elapsed,
                    )

            except FileNotFoundError:
                return SpiceResult(
                    success=False,
                    error=f"ngspice not found at {ngspice_path}",
                )
        finally:
            if spiceinit_path is not None and spiceinit_path.exists():
                spiceinit_path.unlink()

        elapsed = time.monotonic() - t0
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        _log = __import__("logging").getLogger(__name__)
        if elapsed < 0.1 and proc.returncode != 0:
            _log.warning(
                "ngspice returned in %.3fs with code %d (suspect immediate failure). "
                "cir=%s, stdout_len=%d, stderr_len=%d, stderr_tail=%s",
                elapsed, proc.returncode, cir_path, len(stdout), len(stderr),
                stderr[-500:] if stderr else "(empty)",
            )

        # ngspice sometimes exits with code 1 on warnings (e.g., gmin stepping)
        # but still produces valid results. Only fail for code > 1 or missing output.
        if proc.returncode != 0 and proc.returncode != 1:
            return SpiceResult(
                success=False,
                error=f"ngspice exited with code {proc.returncode}",
                sim_time_s=elapsed,
                stdout_tail=stdout[-3000:],
                stderr_tail=stderr[-2000:],
            )

        if proc.returncode == 1 and not _has_measurements(stdout):
            return SpiceResult(
                success=False,
                error="ngspice exited with code 1 (no measurements)",
                sim_time_s=elapsed,
                stdout_tail=stdout[-3000:],
                stderr_tail=stderr[-2000:],
            )

        return self._parse_output(stdout, stderr, elapsed)

    def _parse_output(
        self, stdout: str, stderr: str, sim_time_s: float
    ) -> SpiceResult:
        """Parse ngspice stdout for .meas ac results.

        Recognized measurements (case-insensitive prefix matching):
            adc_peak  -> Adc_peak_dB
            adc       -> Adc_dB
            gbw       -> GBW_Hz
            pgbw      -> PM_deg (inverting OTA convention)

        Any other "name = value" lines are stored in measurements dict.
        """
        result = SpiceResult(
            success=True,
            sim_time_s=sim_time_s,
            stdout_tail=stdout[-3000:],
            stderr_tail=stderr[-2000:],
        )

        for line in stdout.splitlines():
            stripped = line.strip().lower()

            # Skip lines without '=' (not measurement output)
            if "=" not in stripped:
                continue

            # Try to parse a measurement value
            val = _parse_meas_value(stripped)
            if val is None:
                continue

            # Route to known fields
            if stripped.startswith("adc_peak"):
                result.Adc_peak_dB = val
                result.measurements["Adc_peak_dB"] = val
            elif stripped.startswith("adc") and not stripped.startswith("adc_"):
                result.Adc_dB = val
                result.measurements["Adc_dB"] = val
            elif stripped.startswith("gbw"):
                result.GBW_Hz = val
                result.measurements["GBW_Hz"] = val
            elif stripped.startswith("pgbw") and not stripped.startswith("pgbw_"):
                result.PM_deg = val
                result.measurements["PM_deg"] = val
            else:
                # Store any other measurement by its name
                name = stripped.split("=")[0].strip()
                if name:
                    result.measurements[name] = val

        return result


def _has_measurements(stdout: str) -> bool:
    """Check if ngspice stdout contains any measurement output (AC or transient)."""
    low = stdout.lower()
    return "meas ac" in low or "meas tran" in low or "adc" in low or "td_" in low


def _parse_meas_value(line: str) -> float | None:
    """Extract numeric value from a .meas output line.

    Handles formats like:
        adc                 =  5.55000e+01
        gbw                 =  1.234e+06
        pgbw                = -30.0
    """
    match = _MEAS_RE.search(line)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None
