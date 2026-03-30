"""Magic parasitic extraction (PEX) runner for GF180MCU.

Wraps Magic's ext2spice flow to extract parasitic R/C from a GDS file
and produce an ngspice-compatible netlist. Uses the GF180MCU tech file
and magicrc for process-aware extraction.

Adapted from LibreLane's spice_rcx.tcl (Apache-2.0, Efabless/LibreLane).

Setup requirements:
    - Magic 8.3+ installed and in PATH
    - GF180MCU PDK with libs.tech/magic/ (tech file + magicrc)
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Default extraction corner for GF180MCU
_DEFAULT_CORNER = "ngspice()"

# Tcl script template for flat parasitic extraction
_PEX_TCL_TEMPLATE = """\
# Magic PEX extraction script (auto-generated)
gds read {gds_path}
load {design_name}

# Flatten hierarchy for full extraction
select top cell
flatten flat
load flat
cellname delete {design_name}
cellname rename flat {design_name}
select top cell

# Configure extraction
extract style {corner}
extract do local
{do_capacitance}
{do_resistance}
extract do coupling
extract do adjust
extract do unique
extract warn all

# Perform extraction
extract all

# Convert to SPICE netlist
ext2spice lvs
ext2spice cthresh {cthresh}
{extresist_line}
ext2spice -f ngspice -o {output_path} {design_name}.ext

quit -noprompt
"""

# Hierarchical extraction: does NOT flatten, preserves subcell boundaries
_PEX_HIERARCHICAL_TCL_TEMPLATE = """\
# Magic hierarchical PEX extraction script (auto-generated)
gds read {gds_path}
load {design_name}
select top cell

# Configure extraction
extract style {corner}
extract do local
{do_capacitance}
{do_resistance}
extract do coupling
extract do adjust
extract warn all

# Hierarchical extraction (no flatten)
extract all

# Convert to SPICE netlist
ext2spice lvs
ext2spice cthresh {cthresh}
{extresist_line}
ext2spice hierarchy on
ext2spice subcircuit top auto
ext2spice -f ngspice -o {output_path} {design_name}.ext

