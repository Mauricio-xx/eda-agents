# GF180MCU RTL-to-GDS inside IIC-OSIC-TOOLS

This skill assumes the container is up per the common section. It
takes a digital design from RTL to a signed-off GDS using LibreLane
v3 and the wafer-space `gf180mcu-project-template`.

## Reference design and template

The canonical bootstrap is the upstream `wafer-space/gf180mcu-project-template`:

- Source: https://github.com/wafer-space/gf180mcu-project-template
- Die: 3932 x 5122 um (slot_1x1) with padring.
- Core area: 3048 x 4238 um.
- Clock: 40 ns period (25 MHz) on `clk_PAD`.
- Standard cells: `gf180mcu_fd_sc_mcu7t5v0` (7-track, 5V0).
- Reference logic: 42-bit counter in `src/chip_core.sv`, plus 2x
  SRAM512x8, ID/logo macros. Swap `chip_core.sv` for your own RTL
  without changing the chip-level padring.

You can start from this template verbatim for quick smoke tests, or
clone it once and then replace the core logic.

## Project layout required by LibreLane

```
<project>/
  src/
    chip_top.sv           # top-level (padring + core instance)
    chip_core.sv          # user RTL — replace this
    <other .sv / .v>
  librelane/
    config.yaml           # design-wide: VERILOG_FILES, clock, MACROS, PDN
    slots/slot_1x1.yaml   # floorplan: DIE_AREA, CORE_AREA, pad order
    pdn_cfg.tcl           # custom PDN straps for SRAMs and voltage domains
    chip_top.sdc          # clock constraints
  tb/                     # optional cocotb testbench
  Makefile                # provided by the template (clone-pdk, librelane, ...)
```

When authoring a new design from scratch, either:

1. Clone the template and replace `src/chip_core.sv` (fast, retains
   padring / constraints), or
2. Author `src/`, `librelane/config.yaml`, and
   `librelane/slots/slot_1x1.yaml` from scratch using the template as
   a structural reference.

## Bootstrap — first run only

From the host, once the container is up:

```bash
# Scaffold the work tree inside the bind mount
docker exec gf180 bash -lc '
  cd /foss/designs
  git clone --depth 1 https://github.com/wafer-space/gf180mcu-project-template.git template
  cd template
  git clone --depth 1 --branch 1.8.0 https://github.com/wafer-space/gf180mcu.git gf180mcu
'
```

The second clone pulls the wafer-space PDK fork with the custom I/O
cells (see Gotcha 3 in the common section).

If you are replacing the RTL, drop your Verilog under
`~/eda/designs/template/src/` and adjust
`~/eda/designs/template/librelane/config.yaml` to reference the new
files (update `VERILOG_FILES`, `DESIGN_NAME`, clock port if renamed).

## config.yaml essentials

The fields that matter for a fresh design (all are strings or numbers
in LibreLane v3 YAML):

```yaml
meta:
  version: 3
  flow: Chip

DESIGN_NAME: chip_top
VERILOG_FILES:
  - dir::../src/chip_top.sv
  - dir::../src/chip_core.sv

VDD_NETS: [VDD]
GND_NETS: [VSS]

CLOCK_PORT: clk_PAD
CLOCK_NET: clk_pad/Y
CLOCK_PERIOD: 40   # ns — 25 MHz

PDN_VWIDTH: 5
PDN_HWIDTH: 5
PDN_VPITCH: 75
PDN_HPITCH: 75
PDN_CORE_RING: true
PDN_CORE_RING_VWIDTH: 25
PDN_CORE_RING_HWIDTH: 25
PDN_CFG: dir::pdn_cfg.tcl
```

Leave PDN numbers at their template defaults unless you have a
concrete reason to change them — they come from the audited
slot_1x1 configuration.

## slot_1x1.yaml essentials

```yaml
FP_SIZING: absolute
DIE_AREA: [0, 0, 3932, 5122]
CORE_AREA: [442, 442, 3490, 4680]
PAD_SOUTH: [clk_pad, rst_n_pad, ...]
PAD_EAST:  [...]
PAD_NORTH: [...]
PAD_WEST:  [...]
```

