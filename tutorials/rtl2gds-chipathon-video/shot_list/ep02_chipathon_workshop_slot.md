# Ep 02 shot-list -- Use the chipathon workshop slot (target 25-30 min)

The chipathon-2026 entry path. The viewer's own RTL drops into the pre-built workshop padring slot (2935 x 2935 um, 60 analog + 20 bidir + 4 power + 4 ground + clock + reset, the slot mirror of JuanMoya's reference padring) and the full Chip flow signs it off. Wall-clock is ~35-45 min, so this episode is half live, half pre-recorded.

This episode merges the old "Slots explained" (read-only walk) and "Use the chipathon workshop slot" (run the flow). The slot concept gets ONE slide of explanation up front; everything else is the flow.

## Hook narrative

> *"A 'slot' is the chipathon-2026 die contract — three files (chip_core.sv, slot_defines.svh, slot_workshop.yaml) that wrap the padring around your logic. One slide of explanation, then we drop our RTL in and watch the flow close all four signoff metrics in one run."*

The viewer learns the workshop padring abstraction in 60 seconds, sees a chipathon-style design close all four signoff metrics in one run.

## Bootstrap (run once, off-camera, before opening panes)

```bash
export GF180=$(docker ps -q --filter ancestor=hpretl/iic-osic-tools:chipathon26 | head -1)
test -n "$GF180" || { echo "No :chipathon26 container running — bootstrap one first"; }
```

Each of the three recording panes opens with **one** command — entering the container interactively:

```bash
docker exec -it "$GF180" bash -l
```

The chipathon padring template is staged in the bind-mount as `/foss/designs/chipathon_padring/template/` (cloned once from upstream `sscs-chipathon-2026/resources/Integration/workshop_padring_librelane/`; see README pre-flight). Pre-recorded artifacts live at `/foss/designs/prerecorded/ep02_*` (these get harvested from the same flow run that the old Ep 03 used to record — file basenames bumped to `ep02_*`).

## Pane layout (3-pane, all inside container, half live half pre-recorded)

| Pane | Contents (after `docker exec -it` at start) |
|---|---|
| Pane 1 (left) | `cat`/`less` over the staged template files: `slot_workshop.yaml`, `chip_core.sv`, `config.yaml`; later `awk` over `prerecorded/ep02_metrics.csv` |
| Pane 2 (centre) | `source sak-pdk-script.sh ...`, then `make librelane SLOT=workshop PDK_ROOT=./gf180mcu` |
| Pane 3 (right) | `tail -f flow.log` live for the first ~5 min, then pre-recorded log; later `klayout -n gf180mcu -e <gds> &` |

## Episode timeline

| Time | Section | Pane | Action |
|---|---|---|---|
| 00:00 | Pre-roll: `mini_decks/ep02_intro.html` | -- | 4 slides (cover + Slot YAML / die contract + Two-level YAML + Signoff overview) |
| 02:30 | Title card: "what a slot is, in 60 seconds" | -- | text overlay |
| 03:00 | Walk into the staged template | Pane 1 | `cd /foss/designs/chipathon_padring/template && ls -R src/ librelane/` |
| 04:00 | The slot YAML (the entire concept in one file) | Pane 1 | `cat librelane/slots/slot_workshop.yaml` -- die area 2935x2935, four PAD_* arrays clockwise |
| 06:00 | The placeholder `chip_core.sv` (today this is your design) | Pane 1 | `cat src/chip_core.sv` -- minimal stub; in a real entry you replace it with your logic |
| 07:30 | The chip-top `config.yaml` -- empty `MACROS:` for a logic-only design | Pane 1 | `cat librelane/config.yaml` |
| 08:30 | Verify the wafer-space PDK fork (one-line check) | Pane 2 | `cd /foss/designs/chipathon_padring/template/gf180mcu && git describe --tags && cd -` |
| 09:00 | Activate PDK + run the flow | Pane 2 | `source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 && make librelane SLOT=workshop PDK_ROOT=./gf180mcu` |
| 09:00 | Live log tail -- first ~5 min (Yosys + Floorplan + PDN visible) | Pane 3 | `tail -f $(ls -td librelane/runs/*/ \| head -1)flow.log` |
| 14:00 | **Cut to time-lapse** -- routing iterations through Magic DRC | -- | 4x speed of pre-recorded log; voice-over the routing iterations |
| 18:00 | **Cut to pre-recorded artifacts** -- final banner | -- | screen-record from `/foss/designs/prerecorded/ep02_chipathon_use.log` |
| 19:00 | Read the pre-recorded `metrics.csv` -- all-zero signoff | Pane 1 | `awk -F, '$1 ~ /error__count\|violation__count\|drc_error\|antenna__viol\|lvs_.*__count/' /foss/designs/prerecorded/ep02_metrics.csv` |
| 22:00 | Open the chip-top GDS in KLayout | Pane 3 | `klayout -n gf180mcu -e /foss/designs/prerecorded/ep02_chipathon_use.gds &` |
| 24:00 | Zoom -- padring perimeter, core area, std cells | Pane 3 | KLayout viewport navigation |
| 26:00 | Tease Ep 03 -- "now real macros instead of a placeholder" | -- | -- |
| 27:00 | Out | -- | -- |

## Cuts / time-lapses

- **14:00 -> 18:00**: time-lapse the routing + Magic DRC stages (the long ones). Voice-over.
- **18:00 -> end**: pre-recorded artifacts only. Live machine does not need to finish the flow on camera.
- Magic DRC on this design is ~10-15 min; KLayout DRC on chip-top can be 30-60 min — both off-camera.

