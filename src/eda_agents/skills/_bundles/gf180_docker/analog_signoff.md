# GF180MCU analog signoff inside IIC-OSIC-TOOLS

This skill covers DRC and LVS for analog layouts on GF180MCU — OTAs,
comparators, biasgens, and similar cells produced by tools such as
`analog_composition_loop.py`, gLayout, or hand layout in Magic /
KLayout. It assumes the container is up per the common section.

## When to use which LVS toolchain

The container ships both KLayout LVS and Magic + Netgen LVS. They do
not behave identically on analog cells:

- **Magic + Netgen (recommended for signoff).** Correctly handles the
  GF180 ngspice wrapper (`.include sm141064.ngspice` with parameter
  expressions) via `gf180mcuD_setup.tcl` `equate classes`. This is
  the combo the wafer-space signoff flow uses.
- **KLayout LVS (useful for early checks, not for signoff).** Its
  device-extraction pipeline cannot parse ngspice `.include` with
  parameter expressions — it falsely reports mismatches on devices
  that Magic+Netgen reconciles cleanly. Use it as a quick sanity
  check but do not gate signoff on it.

KLayout DRC has no such issue — it reads GDS + rule decks and is the
standard choice for geometric DRC in this container.

**Default policy:** KLayout for DRC, Magic+Netgen for LVS.

## Inputs required on the bind mount

The analog block under test must ship:

| Artefact | Host path | Container path |
|----------|-----------|----------------|
| Layout GDS | `~/eda/designs/<block>/<block>.gds` | `/foss/designs/<block>/<block>.gds` |
| Schematic netlist (SPICE) | `~/eda/designs/<block>/<block>.spice` | `/foss/designs/<block>/<block>.spice` |

The netlist must declare the same top-level cell name as the GDS top
cell. If you came from gLayout or `analog_composition_loop.py`, the
SPICE file is auto-generated alongside the GDS.

## KLayout DRC

KLayout exposes the GF180 signoff runset inside the container; invoke
it in batch mode:

```bash
docker exec gf180 bash -lc '
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 &&
  cd /foss/designs/<block> &&
  klayout -b \
      -r $PDKPATH/libs.tech/klayout/drc/gf180mcu.drc \
      -rd input=<block>.gds \
      -rd report=<block>.drc.lyrdb \
      -rd topcell=<top>
'
```

Parse the `.lyrdb` (XML) for violation counts. The 0-violation
outcome is a single line in the XML declaring zero items. Any other
shape indicates real DRC to triage.

## Magic + Netgen LVS

Three commands, run in order:

```bash
# 1. Extract the layout to a .spice using Magic
docker exec gf180 bash -lc '
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 &&
  cd /foss/designs/<block> &&
  magic -dnull -noconsole -rcfile $PDKPATH/libs.tech/magic/gf180mcuD.magicrc \
      <<EOF
  gds read <block>.gds
  load <top>
  extract all
  ext2spice lvs
  ext2spice -o <block>.magic.spice
  quit -noprompt
EOF
'

# 2. Run Netgen LVS against the schematic netlist
docker exec gf180 bash -lc '
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 &&
  cd /foss/designs/<block> &&
  netgen -batch lvs \
      "<block>.magic.spice <top>" \
      "<block>.spice <top>" \
      $PDKPATH/libs.tech/netgen/gf180mcuD_setup.tcl \
      <block>.lvs.out
'
```

Netgen writes a human-readable report to `<block>.lvs.out`. A clean
run ends with a line `Circuits match uniquely`. Anything else — port
mismatches, device count mismatches, net topology divergence — needs
triage.

## Interpreting results — composition hint

- **DRC violations:** pull the summary and compose
  `render_skill(name="flow.drc_checker")` to categorize them by class
  (SHORT, SPACING, WIDTH, ENCLOSURE, ANTENNA, DENSITY, OFF_GRID).
  For patch suggestions compose
  `render_skill(name="flow.drc_fixer")` — it returns the canonical
  fix heuristics by category. These are the authoritative prompts;
  do not rewrite them in the subagent body.
- **LVS mismatches:** the common GF180 cases are (a) missing
  substrate tie to `VSS` on analog cells, (b) PMOS body tie not to
  `VDD`, (c) parasitic devices from extraction (Magic picks up
  unintended fingers — check the device count line in Netgen's
  report), (d) schematic / layout port order mismatch on
  differential pairs. The Netgen report tags each category
  explicitly; surface that tag plus the offending net / device.

## Acceptance test

For a block to be considered signoff-clean:

1. `klayout -b ... drc ...` produces a `.lyrdb` with zero items and
   the process exits 0.
2. Netgen's `.lvs.out` ends with `Circuits match uniquely`.
3. No warnings about parametric discrepancies (W, L) in the Netgen
   report — those can indicate unintended device resizing, not just
   nomenclature drift.

Anything short of that is a real defect — report the exact class and
the offending net / device, do not summarize it as "mostly clean".
