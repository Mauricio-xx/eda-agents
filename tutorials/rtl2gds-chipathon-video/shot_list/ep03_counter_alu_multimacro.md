# Ep 03 shot-list -- Multi-macro chip-top: counter + 4-bit ALU (target 25-30 min)

The capstone. Two RTL modules (an 8-bit counter + a 4-bit registered ALU) are hardened separately as macros, then merged into the chipathon-2026 workshop padring as a single chip-top with `MACROS:` + `PDN_MACRO_CONNECTIONS:` declarations. cocotb runs both pre-PnR (RTL) and post-synthesis (GL netlist) on each macro. Chip-top wall-clock is ~60-90 min, so this episode is mostly pre-recorded.

## Hook narrative

> *"Two macros, one chip-top, one set of signoff metrics. The pattern that lets you compose chips from independently-validated blocks. We harden each macro standalone, patch the workshop padring to call them, and the chip flow routes the rest."*

The viewer learns the multi-macro pattern, sees a real chipathon-style validation sequence (cocotb pre-PnR + GL sim post-synth), and watches the chip-top close on the first attempt.

## Bootstrap (run once, off-camera, before opening panes)

```bash
export GF180=$(docker ps -q --filter ancestor=hpretl/iic-osic-tools:chipathon26 | head -1)
test -n "$GF180" || { echo "No :chipathon26 container running — bootstrap one first"; }
```

Each of the three recording panes opens with **one** command — entering the container interactively:

```bash
docker exec -it "$GF180" bash -l
```

Ep 02 must have run first so the padring fork is staged at `/foss/designs/chipathon_padring/template/`. This episode uses a **dedicated copy** at `/foss/designs/multimacro_chipathon/template/` so the Ep 02 baseline stays clean. The multimacro working dir at `/foss/designs/multimacro_chipathon/user_macros/` is staged from upstream `examples/librelane_rtl2gds_gf180/04_counter_alu_multimacro/{rtl,tb,librelane}` (see README pre-flight; one-time `cp -r` on the host before recording).

## Pane layout (3-pane, all inside container, mostly pre-recorded)

| Pane | Contents (after `docker exec -it` at start) |
|---|---|
| Pane 1 (left) | `cat`/`less` over `librelane/{counter_macro,alu_macro}.yaml`, `rtl/chip_core_multi.sv`, the cocotb `tb/test_*.py`; later `awk` over chip-top `metrics.csv` and `sed` to extract the post-patch MACROS dict |
| Pane 2 (centre) | `source sak-pdk-script.sh ...` once, then per-macro hardening + chip-top `make librelane` |
| Pane 3 (right) | `tail -f` of the active LibreLane/cocotb log; later `klayout -n gf180mcu -e <gds> &` |

## Episode timeline

