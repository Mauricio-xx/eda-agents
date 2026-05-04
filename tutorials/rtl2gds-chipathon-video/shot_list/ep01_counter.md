# Ep 01 shot-list -- Counter through the Classic flow (target 20-25 min)

The first flow run. A bare 4-bit counter, no padring, ~1-2 min on a modern laptop. The whole point of this episode is to make LibreLane feel small and reproducible -- one Verilog file, one config, one command, one `metrics.csv`. After this, every subsequent episode just adds context (custom slot, multi-macro, etc.) on top of the same skeleton.

## Hook narrative

> *"One Verilog file. One YAML. One command. Two minutes. The simplest possible RTL-to-GDS flow on GF180MCU -- so simple you can keep the whole pipeline in your head while it runs."*

This is the one episode where the flow is short enough to record raw, no time-lapse, no pre-grabados. The viewer sees Yosys, OpenROAD, Magic, KLayout, Netgen all run in two minutes.

## Bootstrap (run once, off-camera, before opening panes)

```bash
export GF180=$(docker ps -q --filter ancestor=hpretl/iic-osic-tools:chipathon26 | head -1)
test -n "$GF180" || { echo "No :chipathon26 container running — bootstrap one first"; }
```

Each of the three recording panes opens with **one** command — entering the container interactively:

```bash
docker exec -it "$GF180" bash -l
```

Everything from then on runs **inside the container**. No `docker exec` wrappers in camera-visible commands; no host-side `~/eda/designs/...` paths in camera-visible commands.

## Pane layout (3-pane, all inside container)

| Pane | Contents (after `docker exec -it` at start) |
|---|---|
| Pane 1 (left) | `cat counter.v`, `cat config.yaml`, `awk` over `metrics.csv` |
| Pane 2 (centre) | `source sak-pdk-script.sh ...`, then `librelane config.yaml` |
| Pane 3 (right) | `tail -f` the flow.log; later `klayout -n gf180mcu -e <gds> &` |

## Episode timeline

| Time | Section | Pane | Action |
|---|---|---|---|
| 00:00 | Pre-roll: `mini_decks/ep01_intro.html` | -- | 5 slides (cover + RTL/GDS + Pipeline + Three hardening stages + One command) |
| 02:30 | Open the working dir inside container | Pane 1 | `cd /foss/designs/counter_demo && ls -la` |
| 03:00 | Show the Verilog -- 4-bit synchronous counter, active-high sync reset | Pane 1 | `cat counter.v` |
| 04:30 | Show the LibreLane config -- one file, ~10 lines | Pane 1 | `cat config.yaml` |
| 06:00 | Verify shell prompt is inside container (UID 30034, container path) | Pane 2 | `id -u; pwd` |
| 06:30 | Activate the GF180 PDK (one-time per session) | Pane 2 | `source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 && which librelane` |
| 07:30 | Run the flow | Pane 2 | `cd /foss/designs/counter_demo && librelane config.yaml` |
| 07:30 | Tail the log live (start ~5 s after pane 2 begins, so the run dir exists) | Pane 3 | `cd /foss/designs/counter_demo && tail -f $(ls -td runs/*/ \| head -1)flow.log` |
| 08:30 | Yosys done | Pane 3 | spot the `Synthesis OK` banner |
| 09:30 | Floorplan + PDN visible in log | Pane 3 | narrate the step headers |
| 11:00 | Routing iterations | Pane 3 | spot the iteration counter |
| 12:30 | Magic DRC + KLayout antenna -- the slow part | Pane 3 | narrate (~30 s on this design) |
| 13:30 | Final banner -- run dir + `metrics.csv` path | Pane 3 | grep `Flow successful` |
| 14:00 | Read `metrics.csv` for signoff | Pane 1 | `awk -F, '$1 ~ /error__count\|violation__count\|drc_error\|antenna__viol\|lvs_.*__count/' $(ls -td runs/*/ \| head -1)final/metrics.csv` |
| 16:00 | Worst slacks positive on every corner | Pane 1 | `awk -F, '/timing__setup__ws/ \|\| /timing__hold__ws/' $(ls -td runs/*/ \| head -1)final/metrics.csv` |
| 17:00 | Open the GDS in KLayout (X11 forward, window pops on host) | Pane 3 | `klayout -n gf180mcu -e $(ls -td runs/*/ \| head -1)final/gds/counter.gds &` |
| 19:00 | Walk the layout -- core, IO, std cells | Pane 3 | zoom the cell array |
| 21:00 | Recap -- one Verilog, one YAML, one command | Pane 1 | flip back to `cat config.yaml` |
| 22:00 | Tease Ep 02 -- "before chip-top, the three files that define a slot" | -- | -- |
| 23:00 | Out | -- | -- |

