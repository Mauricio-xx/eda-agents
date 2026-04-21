# IIC-OSIC-TOOLS Docker container — common setup for GF180MCU

This section applies to any flow (digital RTL-to-GDS, analog signoff)
that runs inside the `hpretl/iic-osic-tools` container. Read it once;
every more specific skill assumes the container is up and configured
per these rules.

## Image coordinates

- **Floating tag (latest):** `hpretl/iic-osic-tools:next`
- **Reproducible pin:** `hpretl/iic-osic-tools:2026.04.13` — audited
  end-to-end against the wafer-space `slot_1x1` GF180 template; 79
  LibreLane steps, DRC/LVS clean, no setup/hold violations. Use the
  pin when reproducibility matters more than bleeding-edge tool
  versions.
- Registry: Docker Hub.
- Compressed size: ~15 GB. Uncompressed: ~20 GB per image.
- Source branch: IIC-OSIC-TOOLS `next_release`.

Tool versions bundled at the pinned tag: LibreLane 3.0.2, Yosys 0.64,
OpenROAD (librelane build `dcf36133`), KLayout 0.30.7, Magic 8.3.635,
Netgen 1.5.318, ngspice-46 with OSDI, cocotb 2.0.1, iverilog, RISC-V
GNU toolchain 2026.04.05.

## Host prerequisites

Before the first `docker pull`:

- Docker Engine installed and the daemon running.
- At least 20 GB free disk on the partition that hosts Docker's
  image store.
- A dedicated host work directory, e.g. `~/eda/designs`, which will be
  bind-mounted into the container at `/foss/designs`. Create it once:

  ```bash
  mkdir -p ~/eda/designs
  ```

- **No PDK install on the host is needed.** The image ships GF180MCU
  (variant D only, 7T and 9T standard cells + `gf180mcu_fd_io` +
  `gf180mcu_fd_ip_sram`) under `/foss/pdks/gf180mcuD/`. Wafer-space
  custom I/O cells (`gf180mcu_ws_io__dvdd`, `gf180mcu_ws_io__dvss`)
  are **not** in upstream open_pdks; see Gotcha 3 below.

## Canonical `docker run`

Launch the container headless, bind-mount the host work dir, and keep
it alive for `docker exec`:

```bash
docker run -d --name gf180 \
    -v ~/eda/designs:/foss/designs:rw \
    --user $(id -u):$(id -g) \
    hpretl/iic-osic-tools:next \
    --skip sleep infinity
```

What each flag does:

- `-d` — detached / background.
- `--name gf180` — fixed name so subsequent commands can do
  `docker exec gf180 ...`. If you want multiple containers, pick
  distinct names.
- `-v ~/eda/designs:/foss/designs:rw` — host `~/eda/designs` visible
  inside the container as `/foss/designs`. Everything written there by
  the container lands on the host and survives container teardown.
- `--user $(id -u):$(id -g)` — run as the host user so files produced
  inside the container are owned by you, not root.
- `--skip sleep infinity` — bypass the container's interactive GUI
  entrypoint and just hold the container alive for scripted access.

Optional — X11 GUI access (KLayout / OpenROAD GUI):

```bash
xhost +local:docker                     # one-time, on the host

docker run -d --name gf180 \
    -e DISPLAY=$DISPLAY \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    -v ~/eda/designs:/foss/designs:rw \
    --user $(id -u):$(id -g) \
    hpretl/iic-osic-tools:next \
    --skip sleep infinity
```

## Volume / path convention

Anything your flow needs — RTL sources, config files, extracted
netlists, output GDS — must live under the bind-mounted directory so
both host and container can see it.

| Host path | Container path |
|-----------|----------------|
| `~/eda/designs/<project>/src/` | `/foss/designs/<project>/src/` |
| `~/eda/designs/<project>/librelane/` | `/foss/designs/<project>/librelane/` |
| `~/eda/designs/<project>/final/` | `/foss/designs/<project>/final/` |

