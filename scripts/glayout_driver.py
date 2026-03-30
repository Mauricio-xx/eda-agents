#!/usr/bin/env python3
"""gLayout driver script -- runs inside the gLayout venv.

Reads a JSON spec from stdin, generates a GDS component,
writes JSON result to stdout.

Input JSON format::

    {
        "component": "nmos",
        "params": {"width": 1.0, "length": 0.28, "fingers": 2},
        "output_dir": "/tmp/out",
        "pdk": "gf180mcu"
    }

Output JSON format::

    {"success": true, "gds_path": "/tmp/out/nmos.gds"}
    or
    {"success": false, "error": "error message"}

Invoked by GLayoutRunner as::

    .venv-glayout/bin/python scripts/glayout_driver.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _add_port_labels(gds_path: str, cell) -> None:
    """Add text labels at port locations for Magic extraction.

    gLayout stores port info in Component.ports but doesn't write GDS
    text labels. Magic needs these labels to identify nets during
    parasitic extraction. This post-processes the GDS to add them.

    Mapping from gLayout pin prefixes to netlist port names:
        pin_vdd_*                -> VDD       (met4, layer 46)
        pin_gnd_* / gnd_route_* -> GND       (met4, layer 46)
        pin_diffpairibias_*     -> DIFFPAIR_BIAS (met3, layer 42)
        pin_commonsourceibias_* -> CS_BIAS   (met5, layer 81)
        pin_plus_*              -> VP        (met3, layer 42)
        pin_minus_*             -> VN        (met3, layer 42)
        special_con_npr_con_*   -> VOUT      (met5, layer 81)
    """
    try:
        import gdstk
    except ImportError:
        return

    if not hasattr(cell, 'ports') or not cell.ports:
        return

    # Map: (port_name_prefix, netlist_label)
    # Use first match per label (prefer e1/N orientation for stable position)
    _PIN_MAP = [
        ("pin_vdd_e1", "VDD"),
        ("pin_gnd_N", "GND"),
        ("pin_diffpairibias_e1", "DIFFPAIR_BIAS"),
        ("pin_commonsourceibias_e1", "CS_BIAS"),
        ("pin_plus_e1", "VP"),
        ("pin_minus_e1", "VN"),
        ("special_con_npr_con_N", "VOUT"),
    ]

    port_matches = {}
    for prefix, label in _PIN_MAP:
        if prefix in cell.ports:
            port_matches[label] = cell.ports[prefix]

    if not port_matches:
        return

    lib = gdstk.read_gds(gds_path)
    top_cells = lib.top_level()
    if not top_cells:
        return

    top = top_cells[0]

    for label_text, port_obj in port_matches.items():
        layer = port_obj.layer[0] if isinstance(port_obj.layer, (tuple, list)) else port_obj.layer
        x, y = float(port_obj.center[0]), float(port_obj.center[1])
        # texttype=10 is required for Magic to recognize labels as port labels
        # (GF180MCU tech: calma <layer> 10 -> port, calma <layer> 0 -> noport)
        top.add(gdstk.Label(label_text, (x, y), layer=layer, texttype=10))

    lib.write_gds(gds_path)


def _import_fet(name: str):
    """Import nmos or pmos from gLayout, trying current then legacy paths."""
    for mod_path in ("glayout.primitives.fet", "glayout.flow.primitives.fet"):
        try:
            mod = __import__(mod_path, fromlist=[name])
            return getattr(mod, name)
        except (ImportError, AttributeError):
            continue
    raise ImportError(f"Cannot import {name} from glayout.primitives.fet or glayout.flow.primitives.fet")


def _import_mimcap():
    """Import mimcap from gLayout, trying current then legacy paths."""
    for mod_path in ("glayout.primitives.mimcap", "glayout.flow.primitives.mimcap"):
        try:
            mod = __import__(mod_path, fromlist=["mimcap"])
            return getattr(mod, "mimcap")
        except (ImportError, AttributeError):
            continue
    raise ImportError("Cannot import mimcap from glayout")


def _generate_opamp(pdk_obj, params: dict):
    """Generate a two-stage opamp using gLayout's opamp_twostage().

    Parameters
    ----------
    pdk_obj : MappedPDK
        GF180 PDK object.
    params : dict
        Keys matching opamp_twostage() signature:
        - half_diffpair_params: [W, L, fingers]
        - diffpair_bias: [W, L, fingers]
        - half_common_source_params: [W, L, fingers, mults]
        - half_common_source_bias: [W, L, fingers, mults]
        - half_pload: [W, L, fingers]
        - mim_cap_size: [W, L]
        - mim_cap_rows: int
    """
    for mod_path in (
        "glayout.blocks.composite.opamp.opamp_twostage",
        "glayout.flow.blocks.composite.opamp.opamp_twostage",
    ):
        try:
            mod = __import__(mod_path, fromlist=["opamp_twostage"])
            opamp_fn = getattr(mod, "opamp_twostage")
            break
        except (ImportError, AttributeError):
            continue
    else:
        raise ImportError("Cannot import opamp_twostage from gLayout")

    kwargs = {}
    for key in (
        "half_diffpair_params",
        "diffpair_bias",
        "half_common_source_params",
        "half_common_source_bias",
        "half_pload",
        "mim_cap_size",
    ):
        if key in params:
            kwargs[key] = tuple(params[key])

    if "mim_cap_rows" in params:
        kwargs["mim_cap_rows"] = int(params["mim_cap_rows"])

    return opamp_fn(pdk_obj, **kwargs)


def generate(spec: dict) -> dict:
    """Generate a layout component from spec."""
    component = spec["component"]
    params = spec.get("params", {})
    output_dir = Path(spec["output_dir"])
    _ = spec.get("pdk", "gf180mcu")  # reserved for multi-PDK support

    output_dir.mkdir(parents=True, exist_ok=True)

    # Import gLayout components -- try current import paths first, then legacy
    try:
        import gdstk  # noqa: F401
    except ImportError:
        return {"success": False, "error": "gdstk not installed in this venv"}

    pdk_obj = None
    # Try multiple module paths and symbol names (API changed across gLayout versions)
    _pdk_candidates = [
        ("glayout.pdk.gf180_mapped", "gf180_mapped_pdk"),
        ("glayout.pdk.gf180_mapped", "gf180"),
        ("glayout.flow.pdk.gf180_mapped", "gf180"),
    ]
    for pdk_module, pdk_attr in _pdk_candidates:
        try:
            mod = __import__(pdk_module, fromlist=[pdk_attr])
            pdk_obj = getattr(mod, pdk_attr)
            break
        except (ImportError, AttributeError):
            continue

    if pdk_obj is None:
        return {
            "success": False,
            "error": "gLayout PDK not found (tried glayout.pdk and glayout.flow.pdk)",
        }

    # Map component names to gLayout generators
    component_lower = component.lower()

    try:
        if component_lower in ("nmos", "nfet"):
            nmos = _import_fet("nmos")
            width = float(params.get("width", 1.0))
            length = float(params.get("length", 0.28))
            fingers = int(params.get("fingers", 1))
            cell = nmos(pdk=pdk_obj, width=width, length=length, fingers=fingers)

        elif component_lower in ("pmos", "pfet"):
            pmos = _import_fet("pmos")
            width = float(params.get("width", 1.0))
            length = float(params.get("length", 0.28))
            fingers = int(params.get("fingers", 1))
            cell = pmos(pdk=pdk_obj, width=width, length=length, fingers=fingers)

        elif component_lower in ("mimcap", "mim_cap", "mim"):
            mimcap = _import_mimcap()
            cap_size = (
                float(params.get("width", 5.0)),
                float(params.get("length", 5.0)),
            )
            cell = mimcap(pdk=pdk_obj, size=cap_size)

        elif component_lower in ("opamp", "opamp_twostage", "ota"):
            cell = _generate_opamp(pdk_obj, params)

        else:
            return {
                "success": False,
                "error": (
                    f"Unknown component: {component}. "
                    f"Supported: nmos, pmos, mimcap, opamp"
                ),
            }

        # Rename top cell to a predictable name (gLayout uses auto-generated hashes)
        desired_name = component_lower
        if hasattr(cell, 'name') and cell.name != desired_name:
            try:
                cell.name = desired_name
            except Exception:
                pass  # some gdsfactory versions don't allow renaming

        # Write GDS
        gds_path = output_dir / f"{component_lower}.gds"
        cell.write_gds(str(gds_path))

        if not gds_path.is_file():
            return {"success": False, "error": f"GDS not written: {gds_path}"}

        # Add port labels to GDS for Magic extraction
        _add_port_labels(str(gds_path), cell)

        top_cell_name = cell.name if hasattr(cell, 'name') else component_lower
        result = {"success": True, "gds_path": str(gds_path), "top_cell": top_cell_name}

        # Export netlist if available (opamp_twostage stores it in info['netlist'])
        netlist_obj = cell.info.get("netlist") if hasattr(cell, "info") else None
        if netlist_obj is not None:
            try:
                netlist_text = netlist_obj.generate_netlist()
                netlist_path = output_dir / f"{component_lower}.spice"
                netlist_path.write_text(netlist_text)
                if netlist_path.is_file():
                    result["netlist_path"] = str(netlist_path)
            except Exception as ne:
                result["netlist_warning"] = f"Netlist export failed: {ne}"

        return result

    except Exception as e:
        return {
            "success": False,
            "error": f"{type(e).__name__}: {e}",
        }


def main():
    try:
        raw = sys.stdin.read()
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        result = {"success": False, "error": f"Invalid JSON input: {e}"}
        print(json.dumps(result))
        sys.exit(1)

    result = generate(spec)
    print(json.dumps(result))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
