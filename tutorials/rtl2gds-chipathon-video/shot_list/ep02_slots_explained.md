# Ep 02 shot-list -- Slots explained (target 20-25 min)

A read-only conceptual walkthrough of what a "slot" is in the chipathon-2026 padring template. No LibreLane flow runs in this episode -- the goal is to give the viewer a mental model of the three files that define a slot before later episodes start running flows over them.

## Hook narrative

> *"You just ran LibreLane in two minutes on a bare counter. Now we step back: the chipathon-2026 padring slot you will eventually plug a real chip into is three files. Once you can name them, every later episode is just edit-one or run-the-flow."*

The deck calls this Part 3, the slot anatomy. We do it after Ep 01 (counter) so the viewer already has tactile sense of LibreLane before going conceptual.

## Bootstrap (run once, off-camera, before opening panes)

```bash
export GF180=$(docker ps -q --filter ancestor=hpretl/iic-osic-tools:chipathon26 | head -1)
test -n "$GF180" || { echo "No :chipathon26 container running — bootstrap one first"; }
```

Each of the three recording panes opens with **one** command — entering the container interactively:

```bash
docker exec -it "$GF180" bash -l
```

The slot files (the `workshop_padring_librelane` template) live in the bind-mount at `/foss/designs/chipathon_padring/template/`. The diagrams live in the bind-mount at `/foss/designs/diagrams/` (staged once from the upstream chipathon repo, see README pre-flight).

## Pane layout (3-pane, all inside container, read-only)

| Pane | Contents (after `docker exec -it` at start) |
|---|---|
| Pane 1 (left) | `cat` over the three slot files in sequence: `src/chip_core.sv`, `src/slot_defines.svh`, `librelane/slots/slot_workshop.yaml` |
| Pane 2 (centre) | `klayout -n gf180mcu -e final/gds/chip_top.gds &` -- the pre-built workshop padring GDS (output of an earlier padring run) |
| Pane 3 (right) | `firefox /foss/designs/diagrams/slot_anatomy.svg /foss/designs/diagrams/workshop_pad_map.svg &` -- two tabs, advance with Ctrl+Tab |

No LibreLane runs in this episode. Pane 2 is a static layout viewer; pane 3 is Firefox showing static SVGs.

## Episode timeline

| Time | Section | Pane | Action |
|---|---|---|---|
| 00:00 | Pre-roll: `mini_decks/ep02_intro.html` | -- | 6 slides (cover + Three stages + Project template + Two-level YAML + MACROS dict + Slot YAML) |
| 03:00 | Title card: "the three files that define a slot" | -- | text overlay before the live panes |
| 03:30 | Open the slot template tree | Pane 1 | `cd /foss/designs/chipathon_padring/template && ls -R src/ librelane/slots/` |
| 04:30 | File 1 -- chip-top Verilog (the only file the user authors) | Pane 1 | `cat src/chip_core.sv` |
| 06:30 | File 2 -- slot defines (`NUM_*` per pad category: DVDD, DVSS, INPUT, BIDIR, ANALOG) | Pane 1 | `cat src/slot_defines.svh` |
| 09:00 | File 3 -- LibreLane slot YAML (die area, pad order) | Pane 1 | `cat librelane/slots/slot_workshop.yaml` |
| 11:30 | Open the pre-built padring GDS in KLayout | Pane 2 | `klayout -n gf180mcu -e final/gds/chip_top.gds &` |
| 12:30 | Slot anatomy diagram | Pane 3 | `firefox /foss/designs/diagrams/slot_anatomy.svg &` |
| 14:00 | Pad arithmetic -- does the ring close? | Pane 1 | walk the `NUM_INPUT_PADS + NUM_OUTPUT_PADS + ...` sum, narrate against the GDS in pane 2 |
| 16:00 | Clockwise-from-SW rule | Pane 2 + 3 | trace the perimeter on the GDS while the SVG annotates the convention |
| 17:30 | The PAD_NORTH/EAST/SOUTH/WEST clockwise lists (in the slot YAML, not the defines) | Pane 1 | `grep -nE 'PAD_(NORTH\|EAST\|SOUTH\|WEST)' librelane/slots/slot_workshop.yaml` |
| 19:30 | Workshop pad map | Pane 3 | new firefox tab: `firefox /foss/designs/diagrams/workshop_pad_map.svg &` (or Ctrl+T then drag) |
| 20:30 | "Rolling your own slot" preview | Pane 1 | scroll an existing notebook section in `less`, no edit |
| 22:00 | Recap -- three files, four sides, one diagram | Pane 3 | hold the slot anatomy SVG |
| 23:00 | Close: "next episode runs the chipathon workshop slot end-to-end -- your RTL in, fab-ready GDS out" | -- | tease Ep 03 |
| 24:00 | Out | -- | -- |

