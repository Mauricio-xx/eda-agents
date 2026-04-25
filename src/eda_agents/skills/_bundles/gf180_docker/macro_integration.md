# LibreLane macro integration on GF180MCU (Chip flow)

This skill teaches an agent how to integrate **pre-hardened macros**
into a `flow: Chip` LibreLane v3 design on GF180MCU. It assumes the
container is up per the common section, and that you have one or more
macros already hardened via the Classic flow with `--save-views-to`.

Five chip-flow integration pitfalls are baked in below — each one was
paid for in real iteration time on the chipathon multi-macro example
(`Mauricio-xx/chipathon-gf180mcu-librelane-examples` example 04, tip
`de63a84`). Re-deriving them costs hours per pitfall; quoting the
file:line of this skill is faster than redebugging.

## When to compose this skill

Compose this skill when **all** of:

- `meta.flow: Chip` (chip-top with padring), AND
- One or more user macros consumed via `MACROS:` (i.e. macros you
  hardened, not just stock `chip_id` + `logo` from the template).

Examples: CPU + accelerator on a padring; counter + ALU in chipathon
example 04; SRAM + opamp + SAR DAC in a mixed-signal design.

If the design is bare-block (single Classic flow, no padring) →
`flow.rtl2gds_gf180_docker` covers it.

If the design is pure padring with stock macros only → that same skill
handles it as well; do not pull this one (the MACROS structure is
already correct in the wafer-space template).

## Pre-flight: harden each macro via the Classic flow

Each macro needs a hardened view set BEFORE the chip-top can integrate
it. Run the Classic flow with `--save-views-to <dir>`:

```bash
docker exec gf180 bash -lc '
  source sak-pdk-script.sh gf180mcuD gf180mcu_fd_sc_mcu7t5v0 &&
  cd /foss/designs/<project>/macros/<macro_name> &&
  librelane config.yaml --save-views-to /foss/designs/<project>/build/<macro_name>
'
```