## Cuts / time-lapses

None planned. The full flow is ~1-2 min on a modern laptop, so the live recording captures it raw.

If the recording machine is slower than expected and the flow exceeds 5 min, voice-over the routing repair iterations rather than cutting -- the iteration log is part of the story.

## Exact commands

Container assumed running off `hpretl/iic-osic-tools:chipathon26` (any name) with bind-mount `~/eda/designs <-> /foss/designs`. Each pane opens with the bootstrap snippet above (off-camera) and then `docker exec -it "$GF180" bash -l` to enter the container.

**Each pane, 02:30 (entry)**:
```bash
docker exec -it "$GF180" bash -l
```
The login shell prints two `[INFO] Final PATH/PYTHONPATH variable: ...` lines on entry — that is normal. After that you are inside the container.

**Pane 1, 02:30 (working dir)**:
```bash
cd /foss/designs/counter_demo
ls -la
```

**Pane 1, 03:00 / 04:30 (read source files)**:
```bash
cat counter.v
cat config.yaml
```

**Pane 2, 06:30 (activate PDK)**:
```bash
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
which librelane
```

**Pane 2, 07:30 (run the flow)**:
```bash
cd /foss/designs/counter_demo
librelane config.yaml
```

**Pane 3, 07:30 (log tail, ~5 s after pane 2 begins so the run dir exists)**:
```bash
cd /foss/designs/counter_demo
tail -f $(ls -td runs/*/ | head -1)flow.log
```

**Pane 1, 14:00 (signoff sanity check)**:
```bash
RUN=$(ls -td runs/*/ | head -1)
awk -F, '$1 ~ /error__count|violation__count|drc_error|antenna__viol|lvs_.*__count/' \
  "${RUN}final/metrics.csv"
```
The metric names use **double** underscore (`error__count`, `violation__count`); a single-underscore regex misses them.

**Pane 3, 17:00 (open the GDS with GF180 tech, backgrounded so the shell stays usable)**:
```bash
RUN=$(ls -td runs/*/ | head -1)
klayout -n gf180mcu -e "${RUN}final/gds/counter.gds" &
```
`-n gf180mcu` pins the technology (do *not* use `-t gf180mcu` — that flag means "don't update config" and KLayout will treat `gf180mcu` as a filename). `&` backgrounds the process so the pane returns to a prompt; the KLayout window appears on your host display via the X11 forward set up by `start_x.sh`.

## Honest scope (lines on camera)

- Min 02:30: *"The counter has no padring, no SRAM, no analog. It is the bare-metal LibreLane experience -- four flip-flops, an adder, a clock. The pre-roll's 'three hardening stages' slide places this episode on the chipathon map: today is **stage 01, macro hardening** -- RTL goes in, four reusable views (.gds, .lef, .lib, blackbox.v) come out. Episodes 02 and 03 do stages 02 and 03 (slot + chip-top integration). If this episode fails, your environment is broken; if it succeeds, you have everything you need to run the rest of the series."*
- Min 06:00: *"Notice the prompt -- I am inside the container. Three panes, three `docker exec -it` sessions, one image. Every command from here on runs inside this image."*
- Min 12:30: *"This is the part that dominates wall-clock on bigger designs. Magic DRC and KLayout antenna can run for half an hour on a chip-top. On a 4-bit counter they take seconds because there is barely anything to check."*
- Min 13:30: *"`metrics.csv` is the signoff gate. Every field ending in `error__count` must be zero -- note the double underscore. One line of `awk` is enough to confirm the chip is foundry-ready, no GUI required."*

## Pre-recorded fallback

None mandatory -- the flow is fast enough to record raw. If the live recording fails for unrelated reasons (Docker daemon down, disk full), keep a known-good `runs/<ts>/` directory cached on the recording machine and replay the metrics + GDS view from it. No log replay needed.
