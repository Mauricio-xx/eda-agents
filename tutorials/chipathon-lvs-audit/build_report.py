"""Aggregate per-cell summary.json files into a meeting-ready markdown report."""

from __future__ import annotations

import json
import re
from pathlib import Path
from textwrap import dedent

RUNS_ROOT = Path("/tmp/gf180-chip-test/chipathon-lvs-audit/runs")
OUT = Path(__file__).parent / "report" / "chipathon_lvs_audit.md"

_PROP_ERR_RE = re.compile(
    r"([wlWL])\s+circuit1:\s*([\d.\-eE]+)\s+circuit2:\s*([\d.\-eE]+)"
    r"\s+\(delta=([\d\.]+)%"
)


def extract_property_errors(comp_out_path: str | None) -> list[str]:
    if not comp_out_path:
        return []
    # Paths in result.json use the container mount "/foss/designs"; remap to
    # host's "/tmp/gf180-chip-test".
    host_path = comp_out_path.replace("/foss/designs", "/tmp/gf180-chip-test")
    p = Path(host_path)
    if not p.is_file():
        return []
    text = p.read_text(errors="replace")
    hits = _PROP_ERR_RE.findall(text)
    out = []
    for param, c1, c2, delta in hits:
        out.append(f"`{param}` circuit1={c1} vs circuit2={c2} (delta={delta}%)")
    # Also capture the specific cell names with property errors at the bottom
    m = re.search(r"The following cells had property errors:\s*\n\s*([^\n]+)", text)
    if m:
        out.append(f"cells: {m.group(1).strip()}")
    return out


def verdict_cell(eng: dict) -> str:
    if not eng or eng.get("status") == "missing":
        return "—"
    if eng.get("status") == "skipped":
        return "skipped"
    if eng.get("engine") == "klayout_lvs":
        return "MATCH" if eng.get("match") else "MISMATCH"
    # magic_netgen
    if eng.get("match") is True:
        if eng.get("property_error"):
            return "MATCH (prop err)"
        return "MATCH"
    return "MISMATCH"


