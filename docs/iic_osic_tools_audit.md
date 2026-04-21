# IIC-OSIC-TOOLS `next_release` Audit for GF180MCU RTL-to-GDS

**Date:** 2026-04-13
**Image:** `hpretl/iic-osic-tools:next` (Docker Hub, updated 2026-04-13)
**Source:** `next_release` branch inspected at `/tmp/iic-osic-tools-audit/`
**Purpose:** Evaluate whether IIC-OSIC-TOOLS can run LibreLane v3 GF180MCU full-chip flows,
and whether it can replace or complement our nix-shell-based local setup.

---

## Executive Summary

**Verdict: Fully functional for GF180MCU RTL-to-GDS with LibreLane v3.**

We ran the `wafer-space/gf180mcu-project-template` full-chip flow (slot_1x1:
3.9x5.1mm die with padring, SRAMs, I/O cells) end-to-end inside the container.
All 79 steps completed successfully (exit code 0, empty error log). DRC clean,
LVS clean, no setup/hold violations. Two minor configuration adjustments were needed:

1. Clone the wafer-space PDK fork (for custom I/O cells not in upstream open_pdks)
2. Switch the PDK environment from the default `ihp-sg13g2` to `gf180mcuD`

The image ships LibreLane 3.0.2, Yosys 0.64, OpenROAD (custom librelane build),
KLayout 0.30.7, Magic 8.3.635, and Netgen 1.5.318 -- all newer than our local
nix-shell versions.

---

## 1. Tool Version Comparison

| Tool | IIC-OSIC `next` | Local nix-shell (fazyrv) | Local host | Delta |
|------|-----------------|--------------------------|------------|-------|
| **LibreLane** | 3.0.2 (pip) | v3.0.0.dev45 (leo/gf180mcu) | v3.0.0rc0 (dev) | Stable release vs dev branch |
| **Yosys** | v0.64 | 0.54 | 0.43 | +10 minor versions |
| **OpenROAD** | dcf36133 (librelane) | 4534556345 | -- | Different commits; both functional |
| **KLayout** | v0.30.7 | 0.30.4 | 0.30.3 | +3 patches |
| **Magic** | 8.3.635 | 8.3.581 | 8.3.542 | +54 patches |
| **Netgen** | 1.5.318 | 1.5.287 | -- | +31 patches |
| **Verilator** | v5.046 | 5.038 | 5.031 | +8 patches |
| **iverilog** | 9b0d46b (commit) | -- | -- | Available in image |
| **Python** | 3.12.x (Ubuntu 24.04) | 3.12.10 | 3.12.3 | Same family |
| **ngspice** | ngspice-46 (OSDI) | -- | -- | Available in image |
| **cocotb** | 2.0.1 | 2.0.0 | -- | Patch bump |
| **RISCV toolchain** | 2026.04.05 | MISSING | -- | Image has it; nix doesn't |

**Source:** `/tmp/iic-osic-tools-audit/_build/tool_metadata.yml` and
`_build/images/iic-osic-tools/skel/headless/scripts/install_eda.sh`

---

## 2. GF180MCU PDK Handling

### Installation method
- **ciel** v2.4.0 (pip-installed, replaces volare)
- open_pdks commit: `7b70722e33c03fcb5dabcf4d479fb0822d9251c9`
- Command: `ciel enable "${OPEN_PDKS_REPO_COMMIT}" --pdk-family gf180mcu`
- Location: `/foss/pdks/gf180mcuD/`

### What's installed
- **gf180mcuD only** (A/B/C removed for image size)
- Standard cell libraries: `gf180mcu_fd_sc_mcu7t5v0` (7-track) AND `gf180mcu_fd_sc_mcu9t5v0` (9-track)
- I/O cells: `gf180mcu_fd_io` (standard efabless I/O)
- SRAM IP: `gf180mcu_fd_ip_sram` (including `sram512x8m8wm1`)
- ngspice models, Liberty, LEF, techlef, GDS -- all present

### What's NOT installed (upstream PDK limitation)
- `gf180mcu_ws_io__dvdd` and `gf180mcu_ws_io__dvss` -- custom wafer-space I/O cells
- These are required for the `gf180mcu-project-template` chip-top flow
- Workaround: clone the wafer-space PDK fork (see Section 5)

### Environment variables
```
PDK_ROOT=/foss/pdks
PDK=ihp-sg13g2          # DEFAULT -- must be overridden for GF180!
STD_CELL_LIBRARY=sg13g2_stdcell  # DEFAULT -- must be overridden
```

