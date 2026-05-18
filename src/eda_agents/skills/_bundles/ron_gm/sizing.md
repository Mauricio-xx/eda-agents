# Ron/gm Sizing API -- RonGmLookup

This skill assumes you already read `analog.gmid_sizing`. The `RonGmLookup` class wraps `GmIdLookup` and adds the Ron-centric views the Wrøngm methodology needs. No new LUT data is required: the same PSP103 (.npz) LUT that powers gm/ID is used, with `Ron` derived analytically from `Vds / Id` at the user-specified on-state operating point.

## Two operating points

Every Ron/gm query carries two independent operating points:

- **On-state** (`Vgs_on`, `Vds_on`): the transistor is fully driven, conducting the large-signal RC settling current. Default `Vgs_on = PDK.VDD`, `Vds_on = 0.05 V` (linear-region edge of the LUT sweep). The `Ron = Vds_on / Id_on` read picks up the switch-on resistance the device exhibits during the non-linear settling phase.
- **Bias point** (`Vgs_bias`, `Vds_bias`): the small-signal operating point that sets `gm`. Default `Vds_bias = PDK.VDD / 2` (mid-rail).

Both points scale linearly with `W`: `Ron ∝ 1/W`, `gm ∝ W`. Therefore `Ron/gm ∝ 1/W^2` -- the metric is W-dependent and must be computed at the user's actual target width.

## Canonical sizing call

```python
from eda_agents.core.ron_gm_lookup import RonGmLookup

ron_gm = RonGmLookup(pdk="ihp_sg13g2")
out = ron_gm.size_from_ron_gm(
    ron_gm_target=50e6,        # the headline Ron/gm spec, Wrøngm uses 50e6 for IBA bias=0.5 uA
    mos_type="nmos",
    L_um=3.0,
    Ibias_uA=5.0,              # characterisation current; Wrøngm uses 5 uA on IHP SG13G2 LV
)
print(out.W_um, out.vgs_V, out.Ron_ohm, out.gm_uS, out.Ron_gm, out.Ipeak_uA)
```

`size_from_ron_gm` returns a `RonGmPoint` whose `as_sizing_dict()` mirrors the canonical `GmIdLookup.size()` schema, plus the Ron-specific fields `Ron_ohm`, `Ron_gm`, `Ipeak_uA`, `Vds_on_V`, `Vgs_on_V`, `deadzone_bias_V`, `deadzone_threshold`. Use the dict form when feeding downstream code that already consumes `gm/ID` dicts (autoresearch, MCP tools).

## Algorithm

For each Vbias in `[Vbias_min, Vbias_max]` (default `[0, VDD]`):

1. Read `Id/W` and `gm/W` at the bias point `(Vbias, Vds_bias)`.
2. Compute `W = Ibias / (Id/W)` so the device delivers the requested bias current.
3. Compute `Ron = Vds_on / (Id/W * W)` at the on-state, `gm = gm/W * W` at the bias point, `Ron/gm = Ron / gm`.
4. Discard candidates where `gm/ID > gmid_max` (default 20 S/A) to avoid deep subthreshold solutions where `gm/ID` is artificially high and the sized device balloons.

Among the remaining candidates that meet `Ron/gm <= target`, the helper picks the highest Vbias (smallest device, fastest large-signal). If no candidate meets the target, it returns the closest miss and logs a warning -- in those cases, raise `Ibias_uA` to a higher characterisation current and post-scale `W` to the design bias level.

## Width scaling across bias levels

Wrøngm's methodology characterises at a fixed `Ibias_char_uA` (5 uA on IHP SG13G2 LV) and post-scales `W` to the design bias:

```python
W_design = W_char * (Ibias_design / Ibias_char)
```

This keeps the operating point (`Vbias`, `gm/ID`) constant but changes `Ron` and `gm` linearly with `W`, so `Ron/gm` shifts as `(Ibias_char / Ibias_design)^2`. Post-scaling is a methodology choice, not an invariance claim -- record both characterisation and design Ron/gm in design notes so reviewers can see the shift.

## Diagnostics

`operating_range(mos_type)` reports the achievable `Ron/gm` envelope across the LUT's length axis at the default on/bias points. Call this first when targeting a new spec to see whether the LUT supports it at all.

`deadzone_bias(mos_type, L_um, W_um, ron_gm_threshold, ...)` finds the Vbias at which `Ron/gm` crosses a designer-chosen threshold from above. Use it to surface the boundary of the two-phase settling window before sizing.

## Failure signatures

- **`size_from_ron_gm` raises "No valid Vbias ... with gm/ID <= gmid_max"**: the LUT cannot deliver `Ibias_uA` at this `L_um` while staying out of subthreshold. Raise `Ibias_uA` (characterisation current), shorten `L_um`, or relax `gmid_max` (only if you accept subthreshold operation).
- **Achieved `Ron/gm` is order-of-magnitude above target**: at low bias currents (sub-µA), the LUT may not reach the target Ron/gm at any `L`. Characterise at 5 uA and post-scale, accepting the post-scale Ron/gm shift.
- **`Vgs=...V clipped to LUT max ...V` warning**: the on-state `Vgs_on` (defaults to PDK.VDD) exceeds the LUT's `vgs_max`. The LUT was generated up to `vgs_max = 1.5 V` on IHP SG13G2; if `Ron` numbers look off, regenerate the LUT with `vgs_max = VDD` (1.65 V on IHP LV) via the ihp-gmid-kit scripts.
