# S12-B Gap 4 — SG13G2 `opamp_twostage` live investigation

Baseline-run evidence captured against gLayout fork branch
`feature/sg13g2-pdk-support` at `efe1779`. Context: the S11 delivery
declared `opamp_twostage` WIP on SG13G2; this session's scratch runs
reveal exactly what's missing for a true LVS-clean landing.

## What works out of the box

- All sub-composites **build without exceptions** on SG13G2: primitives
  (`nmos`, `pmos`, `mimcap`), composites (`diff_pair`,
  `current_mirror`, `FVF`, `low_voltage_cmirror`,
  `stacked_nfet_current_mirror`, `diff_pair_ibias`,
  `diff_pair_stackedcmirror`,
  `differential_to_single_ended_converter`,
  `row_csamplifier_diff_to_single_ended_converter`), and the full
  `opamp_twostage`.
- `Component.write_gds()` succeeds for each; end-to-end opamp GDS is
  ~2.7 MB.
- `sg13g2_mapped_pdk.drc(comp, design_name)` runs to completion.
- `sg13g2_mapped_pdk.lvs_klayout(comp, design_name, spice_path)` runs
  through extraction and reaches "Netlists don't match" (i.e. the LVS
  deck finds the top cell, extracts, and hits the comparison stage —
  infrastructure healthy; the gap is in the netlist/layer mapping).

## What does not work

### 1. DRC violations (70 markers across 6 metal rules)

```
 count  rule
    30  M3.b
    16  M2.b
     8  M1.b
     8  M2.e
     4  M3.e
     4  M4.b
```

All are metal min-spacing (`.b`) or via-enclosure (`.e`) violations in
the top-level opamp routing (`__create_and_route_pins`,
`__add_mimcap_arr`). The pattern matches what the fork already fixed
for `diff_pair` and `low_voltage_cmirror`: SG13G2's 0.21 µm met2–met5
min-separation is tighter than sky130/gf180's, so routes that are
clean on those PDKs notch the IHP rules. Fix pattern: SG13G2 branches
that add `pdk.get_grule("metN")["min_separation"]` margin to route
extensions, and optional met-bridge patches at via pads following
`low_voltage_cmirror.py:85-95`.

### 2. LVS extracted vs flattened-schematic delta

Baseline (`half_diffpair_params=(6,1,4)`,
`diffpair_bias=(6,2,4)`, `half_common_source_params=(7,1,10,3)`,
`half_common_source_bias=(6,2,8,2)`, `half_pload=(6,1,6)`,
`mim_cap_size=(12,12)`, `mim_cap_rows=3`, `rmult=2`):

| metric | schematic | extracted |
|---|---|---|
| NMOS device count | 5 | 5 |
| PMOS device count | 5 | 5 |
| MIM cap count | 6 | 0 |
| distinct NMOS W values | {24, 48} | {24, 48, 288} |

Two root causes:

**2a. CS bias NMOS count/width mismatch** — `opamp_twostage.py:226-231`
calls `current_mirror_netlist` with `diffpair_bias` params
(`w=6, l=2, m=4`) instead of `half_common_source_bias` params
(`w=6, l=2, f=8, m=2`). The layout builds the CS bias as
`stacked_nfet_current_mirror(half_common_source_bias, ...)` twice (L
and R), yielding 2×ref + 2×out + 2×dummy NMOS that the KLayout flat
extractor merges into one W=288 µm device because (a) dummies share
all four terminals on GND/VB, (b) all gates tie via
`halfmultn_gate_routeref`, and (c) the `with_dummy=True` branch in
`current_mirror_netlist` emits `XDUMMY VB VB VB VB` which the flat
schematic drops. The schematic should emit a single W=288 µm merged
device (or an explicitly expanded 4×48+4×72 structure) to match the
flat-extracted layout.

**2b. MIM cap extraction zero hits** — **architectural blocker**.
IHP's KLayout LVS deck
(`libs.tech/klayout/tech/lvs/rule_decks/cap_derivations.lvs:30-36`)
derives `cap_cmim` as:

```
mim_drw = get_polygons(36, 0)
mim_top = mim_drw.overlapping(topmetal1_con)
mim_btm = mim_drw.and(metal5_con)
mim_via = vmim_drw.join(topvia1_drw).and(mim_drw)
cmim_top = mim_top.not(mimcap_exclude)
cmim_btm = mim_btm.covering(cmim_top)
cmim_dev = mim_drw.covering(cmim_top).and(cmim_btm)
```