### PDK switching
```bash
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
# Sets: PDK, PDKPATH, STD_CELL_LIBRARY, SPICE_USERINIT_DIR, KLAYOUT_PATH
```

### PDK fixes applied by the image build
- Empty `.spiceinit` in ngspice dir (harmonized with SG13G2)
- Fixed xschem test schematic relative paths
- Fixed xschemrc model path to include `gf180mcuD/` prefix
- Fixed xschem symbol OP annotations (was sky130-specific, now correct `m0`)
- Replaced KLayout pymacros with gdsfactory v9 pcells (martinjankoehler fork)

---

## 3. LibreLane in the Image

### Version and installation
- **LibreLane 3.0.2** installed via `pip3 install librelane==3.0.2`
- CLI entrypoint: `/foss/tools/bin/librelane` (wrapper script)

### Wrapper script mechanism
File: `_build/images/iic-osic-tools/skel/headless/scripts/install_links.sh:41-46`
```bash
#!/bin/bash
export PATH=${TOOLS}/openroad-librelane/bin:${PATH}
exec -a "$0" /usr/local/bin/librelane --manual-pdk "$@"
```

This does three things:
1. Prepends `openroad-librelane/bin` to PATH so LibreLane finds the correct OpenROAD build
2. Passes `--manual-pdk` to disable PDK auto-detection (uses env vars)
3. Execs the real librelane installed by pip

### Two OpenROAD builds
| Build | Commit | Purpose | PATH priority |
|-------|--------|---------|---------------|
| `openroad` | `b7b01536` | Generic (ORFS, custom scripts) | Default in `$TOOLS/bin/` |
| `openroad-librelane` | `dcf36133` | LibreLane-specific | Activated by librelane wrapper |

The librelane build has a custom patch: `AUTO_TAPER_NDR_NETS = false` (controls
analog route tapering in detailed router).

### Symlink strategy
Binaries from `openroad-librelane/bin/*` get `-librelane` suffix symlinks
(e.g., `openroad-librelane`). The wrapper temporarily overrides PATH.

### Test coverage in image
- Test 01: LibreLane smoke test (sky130A)
- Test 04: LibreLane smoke test (gf180mcuD) -- `counter.json`, JSON v1/v2 format
- Test 07: LibreLane with VHDL (sky130A)
- Test 18: LibreLane (ihp-sg13g2)
- Test 19: LibreLane (ihp-sg13cmos5l)

---

## 4. Compatibility Assessment

### Version gaps that DON'T cause problems
- **Yosys v0.64 vs 0.54:** LibreLane abstracts yosys commands; synthesis completed
  successfully. No custom tcl scripts needed.
- **KLayout v0.30.7 vs 0.30.4:** DRC runsets, antenna check, density check, XOR --
  all ran without issues.
- **Magic 8.3.635 vs 8.3.581:** GDS streamout and DRC worked correctly.
- **LibreLane 3.0.2 vs 3.0.0.dev45:** YAML v3 config format with `meta.version: 3`
  and `flow: Chip` parsed and executed correctly. All config keys (PDN_VWIDTH,
  PDN_CORE_RING, MACROS, etc.) were recognized.

### Issues encountered during testing

#### Issue 1: Default PDK is ihp-sg13g2
The container profile sets `PDK=ihp-sg13g2` and `STD_CELL_LIBRARY=sg13g2_stdcell`.
The Makefile uses `PDK ?= gf180mcuD` (conditional assignment), so the env var wins.

**Symptom:**
```
_tkinter.TclError: no files matched glob pattern
"/foss/designs/template/gf180mcu/gf180mcuD/libs.ref/sg13g2_stdcell/techlef/*__nom.tlef"
```

**Fix:** Source the PDK switch script before running:
```bash
source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
make librelane SLOT=1x1 PDK=gf180mcuD PDK_ROOT=/path/to/pdk
```

#### Issue 2: wafer-space custom I/O cells not in upstream PDK
The `gf180mcu-project-template` uses `gf180mcu_ws_io__dvdd` and
`gf180mcu_ws_io__dvss` I/O cells that exist only in the wafer-space PDK fork.

**Symptom:** Path validation error for SRAM/macro GDS files pointing to wrong PDK.

**Fix:** Clone the wafer-space fork:
```bash
git clone --depth 1 --branch 1.8.0 \
  https://github.com/wafer-space/gf180mcu.git /path/to/gf180mcu
# Then pass PDK_ROOT=/path/to/gf180mcu to make
```

