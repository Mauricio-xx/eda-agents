"""Generate S12-B Gap 4 partial-landing evidence.

Drives generate_analog_layout via GLayoutRunner on SG13G2 for
opamp_twostage and records structured result.json.

DRC + LVS data is collected separately by `run_drc_lvs.py` (needs the
gLayout venv).

Run:
    cd bench/results/s12b_sg13g2_opamp_layout
    python generate_evidence.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from eda_agents.core.glayout_runner import GLayoutRunner

HERE = Path(__file__).resolve().parent

VENV = "/home/montanares/personal_exp/eda-agents/.venv-glayout"
PARAMS = {
    "half_diffpair_params": [6.0, 1.0, 4],
    "diffpair_bias": [6.0, 2.0, 4],
    "half_common_source_params": [7.0, 1.0, 10, 3],
    "half_common_source_bias": [6.0, 2.0, 8, 2],
    "half_pload": [6.0, 1.0, 6],
    "mim_cap_size": [12.0, 12.0],
    "mim_cap_rows": 3,
}


def main() -> None:
    out_dir = HERE / "artifacts"
    out_dir.mkdir(exist_ok=True)

    runner = GLayoutRunner(glayout_venv=VENV, pdk="ihp_sg13g2", timeout_s=600)

    t0 = time.time()
    gen = runner.generate_component(
        component="opamp_twostage",
        params=PARAMS,
        output_dir=out_dir,
    )
    gen_elapsed = time.time() - t0

    result: dict = {
        "session": "S12-B",
        "gap": 4,
        "landing_mode": "partial",
        "pdk": "ihp_sg13g2",
        "component": "opamp_twostage",
        "params": PARAMS,
        "generate": {
            "success": gen.success,
            "gds_path": gen.gds_path,
            "netlist_path": gen.netlist_path,
            "top_cell": gen.top_cell,
            "run_time_s": round(gen_elapsed, 2),
            "error": gen.error,
        },
        "verification_baseline_from_scratch_run": {
            "drc": {
                "total": 70,
                "per_rule": {
                    "M3.b": 30,
                    "M2.b": 16,
                    "M1.b": 8,
                    "M2.e": 8,
                    "M3.e": 4,
                    "M4.b": 4,
                },
                "note": (
                    "Baseline captured with scratch run. Matches diff_pair/"
                    "FVF's pre-fix profile; fix pattern = SG13G2 spacing/"
                    "layer branches in __create_and_route_pins and "
                    "__add_mimcap_arr. Follow-up PR."
                ),
            },
            "lvs": {
                "passed": False,
                "stage": "netlists_dont_match",
                "schematic": {
                    "nmos": 5, "pmos": 5, "mim_cap": 6,
                    "nmos_widths_um": [24, 24, 24, 24, 48, 48],
                },
                "extracted": {
                    "nmos": 5, "pmos": 5, "mim_cap": 0,
                    "nmos_widths_um": [24, 24, 48, 48, 288],
                },
                "delta": [
                    "cs_bias stacked cmirror: schematic 2xW=24 vs extracted 1xW=288 (merged ref+out+dummies)",
                    "mim cap: schematic 6 vs extracted 0 (IHP cap_cmim extractor expects topmetal1 + vmim markers gLayout doesn't emit)",
                ],
            },
        },
        "blocker": {
            "mimcap_topmetal1_mapping": {
                "description": (
                    "gLayout maps SG13G2 MIM caps to met4-mim-met5 with a "
                    "via_array(met4->met5). IHP's KLayout LVS deck "
                    "(libs.tech/klayout/tech/lvs/rule_decks/cap_derivations.lvs:30-36) "
                    "derives cap_cmim from mim_drw.overlapping(topmetal1_con) + "
                    "mim_drw.and(metal5_con) + vmim_drw(129,0) + topvia1_drw(125,0). "
                    "Zero MIM caps extracted in opamp_twostage layout."
                ),
                "fix_paths": [
                    "Path A (upstream): add topmetal1 to gLayout valid_glayers + rework mimcap primitive to emit vmim/topvia1/topmetal1 on SG13G2.",
                    "Path B (fork-local): sg13g2_decorator.py hook synthesising those markers post-write, leaving mimcap.py unchanged for other PDKs.",
                ],
                "see": "docs/s12_findings/s12b_sg13g2_opamp_twostage.md",
            },
            "cs_bias_netlist_mismatch": {
                "description": (
                    "opamp_twostage.py:226 calls current_mirror_netlist with "
                    "diffpair_bias params (w=6, l=2, m=4) instead of "
                    "half_common_source_bias (w=6, l=2, f=8, m=2). Layout uses "
                    "stacked_nfet_current_mirror so flat extract merges "
                    "4 FETs + dummies into one W=288um device. Fix: custom "
                    "stacked_cs_bias_netlist helper that matches the flat "
                    "layout, applicable to both PDKs."
                ),
                "see": "docs/s12_findings/s12b_sg13g2_opamp_twostage.md",
            },
        },
    }

    (HERE / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
