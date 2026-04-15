# IHP-Open-PDK: KLayout LVS deck errors on stdcell CDL ("Can't find a value for a R, C or L device")

**Target upstream:** https://github.com/IHP-GmbH/IHP-Open-PDK
**Status:** draft — DO NOT post without human review.
**Affected files:** `libs.tech/klayout/lvs/sg13g2.lvs` on IHP-Open-PDK
`dev` at HEAD.
**Affected flows:** LibreLane Classic flow on IHP SG13G2 when
`RUN_LVS: true` with `Netgen.LVS` substituted for `KLayout.LVS`
(our workaround for the Magic.SpiceExtraction hang; see
`ihp_magic_hang.md`).

## Summary

Running `KLayout.LVS` against the standard-cell CDL netlists on IHP
SG13G2 aborts with:

```
Can't find a value for a R, C or L device
```

during deck parsing. Error is reproducible for trivial designs
(single-stdcell instantiation) so the failure is in the deck itself,
not in any design-dependent layout geometry.

## Minimal repro

1. IHP-Open-PDK `dev` at HEAD, submodules initialised.
2. Generate a stdcell-only CDL with `OpenROAD.WriteCDL` (it is the
   LibreLane step that feeds the LVS input):

   ```
   cd <run_dir>
   openroad -exit -no_splash -no_init -python write_cdl.py
   ```
   where `write_cdl.py` exports the design.

3. Invoke the deck directly:

   ```
   klayout -b -r $PDK_ROOT/ihp-sg13g2/libs.tech/klayout/lvs/sg13g2.lvs \
       -rd source=counter_4bit.cdl \
       -rd layout=counter_4bit.gds \
       -rd schematic=counter_4bit.cdl
   ```

4. KLayout prints:

   ```
   ERROR: Can't find a value for a R, C or L device
   ```

   and exits with a non-zero status before any LVS comparison runs.

## Dependency chain that makes this a practical blocker

LVS on LibreLane Classic has two available tools:

- `Netgen.LVS`, which consumes the output of `Magic.SpiceExtraction`.
- `KLayout.LVS`, which consumes the output of `OpenROAD.WriteCDL`.

`Magic.SpiceExtraction` currently hangs the same way Magic.StreamOut
does (see `ihp_magic_hang.md`), which rules out Netgen. `KLayout.LVS`
is the only remaining option on HEAD — and it fails at deck parsing.

**Result:** IHP Classic LVS is effectively broken on `dev` HEAD until
either the Magic hang is fixed or the KLayout deck error is.

## Workaround (shipping in our project)

`RUN_LVS: false` in our template, documented as a known limitation:

```yaml
# IHP KLayout LVS deck currently errors on stdcell CDL parsing; our
# Classic template ships with LVS disabled until upstream resolves
# the issue.
RUN_LVS: false
```

This lets us deliver a clean signoff path (KLayout.DRC + OpenROAD
antennas/IR-drop) for pre-silicon exploration and teaching, at the
cost of no layout-vs-schematic verification.

## Suspected root cause

The error string "Can't find a value for a R, C or L device" comes
from KLayout's `NetlistReader` when it hits a SPICE subcircuit that
has an `R`/`C`/`L` primitive without a value field. It is most likely
that `sg13g2.lvs` parses `.subckt` lines from the stdcell CDL with a
specific width/length-convention assumption that no longer matches
what `OpenROAD.WriteCDL` emits on LibreLane v3. A reasonable suspect
is a change in the formatting of the OpenROAD CDL output (swapping
named vs positional args) combined with a rigid pattern in the
deck's `#{...}` macros.

## Proposed fix

Either:
1. Loosen the deck's parser for R/C/L primitives so a missing value
   doesn't abort (upstream recommended: match on `W=` / `L=` rather
   than positional).
2. Document the incompatibility and pin a working LibreLane revision.

We are happy to land a minimal PR once the deck maintainer confirms
the intended pattern — but without that confirmation we cannot
distinguish "deck parser too strict" from "OpenROAD CDL output wrong".