### What's missing from the image
| Item | Impact | Workaround |
|------|--------|------------|
| Nix | Cannot use fazyrv's flake.nix | Use Docker instead (that's the point) |
| Claude Code CLI | Cannot use cc_cli backend | Install separately if needed |
| wafer-space I/O cells | Chip-top padring fails | Clone wafer-space PDK fork |
| Default PDK = ihp-sg13g2 | Must explicitly switch | `source sak-pdk-script.sh gf180mcuD` |

---

## 5. End-to-End Validation: gf180mcu-project-template

### Test setup
```bash
# Pull image
docker pull hpretl/iic-osic-tools:next

# Launch container
docker run -d --name gf180-chip-test \
  -v /tmp/gf180-chip-test:/foss/designs:rw \
  --user $(id -u):$(id -g) \
  hpretl/iic-osic-tools:next \
  --skip sleep infinity

# Clone template + wafer-space PDK fork
docker exec gf180-chip-test bash -lc '
  cd /foss/designs
  git clone --depth 1 https://github.com/wafer-space/gf180mcu-project-template.git template
  git clone --depth 1 --branch 1.8.0 https://github.com/wafer-space/gf180mcu.git template/gf180mcu
'

# Run the full chip flow
docker exec gf180-chip-test bash -lc '
  cd /foss/designs/template
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
  make librelane SLOT=1x1 PDK=gf180mcuD PDK_ROOT=/foss/designs/template/gf180mcu
'
```

### Design under test
- **Template:** `wafer-space/gf180mcu-project-template` (main branch)
- **PDK:** `wafer-space/gf180mcu` tag 1.8.0 (fork with custom I/O cells)
- **Config:** LibreLane v3 YAML (`meta.version: 3`, `flow: Chip`)
- **Slot:** 1x1 (3932x5122 um die, 3048x4238 um core)
- **Design:** 42-bit counter (`chip_core.sv`) with padring, 2x SRAM512x8, ID/logo macros
- **Clock:** 40ns (25 MHz) on `clk_PAD`
- **Standard cells:** `gf180mcu_fd_sc_mcu7t5v0` (7-track, 5V0)

### Flow execution results

| Step | Tool | Description | Status | Notes |
|------|------|-------------|--------|-------|
| 06 | Yosys v0.64 | Synthesis | PASS | |
| 13 | OpenROAD | Floorplan | PASS | |
| 16 | ODB | Set power connections | PASS | |
| 17 | OpenROAD | Padring generation | PASS | Uses gf180mcu_ws_io cells |
| 18 | ODB | Manual macro placement | PASS | 2x SRAM + ID + logo |
| 19 | OpenROAD | Cut rows | PASS | |
| 20 | OpenROAD | Tapcell insertion | PASS | |
| 22 | OpenROAD | PDN generation | PASS | Custom pdn_cfg.tcl with SRAM grids |
| 25 | OpenROAD | Global placement | PASS | ~600 iterations to converge |
| 27 | OpenROAD | GP resizing | PASS | |
| 31 | OpenROAD | Repair design | PASS | |
| 33 | OpenROAD | Detailed placement | PASS | |
| 34 | OpenROAD | CTS | PASS | clkbuf_8 selected |
| 36 | OpenROAD | Post-CTS resizing | PASS | |
| 38 | OpenROAD | Global routing | PASS | |
| 41 | OpenROAD | Antenna repair (diode insertion) | PASS | |
| 43 | OpenROAD | Detailed routing | PASS | 0 DRC violations |
| 47 | ODB | Disconnected pins report | PASS | |
| 51 | OpenROAD | Filler insertion | PASS | 166,076 fillers |
| 53 | OpenROAD | RCX parasitic extraction | PASS | 3 corners (nom/min/max) |
| 54 | OpenROAD | STA post-PnR | PASS | 9 timing corners |
| 55 | OpenROAD | IR Drop report | PASS | ~5 min (3.1GB RSS) |
| 56 | Magic 8.3.635 | GDS streamout | PASS | |
| 57 | KLayout v0.30.7 | GDS streamout | PASS | All custom GDS merged |
| 58 | KLayout | Layout render | PASS | |
| 59 | KLayout | XOR (Magic vs KLayout) | PASS | **0 differences** |
| 60 | Checker | XOR | PASS | Clear |
| 61 | KLayout | Antenna DRC (deep mode) | PASS | ~7 min |
| 62 | Checker | KLayout antenna | PASS | **0 antenna errors** |
| 63 | KLayout | Sealring | PASS | |
| 64 | KLayout | Filler (metal density) | PASS | |
| 65 | KLayout | Density check | PASS | |
| 66 | Checker | KLayout density | PASS | **0 density errors** |
| 67 | Magic 8.3.635 | DRC (flatten) | PASS | **0 DRC errors** (~32 min) |
| 68 | KLayout v0.30.7 | DRC | PASS | **0 DRC errors** |
| 69 | Checker | Magic DRC | PASS | Clear |
| 70 | Checker | KLayout DRC | PASS | Clear |
| 71 | Magic | SPICE extraction | PASS | |
| 72 | Checker | Illegal overlap | PASS | **0 overlaps** |
| 73 | Netgen 1.5.318 | LVS | PASS | **LVS clean** |
| 74 | Checker | LVS | PASS | Clear |
| 75 | Checker | Setup violations | PASS | **0 violations** |
| 76 | Checker | Hold violations | PASS | **0 violations** |
| 77 | Checker | Max slew violations | WARN | Slew violations in ss_125C corners |
| 78 | Checker | Max cap violations | WARN | Cap violations in all corners |
| 79 | Misc | Report manufacturability | PASS | Final report generated |