I.e. **bottom plate = met5, top plate = TopMetal1, via = vMIM / TopVia1**.

gLayout's fork currently maps SG13G2 MIM caps as
**met4-mim-met5** (see `sg13g2_grules.py:467-475`, which documents
the choice explicitly: *"gLayout has no TopMetal1 in valid_glayers,
so we map: capmetbottom = Metal4, capmettop = Metal5"*). The
`mimcap` primitive draws a `via_array(met4→met5)` with a `mim`
(36,0) marker and a met4 enclosure — sufficient for DRC but not for
IHP's LVS device extraction, which fails silently (no MIM cap
emitted in the extracted netlist).

Fix surfaces two upstream paths:
- **Path A (preferred long-term)**: add `topmetal1` (and optionally
  `topvia1`, `topmetal2`, `topvia2`) to
  `glayout.pdk.mappedpdk.valid_glayers`, extend the
  `sg13g2_glayer_mapping` accordingly, rework `mimcap.py` so the
  primitive emits vMIM (129, 0) and TopVia1 (125, 0) + TopMetal1
  (126, 0) when the PDK's `capmettop` maps to a TopMetal tier.
- **Path B (lower-surface-area patch)**: ship an SG13G2-specific
  decorator hook in `sg13g2_decorator.py` (like the existing nSD
  removal) that **synthesises** vMIM + TopVia1 + TopMetal1 markers
  over the MIM cap area post-write. Schema stays the same upstream;
  the fork adds a 20-line post-processor.

Both exceed the S12-B session budget and are the reason Gap 4 lands
partial (see S12-B handoff + plan file).

## What this session lands (scope per plan)

1. `scripts/glayout_driver.py:314-326` — remove the hard-rejection of
   SG13G2 opamps so `generate_analog_layout` produces a GDS + netlist
   (LVS status reported as annotation, not error).
2. `tests/test_glayout_runner.py::TestGLayoutPdkDispatch` — replace
   `test_opamp_rejects_sg13g2` with `test_opamp_twostage_sg13g2`
   asserting `success`, `gds_path` exists, `netlist_path` exists. LVS
   reality (not yet clean) is documented here, not gated.
3. `bench/results/s12b_sg13g2_opamp_layout/` — README + structured
   result JSON with params, GDS/netlist paths, DRC counts, LVS delta,
   blocker summary.
4. This document (`docs/s12_findings/s12b_sg13g2_opamp_twostage.md`) —
   the architectural context for whoever picks up the MIM cap rework
   upstream.

## Reproduction

Scratch scripts used to produce the numbers above live at
`/tmp/s12b_opamp_baseline.py`, `/tmp/s12b_opamp_baseline2.py`,
`/tmp/s12b_lvs_diff.py`, `/tmp/s12b_drc_summary.py`. They are not
committed (per global rule: scratch stays under `/tmp/`); re-authoring
them is straightforward from this document.

Environment:
- gLayout fork checked out on `feature/s12-opamp-sg13g2-integration`
  at `/home/montanares/personal_exp/gLayout`.
- `.venv-glayout` at `/home/montanares/personal_exp/eda-agents/.venv-glayout`
  (editable install of the fork).
- `PDK_ROOT=/home/montanares/git/IHP-Open-PDK`.

## Follow-up issues to file (upstream or fork)

1. `mimcap` primitive architectural mismatch with IHP SG13G2 cap_cmim
   LVS deck (met5-topmetal1 expected, met4-met5 current). Recommended
   fix: Path A above.
2. `opamp_twostage::opamp_gain_stage_netlist` uses `diffpair_bias`
   params for the CS bias schematic instead of `half_common_source_bias`
   — pre-existing mismatch between schematic and the
   `stacked_nfet_current_mirror`-based layout; needs aligned schematic
   with flat-merge support for both PDKs (GF180 netgen handles m
   separately; SG13G2 flat-extraction needs the merge modelled).
3. `opamp_twostage` top-level routing has 70 DRC violations on SG13G2
   in the M1–M4 spacing rules. Fix pattern already established in the
   fork for `diff_pair`, `FVF`, `low_voltage_cmirror`. Scoped as a
   follow-up PR.
