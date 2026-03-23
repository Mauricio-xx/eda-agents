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
import traceback
from pathlib import Path


def generate(spec: dict) -> dict:
    """Generate a layout component from spec."""
    component = spec["component"]
    params = spec.get("params", {})
    output_dir = Path(spec["output_dir"])
    pdk = spec.get("pdk", "gf180mcu")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Import gLayout components
    try:
        import gdstk
    except ImportError:
        return {"success": False, "error": "gdstk not installed in this venv"}

    try:
        from glayout.flow.pdk.gf180_mapped import gf180
    except ImportError:
        return {
            "success": False,
            "error": "gLayout (glayout.flow.pdk.gf180_mapped) not installed",
        }

    pdk_obj = gf180

    # Map component names to gLayout generators
    component_lower = component.lower()

    try:
        if component_lower in ("nmos", "nfet"):
            from glayout.flow.primitives.fet import nmos

            width = float(params.get("width", 1.0))
            length = float(params.get("length", 0.28))
            fingers = int(params.get("fingers", 1))

            cell = nmos(
                pdk=pdk_obj,
                width=width,
                length=length,
                fingers=fingers,
            )

        elif component_lower in ("pmos", "pfet"):
            from glayout.flow.primitives.fet import pmos

            width = float(params.get("width", 1.0))
            length = float(params.get("length", 0.28))
            fingers = int(params.get("fingers", 1))

            cell = pmos(
                pdk=pdk_obj,
                width=width,
                length=length,
                fingers=fingers,
            )

        elif component_lower in ("mimcap", "mim_cap", "mim"):
            from glayout.flow.primitives.mimcap import mimcap

            cap_size = (
                float(params.get("width", 5.0)),
                float(params.get("length", 5.0)),
            )

            cell = mimcap(pdk=pdk_obj, size=cap_size)

        else:
            return {
                "success": False,
                "error": (
                    f"Unknown component: {component}. "
                    f"Supported: nmos, pmos, mimcap"
                ),
            }

        # Write GDS
        gds_path = output_dir / f"{component_lower}.gds"
        cell.write_gds(str(gds_path))

        if not gds_path.is_file():
            return {"success": False, "error": f"GDS not written: {gds_path}"}

        return {"success": True, "gds_path": str(gds_path)}

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
