# Miller OTA — gm/ID Sizing

This skill assumes you already read `analog.gmid_sizing`. Everything
below layers Miller-specific constraints on top of the generic
`GmIdLookup` workflow.

## Input pair (M1/M2)

The input pair sets GBW (via `gm1`) and input-referred noise. Target
**moderate inversion**: `gmid_input` in the 10-15 S/A range. Lower
values waste current without buying speed because fT plateaus; higher
values increase gm/ID efficiency but push the non-dominant pole in,
hurting PM.

- `gm1 = gmid_input · (Ibias/2)` since M1 and M2 share the tail.
- Channel length `L_input_um` sets intrinsic gain and flicker noise:
  0.5-1.0 µm is the usual sweet spot for a low-noise input pair; only
  drop to the minimum 0.13 µm when GBW demands it and you can accept
  the gain hit.

## Load pair (M3/M4)

The PMOS mirror load contributes half the first-stage gain and the
load-side pole. Target **weaker inversion**: `gmid_load` in the 10-20
S/A range to keep rds high.

- The mirror ratio is 1:1 for matching; any asymmetry biases the
  diff pair output.
- Longer `L_load_um` (0.5-2 µm) raises rds and therefore stage-1
  gain, at the cost of more area and lower fT. Avoid minimum L unless
  the gain budget is already met elsewhere.

## Second-stage gm6 / PMOS CS

Sized from the phase-margin constraint, not from raw gm/ID intuition.
From `fp2 ≳ 2.2 · GBW`:

```
gm6 ≥ 2.2 · (CL / Cc) · gm1
```

For `CL = 1 pF` and `Cc = 0.5 pF`, that's `gm6 ≥ 4.4 · gm1`. Size M6
for that gm at whatever gm/ID gives enough intrinsic gain to hit the
Adc target — typically gm/ID ≈ 8-12 in strong/moderate inversion.

## Second-stage bias current `Ibias2`

`Ibias2 = gm6 / gmid2`. Because the second stage dominates power when
gm6 is high, push gmid2 high (12-15 S/A) to keep current reasonable
unless slew rate forces you lower. Slew-limited designs want strong
inversion for speed: gmid2 = 5-8 S/A costs current but unlocks large
Vdsat headroom.

## Interactions with the knob set

- **Ibias_uA** is the primary power/speed knob. Doubling it doubles
  gm1, doubles GBW, and requires doubling gm6 to keep PM — total
  power more than doubles.
- **Cc_pF** trades bandwidth for phase margin (see compensation.md).
- **Length ratios** affect pole placement: longer input-pair L
  reduces stage-1 gain loss but may push the stage-1 pole lower.
  Keep `L_input_um / L_load_um` in the 0.5-2× range unless there is
  a good reason.

## Typical failure signatures

- **Adc low, GBW high**: gm/ID on the input pair is too low or Ls
  are too short. Bump `L_input_um` to 1 µm, raise `gmid_load`.
- **Adc high, GBW low**: Ibias is undersized. Scale Ibias up by the
  GBW deficit factor; re-check PM afterwards.
- **PM < 45° even at large Cc**: gm6 is undersized relative to gm1.
  Raise Ibias2 (through gmid_load on M6) or widen Cc.
