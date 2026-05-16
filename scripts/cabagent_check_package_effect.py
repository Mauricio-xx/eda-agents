#!/usr/bin/env python3
"""Real-tool validation for PostLayoutResult.to_package().

Runs the full PostLayoutValidator pipeline (gLayout + KLayout DRC + LVS +
Magic PEX + ngspice pre/post) on a GF180MCU OTA, then bundles the result
into a flat package and prints a structured report so a human reviewer
can confirm the manifest schema and file presence.

This is the mandatory real-tool validation per
feedback_always_validate.md — no mocks, no stubs.

Usage:
    PDK_ROOT=/path/to/wafer-space-gf180mcu \
        .venv/bin/python scripts/cabagent_check_package_effect.py

Optional knobs:
    --pdk-root PATH    Override PDK_ROOT lookup.
    --output-dir PATH  Where the run + package live (default /tmp/...).
    --skip-drc, --skip-lvs   Bypass those stages (manifest will reflect).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def _build(pdk_root: Path, skip_drc: bool, skip_lvs: bool):
    from eda_agents.agents.postlayout_validator import PostLayoutValidator
    from eda_agents.core.glayout_runner import GLayoutRunner
    from eda_agents.core.magic_pex import MagicPexRunner
    from eda_agents.core.spice_runner import SpiceRunner
    from eda_agents.topologies.ota_gf180 import GF180OTATopology

    topo = GF180OTATopology()
    spice = SpiceRunner(pdk="gf180mcu", pdk_root=str(pdk_root))
    glayout = GLayoutRunner()
    pex = MagicPexRunner(pdk_root=str(pdk_root))

    drc_runner = None
    lvs_runner = None
    if not skip_drc:
        from eda_agents.core.klayout_drc import KLayoutDrcRunner
        drc_runner = KLayoutDrcRunner(pdk_root=str(pdk_root))
    if not skip_lvs:
        from eda_agents.core.klayout_lvs import KLayoutLvsRunner
        lvs_runner = KLayoutLvsRunner(pdk_root=str(pdk_root))

    validator = PostLayoutValidator(
        topology=topo,
        glayout_runner=glayout,
        magic_pex_runner=pex,
        spice_runner=spice,
        drc_runner=drc_runner,
        lvs_runner=lvs_runner,
    )
    return topo, spice, validator


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pdk-root", type=str, default=None)
    p.add_argument("--output-dir", type=str, default="/tmp/cabagent_pkg_effect")
    p.add_argument("--skip-drc", action="store_true")
    p.add_argument("--skip-lvs", action="store_true")
    p.add_argument(
        "--mode",
        choices=("full", "hybrid"),
        default="full",
        help="full = validate() (DRC/LVS/full-PEX path); hybrid = validate_hybrid() (parasitics overlay onto pre-layout OTA)",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    pdk_root = Path(args.pdk_root or "/home/montanares/git/wafer-space-gf180mcu")
    if not pdk_root.is_dir():
        print(f"PDK root not found: {pdk_root}", file=sys.stderr)
        return 2

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    topo, spice, validator = _build(pdk_root, args.skip_drc, args.skip_lvs)

    # 1) Pre-layout SPICE (baseline) ----------------------------------------
    params = {
        "Ibias_uA": 200.0,
        "L_dp_um": 2.0,
        "L_load_um": 5.0,
        "Cc_pF": 2.0,
        "W_dp_um": 10.0,
    }
    sizing = topo.params_to_sizing(params)
    pre_dir = out / "pre_layout"
    pre_dir.mkdir(parents=True, exist_ok=True)
    cir_pre = topo.generate_netlist(sizing, pre_dir)
    pre = spice.run(cir_pre, work_dir=pre_dir)
    if not pre.success:
        print(f"Pre-layout SPICE failed: {pre.error}", file=sys.stderr)
        return 3
    pre_fom = topo.compute_fom(pre, sizing)
    print(f"[pre] Adc={pre.Adc_dB:.1f}dB GBW={pre.GBW_Hz/1e6:.2f}MHz PM={pre.PM_deg:.1f}deg FoM={pre_fom:.2e}")

    # 2) Post-layout pipeline -----------------------------------------------
    if args.mode == "hybrid":
        result = validator.validate_hybrid(
            params=params,
            pre_layout_fom=pre_fom,
            pre_layout_spice=pre,
            work_dir=out / "postlayout_hybrid",
        )
    else:
        result = validator.validate(
            params=params,
            pre_layout_fom=pre_fom,
            pre_layout_spice=pre,
            work_dir=out / "postlayout",
        )
    # Caller-side: caller knows where the pre-layout sim ran.
    result.pre_sim_dir = str(pre_dir)

    print(f"[post] {result.summary}")
    if result.error:
        print(f"  error: {result.error}", file=sys.stderr)
        # Still try to package whatever survived for diagnostic value.

    # 3) Package -----------------------------------------------------------
    pkg_dir = out / "package"
    if pkg_dir.exists():
        import shutil as _sh
        _sh.rmtree(pkg_dir)
    result.to_package(pkg_dir)

    # 4) Report ------------------------------------------------------------
    manifest_path = pkg_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    print()
    print("=" * 60)
    print(f"PACKAGE: {pkg_dir}")
    print("=" * 60)
    for f in sorted(pkg_dir.rglob("*")):
        rel = f.relative_to(pkg_dir)
        size = f.stat().st_size if f.is_file() else 0
        marker = "/" if f.is_dir() else f" ({size:>8d} B)"
        print(f"  {rel}{marker}")
    print()
    print("MANIFEST KEYS:")
    for k, v in manifest.items():
        if isinstance(v, (dict, list)):
            print(f"  {k}: {json.dumps(v)}")
        else:
            print(f"  {k}: {v!r}")
    print()
    must_have = {
        "schema_version": str,
        "pdk": (str, type(None)),
        "topology": (str, type(None)),
        "params": dict,
        "deltas": dict,
        "post_layout_metrics": dict,
        "pex_corner": str,
    }
    failures = []
    for key, t in must_have.items():
        if key not in manifest:
            failures.append(f"missing key {key!r}")
        elif not isinstance(manifest[key], t):
            failures.append(f"wrong type for {key!r}: {type(manifest[key]).__name__}")
    if failures:
        print("MANIFEST SCHEMA FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 4

    print("MANIFEST SCHEMA: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