| Time | Section | Pane | Action |
|---|---|---|---|
| 00:00 | Pre-roll: `mini_decks/ep03_intro.html` | -- | 5 slides (cover + Multi-macro + Signoff overview + PDK choice + metrics) |
| 03:30 | Walk into the staging tree | Pane 1 | `cd /foss/designs/multimacro_chipathon/user_macros && ls -R rtl/ tb/ librelane/` |
| 05:30 | RTL verification (cocotb, ~15 s) | Pane 2 | `cd /foss/designs/multimacro_chipathon/user_macros/tb && make clean && make test-counter && make test-alu` |
| 07:00 | Read the cocotb tests | Pane 1 | `cat tb/test_counter.py`; `cat tb/test_alu.py` |
| 08:30 | Activate PDK + harden counter macro -- Classic flow, ~1.5-3 min | Pane 2 | `cd /foss/designs/multimacro_chipathon/user_macros && source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 && librelane librelane/counter_macro.yaml --pdk gf180mcuD --pdk-root /foss/designs/multimacro_chipathon/template/gf180mcu --manual-pdk --save-views-to build/counter` |
| 08:30 | Live tail of the counter hardening | Pane 3 | `cd /foss/designs/multimacro_chipathon/user_macros && tail -f $(ls -td build/counter/runs/*/ \| head -1)flow.log` |
| 11:30 | Counter macro done -- four artifacts | Pane 1 | `ls build/counter/{gds,lef,nl,lib}/` |
| 12:30 | Harden alu_macro -- Classic flow, ~1.5-3 min | Pane 2 | `librelane librelane/alu_macro.yaml --pdk gf180mcuD --pdk-root /foss/designs/multimacro_chipathon/template/gf180mcu --manual-pdk --save-views-to build/alu_macro` |
| 12:30 | Live tail of the ALU hardening | Pane 3 | `tail -f $(ls -td build/alu_macro/runs/*/ \| head -1)flow.log` |
| 15:30 | Post-synth GL sim of both macros (~15 s) | Pane 2 | `cd /foss/designs/multimacro_chipathon/user_macros/tb && make test-counter-gl && make test-alu-gl` |
| 17:00 | Patch the padring fork's `chip_core.sv` | Pane 2 | `cp /foss/designs/multimacro_chipathon/user_macros/rtl/chip_core_multi.sv /foss/designs/multimacro_chipathon/template/src/chip_core.sv` |
| 18:30 | Show the post-patch MACROS dict | Pane 1 | `sed -n '/^MACROS:/,/^FP_MACRO_HORIZONTAL_HALO:/p' /foss/designs/multimacro_chipathon/template/librelane/config.yaml` |
| 19:30 | Kick off chip-top flow (`SLOT=workshop make librelane`) | Pane 2 | `cd /foss/designs/multimacro_chipathon/template && make librelane SLOT=workshop PDK=gf180mcuD PDK_ROOT=/foss/designs/multimacro_chipathon/template/gf180mcu` |
| 19:30 | Live tail for ~3 min | Pane 3 | `cd /foss/designs/multimacro_chipathon/template && tail -f $(ls -td librelane/runs/*/ \| head -1)flow.log` |
| 22:30 | **Cut to time-lapse** -- chip-flow stages (4-8x speed) | -- | voice-over OpenROAD + Magic DRC + KLayout DRC stages |
| 24:00 | **Cut to pre-recorded artifacts** | -- | screen-record from `/foss/designs/prerecorded/ep03_multimacro.log` |
| 25:00 | Read the chip-top `metrics.csv` -- ~84,631 instances, all-zero signoff | Pane 1 | `awk -F, '$1 ~ /error__count\|violation__count\|drc_error\|antenna__viol\|lvs_.*__count\|instance__count/' /foss/designs/prerecorded/ep03_metrics.csv` |
| 26:30 | Open the chip-top GDS in KLayout -- both macros visible | Pane 3 | `klayout -n gf180mcu -e /foss/designs/prerecorded/ep03_multimacro.gds &` |
| 28:00 | Recap -- the multi-macro pattern, signoff zeros, ~80-90 min wall-clock | Pane 1 | flip to the master deck's "Multi-macro pattern" slide |
| 29:00 | Closing -- "where to go next: more macros, your own slot, tape-out" | -- | -- |
| 30:00 | Out | -- | -- |

## Cuts / time-lapses

- **08:30 -> 11:30**: counter macro hardening (~1.5-3 min real, recorded raw).
- **12:30 -> 15:30**: ALU macro hardening (~1.5-3 min real, recorded raw).
- **22:30 -> 24:00**: chip-flow time-lapse, 4-8x speed depending on log density.
- **24:00 -> end**: pre-recorded artifacts only. Chip-top wall-clock 60-90 min (Magic DRC + KLayout DRC dominate); do not wait for it on camera.

The cocotb pre-PnR and GL sim are short (~15 s each) and unique to this episode — keep them raw.

## Pre-recorded artifacts (required)

| File (in bind-mount) | What it is | Size approx |
|---|---|---|
| `~/eda/designs/prerecorded/ep03_multimacro.log` | Full chip-flow `flow.log` from a clean ~80 min run | ~6 MB |
| `~/eda/designs/prerecorded/ep03_multimacro.gds` | Final chip-top GDS | ~50 MB |
| `~/eda/designs/prerecorded/ep03_metrics.csv` | Chip-top `final/metrics.csv` | ~10 kB |
| `~/eda/designs/prerecorded/ep03_layout_render.png` | KLayout PNG export of the chip-top layout | ~2 MB |
| `~/eda/designs/prerecorded/ep03_cocotb_gl_output.txt` | cocotb GL sim transcript | ~10 kB |

Generate before recording (one-time, ~80-90 min, off-camera):

```bash
docker exec -it "$GF180" bash -l
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
cd /foss/designs/multimacro_chipathon/template
make librelane SLOT=workshop PDK=gf180mcuD PDK_ROOT=/foss/designs/multimacro_chipathon/template/gf180mcu
LATEST=$(ls -td librelane/runs/*/ | head -1)
cp "$LATEST/flow.log"                  /foss/designs/prerecorded/ep03_multimacro.log
cp "$LATEST/final/gds/chip_top.gds"    /foss/designs/prerecorded/ep03_multimacro.gds
cp "$LATEST/final/metrics.csv"         /foss/designs/prerecorded/ep03_metrics.csv
```

## Exact commands

Each pane opens with the bootstrap snippet above (off-camera) and then `docker exec -it "$GF180" bash -l` to enter the container.

**Each pane, 02:30 (entry into container)**:
```bash
docker exec -it "$GF180" bash -l
```

**Pane 1, 03:30 (walk staging tree)**:
```bash
cd /foss/designs/multimacro_chipathon/user_macros
ls -R rtl/ tb/ librelane/
```

**Pane 2, 05:30 (cocotb pre-PnR)**:
```bash
cd /foss/designs/multimacro_chipathon/user_macros/tb
make clean
make test-counter
make test-alu
```