quit -noprompt
"""


def _inject_ports(netlist_path: Path, port_names: list[str] | None) -> None:
    """Post-process extracted netlist to add port declarations.

    Magic's ext2spice often produces `.subckt <name>` with no ports when
    extracting from GDS with text labels. This function finds the subckt
    line and adds the specified port names.

    Only modifies the file if the subckt line has no ports AND the
    port names are found as internal net names in the netlist body.
    """
    if not port_names or not netlist_path.is_file():
        return

    text = netlist_path.read_text()
    lines = text.splitlines()
    modified = False

    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if stripped.startswith(".subckt"):
            parts = line.split()
            if len(parts) == 2:  # ".subckt name" with no ports
                subckt_name = parts[1]
                # Verify port names exist as nets in the body
                body = "\n".join(lines[i + 1:])
                valid_ports = [p for p in port_names if p in body or p.lower() in body.lower()]
                if valid_ports:
                    lines[i] = f".subckt {subckt_name} {' '.join(valid_ports)}"
                    modified = True
            break  # only process first subckt

    if modified:
        netlist_path.write_text("\n".join(lines) + "\n")


@dataclass
class MagicPexResult:
    """Result of a Magic parasitic extraction run."""

    success: bool
    extracted_netlist_path: str | None = None
    corner: str = _DEFAULT_CORNER
    run_time_s: float = 0.0
    error: str | None = None

    @property
    def summary(self) -> str:
        if self.error:
            return f"Magic PEX error: {self.error}"
        return f"Magic PEX: extracted -> {self.extracted_netlist_path} ({self.corner})"


class MagicPexRunner:
    """Extract parasitics from a GDS file using Magic.

    Generates a Tcl script that flattens the design, runs Magic's
    extractor, and converts to an ngspice-compatible SPICE netlist
    with parasitic R and C.

    Parameters
    ----------
    pdk_root : str or None
        Path to PDK root. Falls back to PDK_ROOT env, then GF180MCU_D default.
    corner : str
        Extraction style/corner. Default "ngspice()" (nominal).
    timeout_s : int
        Maximum runtime in seconds.
    do_resistance : bool
        Extract parasitic resistance. Default True.
    do_capacitance : bool
        Extract parasitic capacitance. Default True.
    cthresh : float
        Capacitance threshold in fF for ext2spice filtering. Default 0.01.
    """

    def __init__(
        self,
        pdk_root: str | None = None,
        corner: str = _DEFAULT_CORNER,
        timeout_s: int = 300,
        do_resistance: bool = True,
        do_capacitance: bool = True,
        cthresh: float = 0.01,
        flatten: bool = True,
    ):
        self.corner = corner
        self.timeout_s = timeout_s
        self.do_resistance = do_resistance
        self.do_capacitance = do_capacitance
        self.cthresh = cthresh
        self.flatten = flatten

        if pdk_root:
            self.pdk_root = Path(pdk_root)
        else:
            # Default to GF180MCU_D (not PDK_ROOT env, which may be IHP)
            from eda_agents.core.pdk import GF180MCU_D
            self.pdk_root = Path(GF180MCU_D.default_pdk_root)

        self._tech_file = self.pdk_root / "gf180mcuD/libs.tech/magic/gf180mcuD.tech"
        self._magicrc = self.pdk_root / "gf180mcuD/libs.tech/magic/gf180mcuD.magicrc"

    def validate_setup(self) -> list[str]:
        """Check prerequisites. Returns list of problems (empty = OK)."""
        problems = []

        if not shutil.which("magic"):
            problems.append("magic not found in PATH")

        if not self.pdk_root.is_dir():
            problems.append(f"PDK root not found: {self.pdk_root}")

        if not self._tech_file.is_file():
            problems.append(f"Magic tech file not found: {self._tech_file}")

        if not self._magicrc.is_file():
            problems.append(f"Magic RC file not found: {self._magicrc}")

        return problems

    def run(
        self,
        gds_path: str | Path,
        design_name: str,
        work_dir: str | Path,
        port_names: list[str] | None = None,
    ) -> MagicPexResult:
        """Run parasitic extraction on a GDS file.

        Parameters
        ----------
        gds_path : path
            Input GDS file.
        design_name : str
            Top cell name in the GDS.
        work_dir : path
            Working directory for extraction artifacts.
        port_names : list[str] or None
            Port names to inject into the extracted subcircuit declaration.
            Magic often produces `.subckt <name>` with no ports because
            GDS text labels don't define port order. If provided, the
            extracted netlist is post-processed to add these ports.

        Returns
        -------
        MagicPexResult
        """
        import subprocess

        gds_path = Path(gds_path).resolve()
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        if not gds_path.is_file():
            return MagicPexResult(
                success=False,
                error=f"GDS file not found: {gds_path}",
            )

        if not shutil.which("magic"):
            return MagicPexResult(
                success=False,
                error="magic not found in PATH",
            )

        if not self._magicrc.is_file():
            return MagicPexResult(
                success=False,
                error=f"Magic RC file not found: {self._magicrc}",
            )

        output_path = work_dir / f"{design_name}.rcx.spice"

        # Build Tcl script
        template = _PEX_TCL_TEMPLATE if self.flatten else _PEX_HIERARCHICAL_TCL_TEMPLATE
        tcl_content = template.format(
            gds_path=str(gds_path),
            design_name=design_name,
            corner=self.corner,
            do_capacitance="extract do capacitance" if self.do_capacitance else "",
            do_resistance="extract do resistance" if self.do_resistance else "",
            cthresh=self.cthresh,
            extresist_line="ext2spice extresist on" if self.do_resistance else "",
            output_path=str(output_path),
        )

        tcl_path = work_dir / "_pex_extract.tcl"
        tcl_path.write_text(tcl_content)

        env = os.environ.copy()
        env["PDK_ROOT"] = str(self.pdk_root)

        cmd = [
            "magic",
            "-dnull",
            "-noconsole",
            "-rcfile",
            str(self._magicrc),
            str(tcl_path),
        ]

        logger.info("Running Magic PEX: %s", " ".join(cmd))
        t0 = time.monotonic()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=str(work_dir),
                env=env,
            )
        except FileNotFoundError:
            return MagicPexResult(
                success=False,
                error="magic executable not found",
            )
        except subprocess.TimeoutExpired:
            return MagicPexResult(
                success=False,
                error=f"Magic PEX timed out after {self.timeout_s}s",
                run_time_s=time.monotonic() - t0,
            )

        elapsed = time.monotonic() - t0

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            return MagicPexResult(
                success=False,
                error=stderr[-500:] or f"Magic exited with code {proc.returncode}",
                run_time_s=elapsed,
            )

        # Check output netlist was actually produced
        if not output_path.is_file():
            # Sometimes Magic writes it but with slightly different path
            rcx_files = list(work_dir.glob("*.rcx.spice"))
            if rcx_files:
                output_path = rcx_files[0]
            else:
                return MagicPexResult(
                    success=False,
                    error="Magic completed but no .rcx.spice output found",
                    run_time_s=elapsed,
                )

        # Post-process: inject port declarations if missing
        _inject_ports(output_path, port_names)

        return MagicPexResult(
            success=True,
            extracted_netlist_path=str(output_path),
            corner=self.corner,
            run_time_s=elapsed,
        )
