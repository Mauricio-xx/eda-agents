# RTL-to-GDS on GF180MCU — hands-on notebooks

Five Jupyter notebooks that take you from a tiny bare-block flow to
a Chipathon 2026 tape-out on the workshop padring. They are designed
to be read in order; each one builds on the mental model the previous
set up. The companion slide deck (`../main.pdf`) is optional
reference material.

All heavy steps are gated by `RUN_*` flags that default to `False`,
so the first pass through any notebook is a dry run that only prints
what it would execute. Flip the flag to commit.

## Reading order

| # | Notebook | Time | What you learn |
|---|----------|------|----------------|
| 00 | `00_slots_explained.ipynb` | 10 min, read-only | What a slot is, what it is *not* (LibreLane convention vs template convention), and the three files that make one. **Start here.** |
| 01 | `rtl2gds_counter.ipynb` | 1-2 min flow + 10 min reading | A 4-bit counter through the Classic flow. Bare block, no padring. Warm-up: you learn the Docker + LibreLane mechanics. Self-contained; no repo clone required. |
| 02 | `rtl2gds_chip_top_custom.ipynb` | 35-45 min flow + 15 min reading | Full-chip flow on the stock `slot_1x1` with the counter from notebook 01 dropped in as a custom macro replacing one SRAM. Teaches the hierarchical macro flow. |
| 03 | `rtl2gds_chipathon_padring.ipynb` | 35-45 min flow + 20 min reading | Creation of the Chipathon 2026 workshop slot (2935 × 2935 µm, 60 analog + 20 bidir + 4/4 power, mirror of [JuanMoya/padring_gf180](https://github.com/JuanMoya/padring_gf180)) from scratch. Puts notebook 00 into practice. |
| 04 | `rtl2gds_chipathon_use.ipynb` | 35-45 min flow + 10 min reading | Consume the pre-built workshop slot with your own RTL. **This is the notebook you mostly live in during the chipathon.** |

## Quick start — notebook 01

Notebook 01 is the only one that runs fully self-contained (no repo
clone, no wafer-space PDK fork, RTL embedded inline). Use it as a
smoke test before committing to the heavier notebooks.

```bash
# 1. Place the file anywhere -- a download folder, /tmp, a fresh dir...
cd ~/Downloads

# 2. Make sure Docker works
docker ps

# 3. Open the notebook, flip RUN_* flags in Step 0, run cells top to bottom
jupyter lab rtl2gds_counter.ipynb
```

Expected wall time for the counter run itself: 1-2 min on a modern
laptop.

## Reference-run artifacts on the author's machine

Each notebook writes its state under a fixed host path so re-runs are
incremental. The full-chip notebooks assume the bind-mount
`~/eda/designs/ <-> /foss/designs/` already exists in the `gf180`
container.

| Notebook | Working dir (host) | Working dir (container) |
|----------|-------------------|--------------------------|
| 01 counter | `~/eda/designs/counter_demo/` | `/foss/designs/counter_demo/` |
| 02 chip_top_custom | `~/eda/designs/chip_custom/template/` | `/foss/designs/chip_custom/template/` |
| 03 chipathon_padring | `~/eda/designs/chipathon_padring/template/` | `/foss/designs/chipathon_padring/template/` |
| 04 chipathon_use | `~/eda/designs/chipathon_padring/template/` (same as 03) | same |

## Prerequisites

- Linux host, x86_64 recommended, ~40 GB free disk.
- Docker daemon running (test with `docker ps`).
- `hpretl/iic-osic-tools:next` image pulled (~15 GB, one-time).
- Python 3.9+ for the notebook kernels. No pip packages beyond
  stdlib + IPython.

The container is started once (a named container `gf180`, bind-mount
`~/eda/designs <-> /foss/designs`), then every notebook runs inside
it via `docker exec`. See the `Setup` cell of any notebook for the
`docker run -d --name gf180 ...` command.

## Troubleshooting

- **`docker: command not found`**: install Docker Engine and make sure
  your user is in the `docker` group.
- **`Error response from daemon: Conflict. The container name "/gf180"
  is already in use`**: reuse it (everything runs inside via
  `docker exec`) or remove: `docker stop gf180 && docker rm gf180`.
- **LibreLane exits non-zero on a PDK glob**: the container defaults
  to IHP-SG13G2. The flow needs `sak-pdk-script.sh gf180mcuD
  gf180mcu_fd_sc_mcu7t5v0` activation AND an explicit `--pdk-root`
  at the wafer-space fork.
- **`sak-pdk-script.sh: command not found`**: verify the container
  is the upstream `hpretl/iic-osic-tools:next` (or a pinned dated
  tag). The helper ships with the image.
- **Render PNG blank**: headless KLayout PNG export sometimes fights
  Qt. Open the GDS on the host:
  `klayout <project>/final/gds/chip_top.gds`.
- **Yosys post-synth check fails on `input_PAD2CORE[-1:0]`**: your
  slot has `NUM_INPUT_PADS = 0` and hit the zero-width-vector
  quirk. Set it to `1` in `slot_defines.svh` and list the dummy
  pad as `"inputs\\[0\\].pad"` in the PAD_SOUTH list.

## Cleanup

```bash
docker stop gf180
docker rm gf180
# Reclaim ~15 GB by purging the image
docker image rm hpretl/iic-osic-tools:next
# Wipe artifacts (choose which)
rm -rf ~/eda/designs/{counter_demo,chip_custom,chipathon_padring}
```
