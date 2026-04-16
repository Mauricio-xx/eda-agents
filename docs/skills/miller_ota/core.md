# Miller OTA — Core Architecture

## Topology

A two-stage operational transconductance amplifier with Miller
compensation. The canonical variant shipped in eda-agents
(`topologies/miller_ota.py`) uses:

- **Stage 1**: NMOS differential pair (M1, M2) with PMOS current-mirror
  load (M3 diode, M4 mirror). Single-ended output at the drain of
  M2/M4.
- **Stage 2**: PMOS common-source (M6) with NMOS current-source load
  (M7). DC-coupled to stage 1 through the Miller capacitor `Cc`.
- **Tail bias**: ideal current source `Ibias` sinking the tail of the
  diff pair. Replace with a cascoded mirror for silicon.

The topology has a **single dominant pole**: before compensation the
two stages each contribute a pole near 1/(R·CL,stage), and the right-
half-plane (RHP) zero from Cc feeding forward sits at `gm6 / Cc`. The
Miller capacitor splits these by pole-splitting: the dominant pole
drops to `1/(gm6 · R1 · R2 · Cc)`, the non-dominant pole rises to
`gm6 / CL`. The trade to buy stability is bandwidth: GBW ≈ `gm1 / Cc`.

## Small-signal model

Define:
- `gm1` — transconductance of the input pair devices M1/M2.
- `gm6` — transconductance of the second-stage device M6.
- `R1` — output resistance at node 1 (parallel combination of
  `ro2 || ro4`).
- `R2` — output resistance at node 2 (parallel combination of
  `ro6 || ro7`).
- `Cc` — Miller compensation capacitor.
- `CL` — total capacitance at the output node (load + parasitic).

DC gain `Adc = gm1 · R1 · gm6 · R2`. The dominant pole is
`fp1 = 1 / (2π · gm6 · R1 · R2 · Cc)`. The non-dominant pole is
`fp2 ≈ gm6 / (2π · CL)`. The gain-bandwidth product is
`GBW = gm1 / (2π · Cc)`. Phase margin depends on the ratio
`fp2 / GBW`: 60° requires `fp2 ≳ 2.2 · GBW`, i.e.
`gm6 ≳ 2.2 · (CL/Cc) · gm1`.

## When Miller is the right choice

- **Yes**: you need > 40 dB gain on a single-ended output with a 1-10
  pF load and modest GBW (kHz to low-MHz). Well-understood stability
  behaviour and excellent DC gain per unit area.
- **No**: you need high slew rate (Miller is slew-limited by
  `Ibias/Cc`), or very wide bandwidth (the Miller pole-split caps GBW
  at about `gm6 / (2.2 · CL)`). Consider folded cascode or
  feedforward-compensated topologies for those.

## Reference point on IHP SG13G2

The designer ships with a known-good sizing:
`gmid_input=12, gmid_load=10, L_input_um=0.5, L_load_um=0.5,
Cc_pF=0.5, Ibias_uA=10`. Expect Adc ≈ 55 dB, GBW ≈ 3 MHz, PM ≈ 65° on
a 1 pF load. Use this as the starting point before exploring.
