"""EDA tool wrappers for agent-callable operations.

These functions wrap external EDA tools (Magic, KLayout, Netgen,
gLayout) into agent-callable interfaces compatible with Google ADK
FunctionTool.

Each function returns a dict with structured results suitable
for LLM consumption. Errors are returned as dicts with "error"
key rather than raising exceptions.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# Magic DRC (unchanged, for reference / IHP flows)
# ---------------------------------------------------------------------------


def run_magic_drc(
    gds_path: str,
    pdk_root: str,
    top_cell: str = "",
    timeout_s: int = 300,
) -> dict:
    """Run Magic DRC on a GDS file.

    Parameters
    ----------
    gds_path : str
        Path to the GDS file to check.
    pdk_root : str
        Path to PDK root (for technology files).
    top_cell : str
        Top cell name. If empty, Magic auto-detects.
    timeout_s : int
        Maximum runtime in seconds.

    Returns
    -------
    dict
        Keys: success, total_errors, clean, summary, stdout_tail,
        or error on failure.
    """
    gds = Path(gds_path)
    if not gds.is_file():
        return {"error": f"GDS file not found: {gds_path}"}

    cell = top_cell or gds.stem
    tcl_script = f"""
gds read {gds}
load {cell}
select top cell
drc check
drc catchup
set count [drc count total]
puts "DRC_TOTAL: $count"
quit
"""
    script_path = gds.parent / "_drc_check.tcl"
    script_path.write_text(tcl_script)

    env = os.environ.copy()
    env["PDK_ROOT"] = pdk_root

    try:
        proc = subprocess.run(
            [
                "magic",
                "-dnull",
                "-noconsole",
                "-rcfile",
                f"{pdk_root}/gf180mcuD/libs.tech/magic/gf180mcuD.magicrc",
                str(script_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(gds.parent),
            env=env,
        )
    except FileNotFoundError:
        return {"error": "magic not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"error": f"Magic DRC timed out ({timeout_s}s)"}

    stdout = proc.stdout or ""

    total = 0
    for line in stdout.splitlines():
        if "DRC_TOTAL:" in line:
            try:
                total = int(line.split("DRC_TOTAL:")[1].strip())
            except ValueError:
                pass

    return {
        "success": proc.returncode == 0,
        "total_errors": total,
        "clean": total == 0,
        "summary": f"Magic DRC: {total} violations" if total else "Magic DRC: clean",
        "stdout_tail": stdout[-2000:],
    }


# ---------------------------------------------------------------------------
# KLayout DRC (GF180MCU PDK -- real implementation)
# ---------------------------------------------------------------------------


def run_klayout_drc(
    gds_path: str,
    top_cell: str = "",
    variant: str = "C",
    table: str = "",
    pdk_root: str = "",
    timeout_s: int = 600,
) -> dict:
    """Run KLayout DRC on a GDS file using the GF180MCU PDK rule deck.

    Invokes the PDK's run_drc.py script which handles rule deck
    generation, parallel table execution, and results reporting.

    Parameters
    ----------
    gds_path : str
        Path to the GDS file.
    top_cell : str
        Top cell name. Auto-detected if empty.
    variant : str
        PDK variant (A-F). Default "C" = 5LM, 9K top metal, MIM-B.
    table : str
        Specific rule table to check (e.g., "comp", "metal1").
        Empty = all tables.
    pdk_root : str
        Path to PDK root. Uses PDK_ROOT env or GF180MCU default if empty.
    timeout_s : int
        Maximum runtime in seconds.

    Returns
    -------
    dict
        Keys: success, total_errors, clean, violated_rules, report_path,
        summary, run_time_s, or error.
    """
    from eda_agents.core.klayout_drc import KLayoutDrcRunner

    runner = KLayoutDrcRunner(
        pdk_root=pdk_root or None,
        variant=variant,
        timeout_s=timeout_s,
    )

    gds = Path(gds_path)
    run_dir = gds.parent / f"_drc_{gds.stem}"

    result = runner.run(
        gds_path=gds_path,
        run_dir=run_dir,
        top_cell=top_cell or None,
        table=table or None,
    )

    if result.error:
        return {"error": result.error}

    return {
        "success": result.success,
        "total_errors": result.total_violations,
        "clean": result.clean,
        "violated_rules": result.violated_rules,
        "report_path": result.report_path,
        "summary": result.summary,
        "run_time_s": result.run_time_s,
    }


def read_drc_summary(report_path: str) -> dict:
    """Parse a KLayout .lyrdb report into a structured summary.

    Parameters
    ----------
    report_path : str
        Path to a .lyrdb file.

    Returns
    -------
    dict
        Keys: total_violations, violated_rules, clean, markdown_summary.
    """
    path = Path(report_path)
    if not path.is_file():
        return {"error": f"Report not found: {report_path}"}

    from eda_agents.core.klayout_drc import parse_lyrdb
    from eda_agents.parsers.klayout_drc import KLayoutDrcParser

    rules = parse_lyrdb(path)
    total = sum(rules.values())

    parser = KLayoutDrcParser()
    items = parser.parse(path)
    md = items[0].content if items else ""

    return {
        "total_violations": total,
        "violated_rules": rules,
        "clean": total == 0,
        "markdown_summary": md,
    }


# ---------------------------------------------------------------------------
# KLayout LVS (GF180MCU PDK -- real implementation)
# ---------------------------------------------------------------------------


def run_klayout_lvs(
    gds_path: str,
    netlist_path: str,
    top_cell: str = "",
    variant: str = "C",
    pdk_root: str = "",
    lvs_sub: str = "",
    timeout_s: int = 600,
) -> dict:
    """Run KLayout LVS comparing layout GDS against schematic netlist.

    Uses the GF180MCU PDK's run_lvs.py script.

    Parameters
    ----------
    gds_path : str
        Path to the GDS layout file.
    netlist_path : str
        Path to the reference SPICE/CDL netlist.
    top_cell : str
        Top cell name. Auto-detected if empty.
    variant : str
        PDK variant (A-D). Default "C".
    pdk_root : str
        Path to PDK root. Uses env or default if empty.
    lvs_sub : str
        Substrate net name. Default: gf180mcu_gnd.
    timeout_s : int
        Maximum runtime in seconds.

    Returns
    -------
    dict
        Keys: success, match, extracted_netlist_path, report_path,
        summary, run_time_s, or error.
    """
    from eda_agents.core.klayout_lvs import KLayoutLvsRunner

    runner = KLayoutLvsRunner(
        pdk_root=pdk_root or None,
        variant=variant,
        timeout_s=timeout_s,
    )

    gds = Path(gds_path)
    run_dir = gds.parent / f"_lvs_{gds.stem}"

    result = runner.run(
        gds_path=gds_path,
        netlist_path=netlist_path,
        run_dir=run_dir,
        top_cell=top_cell or None,
        lvs_sub=lvs_sub or None,
    )

    if result.error:
        return {"error": result.error}

    return {
        "success": result.success,
        "match": result.match,
        "extracted_netlist_path": result.extracted_netlist_path,
        "report_path": result.report_path,
        "summary": result.summary,
        "run_time_s": result.run_time_s,
    }


# ---------------------------------------------------------------------------
# Netgen LVS (IHP/generic -- quoting bug fixed)
# ---------------------------------------------------------------------------


def run_netgen_lvs(
    schematic_netlist: str,
    layout_netlist: str,
    pdk_root: str,
    timeout_s: int = 300,
) -> dict:
    """Run Netgen LVS comparison.

    Parameters
    ----------
    schematic_netlist : str
        Path to the schematic SPICE netlist.
    layout_netlist : str
        Path to the extracted layout netlist.
    pdk_root : str
        Path to PDK root (for Netgen setup files).
    timeout_s : int
        Maximum runtime in seconds.

    Returns
    -------
    dict
        Keys: success, match, summary, report_path, stdout_tail, or error.
    """
    for label, path in [("schematic", schematic_netlist), ("layout", layout_netlist)]:
        if not Path(path).is_file():
            return {"error": f"{label} netlist not found: {path}"}

    setup_file = f"{pdk_root}/gf180mcuD/libs.tech/netgen/gf180mcuD_setup.tcl"
    if not Path(setup_file).is_file():
        return {"error": f"Netgen setup not found: {setup_file}"}

    work_dir = Path(schematic_netlist).parent
    comp_out = work_dir / "lvs_comp.out"

    try:
        proc = subprocess.run(
            [
                "netgen",
                "-batch",
                "lvs",
                schematic_netlist,
                layout_netlist,
                setup_file,
                str(comp_out),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(work_dir),
        )
    except FileNotFoundError:
        return {"error": "netgen not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"error": f"Netgen LVS timed out ({timeout_s}s)"}

    stdout = proc.stdout or ""
    match = "match" in stdout.lower() and "unique" not in stdout.lower()

    return {
        "success": proc.returncode == 0,
        "match": match,
        "summary": "LVS: match" if match else "LVS: mismatch",
        "report_path": str(comp_out) if comp_out.is_file() else None,
        "stdout_tail": stdout[-2000:],
    }


# ---------------------------------------------------------------------------
# Layout generation (gLayout / OpenFASOC)
# ---------------------------------------------------------------------------


def generate_layout(
    component: str,
    width_um: float,
    length_um: float,
    fingers: int = 1,
    output_dir: str = "",
    pdk: str = "gf180mcu",
) -> dict:
    """Generate a parameterized analog layout using gLayout.

    Requires .venv-glayout to be set up with gLayout, gdstk, and
    numpy<=1.24.0 installed.

    Parameters
    ----------
    component : str
        Component type: "nmos", "pmos", or "mimcap".
    width_um : float
        Width in micrometers.
    length_um : float
        Length in micrometers.
    fingers : int
        Number of fingers (for transistors).
    output_dir : str
        Output directory. Uses temp dir if empty.
    pdk : str
        Target PDK. Default "gf180mcu".

    Returns
    -------
    dict
        Keys: success, gds_path, component, summary, run_time_s, or error.
    """
    from eda_agents.core.glayout_runner import GLayoutRunner

    if not output_dir:
        import tempfile

        output_dir = tempfile.mkdtemp(prefix="glayout_")

    runner = GLayoutRunner(pdk=pdk)
    params = {
        "width": width_um,
        "length": length_um,
        "fingers": fingers,
    }

    result = runner.generate_component(
        component=component,
        params=params,
        output_dir=output_dir,
    )

    if result.error:
        return {"error": result.error}

    return {
        "success": result.success,
        "gds_path": result.gds_path,
        "component": result.component,
        "summary": result.summary,
        "run_time_s": result.run_time_s,
    }


# ---------------------------------------------------------------------------
# Precheck (deferred -- individual checks covered by DRC flags)
# ---------------------------------------------------------------------------


def run_precheck(
    gds_path: str,
    top_cell: str,
    precheck_dir: str,
    slot_size: str = "4x2",
    timeout_s: int = 600,
) -> dict:
    """Run wafer-space precheck (DRC + dimensions + chip ID).

    Parameters
    ----------
    gds_path : str
        Path to the GDS file.
    top_cell : str
        Top cell name.
    precheck_dir : str
        Path to gf180mcu-precheck repository clone.
    slot_size : str
        Slot size (e.g., "4x2", "2x2").
    timeout_s : int
        Maximum runtime in seconds.

    Returns
    -------
    dict
        Keys: success, passed, summary, stdout_tail, or error.
    """
    gds = Path(gds_path)
    if not gds.is_file():
        return {"error": f"GDS file not found: {gds_path}"}
    if not Path(precheck_dir).is_dir():
        return {"error": f"Precheck dir not found: {precheck_dir}"}

    try:
        proc = subprocess.run(
            [
                "python3",
                f"{precheck_dir}/precheck.py",
                "--gds",
                str(gds),
                "--top-cell",
                top_cell,
                "--slot-size",
                slot_size,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=precheck_dir,
        )
    except FileNotFoundError:
        return {"error": "precheck.py not found"}
    except subprocess.TimeoutExpired:
        return {"error": f"Precheck timed out ({timeout_s}s)"}

    stdout = proc.stdout or ""
    passed = "PASS" in stdout.upper()

    return {
        "success": proc.returncode == 0,
        "passed": passed,
        "summary": "Precheck: PASS" if passed else "Precheck: FAIL",
        "stdout_tail": stdout[-3000:],
    }