### Signoff summary
- **KLayout XOR:** 0 differences (Magic and KLayout GDS match)
- **KLayout Antenna:** 0 errors
- **KLayout Density:** 0 errors
- **KLayout DRC:** 0 errors
- **Magic DRC:** 0 errors
- **Magic Illegal Overlap:** 0 errors
- **Netgen LVS:** Clean (0 errors)
- **Setup violations:** 0
- **Hold violations:** 0
- **Max slew:** Warnings in slow corners (ss_125C) -- not blocking
- **Max cap:** Warnings in all corners -- not blocking
- **Error log:** Empty (no fatal errors)

### Final outputs (saved to `final/`)
```
def/         gds/         json_h/      klayout_gds/  lib/
mag/         mag_gds/     metrics.csv  metrics.json  nl/
odb/         pnl/         render/      sdc/          sdf/
spef/        spice/       vh/
```

### Performance
- **Total flow time:** 79 steps in ~38 minutes (container, single-socket host)
- Slowest steps: Magic DRC (~32 min), KLayout antenna DRC (~7 min), IR Drop (~5 min)
- Memory peak: ~3.1 GB (IR Drop analysis)
- **Exit code: 0**

---

## 6. Integration with eda-agents

### Current state
- `DockerToolEnvironment` in `src/eda_agents/core/tool_environment.py` is a Phase 7
  placeholder (`NotImplementedError`)
- All current flows use `LocalToolEnvironment` (direct subprocess on host)

### Integration path for Phase 7
```python
# Proposed docker exec pattern for eda-agents
docker exec <container> bash -lc '
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
  cd /foss/designs/<project>
  librelane config.yaml --to <stage> --pdk gf180mcuD --pdk-root <pdk_root>
'
```

### What the image provides
- Full EDA tool chain (no nix needed)
- `--skip` entrypoint for headless/scripted use
- Design directory mount at `/foss/designs`
- `sak-pdk-script.sh` for PDK switching
- `librelane` wrapper with correct OpenROAD PATH

### What the image does NOT provide
- No Claude Code CLI or AI/LLM tooling
- No nix (fazyrv-hachure's nix-shell approach won't work)
- No REST API or RPC entrypoint (must use `docker exec`)
- RISCV GNU toolchain IS included (2026.04.05) -- advantage over our nix-shell

### Recommended DockerToolEnvironment implementation
```python
class DockerToolEnvironment(ToolEnvironment):
    def __init__(self, image="hpretl/iic-osic-tools:next", pdk="gf180mcuD"):
        self.container_name = f"eda-agents-{pdk}-{uuid4().hex[:8]}"
        self.pdk = pdk
        self.image = image

    def start(self):
        # docker run -d --skip sleep infinity
        # docker exec: source sak-pdk-script.sh <pdk>
        pass

    def run(self, cmd, cwd=None, timeout=1800):
        # docker exec <container> bash -lc '<cmd>'
        pass

    def stop(self):
        # docker stop + docker rm
        pass
```

---

## 7. Comparison: Docker Image vs Nix-shell

| Aspect | IIC-OSIC-TOOLS (Docker) | Nix-shell (fazyrv) |
|--------|------------------------|--------------------|
| **Isolation** | Docker container | nix-shell / flake |
| **PDK management** | ciel + manual clone | Project-level `make clone-pdk` |
| **Tool versions** | All newer (Yosys 0.64, etc.) | Pinned by flake.lock |
| **Per-project pinning** | One version per image tag | Per-project via flake.lock |
| **RISCV toolchain** | Included | MISSING |
| **Reproducibility** | Image tag + tool_metadata.yml | flake.lock (content-addressed) |
| **Disk overhead** | ~20 GB per image | ~5 GB per devshell |
| **Startup time** | Container startup (~2s) | nix-shell (~10s first time) |
| **Multi-PDK** | sky130A, gf180mcuD, ihp-sg13g2 | One PDK per project |
| **AI integration** | None bundled | Claude Code CLI available |

