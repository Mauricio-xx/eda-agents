"""Reproduction: run gLayout's KLayout-native DRC + LVS on the
`opamp_twostage` artefacts produced by `generate_evidence.py`.

Must run in `.venv-glayout` (gLayout + IHP PDK). Produces DRC + LVS
reports inside `artifacts/` and a `drc_lvs_result.json` next to this
script.

Run:
    /home/montanares/personal_exp/eda-agents/.venv-glayout/bin/python \\
        bench/results/s12b_sg13g2_opamp_layout/run_drc_lvs.py
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from glayout.pdk.sg13g2_mapped import sg13g2_mapped_pdk

HERE = Path(__file__).resolve().parent
ART = HERE / "artifacts"
GDS = ART / "opamp_twostage.gds"
SPICE = ART / "opamp_twostage.spice"


def count_drc(lyrdb: Path) -> dict:
    if not lyrdb.is_file():
        return {"available": False, "total": 0, "per_rule": {}}
    tree = ET.parse(lyrdb)
    counts: Counter[str] = Counter()
    for item in tree.getroot().iter("item"):
        cat = item.findtext("category", default="(uncategorized)").strip().strip("'\"")
        counts[cat] += 1
    return {"available": True, "total": int(sum(counts.values())), "per_rule": dict(counts)}


def main() -> None:
    if not GDS.is_file():
        raise SystemExit(f"Missing {GDS}. Run generate_evidence.py first.")
    if not SPICE.is_file():
        raise SystemExit(f"Missing {SPICE}. Run generate_evidence.py first.")

    # The glayout_driver renames the top cell to lowercase
    # `component_lower` (opamp_twostage), but the gLayout-emitted
    # netlist keeps circuit_name = "OPAMP_TWO_STAGE". This mismatch
    # breaks LVS cell-name matching — a separate driver-level issue
    # noted in the findings doc. Work around it by patching the
    # schematic's .subckt line to lowercase for this reproduction.
    design_name = "opamp_twostage"
    patched_spice = ART / "opamp_twostage_patched.spice"
    raw = SPICE.read_text()
    patched = (
        raw.replace(".subckt OPAMP_TWO_STAGE", f".subckt {design_name}")
           .replace(".ends OPAMP_TWO_STAGE", f".ends {design_name}")
    )
    patched_spice.write_text(patched)

    # DRC
    t = time.time()
    drc_clean = sg13g2_mapped_pdk.drc(str(GDS), design_name)
    drc_elapsed = time.time() - t

    # gLayout's drc(...) writes the lyrdb at
    #   {cwd}/<UPPERCASE_NAME>/sg13g2_<LOWERCASE_NAME>_drcreport.lyrdb
    # (observed — the dir is uppercased but the file keeps the arg's case).
    lyrdb_candidates = [
        Path.cwd() / design_name.upper() / f"sg13g2_{design_name}_drcreport.lyrdb",
        Path.cwd() / design_name / f"sg13g2_{design_name}_drcreport.lyrdb",
        ART / f"sg13g2_{design_name}_drcreport.lyrdb",
    ]
    # Fallback: glob for any drcreport.lyrdb touched in the last 120s
    if not any(p.is_file() for p in lyrdb_candidates):
        now = time.time()
        for root in (Path.cwd(), ART):
            for p in root.rglob("*drcreport*.lyrdb"):
                if now - p.stat().st_mtime < 120:
                    lyrdb_candidates.append(p)
    lyrdb = next((p for p in lyrdb_candidates if p.is_file()), None)
    drc_stats = count_drc(lyrdb) if lyrdb else {"available": False}

    # Copy lyrdb to artifacts for traceability
    if lyrdb and lyrdb.parent != ART:
        dest = ART / lyrdb.name
        dest.write_bytes(lyrdb.read_bytes())
        drc_stats["lyrdb_copy"] = str(dest)

    # LVS (against the case-patched schematic)
    t = time.time()
    lvs = sg13g2_mapped_pdk.lvs_klayout(
        str(GDS), design_name, str(patched_spice), output_dir_or_file=str(ART),
    )
    lvs_elapsed = time.time() - t

    out = {
        "design_name": design_name,
        "gds": str(GDS),
        "netlist": str(SPICE),
        "drc": {
            "clean": bool(drc_clean),
            "run_time_s": round(drc_elapsed, 2),
            **drc_stats,
        },
        "lvs": {
            "passed": bool(lvs.get("passed")),
            "run_time_s": round(lvs_elapsed, 2),
            "result_str_tail": "\n".join(
                (lvs.get("result_str") or "").strip().splitlines()[-10:]
            ),
        },
    }
    (HERE / "drc_lvs_result.json").write_text(json.dumps(out, indent=2, sort_keys=True))
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