## Optional — cocotb testbench before hardening

If the design is non-trivial, compose the
`digital.cocotb_testbench` skill to author a testbench that simulates
against RTL, post-synthesis gate-level, and post-PnR gate-level with
SDF. Pull that skill via `render_skill(name="digital.cocotb_testbench")`
and write the resulting `tb/test_<design>.py` + `tb/Makefile` on the
host. Run cocotb locally with `make -C tb` before spending a full
LibreLane run on a broken design.

## Run the full flow

```bash
docker exec gf180 bash -lc '
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 &&
  cd /foss/designs/template &&
  make librelane SLOT=1x1 PDK=gf180mcuD \
      PDK_ROOT=/foss/designs/template/gf180mcu
'
```

Expected runtime: 35 to 45 minutes on a modern laptop for the
reference design. Substantially longer if the RTL is larger or
utilization is pushed.

## Fast iteration loop (DRC skipped)

During RTL iteration you usually do not need the full signoff DRC on
every run. Use:

```bash
docker exec gf180 bash -lc '
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 &&
  cd /foss/designs/template &&
  make librelane-nodrc SLOT=1x1 PDK=gf180mcuD \
      PDK_ROOT=/foss/designs/template/gf180mcu
'
```

Re-run the full `make librelane` target (the signoff form) before
tapeout — it is the one that produces the audited, clean artefacts.

## Outputs and how to interpret them

LibreLane writes a timestamped run directory plus a set of final
artefacts:

- **Run dir:** `/foss/designs/template/librelane/runs/RUN_<timestamp>/`
  — per-step logs, intermediate DEF/GDS, metrics per step.
- **Final artefacts:** `/foss/designs/template/final/` — promoted at
  the end of a successful flow.

Key files to inspect after a clean run:

| Artefact | Container path | Meaning |
|----------|----------------|---------|
| GDS | `final/gds/chip_top.gds` | The manufacturable layout. |
| Merged LEF | `final/lef/chip_top.lef` | Abstract view for upper-level integration. |
| SDC | `final/sdc/chip_top.sdc` | Applied clock / IO constraints. |
| SDF | `final/sdf/chip_top.sdf` | Post-PnR timing, load into cocotb gate-level. |
| SPEF | `final/spef/chip_top.spef` | Parasitics, for static timing sign-off. |
| Metrics | `final/metrics.csv` | One-line summary: cell count, WNS, TNS, DRC / LVS counts. |
| Render | `final/render/chip_top.png` | Visual sanity check. |

On the host the same files live under
`~/eda/designs/template/final/...`.

## Acceptance test

A flow pass means all of:

1. Exit code 0 from `make librelane`.
2. `final/gds/chip_top.gds` exists and is non-empty.
3. `final/metrics.csv` shows `drc_violations=0`, `lvs_errors=0`,
   `setup_violations=0`, `hold_violations=0`.
4. `final/render/chip_top.png` is generated (quick visual check that
   the core area is populated and the padring is intact).

Anything less is a failed flow — report the offending metric value
and the step that produced it (parse the last `runs/RUN_.../step_*/`
log before the promotion).

## When something fails — composition hint

- **DRC violations:** pull the report and compose
  `render_skill(name="flow.drc_checker")` to categorize them, then
  `render_skill(name="flow.drc_fixer")` to propose config-level
  patches (PDN width, density, halo, antenna iterations).
- **LVS mismatch:** same skills apply, but check for the two most
  common GF180 causes — missing substrate contacts and pad-cell
  renaming between upstream and wafer-space I/O.
- **Timing violations:** inspect the STA report under
  `runs/RUN_.../.../sta/` and consider relaxing `CLOCK_PERIOD`,
  reducing `PL_TARGET_DENSITY_PCT`, or increasing
  `GRT_ANT_ITERS`.

These are the prompts; do not reimplement the categorization logic
in the user-facing subagent.
