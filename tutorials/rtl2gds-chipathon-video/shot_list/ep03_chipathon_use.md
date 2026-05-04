# Ep 03 shot-list -- Use the chipathon workshop slot (target 25-30 min)

The chipathon-2026 entry path. The viewer's own RTL drops into the pre-built workshop padring slot (2935 x 2935 um, 60 analog + 20 bidir + 4 power + 4 ground + clock + reset, the slot mirror of JuanMoya's reference padring) and the full Chip flow signs it off. Wall-clock is ~35-45 min, so this episode is half live, half pre-recorded.

## Hook narrative

> *"This is the notebook you live in during the chipathon. The padring is done. The slot is done. Your job is to drop your `chip_core.sv` into one file, declare your macros (if any) in another, and run the flow. Forty-five minutes later you have a tape-out-ready GDS."*

The viewer learns the workshop padring abstraction and sees a chipathon-style design close all four signoff metrics in one run.

## Bootstrap (run once, off-camera, before opening panes)

```bash
export GF180=$(docker ps -q --filter ancestor=hpretl/iic-osic-tools:chipathon26 | head -1)
test -n "$GF180" || { echo "No :chipathon26 container running — bootstrap one first"; }
```

Each of the three recording panes opens with **one** command — entering the container interactively:

```bash
docker exec -it "$GF180" bash -l
```

The chipathon padring template is staged in the bind-mount as `/foss/designs/chipathon_padring/template/` (cloned once from upstream `sscs-chipathon-2026/resources/Integration/workshop_padring_librelane/`; see README pre-flight). Pre-recorded artifacts live at `/foss/designs/prerecorded/ep03_*`.

## Pane layout (3-pane, all inside container, half live half pre-recorded)

| Pane | Contents (after `docker exec -it` at start) |
|---|---|
| Pane 1 (left) | `cat`/`less` over the staged template files: `slot_workshop.yaml`, `chip_core.sv`, `config.yaml`; later `awk` over `prerecorded/ep03_metrics.csv` |
| Pane 2 (centre) | `source sak-pdk-script.sh ...`, then `make librelane` |
| Pane 3 (right) | `tail -f flow.log` live for the first ~5 min, then pre-recorded log; later `klayout -n gf180mcu -e <gds> &` |

## Episode timeline

| Time | Section | Pane | Action |
|---|---|---|---|
| 00:00 | Pre-roll: `mini_decks/ep03_intro.html` | -- | 5 slides (cover + Two-level YAML + Slot YAML + Multi-macro + Signoff overview) |
| 03:00 | Walk into the staged template | Pane 1 | `cd /foss/designs/chipathon_padring/template && ls -R src/ librelane/` |
| 04:30 | Verify the wafer-space PDK fork is at tag 1.8.0 (local clone in the template, populated once by `make clone-pdk`) | Pane 2 | `cd /foss/designs/chipathon_padring/template/gf180mcu && git describe --tags && cd -` |
| 06:30 | Show the slot YAML -- 2935 x 2935 um, padring contract | Pane 1 | `cat librelane/slots/slot_workshop.yaml` |
| 09:00 | Author your `chip_core.sv` -- replace placeholder logic with viewer's RTL | Pane 1 | `cat src/chip_core.sv` (if showing diff vs template, use `diff -u` against a backup) |
| 11:00 | Show the chip-top `config.yaml` -- empty `MACROS:` for a logic-only design | Pane 1 | `cat librelane/config.yaml` |
| 13:00 | Activate PDK + run the flow (workshop slot, override PDK_ROOT to local wafer-space clone) | Pane 2 | `source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 && make librelane SLOT=workshop PDK_ROOT=./gf180mcu` |
| 13:00 | Live log tail -- first ~5 min (Yosys + Floorplan + PDN visible) | Pane 3 | `tail -f $(ls -td librelane/runs/*/ \| head -1)flow.log` |
| 18:00 | **Cut to time-lapse** -- routing iterations through Magic DRC | -- | 4x speed of pre-recorded log; voice-over the routing iterations |
| 21:00 | **Cut to pre-recorded artifacts** -- final banner | -- | screen-record from `/foss/designs/prerecorded/ep03_chipathon_use.log` |
| 22:00 | Read the pre-recorded `metrics.csv` -- all-zero signoff | Pane 1 | `awk -F, '$1 ~ /error__count\|violation__count\|drc_error\|antenna__viol\|lvs_.*__count/' /foss/designs/prerecorded/ep03_metrics.csv` |
| 24:00 | Open the chip-top GDS in KLayout (assumes `~/eda/designs/prerecorded/` exists with the artifact in place; see README pre-flight) | Pane 3 | `klayout -n gf180mcu -e /foss/designs/prerecorded/ep03_chipathon_use.gds &` |
| 26:00 | Zoom -- padring perimeter, core area, std cells | Pane 3 | KLayout viewport navigation |
| 27:30 | Tease Ep 04 -- "now two macros instead of zero" | -- | -- |
| 28:00 | Out | -- | -- |

## Cuts / time-lapses

