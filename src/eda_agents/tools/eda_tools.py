"""EDA tool wrappers for agent-callable operations.

These functions wrap external EDA tools (Magic, KLayout, Netgen,
wafer-space precheck) into agent-callable interfaces compatible
with Google ADK FunctionTool.

Each function returns a dict with structured results suitable
for LLM consumption. Errors are returned as dicts with "error"
key rather than raising exceptions.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


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
        Keys: success, violations (list), total_errors (int),
        summary (str), or error (str) on failure.
    """
    gds = Path(gds_path)
    if not gds.is_file():
        return {"error": f"GDS file not found: {gds_path}"}

    # Build Magic DRC script
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
            ["magic", "-dnull", "-noconsole", "-rcfile",
             f"{pdk_root}/gf180mcuD/libs.tech/magic/gf180mcuD.magicrc",
             str(script_path)],
            capture_output=True, text=True, timeout=timeout_s,
            cwd=str(gds.parent), env=env,
        )
    except FileNotFoundError:
        return {"error": "magic not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"error": f"Magic DRC timed out ({timeout_s}s)"}

    stdout = proc.stdout or ""

    # Parse DRC count
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


def run_klayout_drc(
    gds_path: str,
    drc_deck: str,
    timeout_s: int = 300,
) -> dict:
    """Run KLayout DRC with a rule deck.

    Parameters
    ----------
    gds_path : str
        Path to the GDS file.
    drc_deck : str
        Path to the KLayout DRC rule deck (.lydrc or .rb).
    timeout_s : int
        Maximum runtime in seconds.

    Returns
    -------
    dict
        Keys: success, total_errors, report_path, or error.
    """
    gds = Path(gds_path)
    if not gds.is_file():
        return {"error": f"GDS file not found: {gds_path}"}
    if not Path(drc_deck).is_file():
        return {"error": f"DRC deck not found: {drc_deck}"}

    report_path = gds.parent / f"{gds.stem}_drc.lyrdb"

    try:
        proc = subprocess.run(
            ["klayout", "-b", "-r", drc_deck,
             "-rd", f"input={gds}",
             "-rd", f"report={report_path}"],
            capture_output=True, text=True, timeout=timeout_s,
            cwd=str(gds.parent),
        )
    except FileNotFoundError:
        return {"error": "klayout not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"error": f"KLayout DRC timed out ({timeout_s}s)"}

    # Count violations from report
    total = 0
    if report_path.is_file():
        content = report_path.read_text()
        total = content.count("<item>")

    return {
        "success": proc.returncode == 0,
        "total_errors": total,
        "clean": total == 0,
        "report_path": str(report_path),
        "summary": f"KLayout DRC: {total} violations" if total else "KLayout DRC: clean",
    }


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
        Keys: success, match (bool), mismatches (int), summary, or error.
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
            ["netgen", "-batch", "lvs",
             f'"{schematic_netlist}" "{layout_netlist}"',
             setup_file, str(comp_out)],
            capture_output=True, text=True, timeout=timeout_s,
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
        Keys: success, checks (dict of check_name: pass/fail), summary.
    """
    gds = Path(gds_path)
    if not gds.is_file():
        return {"error": f"GDS file not found: {gds_path}"}
    if not Path(precheck_dir).is_dir():
        return {"error": f"Precheck dir not found: {precheck_dir}"}

    try:
        proc = subprocess.run(
            ["python3", f"{precheck_dir}/precheck.py",
             "--gds", str(gds),
             "--top-cell", top_cell,
             "--slot-size", slot_size],
            capture_output=True, text=True, timeout=timeout_s,
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
