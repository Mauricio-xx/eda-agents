# RTL-to-GDS walk-through: 4-bit counter

Hands-on companion material to the main deck, intended to be shared as
a stand-alone plus alongside the slides. Drives a 4-bit counter from
Verilog source to a manufacturable GDSII using LibreLane inside the
`hpretl/iic-osic-tools` Docker image.

**Stand-alone by design.** The RTL and LibreLane `config.yaml` are
embedded inline -- no dependency on the `eda-agents` Python package,
no clone of this repo required. Hand a user just the `.ipynb` (or the
`.py`) and they can run it from any folder on their machine. The only
prerequisites on the receiving side are Docker and Python 3.9+. All
state is written to a fixed host path (`~/eda/designs/`) in the user's
HOME, so the notebook's own location doesn't matter.

Two equivalent entry points:

- `rtl2gds_counter.ipynb` -- Jupyter notebook, 8 steps, flags to rehearse
  vs execute each step.
- `rtl2gds_counter.py` -- plain script with `input()` pauses, same 8
  steps. Use when you don't have Jupyter, or for headless demos.

## Quickstart

```bash
# 1. Place the file anywhere -- a download folder, /tmp, a fresh dir...
cd ~/Downloads        # or wherever you put rtl2gds_counter.ipynb

# 2. Make sure Docker works
docker ps

# 3a. Open the notebook
jupyter lab rtl2gds_counter.ipynb
#   - open the Step 0 cell, flip the RUN_* flags you want to execute,
#   - run cells top to bottom.

# 3b. Or run the script
python3 rtl2gds_counter.py            # pauses between steps
python3 rtl2gds_counter.py --no-pause # straight through
```

## Sharing it standalone

The notebook + script are designed to travel alone:

- Send the `.ipynb` (or `.py`) file by itself -- email, USB, download
  link, you name it. No repo clone needed on the receiving end.
- All the assets it needs -- the counter RTL and the LibreLane
  `config.yaml` -- are inlined as Python strings inside the file.
- State (`counter.v`, `config.yaml`, LibreLane runs, GDS) is written
  under `~/eda/designs/counter_demo/` on the user's HOME, not next to
  the notebook file. Putting the file in `~/Downloads` works exactly
  the same as putting it in `/tmp` or anywhere else.
- The only host-side prerequisites are Docker + Python 3.9+.

Both entry points start with all `RUN_*` flags set to `False`, so the
first pass just prints the commands. Flip the flags once you're ready
to commit to the ~15 GB image pull / the flow run.

## The 7 steps

1. **Pull** `hpretl/iic-osic-tools:next` (~15 GB, one time).
2. **Start** a headless container named `gf180`, bind-mounting
   `~/eda/designs` at `/foss/designs`.
3. **Write** the counter RTL inline into
   `~/eda/designs/counter_demo/counter.v`.
4. **Write** a minimal GF180 LibreLane `config.yaml` inline next to it.
5. **Run** `librelane config.yaml` via `docker exec` after activating
   the GF180 standard-cell library with `sak-pdk-script.sh`.
6. **Parse** `runs/demo/final/metrics.csv` -- die area, cell count,
   timing violations, DRC / LVS, total power.
7. **Display** the final-layout PNG that LibreLane auto-generates in
   `runs/demo/final/render/counter.png`.

Expected wall time for the counter run itself: 1-2 minutes on a modern
laptop. The full chip-level template in the main deck takes ~35-45 min.

### PDK note

The walk-through uses the GF180MCU PDK that `ciel` already installed
inside the image at `/foss/pdks/gf180mcuD/` -- no external clone. For
a bare block (no padring, no SRAM) this built-in PDK is sufficient.
The wafer-space GF180MCU fork (`wafer-space/gf180mcu`) only matters
when the design uses the padring I/O cells. The main deck walks that
through for the full-chip template.

## Paths

| What                            | Host                            | Container                       |
|---------------------------------|---------------------------------|---------------------------------|
| Workspace                       | `~/eda/designs`                 | `/foss/designs`                 |
| Project                         | `~/eda/designs/counter_demo`    | `/foss/designs/counter_demo`    |
| GF180MCU PDK                    | n/a (built into the image)      | `/foss/pdks/gf180mcuD`          |
| Final GDS                       | `<project>/runs/demo/final/gds/counter.gds` | same          |
| Metrics                         | `<project>/runs/demo/final/metrics.csv`     | same          |

## Troubleshooting

- **`docker: command not found`:** install Docker Engine and make sure
  your user is in the `docker` group.
- **`Error response from daemon: Conflict. The container name "/gf180"
  is already in use`:** you already have a container with that name.
  Either reuse it (everything runs inside via `docker exec`) or remove:
  `docker stop gf180 && docker rm gf180`.
- **LibreLane exits non-zero on a PDK glob:** the container's default
  PDK is IHP-SG13G2. The flow needs the `sak-pdk-script.sh gf180mcuD`
  activation AND an explicit `--pdk-root` at the wafer-space fork.
  Step 6 does both; if you tweak the script, preserve them.
- **`sak-pdk-script.sh: command not found`:** verify the container is
  the upstream `hpretl/iic-osic-tools:next` image (or a pinned dated
  tag like `2026.04.13`). The helper is part of the image -- not a
  custom script from this repo.
- **Step 8 PNG is blank:** headless KLayout PNG export sometimes fights
  with Qt. Open the GDS directly on the host instead:
  `klayout <project>/runs/demo/final/gds/counter.gds`.

## Cleanup

```bash
docker stop gf180
docker rm gf180
# Optional: reclaim ~15 GB by purging the image
docker image rm hpretl/iic-osic-tools:next
# Optional: wipe artifacts
rm -rf ~/eda/designs/counter_demo
```
