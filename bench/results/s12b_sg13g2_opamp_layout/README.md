# S12-B Gap 4 — SG13G2 `opamp_twostage` partial landing

## Mode: partial (+ Gap A closed on follow-up session 2026-04-18)

User-confirmed scope pivot after live discovery of an architectural
blocker. Session commitment updated from "SG13G2 opamp_twostage
LVS-clean" to "GDS generates, verification blockers documented with
upstream fix paths".

**Gap A update (2026-04-18)**: the MIM cap extraction blocker is now
fixed on the fork branch `feature/s12-opamp-sg13g2-integration`
@ `27194ff`. The `cap_cmim` extraction that previously produced 0 MIM
caps now returns 1 device `m=6` (6 parallel caps unified into one
cluster — matches the schematic's 6 MIMCap instances in parallel).
DRC remains at 70 pre-existing metal-rule violations (no new ones
introduced by the synthesised TopMetal1 / vMIM / TopVia1 markers).
LVS is still not clean end-to-end; the remaining delta is the
`cs_bias` netlist mismatch (next-session Gap 4 follow-up) plus the
routing DRC violations.

See:

- `docs/s12_findings/s12b_sg13g2_opamp_twostage.md` — investigation +
  blocker analysis.
- `/home/montanares/.claude/plans/s12-b-analog-jazzy-hollerith.md`
  (Gap 4 acceptance REVISED section) — the scope pivot conversation.

## What this evidence directory contains

- `generate_evidence.py` — driver script: constructs a `GLayoutRunner`,
  calls `generate_component(component="opamp_twostage",
  pdk="ihp_sg13g2")`, writes `result.json`.
- `run_drc_lvs.py` — reproduction script for DRC + LVS (runs in
  `.venv-glayout`; writes DRC/LVS report copies into `artifacts/`).
- `artifacts/opamp_twostage.gds` — 2.7 MB output of `GLayoutRunner`.
- `artifacts/opamp_twostage.spice` — gLayout-emitted reference netlist
  (unflattened).
- `result.json` — structured record of params, generate result, DRC
  baseline counts, LVS delta, blocker summary.

## Why this matters

At the end of S11 the claim was *"opamp_twostage is gf180mcu-only
today; the SG13G2 upstream port is WIP"*. The `scripts/glayout_driver.py`
guard at lines 314–326 hard-rejected any SG13G2 opamp request. That
was load-bearing for the S11 MCP smoke test, which never actually saw
an SG13G2 opamp GDS.

S12-B's live investigation shows SG13G2 **builds end-to-end** (2.7 MB
GDS, 2167 B netlist, 75 s on a laptop) through every `opamp_twostage`
sub-composite. The remaining gap from LVS-clean is not layout crashes
or missing composites — it's two targeted issues:

1. **MIM cap layer mapping mismatch with IHP's cap_cmim extractor**
   (met4-mim-met5 in gLayout vs met5-mim-topmetal1 + vmim + topvia1
   in IHP). Documented in the findings file with two fix paths; user
   elected to defer to a follow-up PR rather than compress the
   upstream rework into this session.
2. **`cs_bias_netlist` parameter mismatch** at
   `opamp_twostage.py:226` (uses `diffpair_bias` where the layout uses
   `half_common_source_bias`). Pre-existing in the GF180 code too;
   masked by netgen's handling of `m` which flat-KLayout extraction
   doesn't replicate. Fixable in the fork independently of the MIM cap
   work.

With the guard flipped, `generate_analog_layout` now produces the GDS
on SG13G2. The MCP tool's failure mode moves from "hard-rejection
before gLayout runs" to "success with `lvs_passed=False` annotation
referencing these blockers" — which is honest and actionable.

## Re-running

```bash
cd /home/montanares/git/eda-agents-worktrees/s12b-analog-layout
.venv/bin/python bench/results/s12b_sg13g2_opamp_layout/generate_evidence.py
# Writes: artifacts/opamp_twostage.gds, .spice, and result.json
```

DRC + LVS verification (needs `.venv-glayout`):

```bash
/home/montanares/personal_exp/eda-agents/.venv-glayout/bin/python \
    bench/results/s12b_sg13g2_opamp_layout/run_drc_lvs.py
```

## Follow-up commitments

1. **gLayout SG13G2 MIM cap mapping** — **CLOSED (2026-04-18)** via
   Path B. Fork commit `27194ff` on branch
   `feature/s12-opamp-sg13g2-integration`: `sg13g2_decorator`
   synthesises TopMetal1 / vMIM / TopVia1 markers over each MIM
   polygon, and `mimcap_array` draws a unifying TopMetal1 strip over
   the full placed array so parallel caps share one top-plate net.
2. **`opamp_twostage` schematic mismatch with stacked_nfet_current_mirror
   layout** — OPEN. Align `cs_bias_netlist` construction with the
   real bias structure; apply to both PDKs; add SG13G2 flat-merge
   awareness.
3. **SG13G2 top-level routing DRC** — OPEN. 70 violations in M1–M4
   spacing rules. Fix pattern already established for `diff_pair`,
   `FVF`, `low_voltage_cmirror`; mechanical application to
   `__create_and_route_pins` and `__add_mimcap_arr`.

Gap 5's 4-bit current-steering DAC does not depend on any of these
follow-ups — it exercises the composition loop independently.
