# SAR ADC naming convention — cleanup backlog

The "8-bit" label on `SARADCTopology` and
`SARADC8BitBehavioralTopology` is **misleading**. Both topologies
inherit the AnalogAcademy convention where the bit count refers to
the D output bus width, not the converter's effective resolution.

| Topology                       | Bit count in name | Effective resolution | Why |
|--------------------------------|-------------------|----------------------|-----|
| `SARADCTopology` (8b, transistor) | 8                 | 7                    | FSM iterates 7 times (`counter < 7`); CDAC dummy shares LSB switch (`B6`), so B6 controls 2 unit caps. Net: 7 distinct binary weights. |
| `SARADC8BitBehavioralTopology`    | 8                 | 7                    | Reuses the AA CDAC + FSM verbatim, only swaps the comparator. Same 7-effective-bit ceiling. |
| `SARADC11BitTopology`             | 11                | 11                   | Designed from scratch in S7 with the dummy tied permanently to vcm and 11 distinct B switches. True 11-bit. |

## Why this is preserved (for now)

The AA-derived 8-bit topology is the **only silicon-traceable** SAR
in-tree. Renaming the class, the file, and the AA-shipped Verilog
module would create churn across:

- `topologies/sar_adc_8bit.py` (class, module path)
- `topologies/sar_adc_8bit_behavioral.py` (class, module path)
- `topologies/sar_adc_netlist.py` (helper functions named after 8-bit)
- `agents/system_handler.py` (isinstance check on `SARADCTopology`)
- `examples/13b_sar_adc_8bit_behavioral.py`
- `tests/test_sar_adc_8bit_behavioral.py`
- Every doc / handoff / commit message that references "8-bit SAR"
- The upstream AnalogAcademy Verilog (which we depend on by path)

It also breaks the easy mental link from "AA Module 3 — 8-bit SAR
ADC" to the topology in this repo. Until S9 (benchmark suite) has
landed — at which point we will be running a lot more SAR variants
side-by-side — the AA-named class stays put.

## What a clean rename would look like

When we do this, the right move is probably:

1. Add `SAR7BitTopology` and `SAR7BitBehavioralTopology` as the
   canonical names; keep `SARADCTopology` /
   `SARADC8BitBehavioralTopology` as deprecated aliases that emit
   `DeprecationWarning` on first use, pointing at the new names.
2. Rename `topologies/sar_adc_8bit.py` -> `topologies/sar_adc_7bit.py`
   with a `sar_adc_8bit.py` shim re-exporting the alias.
3. Update `examples/13b_*` and `tests/test_*8bit_behavioral*`
   filenames + test class names; keep the alias-based imports in the
   examples for one release.
4. Drop the aliases the release after.

Alternative: leave the misleading names alone forever and rely on
this doc + the docstring callouts to make the convention loud. Pick
one when S9 reshuffles SAR work.

## Until then

- The `SARADCTopology` class docstring opens with the
  "*effectively 7-bit*" warning. Same for the behavioural variant.
- `docs/skills/sar_adc/core-architecture.md` table row spells out
  "Effectively 7-bit" for both 8-bit topologies and "True 11-bit"
  for `SARADC11BitTopology`.
- `docs/skills/sar_adc/sar-logic.md` records the per-topology
  iteration count and bit-weighting formula explicitly so anyone
  writing a new SAR variant knows which convention to follow.
- The upstream `sar_logic.v` file is not modified (it is third-party
  code under AA's licence terms); the convention difference is
  documented from the eda-agents side only.
