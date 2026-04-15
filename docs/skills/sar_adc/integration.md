# Top-level integration

This note records how the blocks fit together at netlist level so a
reviewer can trace a waveform from `vin_pos` all the way to a decoded
output code.

## Net list

| Net                  | Role                                                      |
|----------------------|-----------------------------------------------------------|
| `vin_pos`, `vin_neg` | Differential input, sine or DC.                            |
| `cdac_top_p`, `cdac_top_n` | CDAC top plates, sampled by the ideal bootstrap. |
| `cdac_bot_p_i`, `cdac_bot_n_i` | CDAC bottom plates per bit `i`.                |
| `vcm`                | Common-mode reference (`VDD/2`), pulls bottom plates during sampling. |
| `clk_samp`           | Sampling pulse (first half-period).                        |
| `clk_comp`           | Comparator clock: HIGH = reset, LOW = evaluate.            |
| `clk_algo`           | Free-running algorithm clock produced by the NAND (`comp_outp`, `comp_outn`). Not fed to the SAR FSM directly; left in the deck for observability. |
| `comp_outp`, `comp_outn` | Comparator outputs; routed through `adc_bridge` to the SAR digital domain. |
| `B[i]`, `BN[i]`      | Switch controls for the CDAC bottom plates.                |
| `D[i]`               | Accumulated decision bits (post-`dac_bridge`).             |
| `dac_clk`            | Reconstruction clock (last instant of a conversion).       |

## Mixed-signal bridges

- `adc_bridge(in_low=0.2, in_high=0.8)` quantises the analog
  comparator outputs for the SAR FSM. The `in_low` / `in_high`
  thresholds are tuned to IHP's 1.2 V rails; GF180 at 3.3 V may need
  wider hysteresis if the comparator swing grows beyond the window.
- `dac_bridge(out_low=0.0, out_high=VDD)` drives the CDAC switch
  selectors back to analog. The switch models are `sw_cdac` (R_on=50 Ω)
  and `sw_samp` (R_on=100 Ω) with ideal hysteresis bands.

## What the netlist deliberately leaves out

- **Real bootstrap switch.** See `bootstrap-switch.md`; the ideal
  `sw_ideal` is a placeholder. Distortion numbers below -70 dBc are
  suspect until a real switch model lands.
- **LDO.** See `ldo.md`. The supply is an ideal `VVDD`.
- **ESD / IO.** No I/O devices in the deck; inputs come from
  `Vinp` / `Vinn` without series resistance.
- **DFT.** No scan chain around the SAR Verilog; the digital side is
  a flat FSM.

## Extending to GF180 or ihp-sg13cmos5l

`PdkConfig` already parametrises the PDK. The generator chooses
`pmos_symbol` / `nmos_symbol` / MIM cap model from the active PDK. In
practice the knobs needing attention when porting are:

- `VDD` — GF180 runs at 3.3 V. Comparator bias levels and `adc_bridge`
  thresholds may need a second review.
- `Lmin_m` — affects the NAND helper device sizes.
- `mim_cap_density_fF_um2` — shifts `cdac_C_unit_fF` to area-equivalent
  geometries; the agent-visible range stays in fF so the autoresearch
  workflow is unchanged.

The `ihp-sg13cmos5l` PDK becomes a drop-in target when its
`PdkConfig.register_pdk()` entry lands — nothing in the SAR flow
depends on `ihp_sg13g2` by name.
