# RTL-to-GDS on GF180MCU — chipathon-2026 video tutorial series

Four-episode video tutorial that takes a chipathon-2026 participant from "what is LibreLane?" to "I shipped a multi-macro chip-top through the workshop padring." Episodes Ep 01-03 run **fully inside** the `hpretl/iic-osic-tools:chipathon26` Docker container; Ep 00 is a slide-only conceptual opener.

The series shadows the upstream chipathon notebooks at [`sscs-ose/sscs-chipathon-2026/examples/librelane_rtl2gds_gf180/`](https://github.com/sscs-ose/sscs-chipathon-2026/tree/main/examples/librelane_rtl2gds_gf180) but is the cinematic version: same commands, same outputs, real terminal, no Jupyter on screen.

## Recording format — 3-pane terminal-driven, fully inside the container

Each Eps 01-03 runs as a tmux split (or three side-by-side panes). Every pane opens with **the same one-liner** to enter the running container interactively, and from then on all commands run **inside** the container — no `docker exec` wrappers, no host paths in camera-visible commands. Ep 00 is slide-only and does not use the 3-pane layout.

| Pane | Role (inside the container after `docker exec -it`) |
|---|---|
| Pane 1 (left) | `cat`, `less`, `awk`, `ls -R` over the files currently being read — `config.yaml`, `slot_*.yaml`, `chip_core.sv`, `metrics.csv`, cocotb tests |
| Pane 2 (centre) | `source sak-pdk-script.sh ...` once per session; then `librelane …`, `make librelane`, cocotb `make test-*`, macro hardening |
| Pane 3 (right) | `tail -f` the LibreLane / cocotb log; later `klayout -n gf180mcu -e <gds> &` (window pops on host via X11 forward) |

The container ships `cat`, `less`, `awk`, `tail`, `librelane`, `klayout`, `firefox` (for SVG/PDF viewing) — but **not** `bat`, `tree`, `feh`, `nvim`. The recording uses what the stock chipathon26 image provides; do not assume host tools.

The Jupyter notebooks are **not in the recording**. They are the documentation a viewer reads at home; the video is the cinematic version that uses the same commands but in a real terminal.

## Reading order (per episode)

| Ep | Notebook (upstream chipathon) | Wall time | Live? | Pre-recorded fallback |
|---|---|---|---|---|
| 00 | (no notebook — series intro, slide-only) | ~10 min, no shell | Live entirely | none — slide-only |
| 01 | `01_rtl2gds_counter.ipynb` | 1-2 min flow | Live entirely | none — flow is short enough to record raw |
| 02 | `03_rtl2gds_chipathon_use.ipynb` | ~35-45 min flow | Start live, jump | `prerecorded/ep02_chipathon_use.log` + GDS render |
| 03 | `04_counter_alu_multimacro/` | ~60-90 min + cocotb | Start live, jump | `prerecorded/ep03_multimacro.log` + GDS render + cocotb output |

Target episode length: **10-30 min on screen**, regardless of the underlying flow's wall-clock. Ep 00 is the conceptual pitch (no terminal). Eps 02-03 *will* hit a "now we wait" moment; the cut to the pre-recorded log + final artifacts is the bridge.

## Pre-roll mini-decks

Each episode is preceded by a 4-6 slide pre-roll (HTML, 1920×1080, paper-style, same CSS family as `rtl2gds-gf180-docker/claude_design_slides/`). Slides are pulled from the trimmed master deck (`../rtl2gds-gf180-docker/claude_design_slides/RTL-to-GDS on GF180MCU.html`) so technical content stays consistent across the master deck and the per-episode pre-rolls.

| Ep | Master slides reused (data-label) | Total | What the pre-roll covers |
|---|---|---|---|
| 00 | cover + 5 + 6 + 7 + 8 + four-episodes-overview | 6 | RTL vs GDS, the pipeline, LibreLane as conductor, deliverables, the 3 episodes ahead |
| 01 | cover + 5 + 6 + 12 + 15 + 23 | 6 | RTL vs GDS, the pipeline, **container bootstrap (one-time docker pull)**, **three hardening stages (macro hardening = today)**, the one command |
| 02 | cover + 21 + slot_anatomy.svg + workshop_pad_map.svg + 27 | 5 | Slot = CHIPATON die contract, slot anatomy (3 files), workshop pad map, signoff overview |
| 03 | cover + 20 + 27 + 40 + 25 | 5 | Multi-macro MACROS dict, signoff overview, PDK choice (ws fork rationale), `metrics.csv` |

The *cover* slide on each pre-roll is unique to that episode and announces the episode's title + the notebook it shadows. All other slides are reused verbatim from the master deck.

## Honest scope notes

- **No agents in this series.** This is the LibreLane manual flow. If you want LLM-orchestrated equivalents, watch `idea-to-chip-video/`.
- **GF180MCU only.** No SG13G2 coverage here.
- **Pre-recorded artifacts are required for Ep 03-04.** Each one needs at least one full clean run of the underlying notebook before the recording day. See `prerecorded/` for the artifact slots.
- **Fully inside the container.** Eps 01-04 open three panes; each pane runs `docker exec -it "$GF180" bash -l` once at the top of the recording and stays inside for the whole episode. No `docker exec "$GF180" bash -lc '…'` wrappers in any camera-visible command. The contract is the image (`:chipathon26`) and the `~/eda/designs <-> /foss/designs` bind-mount. Ep 00 frames *why* the container is the deliverable boundary; Eps 01-04 live there.

## PDK source policy — why all LibreLane steps in Ep 03 target the wafer-space fork

Every `librelane` invocation in Ep 03 (macro hardening *and* chip-top integration) sets `--pdk-root` (or the equivalent `PDK_ROOT=` Make variable) to a local clone of `wafer-space/gf180mcu` @ tag `1.8.0`, **not** the system PDK at `/foss/pdks/gf180mcuD` that the `chipathon26` image bundles. This is a hard workaround for a real bug in the docker-bundled PDK, not a stylistic choice.

### Cause

The system PDK in `hpretl/iic-osic-tools:chipathon26` was installed from `open_pdks` at commit `7b70722e` (per `/foss/pdks/gf180mcuD/SOURCES`). That commit ships a `libs.tech/librelane/config.tcl` written against **OpenLane 1.x**, not LibreLane v3 — most variable names are legacy and silently dropped by the v3 config resolver.

Inspecting the resolved config.json that LibreLane v3.0.2 produces inside `runs/<ts>/<NN>-klayout-drc/config.json` shows the scope of the gap:

| Variable in sys `config.tcl` | Resolved config dict | Status |
|---|---|---|
| `VDD_PIN`, `GND_PIN`, `KLAYOUT_TECH`, `KLAYOUT_PROPERTIES`, `KLAYOUT_DEF_LAYER_MAP` | populated correctly | v3 still recognizes these names |
| `LIB_SYNTH`, `TECH_LEF`, `CELLS_LEF`, `GDS_FILES`, `PROCESS`, `FP_PDN_RAIL_LAYER` | **`<missing>`** | renamed in v3 (likely plural, e.g. `TECH_LEFS`, or different schema) |
| `KLAYOUT_DRC_TECH_SCRIPT` | **`<missing>`** | should auto-alias to `KLAYOUT_DRC_RUNSET` via `deprecated_names` per `librelane/steps/klayout.py:454`, but the resolved value is `None` |

The KLayout DRC skip is just the most visible symptom. LibreLane v3 falls back to internal defaults for synth + PnR + Magic DRC + LVS, which is why those steps pass and produce a misleadingly clean `metrics.csv`. KLayout DRC + density + antenna + filler + KLayout LVS are all silently absent because the v3 dict-shaped variables (`KLAYOUT_DRC_RUNSET`, `KLAYOUT_DRC_OPTIONS`, `KLAYOUT_DENSITY_RUNSET`, etc.) are never defined and the legacy alias path drops them anyway. The two warnings on the user-visible log are:

```
WARNING [Checker.KLayoutDRC]
  KLayout.DRC may not be supported for the gf180mcuD PDK.
  This step will be skipped.
WARNING [Misc.ReportManufacturability]
  klayout__drc_error__count not reported. KLayout.DRC may have been skipped.
```

The KLayout DRC rule files themselves *do* exist at `/foss/pdks/gf180mcuD/libs.tech/klayout/tech/drc/`. The bug is purely in the OpenLane 1.x format of `config.tcl`, not in the rule decks.

### The workaround

The `wafer-space/gf180mcu` fork (tag `1.8.0`, derived from `open_pdks` @ `40cee970` plus chipathon-specific deltas) ships a v3-correct `libs.tech/librelane/config.tcl` *and* the `gf180mcu_ws_io__*` IO cells the chipathon padring instantiates. Pointing all LibreLane steps at this fork via `--pdk-root` (macros) or `PDK_ROOT=` (chip-top Make var) recovers full v3 signoff.

### What still uses the docker's system PDK

Not everything jumps to the fork. Inside `:chipathon26` the system PDK at `/foss/pdks/gf180mcuD` is still:

- The default the SAK env script resolves to (`source sak-pdk-script.sh gf180mcuD ...` exports `PDK_ROOT=/foss/pdks` and friends).
- The technology source for the KLayout viewer (`klayout -n gf180mcu …` reads `libs.tech/klayout/` for layer colors and properties; this never validates anything).
- Unused by cocotb pre-PnR (no PDK references at the RTL level).

The `gf180mcu_fd_sc_mcu7t5v0` standard cell library is functionally equivalent at the LEF / LIB / Verilog level between the two PDKs (verified byte-identical). The cell GDS differs by ~0.08% (1155 bytes scattered), `techlef` antenna ratio rule is `2` (sys) vs `15` (ws), and the cdl differs by 218 bytes — none of which affect synthesis or P&R.

### Upstream fix

The bug is upstream in `RTimothyEdwards/open_pdks`, not in `iic-jku/IIC-OSIC-TOOLS`. IIC-OSIC-TOOLS only pins an `open_pdks` commit for the docker build (`_build/images/open_pdks/Dockerfile` line 19, `OPEN_PDKS_REPO_COMMIT="7b70722e..."`). They install via `efabless/ciel` (volare's successor) which downloads pre-built tarballs keyed by an open_pdks commit hash.

The relevant upstream PR is **open_pdks PR #507** (https://github.com/RTimothyEdwards/open_pdks/pull/507) by Leo Moser (also the top contributor to `wafer-space/gf180mcu`). It replaces `KLAYOUT_DRC_TECH_SCRIPT` with the v3 dict-shaped form plus `KLAYOUT_DRC_OPTIONS` plus sibling dicts for `KLAYOUT_DENSITY_RUNSET / ANTENNA_RUNSET / FILLER_SCRIPT / LVS_SCRIPT` plus active `LAYERS_RC` / `VIAS_R` dicts. Filed 2026-04-27, **open and unmerged**. PR description explicitly flags "Update KLayout DRC and LVS" as still pending.

Crucially, **`open_pdks` HEAD also does not have v3 support yet** (verified 2026-05-04 against commit `d815bb30`). Bumping the IIC-OSIC-TOOLS pin to a newer open_pdks SHA does *not* fix this; PR #507 has to merge first. Once PR #507 lands and a volare tarball is published for that commit, IIC-OSIC-TOOLS can bump line 19 and the workaround retires — macros could harden against the system PDK directly, only chip-top (which still needs the IO cells) would target the fork.

For historical context, see the related (long-standing) issue `open_pdks#295` (https://github.com/RTimothyEdwards/open_pdks/issues/295), open since 2022-10-20: gf180mcu config.tcl missing pieces. PR #507 is the current upstreaming pathway from wafer-space.

The `--manual-pdk` flag in the verbose form is technically redundant — `/foss/tools/bin/librelane` is a wrapper that auto-injects it. Pass it explicitly anyway for parity with the upstream chipathon-2026 reference notebook.

Ep 01 (the bare counter) currently uses the system PDK and therefore *also* silently skips KLayout DRC. The episode's signoff narration tolerates this: a 4-flip-flop counter has no realistic KLayout-DRC-only failure mode at this geometry, so the cut to `awk '/error__count/'` over Magic DRC + LVS + antenna is honest enough for that scope. Ep 03 cannot tolerate the skip — chipathon entries must pass KLayout DRC at chip-top, so the workaround is mandatory there.

## Bootstrap (every Eps 01-04 opens with this; Ep 00 is slide-only)

```bash
# Off-camera, before opening recording panes:
export GF180=$(docker ps -q --filter ancestor=hpretl/iic-osic-tools:chipathon26 | head -1)
test -n "$GF180" || { echo "No :chipathon26 container running — bootstrap one first"; }

# Each of the three panes (on-camera at minute 02:30 of each episode):
docker exec -it "$GF180" bash -l
```

`docker exec -it … bash -l` enters the container interactively as a login shell so `/foss/tools/{bin,sak,klayout}` end up on PATH. The container prints two `[INFO] Final PATH/PYTHONPATH variable: …` lines on entry — that is the login script and is normal. After that you are inside the container; everything else runs without a wrapper.

If `$GF180` comes back empty, the canonical way to start the container is via the `iic-osic-tools/start_x.sh` launcher (X11 forward enabled so `klayout` and `firefox` from inside the container show up on your host display):

```bash
git clone https://github.com/iic-jku/IIC-OSIC-TOOLS.git ~/git/IIC-OSIC-TOOLS   # one-time
DOCKER_TAG=chipathon26 CONTAINER_NAME=gf180-x bash ~/git/IIC-OSIC-TOOLS/start_x.sh
```

`start_x.sh` lives in [`iic-jku/IIC-OSIC-TOOLS`](https://github.com/iic-jku/IIC-OSIC-TOOLS); clone the upstream repo once if you do not already have it. The script's defaults already point at `hpretl/iic-osic-tools` so we only override the tag (`chipathon26`) and pick a container name (`gf180-x`). The `$GF180` filter above finds the container by image, not by name — pick whatever name suits you.

Inside the container, GUI tools (`klayout`, `firefox`) are launched in the background with `&`:

```bash
klayout -n gf180mcu -e /foss/designs/<some>/<gds_path>.gds &
firefox /foss/designs/diagrams/workshop_pad_map.svg &
```

`-n gf180mcu` pins the GF180 KLayout technology (layer colors from `/foss/pdks/gf180mcuD/libs.tech/klayout/`). *Do not* use `-t gf180mcu` — that's a different flag (`-t` = "don't update config on exit") and KLayout will treat `gf180mcu` as a filename, popping a "file not found" dialog.

## File layout

```
tutorials/rtl2gds-chipathon-video/
├── README.md                        # this file
├── assets/                          # SVGs reused by mini-decks (chipathon-2026 origin, Apache-2.0)
│   ├── slot_anatomy.svg             # 3-files-per-slot diagram (Ep 02 mini-deck)
│   └── workshop_pad_map.svg         # chipathon die + 91 I/O signal-name lookup (Ep 02 mini-deck)
├── mini_decks/                      # 4 HTML pre-rolls (one per episode)
│   ├── ep00_intro.html              # series intro, 6 slides
│   ├── ep01_intro.html              # counter + macro hardening context, 6 slides
│   ├── ep02_intro.html              # workshop slot (anatomy + pad map + signoff), 5 slides
│   └── ep03_intro.html              # multi-macro, 5 slides
├── shot_list/                       # 4 markdown shot-lists (one per episode)
│   ├── ep00_what_is_librelane.md
│   ├── ep01_counter.md
│   ├── ep02_chipathon_workshop_slot.md
│   └── ep03_counter_alu_multimacro.md
└── prerecorded/                     # text-only artifacts committed here (logs, metrics)
    └── (artifacts live in ~/eda/designs/prerecorded/, NOT in this repo —
         bind-mount path so the container can read them as /foss/designs/prerecorded/)
```

The shot-lists and mini-decks ship in this repo. The pre-recorded artifacts are produced before recording into `~/eda/designs/prerecorded/` (the bind-mount directory the container reads as `/foss/designs/prerecorded/`). The repo's `prerecorded/` slot is for committing small text artifacts (`flow.log`, `metrics.csv`) once validated; large binaries (GDS ~30-50 MB, PNG renders) typically stay in the bind-mount only.

## Pre-flight — day before recording

1. Pull `hpretl/iic-osic-tools:chipathon26` if not already present (~18 GB).
2. Start the container via `start_x.sh` (see Bootstrap above); run the bootstrap snippet and confirm `$GF180` is non-empty.
3. Verify the in-container login shell finds the toolchain (no PDK source needed for the path check):
   ```bash
   docker exec "$GF180" bash -lc 'which librelane && which klayout && which firefox'
   ```
4. Smoke-test KLayout from inside the container — open a known-good GDS:
   ```bash
   docker exec -d "$GF180" bash -lc 'klayout -n gf180mcu -e /foss/designs/chipathon_padring/template/final/gds/chip_top.gds'
   ```
   A KLayout window must appear on your host display with GF180 layer colors. Close it after verifying.
5. Stage the working dirs into the bind-mount so they are visible inside the container as `/foss/designs/...`:
   ```bash
   # Ep 01 — counter (one-shot, ~10 lines):
   mkdir -p ~/eda/designs/counter_demo
   # Extract counter.v + config.yaml from chipathon nb01 (see helper or copy manually)
   cp ~/git/sscs-chipathon-2026/examples/librelane_rtl2gds_gf180/01_rtl2gds_counter.ipynb /tmp/

   # Ep 02 — chipathon padring fork (the slot you tape out):
   cp -r ~/git/sscs-chipathon-2026/resources/Integration/workshop_padring_librelane \
         ~/eda/designs/chipathon_padring/template

   # Ep 03 — multi-macro (rtl + tb + librelane) + dedicated padring fork copy:
   mkdir -p ~/eda/designs/multimacro_demo
   cp -r ~/git/sscs-chipathon-2026/examples/librelane_rtl2gds_gf180/04_counter_alu_multimacro/{rtl,tb,librelane} \
         ~/eda/designs/multimacro_demo/
   mkdir -p ~/eda/designs/multimacro_demo/build

   # Ep 03 — dedicated padring fork copy (so Ep 02 baseline stays clean):
   cp -r ~/git/sscs-chipathon-2026/resources/Integration/workshop_padring_librelane \
         ~/eda/designs/multimacro_chipathon/template
   ```
6. Run the Ep 01 flow once end-to-end on the recording machine (~2 min) and confirm `runs/<ts>/final/metrics.csv` is all-zero.
7. Create the pre-recorded artifact directory inside the bind-mount so the container can see it:
   ```bash
   mkdir -p ~/eda/designs/prerecorded
   ```
   This is a real directory, not a symlink. (A host symlink whose target lives outside the bind-mount dangles inside the container — the container only sees what is under `~/eda/designs/`.) The repo's `tutorials/rtl2gds-chipathon-video/prerecorded/` is the *committed* snapshot; the bind-mount path is where the live container reads and writes. After harvesting a flow run, `cp` the small text artifacts (`flow.log`, `metrics.csv`, `cocotb_gl_output.txt`) into the repo for commit if desired; GDS and PNG renders are large and typically stay in the bind-mount only.
8. Confirm pre-recorded artifacts for Ep 02-03 are in place (`~/eda/designs/prerecorded/ep0[2-3]_*` — log + GDS + metrics, plus `ep03_cocotb_gl_output.txt`) and match the latest upstream notebook revision.
9. Open the master trimmed deck (`../rtl2gds-gf180-docker/claude_design_slides/RTL-to-GDS on GF180MCU.html`) in the browser the recording uses; confirm all slides render.

## Cleanup

```bash
docker stop "$GF180" && docker rm "$GF180"
docker image rm hpretl/iic-osic-tools:chipathon26   # optional, ~18 GB back
rm -rf ~/eda/designs/{counter_demo,chipathon_padring,multimacro_chipathon,multimacro_demo,prerecorded}
```