**Pane 2, 08:30 (activate PDK + harden counter)**:
```bash
cd /foss/designs/multimacro_chipathon/user_macros
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
librelane librelane/counter_macro.yaml \
  --pdk gf180mcuD \
  --pdk-root /foss/designs/multimacro_chipathon/template/gf180mcu \
  --manual-pdk \
  --save-views-to build/counter
```

**Pane 2, 12:30 (harden ALU)**:
```bash
librelane librelane/alu_macro.yaml \
  --pdk gf180mcuD \
  --pdk-root /foss/designs/multimacro_chipathon/template/gf180mcu \
  --manual-pdk \
  --save-views-to build/alu_macro
```

**Pane 2, 15:30 (post-synth GL sim, both macros)**:
```bash
cd /foss/designs/multimacro_chipathon/user_macros/tb
make test-counter-gl
make test-alu-gl
```

**Pane 2, 17:00 (patch the padring fork's chip_core.sv -- inside the container, both paths are in the bind-mount)**:
```bash
cp /foss/designs/multimacro_chipathon/user_macros/rtl/chip_core_multi.sv \
   /foss/designs/multimacro_chipathon/template/src/chip_core.sv
```

**Pane 1, 18:30 (show the post-patch MACROS dict)**:
```bash
sed -n '/^MACROS:/,/^FP_MACRO_HORIZONTAL_HALO:/p' \
  /foss/designs/multimacro_chipathon/template/librelane/config.yaml
```
(The patch is applied off-camera by the notebook's `patch_top()` cell, which adds the MACROS + PDN_MACRO_CONNECTIONS entries shown here.)

**Pane 2, 19:30 (chip-top flow)**:
```bash
cd /foss/designs/multimacro_chipathon/template
make librelane SLOT=workshop \
  PDK=gf180mcuD \
  PDK_ROOT=/foss/designs/multimacro_chipathon/template/gf180mcu
```

**Pane 3, 19:30 (chip-top live tail)**:
```bash
cd /foss/designs/multimacro_chipathon/template
tail -f $(ls -td librelane/runs/*/ | head -1)flow.log
```

**Pane 1, 25:00 (chip-top signoff sanity on pre-recorded)**:
```bash
awk -F, '$1 ~ /error__count|violation__count|drc_error|antenna__viol|lvs_.*__count|instance__count/' \
  /foss/designs/prerecorded/ep03_metrics.csv
```

**Pane 3, 26:30 (open chip-top GDS, backgrounded)**:
```bash
klayout -n gf180mcu -e /foss/designs/prerecorded/ep03_multimacro.gds &
```

## Honest scope (lines on camera)

- Min 03:30: *"Three folders staged in the bind-mount: `rtl/`, `tb/`, `librelane/`. The chip-top patch lives in a dedicated padring fork copy at `/foss/designs/multimacro_chipathon/template/` so the episode-02 baseline stays untouched."*
- Min 05:30: *"cocotb runs on the RTL, before any synthesis. It is the single best safety net you can put in front of LibreLane: a synth bug can bake into a 90-minute flow you only catch at signoff."*
- Min 08:30: *"Each macro hardens with `librelane librelane/<macro>.yaml --save-views-to build/<macro>`. The `--save-views-to` flag is what makes the four signoff views (GDS, LEF, netlist, lib) reusable for the chip-top step. Notice the command runs directly -- no `docker exec` wrapper -- because we are already inside the container."*
- Min 15:30: *"Post-synthesis GL sim is the second safety net. Same testbench, against the synthesized netlist plus the gf180 cell library. If it passes, your synthesis preserved behaviour. Routing and antenna can still break it later, but synthesis is clean."*
- Min 19:30: *"The chip-top flow runs from the padring fork, not from `multimacro_chipathon/user_macros/`. The two macros are referenced through the patched `MACROS:` dict in `multimacro_chipathon/template/librelane/config.yaml`."*
- Min 22:30: *"Real wall-clock is ~80-90 minutes. Magic DRC and KLayout DRC dominate. We watch the first three minutes live, time-lapse the rest, and jump to the pre-recorded final banner. The flow was re-run for the artifacts; it is reproducible."*
- Min 28:00: *"~84,000 instances at chip-top, four signoff metrics zero on the first attempt. That is what `--save-views-to` plus the chipathon-2026 padring buys you: independently-validated blocks composed into a clean tape-out."*

## Pre-recorded fallback

If pre-recorded artifacts are missing on recording day, the episode degrades to a recap of the per-macro hardening (Eps 01-02 already cover that pattern) plus a hand-walked `cat` of the multi-macro `chip_core.sv`. Skip the chip-top flow entirely; quote the upstream README's signoff numbers and show the layout PNG from the chipathon-2026 fork's documentation. The viewer still gets the multi-macro pattern; the live chip-flow shot moves to a follow-up.
