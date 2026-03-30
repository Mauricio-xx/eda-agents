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
# OTA layout generation (gLayout opamp_twostage)
# ---------------------------------------------------------------------------


def generate_ota_layout(
    params: dict[str, float] | None = None,
    output_dir: str = "",
    pdk: str = "gf180mcu",
) -> dict:
    """Generate a full OTA layout from design parameters.

    Uses GF180OTATopology to convert design parameters to gLayout's
    opamp_twostage() format. Produces both GDS and SPICE netlist.

    Parameters
    ----------
    params : dict or None
        Design space parameters (Ibias_uA, L_dp_um, etc.).
        Uses default_params() if None.
    output_dir : str
        Output directory. Uses temp dir if empty.
    pdk : str
        Target PDK. Default "gf180mcu".

    Returns
    -------
    dict
        Keys: success, gds_path, netlist_path, summary, run_time_s, or error.
    """
    from eda_agents.core.glayout_runner import GLayoutRunner
    from eda_agents.topologies.ota_gf180 import GF180OTATopology

    if not output_dir:
        import tempfile
        output_dir = tempfile.mkdtemp(prefix="ota_layout_")

    topo = GF180OTATopology()
    if params is None:
        params = topo.default_params()

    sizing = topo.params_to_sizing(params)
    runner = GLayoutRunner(pdk=pdk)
    result = runner.generate_ota(sizing, output_dir)

    if result.error:
        return {"error": result.error}

    return {
        "success": result.success,
        "gds_path": result.gds_path,
        "netlist_path": result.netlist_path,
        "summary": result.summary,
        "run_time_s": result.run_time_s,
    }


# ---------------------------------------------------------------------------
# Magic PEX (parasitic extraction)
# ---------------------------------------------------------------------------


def run_magic_pex(
    gds_path: str,
    design_name: str = "",
    pdk_root: str = "",
    corner: str = "ngspice()",
    timeout_s: int = 300,
) -> dict:
    """Run Magic parasitic extraction on a GDS file.

    Parameters
    ----------
    gds_path : str
        Path to the GDS file.
    design_name : str
        Top cell name. Defaults to GDS filename stem.
    pdk_root : str
        Path to PDK root. Uses env/default if empty.
    corner : str
        Extraction corner/style. Default "ngspice()".
    timeout_s : int
        Maximum runtime in seconds.

    Returns
    -------
    dict
        Keys: success, extracted_netlist_path, corner, summary, run_time_s, or error.
    """
    from eda_agents.core.magic_pex import MagicPexRunner

    gds = Path(gds_path)
    if not gds.is_file():
        return {"error": f"GDS file not found: {gds_path}"}

    if not design_name:
        design_name = gds.stem

    work_dir = gds.parent / f"_pex_{design_name}"

    runner = MagicPexRunner(
        pdk_root=pdk_root or None,
        corner=corner,
        timeout_s=timeout_s,
    )

    result = runner.run(gds_path=gds_path, design_name=design_name, work_dir=work_dir)

    if result.error:
        return {"error": result.error}

    return {
        "success": result.success,
        "extracted_netlist_path": result.extracted_netlist_path,
        "corner": result.corner,
        "summary": result.summary,
        "run_time_s": result.run_time_s,
    }


# ---------------------------------------------------------------------------
# Full post-layout validation pipeline
# ---------------------------------------------------------------------------


def run_postlayout_validation(
    params: dict[str, float] | None = None,
    pre_layout_fom: float = 0.0,
    output_dir: str = "",
    pdk_root: str = "",
    skip_drc: bool = False,
    skip_lvs: bool = False,
) -> dict:
    """Run the full post-layout validation pipeline.

    Orchestrates: layout -> DRC -> LVS -> PEX -> post-layout SPICE.

    Parameters
    ----------
    params : dict or None
        Design parameters. Uses defaults if None.
    pre_layout_fom : float
        Pre-layout FoM for delta computation.
    output_dir : str
        Output directory. Uses temp dir if empty.
    pdk_root : str
        Path to PDK root.
    skip_drc : bool
        Skip DRC step.
    skip_lvs : bool
        Skip LVS step.

    Returns
    -------
    dict
        Keys: success, summary, gds_path, drc_clean, lvs_match,
        extracted_netlist_path, post_Adc_dB, post_GBW_Hz, post_PM_deg,
        post_fom, fom_delta_pct, total_time_s, or error.
    """
    from eda_agents.agents.postlayout_validator import PostLayoutValidator
    from eda_agents.core.glayout_runner import GLayoutRunner
    from eda_agents.core.magic_pex import MagicPexRunner
    from eda_agents.core.spice_runner import SpiceRunner
    from eda_agents.topologies.ota_gf180 import GF180OTATopology

    if not output_dir:
        import tempfile
        output_dir = tempfile.mkdtemp(prefix="postlayout_")

    topo = GF180OTATopology()
    if params is None:
        params = topo.default_params()

    glayout = GLayoutRunner()
    pex_runner = MagicPexRunner(pdk_root=pdk_root or None)
    spice = SpiceRunner(pdk="gf180mcu")

    drc_runner = None
    lvs_runner = None

    if not skip_drc:
        try:
            from eda_agents.core.klayout_drc import KLayoutDrcRunner
            drc_runner = KLayoutDrcRunner(pdk_root=pdk_root or None)
        except Exception:
            pass

    if not skip_lvs:
        try:
            from eda_agents.core.klayout_lvs import KLayoutLvsRunner
            lvs_runner = KLayoutLvsRunner(pdk_root=pdk_root or None)
        except Exception:
            pass

    validator = PostLayoutValidator(
        topology=topo,
        glayout_runner=glayout,
        magic_pex_runner=pex_runner,
        spice_runner=spice,
        drc_runner=drc_runner,
        lvs_runner=lvs_runner,
    )

    result = validator.validate(
        params=params,
        pre_layout_fom=pre_layout_fom,
        work_dir=output_dir,
    )

    if result.error:
        return {"error": result.error, "total_time_s": result.total_time_s}

    return {
        "success": True,
        "summary": result.summary,
        "gds_path": result.gds_path,
        "drc_clean": result.drc_clean,
        "drc_violations": result.drc_violations,
        "lvs_match": result.lvs_match,
        "extracted_netlist_path": result.extracted_netlist_path,
        "pex_corner": result.pex_corner,
        "post_Adc_dB": result.post_Adc_dB,
        "post_GBW_Hz": result.post_GBW_Hz,
        "post_PM_deg": result.post_PM_deg,
        "post_fom": result.post_fom,
        "post_valid": result.post_valid,
        "fom_delta_pct": result.fom_delta_pct,
        "total_time_s": result.total_time_s,
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