## Cuts / time-lapses

None. The whole episode runs live. If KLayout takes more than 30 s to load, narrate over the loading bar.

## Exact commands

Each pane opens with the bootstrap snippet above (off-camera) and then `docker exec -it "$GF180" bash -l` to enter the container. The chipathon padring template (cloned from upstream `sscs-chipathon-2026/resources/Integration/workshop_padring_librelane/`) is staged in the bind-mount as `/foss/designs/chipathon_padring/template/`. The pre-built `chip_top.gds` is the output of an earlier padring run; the diagrams are staged in `/foss/designs/diagrams/` from the upstream `examples/librelane_rtl2gds_gf180/diagrams/`. See README pre-flight for the staging cp commands.

**Each pane, 02:30 (entry into container)**:
```bash
docker exec -it "$GF180" bash -l
```

**Pane 1, 03:30 (cwd + tree)**:
```bash
cd /foss/designs/chipathon_padring/template
ls -R src/ librelane/slots/
```

**Pane 1, 04:30 / 06:30 / 09:00 (the three slot files)**:
```bash
cat src/chip_core.sv
cat src/slot_defines.svh
cat librelane/slots/slot_workshop.yaml
```
(use `less` instead of `cat` if you want paging on long files)

**Pane 2, 11:30 (KLayout, backgrounded so the shell stays usable)**:
```bash
klayout -n gf180mcu -e /foss/designs/chipathon_padring/template/final/gds/chip_top.gds &
```

**Pane 3, 12:30 (slot-anatomy diagram in Firefox)**:
```bash
firefox /foss/designs/diagrams/slot_anatomy.svg &
```

**Pane 1, 17:30**:
```bash
grep -nE 'PAD_(NORTH|EAST|SOUTH|WEST)' src/slot_defines.svh
grep -nE 'PAD_(NORTH|EAST|SOUTH|WEST)' librelane/slots/slot_workshop.yaml
```
The `PAD_*` clockwise pad-order arrays live in the slot YAML; `slot_defines.svh` only carries the `NUM_*` counts per pad category.

**Pane 3, 19:30 (workshop pad map -- new Firefox tab)**:
```bash
firefox /foss/designs/diagrams/workshop_pad_map.svg &
```
(if Firefox is already open from 12:30, this opens a new tab in the existing window; switch with Ctrl+Tab)

## Honest scope (lines on camera)

- Min 03:00: *"This episode does not run LibreLane. It is conceptual. Three files, four sides, one diagram. If you understand them, the rest of the series is mechanical. If you do not, every later command will look arbitrary."*
- Min 06:00: *"Three panes, three `docker exec -it` sessions into the same container -- everything happens inside that image. Even Firefox runs inside the container; the window pops on your host display via the X11 forward `start_x.sh` set up."*
- Min 14:00: *"The pad arithmetic is the single hardest thing to get right in your own slot. The numbers in `slot_defines.svh` must add up to exactly the perimeter the padring was designed for, or LibreLane stops with an obscure error during chip-top flow. Episode 4 hits this for real."*
- Min 20:30: *"For the chipathon, the workshop slot is provided. The 'roll your own slot' walk lives in the upstream notebook if you want to take a slot beyond the shuttle."*

## Pre-recorded fallback

None. This episode has no flow runs. The KLayout GDS view and the SVGs are static assets in the bind-mount and do not need pre-recording.
