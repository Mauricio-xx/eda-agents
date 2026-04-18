## Sizing guidance

Pre-layout SPICE pass is the first gate: if the composition can't meet
target specs at the schematic level, no amount of DRC fixes or LVS
alignment will save it. Size deliberately.

### gm/ID sizing cheat sheet

- **gm/ID regions** (IHP SG13G2 LV MOS, 27 °C):
  - Weak inversion: gm/ID ≈ 25 V⁻¹. Best for: low-current bias copies,
    reference stages, ultra-low-power front-ends.
  - Moderate: gm/ID ≈ 10–18 V⁻¹. Best for: first-stage amps, bias
    transistors that also need moderate bandwidth.
  - Strong inversion: gm/ID ≈ 5–8 V⁻¹. Best for: output drivers,
    switches, high-fT stages.

- Rough relation: **W/L = ID / (gmid × Vov² × µ × Cox / 2)** for
  strong inversion; for moderate, consult the gm/ID LUT at
  `src/eda_agents/core/gmid_lookup.py`.

### Target conversion rules of thumb

| Spec                 | What to size first |
|----------------------|---|
| DC gain              | transconductance (gm) of first stage; choose weak/moderate gm/ID |
| Bandwidth / GBW      | output pole = gm / (2π × Cload); size gm and check Cload |
| Slew rate            | Itail / Cload directly; set Itail from SR spec |
| Noise (flicker)      | device area (W×L) — bigger reduces 1/f noise by 1/area |
| Offset / matching    | larger W and longer L for matched pairs |
| Power                | total Itail × Vdd; optimise gm/ID to reduce Itail |

### Current-steering DAC specifics (likely target)

If the NL request is a current-steering DAC (4-bit or similar):

1. **Unit current source (cm_unit)**: `current_mirror` with `type=nfet`,
   sized to output the LSB current (e.g. 1 µA). `W` around 2–5 µm,
   `L` around 1–2 µm for matching. `fingers` scaled for parallel copies.
2. **Binary weights**: emit N instances of the unit cmirror at
   `multipliers = 2**bit` (bit 0 → m=1, bit 3 → m=8), OR use a single
   cmirror with variable fingers.
3. **Switches (sw_b0, sw_b1, …)**: `nmos` with short `L` (0.13 µm),
   `W` ≈ 1–2 µm, fingers to handle the peak current. 4 switches for
   4-bit DAC differential output.
4. **Output resistors or cascodes**: not always needed for pre-layout
   verification; can be added if output impedance is a spec.

Target specs for a 4-bit DAC:
- LSB = 1 µA (Iout at bit=0001)
- INL, DNL < 0.5 LSB
- Settling time ≤ 10 ns (at 1 MHz update rate)
- Supply = 1.2 V (SG13G2)

## Sizing pipeline

1. For each sub-block in the composition, emit initial sizing from
   NL constraints + gm/ID rules.
2. Library code runs ngspice and measures against `target_specs`.
3. If any spec is off by > 30 %, propose a sizing patch (not a
   composition change).
4. If sizing oscillates > 2 iterations without convergence, flag an
   architectural mismatch (e.g. "diff_pair alone can't hit this DC
   gain target; the composition needs a second stage or cascode").
