# IHP-Open-PDK: Magic.StreamOut / Magic.WriteLEF / Magic.SpiceExtraction hang indefinitely

**Target upstream:** https://github.com/IHP-GmbH/IHP-Open-PDK
**Status:** draft — DO NOT post without human review.
**Affected branches:** IHP-Open-PDK `dev` at HEAD (685 commits past the
LibreLane-qualified revision `cb7daaa8`).
**Affected flows:** LibreLane Classic flow on IHP SG13G2 via OpenLane
v3 / LibreLane v3.

## Summary

Any LibreLane run on IHP SG13G2 that reaches a `Magic.*` step hangs
indefinitely at ~100 % single-core CPU without producing output. The
affected steps we have observed hanging are:

- `Magic.StreamOut`
- `Magic.WriteLEF`
- `Magic.SpiceExtraction` (which in turn blocks `Netgen.LVS`)

The hang is not a LibreLane regression — the same Magic process, with
the same `.magicrc` / `.tech` from the current `dev` branch, exhibits
the behaviour when invoked directly. The LibreLane subprocess harness
eventually kills the step after its step-internal timeout (~20 min)
but the underlying `magic` process is still pegged at 100 % CPU at
that point.

## Minimal repro

1. Clone IHP-Open-PDK `dev` at HEAD, checkout the `dev` branch.
2. Set `PDK_ROOT=$PWD/IHP-Open-PDK` and install LibreLane v3 in any
   Python venv.
3. Write a trivial design, for instance a 4-bit synchronous counter
   with async reset and enable:

   ```verilog
   module counter_4bit(clk, rst_n, en, count);
       input clk, rst_n, en;
       output reg [3:0] count;
       always @(posedge clk or negedge rst_n)
           if (!rst_n) count <= 4'b0;
           else if (en) count <= count + 1;
   endmodule
   ```

4. Standard LibreLane Classic config (`meta.flow: Classic`,
   `meta.version: 3`, `DESIGN_NAME: counter_4bit`, 10 ns period).
5. Run:

   ```bash
   PDK=ihp-sg13g2 PDK_ROOT=/path/to/IHP-Open-PDK python3 -m librelane \
       config.yaml --overwrite --manual-pdk
   ```

6. The flow completes through `openroad-stapostpnr`,
   `openroad-rcx`, `openroad-irdropreport` successfully, then hangs at
   `magic-streamout` with the LEF loader consuming 100 % CPU.
   Per-step wall-clock limit expires at ~20 min; LibreLane reports:

   ```
   Magic.StreamOut: step exceeded internal timeout (timed out after X seconds)
   ```

   but the `magic` PID is still pegged at 100 % CPU until killed.

## Bisect window

Last known-good: `cb7daaa8` (the LibreLane-qualified revision we used
before the `dev` bump).
First known-bad: `HEAD` (685 commits later).
Tech file touched heavily in this range: `libs.tech/magic/ihp-sg13g2.tech`.

Recommended bisect focus: changes to the `cifinput`, `extract`, and
`cifoutput` sections of `ihp-sg13g2.tech`, particularly any additions
of new layers that would make Magic traverse an expanded cell tree
during streamout.

## Workaround (shipping in our project)

We substitute every Magic step in LibreLane's flow with its KLayout
equivalent plus `OpenROAD.WriteCDL` upstream of LVS, via a template
fragment:

```yaml
meta:
  substituting_steps:
    Magic.StreamOut: null
    Magic.WriteLEF: null
    Magic.SpiceExtraction: null
    Magic.DRC: null
    Checker.MagicDRC: null
    Checker.IllegalOverlap: null
    Odb.CheckDesignAntennaProperties: null
    Netgen.LVS: null
    Checker.LVS: null

RUN_MAGIC_STREAMOUT: false
RUN_MAGIC_WRITE_LEF: false
RUN_MAGIC_DRC: false
RUN_KLAYOUT_XOR: false
RUN_LVS: false  # orthogonal bug, see klayout_lvs_deck.md
PRIMARY_GDSII_STREAMOUT_TOOL: klayout
```

With this patch, a 4-bit counter runs from RTL to signed-off GDS in
~36 s, passes KLayout DRC cleanly, and hits +18.5 ns WNS at the slow
corner.

## Proposed fix

Either:
1. Identify and revert the `ihp-sg13g2.tech` change responsible for
   the hang (bisect preferred).
2. Document the current `dev` HEAD as incompatible with Magic-backed
   LibreLane flows and recommend `PRIMARY_GDSII_STREAMOUT_TOOL: klayout`
   upstream in `ihp-sg13g2-librelane-template`.

Option 2 matches what the PDK team's own template already does on
`dev`, so some awareness of the regression appears to exist. Making
the workaround explicit in documentation (plus pinning a
LibreLane-qualified revision tag) would save downstream users the
two-day debug we hit.
