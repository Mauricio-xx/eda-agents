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
        "landing_mode": "partial+gap_a_closed",
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
        "verification_pre_gap_a_historical": {
            "drc": {
                "total": 70,
                "per_rule": {
                    "M3.b": 30, "M2.b": 16, "M1.b": 8,
                    "M2.e": 8, "M3.e": 4, "M4.b": 4,
                },
                "note": (
                    "Initial baseline with scratch run (pre-Gap A). Matches "
                    "diff_pair / FVF pre-fix profile; fix pattern = SG13G2 "
                    "spacing/layer branches in __create_and_route_pins + "
                    "__add_mimcap_arr. Still the open follow-up."
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
                    "mim cap: schematic 6 vs extracted 0 (IHP cap_cmim extractor needed topmetal1 + vmim markers gLayout was not emitting)",
                ],
            },
        },
        "verification_post_gap_a": {
            "fork_commit": (
                "gLayout feature/s12-opamp-sg13g2-integration @ 27194ff - "
                "SG13G2 MIM cap decorator + mimcap_array TopMetal1 bridge"
            ),
            "drc": {
                "total": 70,
                "per_rule": {
                    "M3.b": 30, "M2.b": 16, "M1.b": 8,
                    "M2.e": 8, "M3.e": 4, "M4.b": 4,
                },
                "delta_vs_pre": "No change (markers added by decorator are DRC-compliant).",
            },
            "lvs": {
                "passed": False,
                "stage": "netlists_dont_match",
                "extracted": {
                    "nmos": 5, "pmos": 5,
                    "mim_cap_devices": 1,
                    "mim_cap_m": 6,
                    "nmos_widths_um": [24, 24, 48, 48, 288],
                },
                "delta_vs_pre": [
                    "mim cap: 0 -> 1 device m=6 (caps extracted as one parallel cluster). Matches schematic's 6 parallel MIMCap instances in intent.",
                    "cs_bias still mismatches (out-of-scope for Gap A).",
                ],
                "note": (
                    "Gap A (MIM cap layer mapping) is closed for LVS extraction. "
                    "Remaining LVS mismatch is the pre-existing cs_bias netlist "
                    "issue documented separately."
                ),
            },
        },
        "blocker": {
            "mimcap_topmetal1_mapping": {
                "status": "CLOSED (2026-04-18)",
                "description": (
                    "IHP cap_cmim LVS derivation requires MIM (36,0) + TopMetal1 "
                    "(126,0) + vMIM (129,0) / TopVia1 (125,0) stack. gLayout's "
                    "mimcap primitive draws only MIM + Metal4/Metal5. Path B fix "
                    "landed on the fork: sg13g2_decorator synthesises per-MIM "
                    "TopMetal1/vMIM/TopVia1 markers, and mimcap_array adds a "
                    "single TopMetal1 bridge spanning the full placed array so "
                    "LVS sees one shared top-plate net (parallel caps)."
                ),
                "see": "docs/s12_findings/s12b_sg13g2_opamp_twostage.md",
            },
            "cs_bias_netlist_mismatch": {
                "status": "OPEN (next session)",
                "description": (
                    "opamp_twostage.py:226 calls current_mirror_netlist with "
                    "diffpair_bias params (w=6, l=2, m=4) instead of "
                    "half_common_source_bias (w=6, l=2, f=8, m=2). Layout uses "
                    "stacked_nfet_current_mirror so flat extract merges 4 FETs + "
                    "dummies into one W=288um device. Fix: custom "
                    "stacked_cs_bias_netlist helper that matches the flat "
                    "layout, applicable to both PDKs."
                ),
                "see": "docs/s12_findings/s12b_sg13g2_opamp_twostage.md",
            },
            "drc_routing_violations": {
                "status": "OPEN (next session)",
                "description": (
                    "70 metal-rule violations in __create_and_route_pins + "
                    "__add_mimcap_arr routing. Fix pattern already established "
                    "on diff_pair / FVF / low_voltage_cmirror: SG13G2 "
                    "min_separation-aware branches in the composite."
                ),
                "see": "docs/s12_findings/s12b_sg13g2_opamp_twostage.md",
            },
        },
    }

    (HERE / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
