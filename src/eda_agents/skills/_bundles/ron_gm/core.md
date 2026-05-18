# Ron/gm Methodology for Inverter-Based Dynamic Amplifiers

Adapted from Wrøngm (SSCS-OSE Code-a-Chip VLSI26, Apache-2.0, Nithin P et al., 2025) into eda-agents. The methodology targets **inverter-based dynamic amplifiers (IBAs)** -- the dominant amplifier in modern switched-capacitor ADCs -- where settling happens in two physically distinct phases.

## Why the conventional gm/ID methodology is incomplete for IBAs

A dynamic amplifier settles into a switched-capacitor feedback network in two phases:

1. **Large-signal RC phase**: when the output step is large, the transistor operates in saturation with the source-drain voltage limited by VDD. Effective time constant is `tau_LS = Ron * CL`, where `Ron = Vds_step / Id` is the on-state resistance (not the small-signal output resistance `rds = 1/gds`). This phase moves the output quickly but does so non-linearly.

2. **Small-signal exponential phase**: once the output enters the bandwidth-limited region, settling is governed by `tau_SS = CL / gm` with `BW = gm / (2*pi*CL)`.

The conventional `gm/ID` methodology characterises only `gm`. The large-signal `Ron` is not in the framework, so the **non-linear settling phase remains invisible until post-simulation**. For IBAs that's the dominant settling phase, so design entry without `Ron` visibility forces multiple SPICE iterations.

## What Ron/gm adds

The Ron/gm methodology pre-characterises both quantities as a function of device geometry, bias, and corner, and reads the design from a single LUT axis. The key analytical relations are:

- `Vbias = V_TH + 2 * Id / gm` (approximate, valid in moderate inversion; Wrøngm Eq. 8)
- `Ron/gm ∝ L^2 / (W * Id)` at fixed Vds; the ratio is W-dependent (scales as `1/W^2`) and L-dependent (scales as `L^2`).
- `gm_bias ∝ (Ron/gm)^(-1/3)` (Wrøngm Eq. 11)
- Peak slew current `I_peak ∝ 1 / (Ron/gm)` (log-log slope = -1, Wrøngm Plot 4.3)

These let a designer read Vbias, Ron, gm, Ipeak, and the deadzone boundary `V_DZN` / `V_DZP` directly from a LUT at design entry, before committing any SPICE budget.

## Deadzone

Each corner has a Vbias range below which the device hasn't entered the operating region where the two-phase settling model holds. Wrøngm's plot 4.1 (`Vbias` vs `log(Ron/gm)`) reads this off graphically: the deadzone boundary is the Vbias at which `Ron/gm` drops to a designer-chosen threshold (typically `5e7` for IHP SG13G2 LV at 0.5 uA bias). Designs must keep the bias above the SS-corner deadzone to remain robust.

## Coverage envelope

The methodology is **per-PDK** -- the LUT is regenerated for each technology node, and the analytical relations hold to the same accuracy. Wrøngm demonstrates it on IHP SG13G2 130 nm LV devices with ngspice 45.2 + OpenVAF + PSP 103.6 NQS; eda-agents extends the same LUT machinery to GF180MCU through `GmIdLookup` so the methodology re-targets to GF180 without code changes.

The methodology subsumes `gm/ID`: the existing `gm/ID` plot can be read off the LUT, but the converse is not true. Use Ron/gm when the design contains an IBA, a ring amplifier, or any block whose dominant settling phase is non-linear; stay with `gm/ID` for traditional linear OTAs.
