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
  accepts a decision only when `Op XOR Om` is asserted (i.e., the
  comparator has resolved).
- `sar_logic_11bit.v` (design reference, **new for S7**, written from
  scratch). Same inputs; outputs grow to `B[9:0]`, `BN[9:0]`,
  `D[10:0]`. Ten resolution cycles instead of seven.

Both modules read comparator decisions on `posedge clk` where `clk` is
`clk_comp` routed through an `adc_bridge`. The SAR expects `clk_comp`
to rise at the end of the evaluate phase, so the comparator outputs
are already stable when the decision lands.

## Cycle count and period budget

The 11-bit flow runs at 1 MHz with `T_algo = 1/22 * f_s` and
`T_algo_PW = 1/44 * f_s`. That gives ten 45 ns evaluate windows inside
each 1 µs period. `check_system_validity` enforces a metastability
bound: a crude `tau_regen ~ 20 ps / (W_latch_p/8)` heuristic must fit
within ~40 % of `T_algo_PW`. Agents that push `W_latch_p` below 2 µm
should expect a FAIL on the metastability gate.

## Output layout and ENOB extraction

`wrdata bit_data.txt D0 D1 ... Dn vin_diff dac_clk` is the canonical
trace for ENOB extraction. Columns alternate `(time, value)` per
variable, which is why `extract_enob` reads column 1 onwards for the
bits and the last two columns for `vin_diff` / `dac_clk`.

The code reconstruction follows the natural MSB-first accumulation
built by the Verilog: `D[counter]` is set to `Op` on iteration
`counter`, so reading column index `1 + i` gives the bit weight `2^i`.
The 8-bit flow currently halts with `D[7]` kept 0 (the FSM only writes
7 decision bits), and the 11-bit flow mirrors that convention at
`D[10]`; both `extract_enob` implementations sum `bits[i] * (1 << i)`
up to the declared bit width.

## Replacing the module

If you need a different SAR policy (e.g., non-binary search, merged
capacitor switching, asynchronous logic), write a new `.v` and pass it
through the `verilog_src=` parameter of the topology constructor. The
port order must match the `Adut` instance line in the netlist
generator — if you diverge, mirror the change in both Verilog and
netlist together or the FSM will desync silently.