When you issue `docker exec gf180 bash -lc 'cd /foss/designs/<project> && ...'`,
paths in logs will be container-side; the equivalent host path is
`~/eda/designs/<project>/...`.

## PDK switching — required for GF180

The image defaults to `PDK=ihp-sg13g2` and
`STD_CELL_LIBRARY=sg13g2_stdcell`. For GF180 flows, **every shell**
must source the PDK switch script before any tool invocation:

```bash
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
```

That sets `PDK`, `PDKPATH`, `STD_CELL_LIBRARY`, `SPICE_USERINIT_DIR`,
and `KLAYOUT_PATH` to the GF180 variant. Skipping this step is the
most common failure mode and produces confusing "file not found"
errors that reference `sg13g2_stdcell` paths — see Gotcha 1.

## Six known gotchas (symptoms and fixes)

### Gotcha 1 — default PDK is IHP, not GF180

**Symptom:**
```
_tkinter.TclError: no files matched glob pattern
"/foss/designs/.../libs.ref/sg13g2_stdcell/techlef/*__nom.tlef"
```

**Cause:** the Makefile uses `PDK ?= gf180mcuD` (conditional), so the
pre-set `PDK=ihp-sg13g2` env var wins. Any tool that reads `PDK` will
look for IHP files.

**Fix:** in the same shell, before running `make` or `librelane`:
```bash
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
```

### Gotcha 2 — macro / SRAM path errors after a shell reconnect

**Symptom:**
```
Path provided for ... is invalid:
'/foss/pdks/ihp-sg13g2/libs.ref/gf180mcu_fd_ip_sram/...' does not exist
```

**Cause:** `PDK_ROOT` or `PDK` got reset between shells (each
`docker exec` spawns a fresh shell).

**Fix:** always pass `PDK_ROOT` explicitly on the make line, and
source the PDK script inside every `bash -lc '...'`:
```bash
docker exec gf180 bash -lc '
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 &&
  make librelane SLOT=1x1 PDK=gf180mcuD \
      PDK_ROOT=/foss/designs/<project>/gf180mcu
'
```

### Gotcha 3 — unknown I/O cell `gf180mcu_ws_io__*`

**Cause:** the upstream open_pdks snapshot inside the image is missing
wafer-space custom I/O cells (`dvdd`, `dvss`).

**Fix:** clone the wafer-space fork *into your project directory* and
point `PDK_ROOT` at it:
```bash
docker exec gf180 bash -lc '
  cd /foss/designs/<project>
  git clone --depth 1 --branch 1.8.0 \
      https://github.com/wafer-space/gf180mcu.git gf180mcu
'
# Then pass PDK_ROOT=/foss/designs/<project>/gf180mcu on make.
```

### Gotcha 4 — the flow is slow

Magic DRC (~32 min) and KLayout antenna DRC (~7 min) dominate. During
iteration, skip them:
```bash
docker exec gf180 bash -lc '
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 &&
  cd /foss/designs/<project> &&
  make librelane-nodrc SLOT=1x1 PDK=gf180mcuD PDK_ROOT=...
'
```
Re-run with the full `make librelane` target before tapeout.

### Gotcha 5 — no GUI inside the container

The headless container has no display server. Either forward X11 (see
optional section above) or copy artefacts to the host and open them
there:
```bash
klayout ~/eda/designs/<project>/final/gds/chip_top.gds
```

### Gotcha 6 — stuck / stale container

```bash
docker ps -a                 # see all containers
docker stop gf180            # stop our named container
docker rm gf180              # remove it
docker image prune -a        # reclaim disk from old images
```

## Confirming the container is ready

Before driving a flow, sanity-check the container is up and the
volume mount is correct:

```bash
docker ps --filter name=gf180 --format '{{.Names}} {{.Status}}'
docker exec gf180 bash -lc 'ls /foss/designs'
```

If the second command does not list the content you created on the
host at `~/eda/designs`, the mount is wrong — stop, rm, and re-run
`docker run` with the correct `-v` path before anything else.
