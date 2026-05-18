# Ron/gm Corner Analysis

The Ron/gm methodology surfaces process-corner sensitivity at design entry, removing the iterative corner sweeps that conventional gm/ID flows defer to post-simulation.

## Why Ron is corner-sensitive

`Ron = Vds / Id` at the on-state. Across process corners:

- **SS corner**: higher `V_TH`, lower mobility -> larger `Ron` at the same `(W, L, Vgs)`. Settling RC phase slows down by a factor of 1.5-2x relative to TT. This is the **worst-case Ron** and the corner that determines whether the design meets the settling spec.
- **FF corner**: lower `V_TH`, higher mobility -> smaller `Ron`. The deadzone boundary shifts toward `0 V`, opening the operating window.

Wrøngm's table-II reference IBA on IHP SG13G2 LV shows the spread:

| Quantity      | FF        | TT        | SS        |
|---------------|-----------|-----------|-----------|
| V_DZN (NMOS deadzone)  | 0.614 V   | 0.658 V   | 0.700 V   |
| V_DZP (PMOS deadzone)  | 1.070 V   | 1.010 V   | 0.959 V   |

The 86 mV spread on `V_DZN` and 111 mV spread on `V_DZP` is directly readable from the LUT at design entry, before any SPICE iteration.

## Current eda-agents coverage

The IHP gm/ID LUT shipped with `ihp-gmid-kit` is a **single-corner (typical) snapshot**. The analytical Ron/gm path inherits that limitation: `RonGmLookup` returns TT-only numbers today. To run corner sweeps:

- Regenerate the LUT at SS and FF using the kit's scripts (`scripts/generate_gmid_lut.py --corner ss/ff`).
- Instantiate one `RonGmLookup` per corner, querying the same operating point on each, and compare Vbias, `Ron`, `Ron/gm`, `Ipeak`.

The Wrøngm companion repo (`chennakeshavadasa/gmid_IHP130` at commit `c31c01edbed41c06078b8272c32997c03db0000e`) ships pre-computed CSV LUTs at TT/SS/FF for IHP SG13G2 LV. Those CSVs are not consumed directly by `RonGmLookup` today; the eventual `RonGmCsvLookup` sibling will close the gap and remove the corner-sweep burden.

## Design discipline

When using Ron/gm on a real silicon target:

1. Pick `ron_gm_target` against the **SS corner**, not TT. Wrøngm's reference IBA targets `Ron/gm = 50 MΩ/S` at SS; the TT path always lands faster.
2. Read the deadzone Vbias **at SS** as the lower bound for the bias-circuit design. The TT and FF deadzones are softer constraints; if SS-bias is honoured, all corners are.
3. Track `Ipeak` at FF as the worst-case slew current -- it sets the upper bound on the bias-circuit current rating.
4. Document the corner the LUT was generated at in the design log. A TT-only LUT must not be conflated with an SS-corner sized device.
