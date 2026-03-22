"""Compile Verilog sources into shared libraries for ngspice d_cosim.

Uses the vlnggen ngspice script which invokes Verilator to convert
Verilog RTL into a .so that ngspice can load via the d_cosim XSPICE
code model. This enables mixed-signal simulation: analog SPICE models
run alongside synthesized digital logic.

Requirements:
  - ngspice (with XSPICE support, digital.cm code model)
  - Verilator (any recent version, tested with 5.031)
  - g++ (for linking the shared object)
  - vlnggen script (ships with ngspice >= 44)

Usage:
    from eda_agents.utils.vlnggen import compile_verilog, find_vlnggen

    vlnggen_path = find_vlnggen()
    so_path = compile_verilog(
        verilog_src=Path("sar_logic.v"),
        work_dir=Path("/tmp/build"),
    )
    # so_path is now a .so ready for d_cosim in ngspice
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Known locations for the vlnggen script
_VLNGGEN_SEARCH_PATHS = [
    Path.home() / ".local/share/ngspice/scripts/vlnggen",
    Path("/usr/local/share/ngspice/scripts/vlnggen"),
    Path("/usr/share/ngspice/scripts/vlnggen"),
]


def find_vlnggen() -> Path | None:
    """Locate the vlnggen ngspice script.

    Searches standard installation paths. Returns None if not found.
    """
    for p in _VLNGGEN_SEARCH_PATHS:
        if p.is_file():
            return p
    return None


def check_prerequisites() -> list[str]:
    """Verify all prerequisites for vlnggen compilation.

    Returns list of missing tools/files (empty if all OK).
    """
    missing = []

    if not shutil.which("ngspice"):
        missing.append("ngspice not found in PATH")

    if not shutil.which("verilator"):
        missing.append("verilator not found in PATH")

    if not shutil.which("g++"):
        missing.append("g++ not found in PATH")

    if find_vlnggen() is None:
        missing.append(
            "vlnggen script not found in standard locations: "
            + ", ".join(str(p) for p in _VLNGGEN_SEARCH_PATHS)
        )

    # Check for XSPICE digital code model
    for prefix in [
        Path.home() / ".local/lib/ngspice",
        Path("/usr/local/lib/ngspice"),
        Path("/usr/lib/ngspice"),
    ]:
        if (prefix / "digital.cm").is_file():
            break
    else:
        missing.append("XSPICE digital.cm code model not found")

    return missing


def compile_verilog(
    verilog_src: Path,
    work_dir: Path | None = None,
    timeout_s: int = 120,
) -> Path:
    """Compile a Verilog source file into a .so for ngspice d_cosim.

    Parameters
    ----------
    verilog_src : Path
        Path to the .v Verilog source file.
    work_dir : Path, optional
        Build directory. Defaults to verilog_src's parent.
    timeout_s : int
        Build timeout in seconds.

    Returns
    -------
    Path
        Path to the compiled .so shared library.

    Raises
    ------
    FileNotFoundError
        If vlnggen or the Verilog source is not found.
    RuntimeError
        If compilation fails.
    """
    verilog_src = Path(verilog_src).resolve()
    if not verilog_src.is_file():
        raise FileNotFoundError(f"Verilog source not found: {verilog_src}")

    vlnggen_path = find_vlnggen()
    if vlnggen_path is None:
        raise FileNotFoundError(
            "vlnggen script not found. Install ngspice >= 44 with XSPICE support."
        )

    if work_dir is None:
        work_dir = verilog_src.parent
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    # Copy Verilog source to work_dir if not already there
    work_src = work_dir / verilog_src.name
    if work_src.resolve() != verilog_src.resolve():
        shutil.copy2(verilog_src, work_src)

    # Expected output: <stem>.so
    expected_so = work_dir / f"{verilog_src.stem}.so"

    # Clean previous build artifacts to avoid stale results
    obj_dir = work_dir / f"{verilog_src.stem}_obj_dir"
    if obj_dir.is_dir():
        shutil.rmtree(obj_dir)
    if expected_so.is_file():
        expected_so.unlink()

    logger.info(
        "Compiling %s via vlnggen (Verilator -> .so)", verilog_src.name
    )

    # vlnggen is an ngspice script, invoked as: ngspice <vlnggen_path> <source.v>
    proc = subprocess.run(
        ["ngspice", str(vlnggen_path), verilog_src.name],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        cwd=str(work_dir),
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"vlnggen compilation failed (exit {proc.returncode}):\n"
            f"stdout: {proc.stdout[-2000:]}\n"
            f"stderr: {proc.stderr[-2000:]}"
        )

    if not expected_so.is_file():
        raise RuntimeError(
            f"vlnggen completed but {expected_so.name} was not produced.\n"
            f"stdout: {proc.stdout[-1000:]}"
        )

    logger.info("Compiled %s (%d bytes)", expected_so.name, expected_so.stat().st_size)
    return expected_so
