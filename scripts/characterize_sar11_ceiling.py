"""Characterize the ENOB ceiling of the default SAR 11-bit architecture.

Closes the measurement side of S9-residual-closure gap #6b.

Drives ``SARADC11BitTopology.generate_system_netlist`` across a
12-point grid over four knobs that the plan identified as the
dominant ENOB levers (StrongARM input sizing, CDAC unit capacitance,
bias voltage). Each configuration is simulated end-to-end on
ngspice + PSP103 OSDI + Verilator SAR FSM, then the measured ENOB /
SNDR / SFDR are written to a TSV next to this script's results dir.

The intended reader is another session (or the user) deciding whether
to raise ``SARADC11BitTopology._SPEC_ENOB_MIN``. The rule applied
in S9-residual-closure was:

    * if ``max(ENOB) > floor(current_spec) + 0.95`` across the sweep
      -> raise ``_SPEC_ENOB_MIN`` to ``floor(max_ENOB) - 0.5``
      (honest margin);
    * otherwise -> thresholds stay, TSV becomes the numerical anchor.

S9-residual-closure measured ``max(ENOB) = 5.64`` against a
pre-sweep floor of 4.0, triggering the raise path: the spec anchors
moved to 4.5 bit / 28 dB and ``default_params`` was shifted to the
ceiling point so the bench baseline keeps margin above the new
thresholds. Future runs of this script should re-verify the ceiling
after any topology change.

This script is **not** wired into ``scripts/run_bench.py``. It is a
one-shot measurement harness; re-run it after any topology change
that could shift the ceiling. The sweep takes roughly 12 x 80 s =
16 min wall-clock on the session reference machine.

Usage:

    set -a && source .env && set +a    # optional; no API key needed
    PYTHONPATH=src .venv/bin/python scripts/characterize_sar11_ceiling.py \
        --out bench/results/sar11_ceiling_characterization
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from eda_agents.core.spice_runner import SpiceRunner
from eda_agents.topologies.sar_adc_11bit import (
    SARADC11BitTopology,
    _SPEC_ENOB_MIN,
    _SPEC_SNDR_MIN,
)

logger = logging.getLogger(__name__)


# L9 Taguchi orthogonal array (3 levels, 4 factors) + 3 corner probes
# = 12 configurations. Levels per factor:
#
#   A = comp_W_input_um:   low=8.0,   mid=32.0,  high=50.0
#   B = comp_L_input_um:   low=0.15,  mid=0.20,  high=0.50
#   C = cdac_C_unit_fF:    low=20.0,  mid=50.0,  high=150.0
#   D = bias_V:            low=0.50,  mid=0.60,  high=0.90
#
# The mid column matches the topology's ``default_params`` so run #5
# (all-mid) reproduces the bench baseline and acts as a sanity anchor.
_LEVELS = {
    "comp_W_input_um":  {1: 8.0,  2: 32.0, 3: 50.0},
    "comp_L_input_um":  {1: 0.15, 2: 0.20, 3: 0.50},
    "cdac_C_unit_fF":   {1: 20.0, 2: 50.0, 3: 150.0},
    "bias_V":           {1: 0.50, 2: 0.60, 3: 0.90},
}

# L9 (A, B, C, D) + three corner rows for coverage of the design-space
# extremes (all-low, all-high, defaults re-run as warm check).
_DESIGN_MATRIX: list[tuple[int, int, int, int]] = [
    # L9 body
    (1, 1, 1, 1),
    (1, 2, 2, 2),
    (1, 3, 3, 3),
    (2, 1, 2, 3),
    (2, 2, 3, 1),
    (2, 3, 1, 2),
    (3, 1, 3, 2),
    (3, 2, 1, 3),
    (3, 3, 2, 1),
    # Corners + anchor
    (1, 1, 1, 1),  # all-low replayed to detect run-to-run jitter
    (3, 3, 3, 3),  # all-high
    (2, 2, 2, 2),  # all-mid = defaults (anchor match)
]


def _resolve_params(indices: tuple[int, int, int, int]) -> dict[str, float]:
    w_idx, l_idx, c_idx, b_idx = indices
    defaults = SARADC11BitTopology().default_params()
    # Start from the full default params so every latch / tail knob
    # stays at its baseline; we only override the four swept factors.
    params = dict(defaults)
    params["comp_W_input_um"] = _LEVELS["comp_W_input_um"][w_idx]
    params["comp_L_input_um"] = _LEVELS["comp_L_input_um"][l_idx]
    params["cdac_C_unit_fF"] = _LEVELS["cdac_C_unit_fF"][c_idx]
    params["bias_V"] = _LEVELS["bias_V"][b_idx]
    return params


def _run_one(
    topo: SARADC11BitTopology,
    params: dict[str, float],
    work_dir: Path,
) -> dict[str, float | str]:
    work_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    cir = topo.generate_system_netlist(params, work_dir)
    runner = SpiceRunner(pdk=topo.pdk, preload_pdk_osdi=True)
    try:
        sp = asyncio.run(runner.run_async(cir, work_dir))
    except RuntimeError:
        sp = runner.run(cir, work_dir=work_dir)
    if not sp.success:
        return {
            "status": "FAIL_SIM",
            "error": sp.error or "ngspice failed",
            "runtime_s": time.monotonic() - t0,
        }
    metrics = topo.extract_enob(work_dir)
    return {
        "status": "OK" if "error" not in metrics else "FAIL_EXTRACT",
        "enob": metrics.get("enob", 0.0),
        "sndr_dB": metrics.get("sndr_dB", 0.0),
        "sfdr_dB": metrics.get("sfdr_dB", 0.0),
        "thd_dB": metrics.get("thd_dB", 0.0),
        "unique_codes": metrics.get("unique_codes", 0.0),
        "code_span": metrics.get("code_span", 0.0),
        "error": metrics.get("error", ""),
        "runtime_s": time.monotonic() - t0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure the ENOB ceiling of SAR 11-bit defaults."
    )
    parser.add_argument(
        "--pdk",
        default=os.environ.get("EDA_AGENTS_PDK", "ihp_sg13g2"),
        help="PDK registry name (ihp_sg13g2 | gf180mcu). Default: ihp_sg13g2.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("bench/results/sar11_ceiling_characterization"),
        help="Results directory. TSV + JSON land here.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    topo = SARADC11BitTopology(pdk=args.pdk)

    args.out.mkdir(parents=True, exist_ok=True)
    tsv_path = args.out / "sweep.tsv"
    json_path = args.out / "sweep.json"

    header = (
        "run_id\tcomp_W_input_um\tcomp_L_input_um\tcdac_C_unit_fF\t"
        "bias_V\tstatus\tENOB\tSNDR_dBc\tSFDR_dBc\tTHD_dBc\t"
        "unique_codes\tcode_span\truntime_s\terror"
    )
    tsv_path.write_text(header + "\n")

    rows: list[dict] = []
    for i, indices in enumerate(_DESIGN_MATRIX, start=1):
        params = _resolve_params(indices)
        work_dir = args.out / f"run_{i:02d}"
        logger.info(
            "Run %d/%d: W=%.1f L=%.2f Cu=%.1f Vb=%.2f",
            i,
            len(_DESIGN_MATRIX),
            params["comp_W_input_um"],
            params["comp_L_input_um"],
            params["cdac_C_unit_fF"],
            params["bias_V"],
        )
        result = _run_one(topo, params, work_dir)
        row = {
            "run_id": f"run_{i:02d}",
            "indices": indices,
            "params": params,
            **result,
        }
        rows.append(row)
        line = (
            f"run_{i:02d}\t"
            f"{params['comp_W_input_um']:.3f}\t"
            f"{params['comp_L_input_um']:.3f}\t"
            f"{params['cdac_C_unit_fF']:.3f}\t"
            f"{params['bias_V']:.3f}\t"
            f"{result['status']}\t"
            f"{result.get('enob', 0.0):.4f}\t"
            f"{result.get('sndr_dB', 0.0):.2f}\t"
            f"{result.get('sfdr_dB', 0.0):.2f}\t"
            f"{result.get('thd_dB', 0.0):.2f}\t"
            f"{result.get('unique_codes', 0.0):.0f}\t"
            f"{result.get('code_span', 0.0):.0f}\t"
            f"{result.get('runtime_s', 0.0):.2f}\t"
            f"{result.get('error', '')}"
        )
        with tsv_path.open("a") as fh:
            fh.write(line + "\n")
        logger.info(
            "  -> status=%s ENOB=%.3f SNDR=%.2f dB (t=%.1fs)",
            result["status"],
            float(result.get("enob", 0.0)),
            float(result.get("sndr_dB", 0.0)),
            float(result.get("runtime_s", 0.0)),
        )

    # JSON dump for downstream consumption (test_sar_adc_11bit_ceiling).
    json_path.write_text(json.dumps(rows, indent=2, default=str))

    valid = [r for r in rows if r["status"] == "OK" and r.get("enob", 0.0) > 0.0]
    if not valid:
        logger.error("No valid runs; cannot characterize ceiling.")
        return 1

    enobs = [float(r["enob"]) for r in valid]
    sndrs = [float(r["sndr_dB"]) for r in valid]
    enob_max = max(enobs)
    enob_min = min(enobs)
    sndr_max = max(sndrs)
    sndr_min = min(sndrs)

    logger.info("")
    logger.info("Summary across %d valid of %d runs:", len(valid), len(rows))
    logger.info(
        "  ENOB:  min=%.3f  max=%.3f  span=%.3f bit",
        enob_min,
        enob_max,
        enob_max - enob_min,
    )
    logger.info(
        "  SNDR:  min=%.2f  max=%.2f  span=%.2f dB",
        sndr_min,
        sndr_max,
        sndr_max - sndr_min,
    )
    logger.info(
        "  current _SPEC_ENOB_MIN = %.2f -> margin at measured ceiling = %.2f bit",
        _SPEC_ENOB_MIN,
        enob_max - _SPEC_ENOB_MIN,
    )
    logger.info(
        "  current _SPEC_SNDR_MIN = %.2f dB -> margin at measured ceiling = %.2f dB",
        _SPEC_SNDR_MIN,
        sndr_max - _SPEC_SNDR_MIN,
    )
    logger.info("TSV: %s", tsv_path)
    logger.info("JSON: %s", json_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