`--save-views-to <dir>` writes the views directly under `<dir>/`
(PITFALL #2). Layout:

```
build/<macro_name>/
  gds/<macro_name>.gds                           # layout
  lef/<macro_name>.lef                           # abstract for chip-top
  nl/<macro_name>.nl.v                           # logical netlist (no VDD/VSS)
  pnl/<macro_name>.pnl.v                         # powered netlist (with VDD/VSS)
  vh/<macro_name>.vh                             # blackbox header
  lib/nom_tt_025C_5v00/<macro_name>__nom_tt_025C_5v00.lib
  lib/nom_ss_125C_4v50/<macro_name>__nom_ss_125C_4v50.lib
  lib/nom_ff_n40C_5v50/<macro_name>__nom_ff_n40C_5v50.lib
  lib/min_tt_025C_5v00/<macro_name>__min_tt_025C_5v00.lib
  lib/min_ss_125C_4v50/<macro_name>__min_ss_125C_4v50.lib
  lib/min_ff_n40C_5v50/<macro_name>__min_ff_n40C_5v50.lib
  lib/max_tt_025C_5v00/<macro_name>__max_tt_025C_5v00.lib
  lib/max_ss_125C_4v50/<macro_name>__max_ss_125C_4v50.lib
  lib/max_ff_n40C_5v50/<macro_name>__max_ff_n40C_5v50.lib
  spef/{nom,min,max}/<macro_name>.{nom,min,max}.spef
  metrics.csv
  final/metrics.csv
```

Note the lib filename pattern: folder uses single underscore between
nom/min/max and the corner; filename uses double underscore between
the macro name and the corner key. Mismatching either separator
produces "file does not exist" at config validation time.

## Chip-top `config.yaml` additions

Append to the chip-top `librelane/config.yaml` (do not rewrite the
bare-block fields — extend them):

```yaml
VERILOG_FILES:                                   # PITFALL #4: macros OUT
  - dir::../src/chip_top.sv
  - dir::../src/chip_core.sv
  # NO macros/<name>/rtl/<name>.sv here. The hardened .nl.v under
  # MACROS.<name>.vh serves as the blackbox. Re-listing the RTL re-
  # synthesises the macro into stdcells inside chip_top.

MACROS:
  counter:                                       # name as instantiated in RTL
    gds: ["/foss/designs/<project>/build/counter/gds/counter.gds"]
    lef: ["/foss/designs/<project>/build/counter/lef/counter.lef"]
    vh:  ["/foss/designs/<project>/build/counter/nl/counter.nl.v"]
    lib:                                         # PITFALL #1: 9 keys, NOT *
      nom_tt_025C_5v00: ["/foss/designs/<project>/build/counter/lib/nom_tt_025C_5v00/counter__nom_tt_025C_5v00.lib"]
      nom_ss_125C_4v50: ["/foss/designs/<project>/build/counter/lib/nom_ss_125C_4v50/counter__nom_ss_125C_4v50.lib"]
      nom_ff_n40C_5v50: ["/foss/designs/<project>/build/counter/lib/nom_ff_n40C_5v50/counter__nom_ff_n40C_5v50.lib"]
      min_tt_025C_5v00: ["/foss/designs/<project>/build/counter/lib/min_tt_025C_5v00/counter__min_tt_025C_5v00.lib"]
      min_ss_125C_4v50: ["/foss/designs/<project>/build/counter/lib/min_ss_125C_4v50/counter__min_ss_125C_4v50.lib"]
      min_ff_n40C_5v50: ["/foss/designs/<project>/build/counter/lib/min_ff_n40C_5v50/counter__min_ff_n40C_5v50.lib"]
      max_tt_025C_5v00: ["/foss/designs/<project>/build/counter/lib/max_tt_025C_5v00/counter__max_tt_025C_5v00.lib"]
      max_ss_125C_4v50: ["/foss/designs/<project>/build/counter/lib/max_ss_125C_4v50/counter__max_ss_125C_4v50.lib"]
      max_ff_n40C_5v50: ["/foss/designs/<project>/build/counter/lib/max_ff_n40C_5v50/counter__max_ff_n40C_5v50.lib"]
    instances:
      "i_chip_core.u_counter":                   # full hierarchical path
        location: [1000, 1000]                   # um, relative to core
        orientation: N                           # N/S/E/W/FN/FE/FS/FW

PDN_MACRO_CONNECTIONS:                           # PITFALL #3: List[str]
  - ".*u_counter.* VDD VSS VDD VSS"              # regex vdd vss vdd_pin vss_pin
  - ".*u_alu.*     VDD VSS VDD VSS"
```

The 9 corners can be generated with a small Python helper to avoid
hand-typing:

```python
CORNERS = [
    'nom_tt_025C_5v00', 'nom_ss_125C_4v50', 'nom_ff_n40C_5v50',
    'min_tt_025C_5v00', 'min_ss_125C_4v50', 'min_ff_n40C_5v50',
    'max_tt_025C_5v00', 'max_ss_125C_4v50', 'max_ff_n40C_5v50',
]
build = Path('/foss/designs/<project>/build')
def macro_entry(name: str, inst: str, xy: list[int]) -> dict:
    base = build / name
    return {
        'gds': [str(base / 'gds' / f'{name}.gds')],
        'lef': [str(base / 'lef' / f'{name}.lef')],
        'vh':  [str(base / 'nl'  / f'{name}.nl.v')],
        'lib': {c: [str(base / 'lib' / c / f'{name}__{c}.lib')] for c in CORNERS},
        'instances': {f'i_chip_core.{inst}': {
            'location': xy, 'orientation': 'N',
        }},
    }
```

## The five Chip-flow + macro pitfalls

### PITFALL #1 — `MACROS.<name>.lib` needs the 9 GF180 corner keys

**Symptom (silent):** chip-top finishes "clean" (`metrics.csv` shows
zero violations) but the STA report only references `tt_025C_5v00`.
You signed off against one corner; the other 8 were never analyzed.

```yaml
# WRONG — silently collapses multi-corner STA to single corner:
lib:
  "*": dir::../build/counter/lib/nom_tt_025C_5v00/counter__nom_tt_025C_5v00.lib
```

**Cause:** the lib map in v3.0.2 schema requires per-corner keys; the
`*` wildcard does not expand to all corners. No warning is emitted.

**Fix:** spell out all 9 corners explicitly:
`{nom,min,max} × {tt_025C_5v00, ss_125C_4v50, ff_n40C_5v50}`. Cross-
check that the STA step ran 9 corners (see "Verification" below).

### PITFALL #2 — `--save-views-to <dir>` writes directly under `<dir>/`

**Symptom:**
```
ConfigError: MACROS.counter.lib.nom_tt_025C_5v00 file
'../build/counter/final/lib/...' does not exist
```

**Cause:** the chip-top config points at `<dir>/final/lib/...` because
that's the layout of the LibreLane run directory (`runs/RUN_*/final/`)
— but `--save-views-to <dir>` places views directly in
`<dir>/{gds,lef,nl,lib/<corner>}/` with no `final/` wrapper. There is
also a `<dir>/final/metrics.csv` for per-macro signoff but the views
the chip-top consumes live at the top of `<dir>/`.

**Fix:** drop `final/` from any path that comes from a
`--save-views-to` output.

### PITFALL #3 — `PDN_MACRO_CONNECTIONS` is `List[str]`, not `Dict`

**Symptom:**
```
ConfigError: Refusing to automatically convert value at
'PDN_MACRO_CONNECTIONS[0]' to a string.
```

**Cause:** writing the connections as a dict is rejected by the
v3.0.2 schema. The OpenROAD `pdngen` script wants string lines.

```yaml
# WRONG — dict is rejected outright:
PDN_MACRO_CONNECTIONS:
  - {instance: u_counter, power: VDD, ground: VSS}
```

**Fix:** one string per macro instance, format
`"<instance_regex> <vdd_net> <vss_net> <vdd_pin> <vss_pin>"`. Use the
regex (e.g. `.*u_counter.*`) to cover hierarchical instance paths and
multiple instances at once:

```yaml
PDN_MACRO_CONNECTIONS:
  - ".*u_counter.* VDD VSS VDD VSS"
  - ".*u_alu.*     VDD VSS VDD VSS"
```

### PITFALL #4 — keep macro RTL OUT of `VERILOG_FILES`

**Symptom:** chip-top synthesises 84 000+ instances when you expected
~30 000 — yosys re-synthesised the macro RTL into raw stdcells instead
of treating it as a blackbox. The layout shows scattered stdcells
where the hardened macro should be a solid block.

**Cause:** if `VERILOG_FILES` lists the macro's RTL alongside chip_top
RTL, yosys flattens the macro into stdcells. The `MACROS:` block then
also tries to place the hardened macro on top, producing either a
double-instance error or a working flow with completely wrong area.

**Fix:** in `VERILOG_FILES`, list ONLY the chip_top.sv +
chip_core.sv (or whichever hierarchy is above the macros). The macro
is wired in via `MACROS.<name>.vh` (which is the post-synth `.nl.v` or
the dedicated `.vh` file); yosys treats that as a blackbox during
chip-top synthesis.

### PITFALL #5 — drop parameter overrides on macro instantiations

**Symptom:**
```
%Error: chip_core_multi.sv:42:3: Parameter not found: 'WIDTH'
chip_core_multi.sv:42:3:    counter #(.WIDTH(8)) u_counter (...);
```
Reported by Verilator lint inside LibreLane chip-flow; the equivalent
yosys error reads `PINNOTFOUND`.

**Cause:** parameter overrides like `counter #(.WIDTH(8))` live in the
RTL of the macro and are baked into the netlist at synthesis time.
The hardened `counter.nl.v` has no `parameter` block; the chip sees a
parameterless module declaration and rejects the override.

**Fix:** in the chip's RTL (e.g. `chip_core_multi.sv`), instantiate
without parameter overrides:

```verilog
// WRONG — counter.nl.v has no parameters after synthesis.
counter #(.WIDTH(8)) u_counter (.clk(clk), .rst_n(rst_n), ...);

// RIGHT — bake WIDTH at macro hardening time, not chip-top time.
counter u_counter (.clk(clk), .rst_n(rst_n), ...);
```

If a macro must be configurable, harden multiple variants
(`counter_w8`, `counter_w16`, ...) or pass the parameter at the
macro's own LibreLane config (e.g. via a yosys `synth_args` setting).

## Verification beyond `metrics.csv`

`metrics.csv` is necessary but NOT sufficient — at least PITFALL #1
lets the flow finish "clean" while silently signing off against one
STA corner. Cross-check three things on every chip-top run:

### 1. Confirm the actual STA corner count

```bash
docker exec gf180 bash -lc '
  ls /foss/designs/<project>/runs/RUN_*/openroad-staposrouting/ 2>/dev/null \
    | grep -E "_(min|max|nom)_" | wc -l
'
```

Expected: **9** directories (one per corner). If you see 1 or 3, the
corner expansion was silently dropped — almost certainly PITFALL #1.

### 2. Run post-synthesis GL sim before chip-top

Compose `digital.cocotb_testbench` and substitute the macro's `.nl.v`
into `VERILOG_SOURCES`. Catches synthesis-vs-RTL drift before the
chip-top flow eats a 60-90 minute budget on broken macros:

```make
test-counter-gl:
	$(MAKE) -f Makefile.cocotb \
	    TOPLEVEL=counter MODULE=test_counter \
	    COMPILE_ARGS="-g2012 -DFUNCTIONAL -DUNIT_DELAY=\#1" \
	    VERILOG_SOURCES="tb/timescale.v $(PDK_VLOG)/primitives.v \
	                     $(PDK_VLOG)/gf180mcu_fd_sc_mcu7t5v0.v \
	                     ../build/counter/nl/counter.nl.v" \
	    SIM_BUILD=sim_build_counter_gl
```

`PDK_VLOG` resolves to
`/foss/designs/<project>/gf180mcu/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog`.
The `tb/timescale.v` and `Timer(1ns)` post-RisingEdge requirements
are documented in the `digital.cocotb_testbench` skill — pull that one
when authoring GL TBs.

### 3. Sanity-check chip-top instance count

```bash
awk -F, '/design__instance__count/{print $2}' \
    ~/eda/designs/<project>/final/metrics.csv
```

If a macro of ~500 instances disappeared into 50 000 stdcells, you
hit PITFALL #4.

## Workspace isolation when iterating against a baseline

If a baseline chip-top is already validated (e.g. the chipathon
padring at `~/eda/designs/chipathon_padring/template/`), do NOT
iterate against the baseline directory. Clone to a feature workspace
first:

```bash
cp -a ~/eda/designs/chipathon_padring/template/ \
      ~/eda/designs/<feature>_chipathon/template/
```

Reason: a half-edited `config.yaml` in the validated path can corrupt
the next reproducibility check. Keep validated baselines read-only;
work in throwaway feature workspaces.

## Composition pointers when something fails

- **DRC violations on the chip-top:** `flow.drc_checker` +
  `flow.drc_fixer`. If the violation is on a macro pin, re-harden the
  macro with adjusted obstruction / halo; do not patch the chip-top
  to work around a macro DRC bug.
- **LVS mismatch:** check `instance` location is on-grid (off-grid
  placement is the GF180-specific cause), then standard
  `flow.lvs_checker`.
- **Antenna violations on macro pins:** raise `GRT_ANT_ITERS` in the
  macro's own Classic flow config first; chip-top antenna repair
  cannot reach inside a hardened macro.
- **Setup STA violation in only one corner:** verify the lib map
  expanded to all 9 corners (PITFALL #1). If only `tt_025C_5v00`
  appears in the report, the wildcard was dropped silently.

These linked skills are the source of truth. Do not invent fix
heuristics inline — pull the relevant skill via
`mcp__eda-agents__render_skill(name=...)` instead.