## Pre-recorded artifacts (required)

| File (in bind-mount) | What it is | Size approx |
|---|---|---|
| `~/eda/designs/prerecorded/ep02_chipathon_use.log` | Full LibreLane `flow.log` from a clean ~35-45 min run | ~3 MB |
| `~/eda/designs/prerecorded/ep02_chipathon_use.gds` | Final chip-top GDS | ~80 MB |
| `~/eda/designs/prerecorded/ep02_metrics.csv` | `runs/<ts>/final/metrics.csv`, all error_count = 0 | ~16 kB |

Generate before recording (one-time, off-camera):

```bash
docker exec -it "$GF180" bash -l
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
cd /foss/designs/chipathon_padring/template
make librelane SLOT=workshop PDK_ROOT=./gf180mcu             # ~35-45 min
LATEST=$(ls -td librelane/runs/*/ | head -1)
cp "$LATEST/flow.log"                /foss/designs/prerecorded/ep02_chipathon_use.log
cp "$LATEST/final/gds/chip_top.gds"  /foss/designs/prerecorded/ep02_chipathon_use.gds
cp "$LATEST/final/metrics.csv"       /foss/designs/prerecorded/ep02_metrics.csv
```

## Exact commands

Each pane opens with the bootstrap snippet above (off-camera) and then `docker exec -it "$GF180" bash -l` to enter the container.

**Each pane, 02:30 (entry into container)**:
```bash
docker exec -it "$GF180" bash -l
```

**Pane 1, 03:00 (cwd + tree)**:
```bash
cd /foss/designs/chipathon_padring/template
ls -R src/ librelane/
```

**Pane 1, 04:00 / 06:00 / 07:30 (the three template files)**:
```bash
cat librelane/slots/slot_workshop.yaml
cat src/chip_core.sv
cat librelane/config.yaml
```

**Pane 2, 08:30 (verify PDK fork tag)**:
```bash
cd /foss/designs/chipathon_padring/template/gf180mcu && git describe --tags && cd -
```
The chipathon padring template ships a `Makefile` that pulls a local clone of the wafer-space PDK fork (`https://github.com/wafer-space/gf180mcu`, tag 1.8.0) into `<template>/gf180mcu/` via `make clone-pdk`. That fork is required because chip_top.sv instantiates `gf180mcu_ws_io__dvdd/dvss` cells that the system PDK at `/foss/pdks/gf180mcuD` does not carry.

**Pane 2, 09:00 (run the flow)**:
```bash
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
cd /foss/designs/chipathon_padring/template
make librelane SLOT=workshop PDK_ROOT=./gf180mcu
```
`SLOT=workshop` selects `librelane/slots/slot_workshop.yaml` (the chipathon shuttle slot — without it the Makefile defaults to `1x1`, a different padring). `PDK_ROOT=./gf180mcu` points at the local wafer-space PDK clone so the `gf180mcu_ws_io__*` IO cells in `src/chip_top.sv` resolve.

**Pane 3, 09:00 (live tail, ~5 s after pane 2 begins)**:
```bash
cd /foss/designs/chipathon_padring/template
tail -f $(ls -td librelane/runs/*/ | head -1)flow.log
```

**Pane 1, 19:00 (signoff sanity check on pre-recorded)**:
```bash
awk -F, '$1 ~ /error__count|violation__count|drc_error|antenna__viol|lvs_.*__count/' \
  /foss/designs/prerecorded/ep02_metrics.csv
```

**Pane 3, 22:00 (open the chip-top GDS, backgrounded)**:
```bash
klayout -n gf180mcu -e /foss/designs/prerecorded/ep02_chipathon_use.gds &
```

## Honest scope (lines on camera)

- Min 02:30: *"A slot is three files: a chip_core.sv stub, a slot_defines.svh with pad-category counts, and a slot_workshop.yaml with the die area plus the four PAD_* clockwise arrays. That's it. Today we'll see slot_workshop.yaml — the chipathon-2026 die contract — and the placeholder chip_core.sv. In a real entry you swap chip_core.sv for your logic."*
- Min 04:00: *"Die 2935 by 2935 microns. Sixty analog plus twenty bidir plus power and ground and clock and reset. Pads listed clockwise from south-west — that's the LibreLane convention, miss the order and the padring does not close."*
- Min 09:00: *"Forty-five minutes is real wall-clock. We watch the first five minutes live -- Yosys, Floorplan, PDN -- and then we time-lapse the long routing + Magic DRC pass. The pre-recorded log is from yesterday's clean run."*
- Min 19:00: *"All four signoff metrics zero on the first attempt. That is what 'use the workshop slot' buys you -- the padring, the PDK fork, the slot YAML are all proven. The only thing that can break is your own logic, which is the part you should be debugging anyway."*
- Min 26:00: *"Today the chip_core.sv was a placeholder. Episode 3 puts two real macros inside — a counter and a 4-bit ALU — using the same padring. The integration pattern."*

## Pre-recorded fallback

If the pre-recorded artifacts are missing on recording day, the episode degrades to a hand-walked version: quote the metrics from the upstream `README.md`'s "validated, all signoff = 0" line, show the layout PNG from the chipathon-2026 fork's repository, explain the cut as "the recording machine does not have a clean run staged today, but the upstream repository documents one."
