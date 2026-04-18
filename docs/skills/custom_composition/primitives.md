## gLayout primitives inventory

All primitives below are **generatable and DRC/LVS-consistent on both
IHP SG13G2 and GF180 today** (except where noted). They are the
atoms you can compose without additional fork work.

### Primitives (elementary)

- **nmos** (`scripts/glayout_driver.py::_generate_nmos_like`)
  - Params: `width` (µm, 0.28–10), `length` (µm, 0.13–2), `fingers` (1–16).
  - Use: any NMOS transistor.
- **pmos** (same signature as nmos)
  - Use: any PMOS transistor.
- **mimcap** (`scripts/glayout_driver.py::_generate_mimcap`)
  - Params: `width` (µm), `length` (µm). Area proxy; no rows/cols at
    primitive level.
  - **SG13G2 caveat**: LVS extraction is currently broken on SG13G2
    (met4-mim-met5 vs IHP's met5-mim-topmetal1). DRC still clean.
    Use pmos/nmos-gate caps as a workaround, or a resistor divider +
    switched-cap circuit that doesn't require cap_cmim extraction.

### Composites

- **diff_pair** (`_generate_diff_pair`)
  - Params: `width`, `length`, `fingers`. ABBA common-centroid.
  - SG13G2-native via the fork's `_diff_pair_netlist_sg13g2` branch.
  - Ports: `in+`, `in-`, `out+`, `out-`, `tail`, `bulk`.

- **current_mirror** (`_generate_current_mirror`)
  - Params: `width`, `length`, `fingers`, `multipliers`, `type`
    (`nfet`|`pfet`). Interdigitized; ref + copy.
  - SG13G2-native; flat-extraction netlist scales with
    `fingers*multipliers`.
  - Ports: `VREF`, `VCOPY`, `VSS`, `VB`.

- **fvf** (`_generate_fvf`)
  - Params: `width`, `length`, `fingers`. Flipped voltage follower.
  - SG13G2-native via the fork's FVF branches.
  - Use when you need a low-impedance source follower stage.

### What's NOT yet SG13G2-ready (treat as unavailable)

- `opamp_twostage` — S12-B Gap 4 partial landing; DRC + LVS both open
  (MIM cap mapping + netlist mismatch). Do **not** consume this
  composite in SG13G2 compositions until the follow-up PR lands.
- Any BJT-based composite (rsil, bandgap, LDO) — the BJT primitives
  exist but there are no SG13G2-ready composite wrappers yet.
- Any XSPICE-only behavioural element — those live in eda-agents's
  XSPICE kit, not gLayout. The library module's `run_spice()` can
  load them via `extra_codemodel`, but they don't produce GDS.

## Connection vocabulary

For the composition graph's `connectivity` field, use dotted port names:

- `<sub_block>.<port>` — e.g. `diffpair_1.out+`, `cm_load.VCOPY`.
- Standard global nets: `VDD`, `VSS` (alias `GND`), `VIN`, `VOUT`,
  `IBIAS` (single-ended bias current input), `VCM` (common-mode
  reference).

## Placement hint

The library module's `generate_layout()` step places sub-blocks on a
**pitched grid**: one row per sub-block type, with sub-blocks ordered
left-to-right in the `composition` array. Wires run straight in met2
or c-route in met3. Don't plan for analog-specific matched layout
(common-centroid, axis-of-symmetry); those matter for performance but
not for a first-pass synthesis loop.