- **18:00 -> 21:00**: time-lapse the routing + Magic DRC stages (the long ones). Voice-over.
- **21:00 -> end**: pre-recorded artifacts only. Live machine does not need to finish the flow on camera.
- Magic DRC on this design is ~10-15 min, the longest single step.

## Pre-recorded artifacts (required)

| File (in bind-mount) | What it is | Size approx |
|---|---|---|
| `~/eda/designs/prerecorded/ep03_chipathon_use.log` | Full LibreLane `flow.log` from a clean ~35-45 min run | ~3 MB |
| `~/eda/designs/prerecorded/ep03_chipathon_use.gds` | Final chip-top GDS | ~30 MB |
| `~/eda/designs/prerecorded/ep03_metrics.csv` | `runs/<ts>/final/metrics.csv`, all error_count = 0 | ~10 kB |
| `~/eda/designs/prerecorded/ep03_layout_render.png` | KLayout PNG export | ~2 MB |

Generate before recording (one-time, off-camera):

```bash
docker exec -it "$GF180" bash -l
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
cd /foss/designs/chipathon_padring/template
make librelane                                 # ~35-45 min
LATEST=$(ls -td librelane/runs/*/ | head -1)
cp "$LATEST/flow.log"                /foss/designs/prerecorded/ep03_chipathon_use.log
cp "$LATEST/final/gds/chip_top.gds"  /foss/designs/prerecorded/ep03_chipathon_use.gds
cp "$LATEST/final/metrics.csv"       /foss/designs/prerecorded/ep03_metrics.csv
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

**Pane 2, 04:30 (verify PDK fork tag)**:
```bash
cd /foss/designs/chipathon_padring/template/gf180mcu && git describe --tags && cd -
```
The chipathon padring template ships a `Makefile` that pulls a local clone of the wafer-space PDK fork (`https://github.com/wafer-space/gf180mcu`, tag 1.8.0) into `<template>/gf180mcu/` via `make clone-pdk`. That fork is required because chip_top.sv instantiates `gf180mcu_ws_io__dvdd/dvss` cells that the system PDK at `/foss/pdks/gf180mcuD` does not carry. The flow at 13:00 below overrides `PDK_ROOT=./gf180mcu` on the make line for the same reason — without that override, LibreLane would resolve `--pdk-root` to `/foss/pdks` and fail at IO-cell elaboration.

**Pane 1, 06:30 / 09:00 / 11:00 (the three template files)**:
```bash
cat librelane/slots/slot_workshop.yaml
cat src/chip_core.sv
cat librelane/config.yaml
```

**Pane 2, 13:00 (run the flow)**:
```bash
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
cd /foss/designs/chipathon_padring/template
make librelane SLOT=workshop PDK_ROOT=./gf180mcu
```
`SLOT=workshop` selects `librelane/slots/slot_workshop.yaml` (the chipathon shuttle slot — without it the Makefile defaults to `1x1`, a different padring). `PDK_ROOT=./gf180mcu` points at the local wafer-space PDK clone so the `gf180mcu_ws_io__*` IO cells in `src/chip_top.sv` resolve.

**Pane 3, 13:00 (live tail, ~5 s after pane 2 begins)**:
```bash
cd /foss/designs/chipathon_padring/template
tail -f $(ls -td librelane/runs/*/ | head -1)flow.log
```

**Pane 1, 22:00 (signoff sanity check on pre-recorded)**:
```bash
awk -F, '$1 ~ /error__count|violation__count|drc_error|antenna__viol|lvs_.*__count/' \
  /foss/designs/prerecorded/ep03_metrics.csv
```

**Pane 3, 24:00 (open the chip-top GDS, backgrounded)**:
```bash
klayout -n gf180mcu -e /foss/designs/prerecorded/ep03_chipathon_use.gds &
```

## Honest scope (lines on camera)

- Min 03:00: *"The workshop padring is vendored in the chipathon repo. You do not build it; you `cp -r` it into your bind-mount and add your `chip_core.sv`. Episode 02 explained the three files; this is the episode where you actually edit one."*
- Min 06:00: *"All three panes are inside the same container -- same image as Eps 01-02. No `docker exec` wrappers in any command. We are inside."*
- Min 13:00: *"Forty-five minutes is real wall-clock. We watch the first five minutes live -- Yosys, Floorplan, PDN -- and then we time-lapse the long routing + Magic DRC pass. The pre-recorded log is from yesterday's clean run."*
- Min 22:00: *"All four signoff metrics zero on the first attempt. That is what 'use the workshop slot' buys you -- the padring, the PDK fork, the slot YAML are all proven. The only thing that can break is your own logic, which is the part you should be debugging anyway."*

## Pre-recorded fallback

If the pre-recorded artifacts are missing on recording day, the episode can downgrade to a hand-walked version using the upstream notebook's printed outputs. Quote the metrics from the upstream `README.md`'s "validated, all signoff = 0" line; show the layout PNG from the chipathon-2026 fork's repository; explain the cut as "the recording machine does not have a clean run staged today, but the upstream repository documents one."