### When to use which
- **IIC-OSIC-TOOLS:** CI/CD, quick experiments, users without nix, multi-PDK work
- **Nix-shell:** Per-project version pinning, FOSSI cache, development workflow
- **Both together:** Docker for CI, nix for local dev (same LibreLane config works in both)

---

## 8. Recommendations

### Immediate use
The image is ready for GF180MCU RTL-to-GDS with the wafer-space project template.
Recipe:

```bash
docker pull hpretl/iic-osic-tools:next
docker run -d --name gf180 \
  -v ~/eda/designs:/foss/designs:rw \
  --user $(id -u):$(id -g) \
  hpretl/iic-osic-tools:next --skip sleep infinity

docker exec gf180 bash -lc '
  cd /foss/designs
  git clone --depth 1 https://github.com/wafer-space/gf180mcu-project-template.git myproject
  cd myproject && make clone-pdk
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0
  make librelane SLOT=1x1 PDK=gf180mcuD PDK_ROOT=$(pwd)/gf180mcu
'
```

### For eda-agents Phase 7
1. Implement `DockerToolEnvironment.run()` using `docker exec`
2. Add `sak-pdk-script.sh gf180mcuD` as pre-command for all GF180 invocations
3. Mount design directory at `/foss/designs`
4. Parse LibreLane output from mounted filesystem

### Upstream improvements (nice-to-have)
- Add wafer-space I/O cells to upstream open_pdks/ciel (eliminates need for fork clone)
- Add YAML v3 config smoke test for gf180mcuD (current test 04 uses JSON v1/v2)
- Default PDK auto-detection from config file (avoid needing `sak-pdk-script.sh`)

---

## 9. Key Files Reference

### In IIC-OSIC-TOOLS repo (`_build/`)
| File | Purpose |
|------|---------|
| `tool_metadata.yml` | Central version registry (all tool commits/tags) |
| `images/openroad-librelane/Dockerfile` | LibreLane-specific OpenROAD build |
| `images/openroad-librelane/scripts/install.sh` | OpenROAD build (AUTO_TAPER patch) |
| `images/open_pdks/scripts/install_ciel.sh` | PDK installation via ciel |
| `images/iic-osic-tools/skel/headless/scripts/install_eda.sh` | pip packages (librelane==3.0.2) |
| `images/iic-osic-tools/skel/headless/scripts/install_links.sh` | librelane wrapper script |
| `images/base/skel/etc/profile.d/iic-osic-tools-setup.sh` | Environment setup |
| `images/iic-osic-tools/skel/foss/tools/sak/sak-pdk-script.sh` | PDK switching |

### In wafer-space template
| File | Purpose |
|------|---------|
| `flake.nix` | Pins LibreLane 3.0.0 for nix-shell |
| `Makefile` | Build targets with PDK_ROOT/PDK vars |
| `librelane/config.yaml` | LibreLane v3 chip-top config |
| `librelane/slots/slot_1x1.yaml` | Die/core area + pad placement |
| `librelane/pdn_cfg.tcl` | Custom PDN with SRAM macro grids |

---

## 10. Artifacts

- Audit source clone: `/tmp/iic-osic-tools-audit/` (next_release branch)
- Template clone: `/tmp/gf180-template-audit/` (for reference)
- Docker container: `gf180-chip-test` (running, with completed flow)
- Flow run: `/foss/designs/template/librelane/runs/RUN_2026-04-13_20-59-02/`
- This report: `/tmp/iic_osic_tools_audit.md`

---

## 11. Codified in eda-agents

The findings in sections 2 through 6 are now exposed as MCP-callable
skills:

- `flow.rtl2gds_gf180_docker` — RTL-to-GDS via the `hpretl/iic-osic-tools`
  container, including PDK switching, wafer-space fork clone, and the
  six gotchas from sections 2 and 4.
- `flow.analog_signoff_gf180_docker` — KLayout DRC and Magic+Netgen LVS
  inside the same container, with the rationale for preferring Magic+Netgen
  LVS on GF180 analog cells.

Subagents that drive these skills: `gf180-docker-digital` and
`gf180-docker-analog` (ship in both `.claude/agents/` and `.opencode/agent/`).

When the image tag or the wafer-space PDK fork moves, re-run the audit in a
scratch container and bump both the `:2026.04.13` pin in the skill bodies and
the version table in section 1 of this document.