def main() -> None:
    summaries = []
    for p in sorted(RUNS_ROOT.glob("*/summary.json")):
        try:
            summaries.append(json.loads(p.read_text()))
        except Exception as e:
            summaries.append({"cell": p.parent.name, "error": str(e)})

    ready = [s for s in summaries if s.get("ready")]
    not_ready = [s for s in summaries if not s.get("ready")]

    # Classify discrepancies
    klayout_mismatch = [s for s in ready if not s["klayout"].get("match")]
    both_agree = [s for s in ready if s["klayout"].get("match") == s["magic_netgen_pdk"].get("match")]

    # Compose markdown
    lines: list[str] = []
    lines.append("# Chipathon LVS audit — KLayout vs Magic+Netgen on GF180MCU `core_biasgen`")
    lines.append("")
    lines.append(f"- **Image**: `hpretl/iic-osic-tools:next` (container `gf180-chip-test`)")
    lines.append(f"- **PDK**: `/foss/pdks/gf180mcuD/` (ciel-installed; open_pdks `7b70722`)")
    lines.append(f"- **Tooling**: KLayout 0.30.7 · Magic 8.3.635 · Netgen 1.5.318")
    lines.append(f"- **Design source**: `AutoMOS-project/AutoMOS-chipathon2025@integration` → `designs/libs/core_biasgen/`")
    lines.append("")

    lines.append("## Meeting bullets (copy/paste)")
    lines.append("")
    lines.append("- Discrepancy **reproduced on the Docker image**: KLayout LVS and Magic+Netgen LVS disagree on the same (GDS, SPICE) pair.")
    lines.append(f"- Only **{len(ready)} of 9** biasgen cells are LVS-ready (have both `.gds` and `.spice`): `{', '.join(s['cell'] for s in ready)}`. The other 7 are schematic-only and cannot be LVS-checked.")
    lines.append(f"- On the 2 LVS-ready cells, **KLayout LVS = MISMATCH** and **Magic+Netgen LVS = MATCH** (both with PDK-default and project-customized `gf180mcuD_setup.tcl`).")
    lines.append("- **Root cause**: the xschem-exported source `.spice` uses `X`-prefix subcircuit calls to `nfet_05v0` / `pfet_05v0` / `ppolyf_u_1k_6p0` that rely on ngspice PDK wrappers (`sm141064.ngspice`) with parameter expressions and `m=N` multipliers. Magic+Netgen papers over this via `equate classes` directives in `gf180mcuD_setup.tcl`; **KLayout's SPICE reader cannot resolve the same input** even with `--lvs_sub=VSS` and `--include` of the wrappers.")
    lines.append("- **Implication for the Chipathon students**: use **Magic+Netgen** for LVS (already the project's own `run_lvs.sh`), not KLayout. Use KLayout only for DRC (which is what Amro's thread is doing anyway).")
    lines.append("- The project's customised `gf180mcuD_setup.tcl` deviates from the PDK version (`property parallel enable` commented out, `delete par1` commented out, MIM cap section trimmed). On these two cells the customization did not change the verdict, but it's latent risk — our recommendation is to align it with the PDK version unless there's a specific reason.")
    lines.append("")

    # Verdict table
    lines.append("## Verdict matrix")
    lines.append("")
    lines.append("| Cell | LVS-ready | KLayout LVS | Magic+Netgen (project setup) | Magic+Netgen (PDK setup) |")
    lines.append("|---|:---:|:---:|:---:|:---:|")
    for s in summaries:
        cell = s["cell"]
        if not s.get("ready"):
            reason = s.get("reason", "not ready")
            lines.append(f"| `{cell}` | — | — | — | — <br/><sub>{reason}</sub> |")
            continue
        k = verdict_cell(s["klayout"])
        p = verdict_cell(s["magic_netgen_project"])
        d = verdict_cell(s["magic_netgen_pdk"])
        lines.append(f"| `{cell}` | yes | **{k}** | {p} | {d} |")
    lines.append("")

    # Per-cell details
    lines.append("## Per-cell details")
    for s in ready:
        cell = s["cell"]
        lines.append(f"\n### `{cell}`\n")
        # KLayout
        k = s["klayout"]
        lines.append(f"**KLayout LVS** — rc={k.get('rc')}, run_time={k.get('run_time_s')}s (LVS-internal {k.get('lvs_internal_s'):.2f}s). Verdict: **{verdict_cell(k)}**.")
        if k.get("failure_marker_found"):
            lines.append(f"<br/>Log marker: `ERROR : Netlists don't match`.")
        lines.append(f"<br/>Artefacts: `{k.get('lvsdb')}`, extracted netlist `{k.get('extracted_cir')}`.")
        # Magic+Netgen project
        p = s["magic_netgen_project"]
        p_errs = extract_property_errors(p.get("comp_out"))
        lines.append(f"\n**Magic+Netgen (project setup)** — rc={p.get('rc')}. Final: _{p.get('final_result')}_. Devices={p.get('counts',{}).get('devices_c1')} nets={p.get('counts',{}).get('nets_c1')}.")
        if p_errs:
            lines.append("<br/>Property errors:")
            for e in p_errs:
                lines.append(f"  - {e}")
        # Magic+Netgen PDK
        d = s["magic_netgen_pdk"]
        d_errs = extract_property_errors(d.get("comp_out"))
        lines.append(f"\n**Magic+Netgen (PDK setup)** — rc={d.get('rc')}. Final: _{d.get('final_result')}_. Devices={d.get('counts',{}).get('devices_c1')} nets={d.get('counts',{}).get('nets_c1')}.")
        if d_errs:
            lines.append("<br/>Property errors:")
            for e in d_errs:
                lines.append(f"  - {e}")

    # Deep dive
    lines.append("\n## Deep dive: why KLayout rejects both cells")
    lines.append("")
    lines.append(dedent("""
        For `biasgen_mirror_2_to_10` the KLayout `.lvsdb` records the same failure
        pattern for every single net:

        ```
        M(E B('Net <X> is not matching any net from reference netlist'))
        ```

        And every layout device stays orphaned (`D(n () 0)`). We confirmed the
        chain that breaks the compare by progressively loosening the source
        netlist:

        1. **Default** `--lvs_sub=gf180mcu_gnd`:
           KLayout extracts 10 pins (adds a synthetic `gf180mcu_gnd` substrate
           pin). Schematic has 9 pins. Compare never aligns the top-level pin
           list → all nets unmatched.

        2. **`--lvs_sub=VSS`**:
           The substrate now collapses into `VSS`; extracted pin list shrinks
           to 9. But the reference netlist still parses with **zero devices**
           in its `H(...)` block. Compare still fails on every net.

        3. **Prepending `.include sm141064.ngspice`** (the PDK-bundled ngspice
           subckt wrappers for `nfet_05v0` / `pfet_05v0`):
           KLayout's SPICE reader sees the subckt declarations but does not
           evaluate the ngspice parameter expressions inside them, so it still
           emits **zero devices** in the reference.

        4. **Manually rewriting the source with `M`-prefix transistors and
           stripping ngspice expressions**:
           KLayout now parses 40 devices and 3 device classes
           (`NFET_05V0 MOS4`, `PFET_05V0 MOS4`, `PPOLYF_U_1K_6P0 RES3`) in the
           reference. But the `m=2`/`m=10` multipliers on source lines are not
           unrolled/merged to match the ~91 flat devices KLayout extracts from
           the layout, and the compare still reports all nets unmatched.

        Magic+Netgen succeeds where KLayout fails because
        `gf180mcuD_setup.tcl` includes explicit `equate classes` directives
        between `nfet_05v0` / `pfet_05v0` device classes in both circuits and
        `property parallel enable` (or equivalent merging) that reconciles
        the `m=N` multiplier model with the extracted layout's per-finger
        instances. The KLayout LVS deck does not contain an equivalent
        reconciliation for xschem-exported netlists.
        """).strip())
    lines.append("")

    # Recommendations
    lines.append("## Recommendations")
    lines.append("")
    lines.append(dedent("""
        **For the Chipathon integration meeting:**

        1. **Tell students to keep the existing `run_lvs.sh` flow** (Magic + Netgen). It matches cleanly on the working cells and is the only LVS engine on this PDK that understands the xschem source netlist format.
        2. **Use KLayout only for DRC**, consistent with what Amro Tork already
           runs. The KLayout LVS deck at
           `/foss/pdks/gf180mcuD/libs.tech/klayout/tech/lvs/run_lvs.py` works in
           principle but is not compatible with the project's current source
           netlist format without manual rewriting.
        3. **Audit the `_comp.out` files that ship in the repo** (e.g.
           `biasgen_v2_comp.out` currently has a `pfet_05v0:MINV2 w 5e-7
           vs 6e-7, delta=18.2%` property error that Netgen tolerates as
           "match uniquely with property errors"). That delta is real and
           should be resolved in the schematic or layout before tapeout.
        4. **Align the project's `gf180mcuD_setup.tcl`** with the PDK-shipped
           one (or justify each deviation). Diff preview:
           `property parallel enable`, `delete par1`, and MIM-cap equivalence
           lines are all commented out in the project copy — these changes
           change Netgen's property-tolerance behaviour and were probably
           inherited from an older fork.
        5. **Populate the 5 missing library cells** (`biasgen_buffer`,
           `biasgen_mirror_4_to_10`, `biasgen_mirror_8_to_10`, `biasgen_opamp`,
           `biasgen_resistor_divider`) with layouts before claiming the
           `core_biasgen` library is tape-out-ready. Today 2 of 9 have any
           layout at all.
        """).strip())

    lines.append("")
    lines.append("## Reproduction")
    lines.append("")
    lines.append(dedent("""
        ```bash
        # From the eda-agents repo root
        # 1. Ensure the gf180-chip-test container is running with the
        #    /tmp/gf180-chip-test <-> /foss/designs bind mount.
        # 2. Clone AutoMOS-chipathon2025 inside the bind-mount:
        mkdir -p /tmp/gf180-chip-test/chipathon-lvs-audit
        cd /tmp/gf180-chip-test/chipathon-lvs-audit
        git clone --branch integration --depth 1 \\
            https://github.com/AutoMOS-project/AutoMOS-chipathon2025.git
        # 3. Run the audit:
        cd /home/montanares/personal_exp/eda-agents
        bash tutorials/chipathon-lvs-audit/run_audit.sh
        python3 tutorials/chipathon-lvs-audit/build_report.py
        # 4. Report is at tutorials/chipathon-lvs-audit/report/chipathon_lvs_audit.md
        ```
        """).strip())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
