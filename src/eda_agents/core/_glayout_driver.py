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


# ---------------------------------------------------------------------------
# PDK dispatch (S11 Fase 4): route by spec['pdk'] name.
# ---------------------------------------------------------------------------


def _resolve_pdk(pdk_name: str):
    """Return the gLayout PDK object for the named PDK, or None.

    Maps canonical eda-agents PDK names to the gLayout module paths:

      - ``gf180mcu``     -> ``glayout.pdk.gf180_mapped:gf180_mapped_pdk``
      - ``ihp_sg13g2``   -> ``glayout.pdk.sg13g2_mapped:sg13g2_mapped_pdk``
      - (fallback for backwards compat: legacy gLayout layouts)

    Returns ``None`` if the PDK is not importable in the active
    ``.venv-glayout`` — the caller surfaces that as a spec error.
    """
    candidates: dict[str, tuple[tuple[str, str], ...]] = {
        "gf180mcu": (
            ("glayout.pdk.gf180_mapped", "gf180_mapped_pdk"),
            ("glayout.pdk.gf180_mapped", "gf180"),
            ("glayout.flow.pdk.gf180_mapped", "gf180"),
        ),
        "ihp_sg13g2": (
            ("glayout.pdk.sg13g2_mapped", "sg13g2_mapped_pdk"),
            ("glayout.pdk.sg13g2_mapped", "sg13g2"),
            # gLayout also exposes an ihp130 alias on the SG13G2 branch.
            ("glayout", "sg13g2"),
            ("glayout", "ihp130"),
        ),
        # Kept for completeness; not exercised by the eda-agents suite today.
        "sky130": (
            ("glayout.pdk.sky130_mapped", "sky130_mapped_pdk"),
            ("glayout", "sky130"),
        ),
    }

    for module_path, attr in candidates.get(pdk_name, ()):
        try:
            mod = __import__(module_path, fromlist=[attr])
            return getattr(mod, attr)
        except (ImportError, AttributeError):
            continue
    return None


def _import_block(name: str, module_candidates):
    """Best-effort import: tries each (module, attr) until one works."""
    for module_path, attr in module_candidates:
        try:
            mod = __import__(module_path, fromlist=[attr])
            return getattr(mod, attr)
        except (ImportError, AttributeError):
            continue
    return None


def _generate_diff_pair(pdk_obj, params: dict):
    """Generate a gLayout differential pair (SG13G2-clean, also works on gf180)."""
    fn = _import_block("diff_pair", (
        ("glayout.blocks.elementary.diff_pair", "diff_pair"),
        ("glayout.flow.blocks.elementary.diff_pair", "diff_pair"),
    ))
    if fn is None:
        raise ImportError("Cannot import diff_pair block from gLayout")
    kwargs = {}
    if "width" in params:
        kwargs["width"] = float(params["width"])
    if "length" in params:
        kwargs["length"] = float(params["length"])
    if "fingers" in params:
        kwargs["fingers"] = int(params["fingers"])
    return fn(pdk=pdk_obj, **kwargs)


def _generate_current_mirror(pdk_obj, params: dict):
    fn = _import_block("current_mirror", (
        ("glayout.blocks.elementary.current_mirror", "current_mirror"),
        ("glayout.flow.blocks.elementary.current_mirror", "current_mirror"),
    ))
    if fn is None:
        raise ImportError("Cannot import current_mirror block from gLayout")
    kwargs = {}
    if "width" in params:
        kwargs["width"] = float(params["width"])
    if "length" in params:
        kwargs["length"] = float(params["length"])
    if "fingers" in params:
        kwargs["fingers"] = int(params["fingers"])
    if "multipliers" in params:
        kwargs["multipliers"] = int(params["multipliers"])
    if "type" in params:
        kwargs["type"] = str(params["type"])
    return fn(pdk=pdk_obj, **kwargs)


def _generate_fvf(pdk_obj, params: dict):
    """Flipped voltage follower composite (SG13G2 LVS-clean; also gf180)."""
    fn = _import_block("flipped_voltage_follower", (
        ("glayout.blocks.elementary.FVF.fvf", "flipped_voltage_follower"),
        ("glayout.flow.blocks.elementary.FVF.fvf", "flipped_voltage_follower"),
    ))
    if fn is None:
        raise ImportError(
            "Cannot import flipped_voltage_follower (FVF) block from gLayout"
        )
    kwargs = {}
    if "width" in params:
        kwargs["width"] = float(params["width"])
    if "length" in params:
        kwargs["length"] = float(params["length"])
    if "fingers" in params:
        kwargs["fingers"] = int(params["fingers"])
    return fn(pdk=pdk_obj, **kwargs)


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
    pdk_name = spec.get("pdk", "gf180mcu")

    output_dir.mkdir(parents=True, exist_ok=True)

    # gdstk is required for port-label post-processing.
    try:
        import gdstk  # noqa: F401
    except ImportError:
        return {"success": False, "error": "gdstk not installed in this venv"}

    pdk_obj = _resolve_pdk(pdk_name)
    if pdk_obj is None:
        return {
            "success": False,
            "error": (
                f"gLayout PDK {pdk_name!r} not importable in this venv. "
                f"For SG13G2, reinstall the glayout fork with the SG13G2 "
                f"branch: "
                f"`.venv-glayout/bin/pip install --no-deps -e "
                f"/path/to/gLayout` (branch feature/sg13g2-pdk-support)."
            ),
        }

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

        elif component_lower in ("diff_pair", "diffpair", "differential_pair"):
            cell = _generate_diff_pair(pdk_obj, params)

        elif component_lower in (
            "current_mirror", "cmirror", "cur_mirror",
        ):
            cell = _generate_current_mirror(pdk_obj, params)

        elif component_lower in (
            "fvf", "flipped_voltage_follower",
        ):
            cell = _generate_fvf(pdk_obj, params)

        else:
            return {
                "success": False,
                "error": (
                    f"Unknown component: {component!r} for pdk={pdk_name!r}. "
                    f"Supported: nmos, pmos, mimcap, opamp (gf180 LVS-clean; "
                    f"SG13G2 GDS-only, see docs/s12_findings/), "
                    f"diff_pair, current_mirror, fvf."
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
