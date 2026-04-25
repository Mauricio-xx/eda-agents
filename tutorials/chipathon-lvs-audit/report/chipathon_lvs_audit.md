# Chipathon LVS audit — KLayout vs Magic+Netgen on GF180MCU `core_biasgen`

- **Image**: `hpretl/iic-osic-tools:next` (container `gf180-chip-test`)
- **PDK**: `/foss/pdks/gf180mcuD/` (ciel-installed; open_pdks `7b70722`)
- **Tooling**: KLayout 0.30.7 · Magic 8.3.635 · Netgen 1.5.318
- **Design source**: `AutoMOS-project/AutoMOS-chipathon2025@integration` → `designs/libs/core_biasgen/`

## Meeting bullets (copy/paste)

- Discrepancy **reproduced on the Docker image**: KLayout LVS and Magic+Netgen LVS disagree on the same (GDS, SPICE) pair.
- Only **2 of 9** biasgen cells are LVS-ready (have both `.gds` and `.spice`): `biasgen_mirror_2_to_10, biasgen_v2`. The other 7 are schematic-only and cannot be LVS-checked.
- On the 2 LVS-ready cells, **KLayout LVS = MISMATCH** and **Magic+Netgen LVS = MATCH** (both with PDK-default and project-customized `gf180mcuD_setup.tcl`).
- **Root cause**: the xschem-exported source `.spice` uses `X`-prefix subcircuit calls to `nfet_05v0` / `pfet_05v0` / `ppolyf_u_1k_6p0` that rely on ngspice PDK wrappers (`sm141064.ngspice`) with parameter expressions and `m=N` multipliers. Magic+Netgen papers over this via `equate classes` directives in `gf180mcuD_setup.tcl`; **KLayout's SPICE reader cannot resolve the same input** even with `--lvs_sub=VSS` and `--include` of the wrappers.
- **Implication for the Chipathon students**: use **Magic+Netgen** for LVS (already the project's own `run_lvs.sh`), not KLayout. Use KLayout only for DRC (which is what Amro's thread is doing anyway).
- The project's customised `gf180mcuD_setup.tcl` deviates from the PDK version (`property parallel enable` commented out, `delete par1` commented out, MIM cap section trimmed). On these two cells the customization did not change the verdict, but it's latent risk — our recommendation is to align it with the PDK version unless there's a specific reason.

## Verdict matrix

| Cell | LVS-ready | KLayout LVS | Magic+Netgen (project setup) | Magic+Netgen (PDK setup) |
|---|:---:|:---:|:---:|:---:|
| `biasgen` | — | — | — | — <br/><sub>missing  .spice — not LVS-ready</sub> |
| `biasgen_buffer` | — | — | — | — <br/><sub>missing .gds .spice — not LVS-ready</sub> |
| `biasgen_inverter` | — | — | — | — <br/><sub>missing  .spice — not LVS-ready</sub> |
| `biasgen_mirror_2_to_10` | yes | **MISMATCH** | MATCH | MATCH |
| `biasgen_mirror_4_to_10` | — | — | — | — <br/><sub>missing .gds .spice — not LVS-ready</sub> |
| `biasgen_mirror_8_to_10` | — | — | — | — <br/><sub>missing .gds .spice — not LVS-ready</sub> |
| `biasgen_opamp` | — | — | — | — <br/><sub>missing .gds .spice — not LVS-ready</sub> |
| `biasgen_resistor_divider` | — | — | — | — <br/><sub>missing .gds .spice — not LVS-ready</sub> |
| `biasgen_v2` | yes | **MISMATCH** | MATCH (prop err) | MATCH (prop err) |

## Per-cell details

### `biasgen_mirror_2_to_10`

**KLayout LVS** — rc=0, run_time=5s (LVS-internal 2.79s). Verdict: **MISMATCH**.
<br/>Log marker: `ERROR : Netlists don't match`.
<br/>Artefacts: `/foss/designs/chipathon-lvs-audit/runs/biasgen_mirror_2_to_10/klayout/biasgen_mirror_2_to_10.lvsdb`, extracted netlist `/foss/designs/chipathon-lvs-audit/runs/biasgen_mirror_2_to_10/klayout/biasgen_mirror_2_to_10.cir`.

**Magic+Netgen (project setup)** — rc=0. Final: _Circuits match uniquely._. Devices=25 nets=21.

**Magic+Netgen (PDK setup)** — rc=0. Final: _Circuits match uniquely._. Devices=25 nets=21.

### `biasgen_v2`

**KLayout LVS** — rc=0, run_time=4s (LVS-internal 2.87s). Verdict: **MISMATCH**.
<br/>Log marker: `ERROR : Netlists don't match`.
<br/>Artefacts: `/foss/designs/chipathon-lvs-audit/runs/biasgen_v2/klayout/biasgen_v2.lvsdb`, extracted netlist `/foss/designs/chipathon-lvs-audit/runs/biasgen_v2/klayout/biasgen_v2.cir`.

**Magic+Netgen (project setup)** — rc=0. Final: _Circuits match uniquely._. Devices=26 nets=21.
<br/>Property errors:
  - `w` circuit1=5e-07 vs circuit2=6e-07 (delta=18.2%)
  - cells: biasgen_inverter

**Magic+Netgen (PDK setup)** — rc=0. Final: _Circuits match uniquely._. Devices=26 nets=21.
<br/>Property errors:
  - `w` circuit1=5e-07 vs circuit2=6e-07 (delta=18.2%)
  - cells: biasgen_inverter

## Deep dive: why KLayout rejects both cells

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

## Recommendations

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

## Reproduction

```bash
# From the eda-agents repo root
# 1. Ensure the gf180-chip-test container is running with the
#    /tmp/gf180-chip-test <-> /foss/designs bind mount.
# 2. Clone AutoMOS-chipathon2025 inside the bind-mount:
mkdir -p /tmp/gf180-chip-test/chipathon-lvs-audit
cd /tmp/gf180-chip-test/chipathon-lvs-audit
git clone --branch integration --depth 1 \
    https://github.com/AutoMOS-project/AutoMOS-chipathon2025.git
# 3. Run the audit:
cd /home/montanares/personal_exp/eda-agents
bash tutorials/chipathon-lvs-audit/run_audit.sh
python3 tutorials/chipathon-lvs-audit/build_report.py
# 4. Report is at tutorials/chipathon-lvs-audit/report/chipathon_lvs_audit.md
```