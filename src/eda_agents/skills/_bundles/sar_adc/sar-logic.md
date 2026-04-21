# SAR finite-state machine

The digital SAR logic is implemented in Verilog and compiled to a
shared library by `eda_agents.utils.vlnggen.compile_verilog`, which
drives the `vlnggen` script shipped with ngspice >= 44. The `.so` is
then loaded by the XSPICE `d_cosim` code model so it runs inside the
same ngspice process as the analog deck.

## Port conventions

Two modules ship in-tree:

- `sar_logic.v` (8 bit, from IHP-AnalogAcademy). Inputs: `clk`, `Op`,
  `En`, `Om`, `rst`. Outputs: `B[6:0]`, `BN[6:0]`, `D[7:0]`. The FSM
  iterates 7 times (counter < 7) — "8-bit" refers to the D bus
  width, the *effective* resolution is 7 bits because the LSB cap
  shares its switch with the dummy in the CDAC array.
- `sar_logic_11bit.v` (design reference, **new for S7**, written from
  scratch). Same inputs; outputs grow to `B[10:0]`, `BN[10:0]`,
  `D[10:0]`. Eleven resolution cycles (counter < 11) — *true*
  11-bit, no LSB doubling. See `core-architecture.md` § "A note on
  the dummy cap" for why the 11-bit topology breaks with the AA
  legacy convention.

Both modules read comparator decisions on `posedge clk` where `clk` is
`clk_comp` routed through an `adc_bridge`. The SAR expects `clk_comp`
to rise at the end of the evaluate phase, so the comparator outputs
are already stable when the decision lands.

## Cycle count and period budget

The 11-bit flow runs at 1 MHz with `T_algo = T / 24` and
`T_algo_PW = T / 48`. That gives eleven 20.8 ns evaluate windows
inside each 1 µs period (sample/hold + 11 resolution + slack).
`check_system_validity` enforces a metastability bound: a crude
`tau_regen ~ 20 ps / (W_latch_p/8)` heuristic must fit within ~40 %
of `T_algo_PW`. Agents that push `W_latch_p` below 2 µm should
expect a FAIL on the metastability gate.

## Output layout and ENOB extraction

`wrdata bit_data.txt D0 D1 ... Dn vin_diff dac_clk` is the canonical
trace for ENOB extraction. Columns alternate `(time, value)` per
variable, which is why `extract_enob` reads column 1 onwards for the
bits and the last two columns for `vin_diff` / `dac_clk`.

The code reconstruction follows the **MSB-first accumulation** built
by both Verilogs: `D[counter]` is set to `Op` on iteration `counter`,
and counter starts at 0. So D[0] holds the **first** decision (the
MSB) and the last bit lands at D[N-1].

- 8-bit `extract_enob`: weights `d_bits[i] * (64 >> i)` for `i` in
  `range(7)` — D[0] = MSB (weight 64), D[6] = LSB (weight 1), D[7]
  unused. The "8-bit" name is the bus width; the resolution is 7.
- 11-bit `extract_enob`: weights `bits[i] * (1 << (10 - i))` for `i`
  in `range(11)` — D[0] = MSB (weight 1024), D[10] = LSB (weight 1).
  All 11 bits carry distinct weights; this is a true 11-bit decode.

The unit test `test_extract_enob_bit_weighting` writes a synthetic
`bit_data.txt` with D[0] = 1 and the rest 0, then asserts the decoded
code is exactly 2^10 — pinning the MSB-first convention against
silent regression.

## Replacing the module

If you need a different SAR policy (e.g., non-binary search, merged
capacitor switching, asynchronous logic), write a new `.v` and pass it
through the `verilog_src=` parameter of the topology constructor. The
port order must match the `Adut` instance line in the netlist
generator — if you diverge, mirror the change in both Verilog and
netlist together or the FSM will desync silently.
