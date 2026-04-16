# Miller OTA — Compensation

## The RHP zero problem

The Miller capacitor Cc feeds the input signal forward from node 1 to
the output. This creates a right-half-plane zero at
`fz = gm6 / (2π · Cc)`. An RHP zero adds gain without adding phase lead
— it erodes phase margin exactly when you were counting on the gm6/CL
pole to stabilise the loop.

For `gm6 = 100 µS` and `Cc = 0.5 pF`, `fz ≈ 32 MHz`. If GBW lands near
that frequency the loop is barely conditionally stable. Two standard
fixes:

1. **Series resistor Rz** in line with Cc (most common). Choosing
   `Rz = 1 / gm6` places the zero at infinity; making `Rz > 1/gm6`
   pushes it into the LHP and can even be used to cancel the
   non-dominant pole.
2. **Voltage-follower-in-the-feedback** (ahuja / cascoded Miller).
   Uses a cascode or source follower to block the feedforward path,
   killing the zero but costing headroom and area.

The eda-agents `MillerOTADesigner` does **not** insert Rz by default
— `Cc_pF` is the only compensation knob in the canonical design
space. If you are hitting PM ceilings around 55-60° and cannot reduce
GBW further, that is the signal to fork the topology and add Rz.

## Choosing Cc (the `Cc_pF` knob)

Picking Cc is a direct trade between GBW and PM.

- `GBW = gm1 / (2π · Cc)` → halving Cc doubles GBW.
- `fp2 / GBW = (gm6 · Cc) / (gm1 · CL)` → halving Cc also halves the
  pole separation, hurting PM.

Practical strategy: fix your GBW target, solve for Cc given gm1, then
check that `gm6 ≥ 2.2 · (CL/Cc) · gm1`. If not, scale gm6 up (more
Ibias on the second stage) rather than shrinking Cc further.

Lower bound: **never size `Cc_pF` smaller than `CL_pF / 10`**. Below
that, the Miller pole-split degenerates and the topology behaves like
two weakly coupled single-pole stages, which is not what the
small-signal analysis above predicts.

Upper bound: 5 pF in this design space. Larger Cc hurts GBW linearly
and wastes area; if stability still demands it, the root problem is
under-sized gm6, not under-sized Cc.

## Phase margin targets

- **60°** — standard target for unity-gain-stable applications.
  Ringing is modest (overshoot ~9%), settling time is fast.
- **75°-80°** — target when the amplifier drives an RC network whose
  pole falls near GBW (common in sampled-data systems); otherwise
  overkill.
- **< 45°** — never ship this. Even if simulations look fine, corner
  variation (SS corner reduces gm6) will kick PM below zero and the
  amplifier oscillates.

## Debugging stability

If a simulation shows PM < 60° at the operating point:

1. Plot open-loop gain and phase. Find `fp1`, `fp2`, `fz` from the
   slopes. If `fp2 < 2 · GBW`, raise Ibias2 / gm6.
2. Check slew rate: Miller OTA slew = `Ibias / Cc` on the Cc node,
   and `Ibias2 / CL` on the output. If the second-stage slew limits
   large-signal response, raise Ibias2 instead of touching Cc.
3. Watch the loop at temperature and corner: SS at 125°C is the usual
   worst case for gm; verify PM > 50° there before declaring the
   design stable.

## When to abandon Miller compensation

If the target specs require GBW > 50 MHz on IHP SG13G2 with a > 1 pF
load, pole-splitting becomes inefficient: Cc has to be tiny and gm6
huge. Switch to **folded cascode** (single-stage, naturally dominant
pole at the output) or a **feedforward topology**. The
`comparator_strongarm` or `ota_analogacademy` topologies may be
better starting points for those regimes.
