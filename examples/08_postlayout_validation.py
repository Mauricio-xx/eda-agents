#!/usr/bin/env python3
"""Post-layout validation for GF180 OTA designs.

Demonstrates the full analog design closure loop:
    sizing -> layout (gLayout) -> DRC (KLayout) -> LVS (KLayout)
    -> PEX (Magic) -> post-layout SPICE -> pre/post comparison

Usage:
    # Validate default OTA design
    python examples/08_postlayout_validation.py

    # Custom parameters
    python examples/08_postlayout_validation.py \
        --ibias 200 --ldp 2.0 --lload 5.0 --cc 2.0 --wdp 10.0

    # Skip DRC/LVS (faster, layout+PEX+SPICE only)
    python examples/08_postlayout_validation.py --skip-drc --skip-lvs

    # Dry run (check prerequisites only)
    python examples/08_postlayout_validation.py --dry-run

    # From autoresearch results directory
    python examples/08_postlayout_validation.py \
        --from-autoresearch /tmp/autoresearch_results/
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("postlayout")


def check_prerequisites(skip_drc: bool = False, skip_lvs: bool = False) -> list[str]:
    """Check all pipeline prerequisites."""
    problems = []

    # gLayout venv
    from eda_agents.core.glayout_runner import GLayoutRunner
    glayout = GLayoutRunner()
    problems.extend(glayout.validate_setup())

    # Magic PEX
    from eda_agents.core.magic_pex import MagicPexRunner
    pex = MagicPexRunner()
    problems.extend(pex.validate_setup())

    # ngspice
    from eda_agents.core.spice_runner import SpiceRunner
    try:
        spice = SpiceRunner(pdk="gf180mcu")
        spice_problems = spice.validate_pdk()
        if spice_problems:
            problems.extend(spice_problems)
    except Exception as e:
        problems.append(f"SpiceRunner init failed: {e}")

    # KLayout DRC
    if not skip_drc:
        try:
            from eda_agents.core.klayout_drc import KLayoutDrcRunner
            drc = KLayoutDrcRunner()
            problems.extend(drc.validate_setup())
        except Exception as e:
            problems.append(f"KLayout DRC: {e}")

    # KLayout LVS
    if not skip_lvs:
        try:
            from eda_agents.core.klayout_lvs import KLayoutLvsRunner
            lvs = KLayoutLvsRunner()
            problems.extend(lvs.validate_setup())
        except Exception as e:
            problems.append(f"KLayout LVS: {e}")

    return problems


def run_single(
    params: dict[str, float],
    output_dir: Path,
    skip_drc: bool = False,
    skip_lvs: bool = False,
) -> None:
    """Run post-layout validation on a single design."""
    from eda_agents.agents.postlayout_validator import PostLayoutValidator
    from eda_agents.core.glayout_runner import GLayoutRunner
    from eda_agents.core.magic_pex import MagicPexRunner
    from eda_agents.core.spice_runner import SpiceRunner
    from eda_agents.topologies.ota_gf180 import GF180OTATopology

    topo = GF180OTATopology()

    # Run pre-layout SPICE first for comparison
    logger.info("Running pre-layout SPICE for baseline...")
    sizing = topo.params_to_sizing(params)
    pre_dir = output_dir / "pre_layout"
    cir_path = topo.generate_netlist(sizing, pre_dir)

    spice = SpiceRunner(pdk="gf180mcu")
    pre_result = spice.run(cir_path, work_dir=pre_dir)

    if pre_result.success:
        pre_fom = topo.compute_fom(pre_result, sizing)
        logger.info(
            "Pre-layout: Adc=%.1fdB, GBW=%.2fMHz, PM=%.1fdeg, FoM=%.2e",
            pre_result.Adc_dB or 0,
            (pre_result.GBW_Hz or 0) / 1e6,
            pre_result.PM_deg or 0,
            pre_fom,
        )
    else:
        logger.warning("Pre-layout SPICE failed: %s", pre_result.error)
        pre_fom = 0.0
        pre_result = None

    # Build runners
    glayout = GLayoutRunner()
    pex_runner = MagicPexRunner()

    drc_runner = None
    lvs_runner = None
    if not skip_drc:
        from eda_agents.core.klayout_drc import KLayoutDrcRunner
        drc_runner = KLayoutDrcRunner()
    if not skip_lvs:
        from eda_agents.core.klayout_lvs import KLayoutLvsRunner
        lvs_runner = KLayoutLvsRunner()

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
        pre_layout_fom=pre_fom,
        pre_layout_spice=pre_result,
        work_dir=output_dir / "postlayout",
    )

    # Report
    print("\n" + "=" * 70)
    print("POST-LAYOUT VALIDATION RESULTS")
    print("=" * 70)
    print(f"  Parameters: {params}")
    print(f"  GDS:        {result.gds_path}")
    print(f"  DRC:        {'clean' if result.drc_clean else f'{result.drc_violations} violations'}")
    print(f"  LVS:        {'match' if result.lvs_match else 'MISMATCH / skipped'}")
    print(f"  PEX:        {result.extracted_netlist_path} ({result.pex_corner})")
    print()
    if result.post_Adc_dB is not None:
        print("  Post-layout SPICE:")
        print(f"    Adc  = {result.post_Adc_dB:.1f} dB  (delta: {result.gain_delta_dB:+.1f} dB)")
        if result.post_GBW_Hz is not None:
            print(f"    GBW  = {result.post_GBW_Hz/1e6:.2f} MHz (delta: {result.gbw_delta_pct:+.1f}%)")
        if result.post_PM_deg is not None:
            print(f"    PM   = {result.post_PM_deg:.1f} deg (delta: {result.pm_delta_deg:+.1f} deg)")
        print(f"    FoM  = {result.post_fom:.2e} (delta: {result.fom_delta_pct:+.1f}%)")
        print(f"    Valid: {result.post_valid}")
    elif result.error:
        print(f"  Error: {result.error}")
    print(f"\n  Total time: {result.total_time_s:.1f}s")
    print("=" * 70)

    # Save JSON summary
    summary_path = output_dir / "postlayout_summary.json"
    summary = {
        "params": result.params,
        "pre_layout_fom": result.pre_layout_fom,
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
        "gain_delta_dB": result.gain_delta_dB,
        "gbw_delta_pct": result.gbw_delta_pct,
        "pm_delta_deg": result.pm_delta_deg,
        "fom_delta_pct": result.fom_delta_pct,
        "total_time_s": result.total_time_s,
        "error": result.error,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    logger.info("Summary saved to %s", summary_path)


def run_from_autoresearch(
    results_dir: Path,
    output_dir: Path,
    skip_drc: bool = False,
    skip_lvs: bool = False,
    top_n: int = 3,
) -> None:
    """Validate top-N designs from autoresearch results."""
    # Look for TSV or JSON results
    tsv_files = list(results_dir.glob("*.tsv"))
    json_files = list(results_dir.glob("*results*.json"))

    designs = []

    if json_files:
        data = json.loads(json_files[0].read_text())
        if isinstance(data, list):
            designs = sorted(data, key=lambda d: d.get("fom", 0), reverse=True)[:top_n]
        elif "top_n" in data:
            designs = data["top_n"][:top_n]
    elif tsv_files:
        import csv
        with open(tsv_files[0]) as f:
            reader = csv.DictReader(f, delimiter="\t")
            rows = sorted(reader, key=lambda r: float(r.get("fom", 0)), reverse=True)
            for row in rows[:top_n]:
                designs.append({
                    "params": {k: float(v) for k, v in row.items() if k in (
                        "Ibias_uA", "L_dp_um", "L_load_um", "Cc_pF", "W_dp_um"
                    )},
                    "fom": float(row.get("fom", 0)),
                })

    if not designs:
        logger.error("No designs found in %s", results_dir)
        sys.exit(1)

    logger.info("Validating top %d designs from %s", len(designs), results_dir)

    for i, design in enumerate(designs):
        logger.info("\n--- Design %d/%d ---", i + 1, len(designs))
        run_single(
            params=design["params"],
            output_dir=output_dir / f"design_{i:03d}",
            skip_drc=skip_drc,
            skip_lvs=skip_lvs,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Post-layout validation for GF180 OTA",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--ibias", type=float, default=200.0, help="Ibias_uA (default: 200)")
    parser.add_argument("--ldp", type=float, default=2.0, help="L_dp_um (default: 2.0)")
    parser.add_argument("--lload", type=float, default=5.0, help="L_load_um (default: 5.0)")
    parser.add_argument("--cc", type=float, default=2.0, help="Cc_pF (default: 2.0)")
    parser.add_argument("--wdp", type=float, default=10.0, help="W_dp_um (default: 10.0)")
    parser.add_argument("--output-dir", type=str, default="/tmp/postlayout_validation")
    parser.add_argument("--skip-drc", action="store_true", help="Skip DRC step")
    parser.add_argument("--skip-lvs", action="store_true", help="Skip LVS step")
    parser.add_argument("--dry-run", action="store_true", help="Check prerequisites only")
    parser.add_argument("--from-autoresearch", type=str, help="Path to autoresearch results dir")
    parser.add_argument("--top-n", type=int, default=3, help="Top-N designs to validate")

    args = parser.parse_args()

    if args.dry_run:
        problems = check_prerequisites(
            skip_drc=args.skip_drc,
            skip_lvs=args.skip_lvs,
        )
        if problems:
            print("Prerequisites NOT met:")
            for p in problems:
                print(f"  - {p}")
            sys.exit(1)
        else:
            print("All prerequisites met. Ready to run.")
            sys.exit(0)

    output_dir = Path(args.output_dir)

    if args.from_autoresearch:
        run_from_autoresearch(
            results_dir=Path(args.from_autoresearch),
            output_dir=output_dir,
            skip_drc=args.skip_drc,
            skip_lvs=args.skip_lvs,
            top_n=args.top_n,
        )
    else:
        params = {
            "Ibias_uA": args.ibias,
            "L_dp_um": args.ldp,
            "L_load_um": args.lload,
            "Cc_pF": args.cc,
            "W_dp_um": args.wdp,
        }
        run_single(
            params=params,
            output_dir=output_dir,
            skip_drc=args.skip_drc,
            skip_lvs=args.skip_lvs,
        )


if __name__ == "__main__":
    main()
