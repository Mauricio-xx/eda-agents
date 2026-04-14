"""LibreLane config templates for from-spec design generation.

One YAML template per supported PDK. Only four design-level fields need
filling in by the spec-to-RTL prompt: DESIGN_NAME, VERILOG_FILES,
CLOCK_PORT, CLOCK_PERIOD (plus die dimensions). PDK-level defaults
(RT layers, PDN straps, stdcell lib paths) come from the PDK's own
`libs.tech/librelane/config.tcl` and are not restated here.

Templates here are **infrastructure**, not design knobs: they are
mirrored from upstream LibreLane templates and are not subject to
autoresearch tuning. Designs plug *into* the template.
"""

from __future__ import annotations

GF180_CONFIG_TEMPLATE = """\
# GF180MCU LibreLane config (auto-generated from spec)
# Fill in: DESIGN_NAME, VERILOG_FILES, CLOCK_PORT, CLOCK_PERIOD

DESIGN_NAME: {design_name}
VERILOG_FILES:
  - dir::{verilog_file}
CLOCK_PORT: {clock_port}
CLOCK_PERIOD: {clock_period}

# Die area (auto-estimated -- adjust if design doesn't fit)
FP_SIZING: absolute
DIE_AREA: [0.0, 0.0, {die_width}, {die_height}]

# Power/Ground
VDD_NETS:
  - VDD
GND_NETS:
  - VSS

# ESD & Layer Rules (GF180MCU standard)
DIODE_ON_PORTS: in
RT_MAX_LAYER: Metal4
PDN_MULTILAYER: false

# PDN Strap Configuration
PDN_VWIDTH: 5
PDN_HWIDTH: 5
PDN_VSPACING: 1
PDN_HSPACING: 1
PDN_VPITCH: 75
PDN_HPITCH: 75
PDN_EXTEND_TO: boundary

# Placement
PL_TARGET_DENSITY_PCT: 65
MAX_FANOUT_CONSTRAINT: 10

# CTS
CTS_CLK_MAX_WIRE_LENGTH: 0
CTS_DISTANCE_BETWEEN_BUFFERS: 0
CTS_SINK_CLUSTERING_SIZE: 20
CTS_SINK_CLUSTERING_MAX_DIAMETER: 60

# Repair After Placement
DESIGN_REPAIR_MAX_SLEW_PCT: 35
DESIGN_REPAIR_MAX_CAP_PCT: 30
DESIGN_REPAIR_MAX_WIRE_LENGTH: 0

# Repair After Routing
GRT_DESIGN_REPAIR_MAX_CAP_PCT: 20
GRT_DESIGN_REPAIR_MAX_SLEW_PCT: 20
GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH: 0
RUN_POST_GRT_DESIGN_REPAIR: true

# Margins
TOP_MARGIN_MULT: 1
BOTTOM_MARGIN_MULT: 1
LEFT_MARGIN_MULT: 6
RIGHT_MARGIN_MULT: 6
"""

GF180_DEFAULTS = {
    "clock_period": 50,       # ns (conservative, 20 MHz)
    "die_width": 300.0,       # um (small design default)
    "die_height": 300.0,      # um
    "clock_port": "clk",
}


# IHP SG13G2 Classic-flow template. PDK-level defaults (RT_MIN_LAYER,
# RT_MAX_LAYER, PDN_{V,H}{WIDTH,SPACING,PITCH,OFFSET}, LAYERS_RC, VIAS_R,
# FP_IO_{H,V}LAYER, STA_CORNERS, DEFAULT_CORNER) are set by
# /ihp-sg13g2/libs.tech/librelane/config.tcl and do not need to be
# repeated here. Upstream template:
#   https://github.com/IHP-GmbH/ihp-sg13g2-librelane-template
IHP_SG13G2_CONFIG_TEMPLATE = """\
# IHP SG13G2 LibreLane config (auto-generated from spec, Classic flow)
# Fill in: DESIGN_NAME, VERILOG_FILES, CLOCK_PORT, CLOCK_PERIOD

meta:
  version: 3
  flow: Classic

DESIGN_NAME: {design_name}
VERILOG_FILES:
  - dir::{verilog_file}
CLOCK_PORT: {clock_port}
CLOCK_PERIOD: {clock_period}

# Better SystemVerilog support (IHP stdcells + macros tend to use SV)
USE_SLANG: true

# Prefer KLayout-generated GDS for signoff (matches PDK config.tcl)
PRIMARY_GDSII_STREAMOUT_TOOL: klayout

# Die area (auto-estimated -- adjust if design doesn't fit)
FP_SIZING: absolute
DIE_AREA: [0.0, 0.0, {die_width}, {die_height}]

# Power/Ground (IHP convention: VDD / VSS)
VDD_NETS:
  - VDD
GND_NETS:
  - VSS

# Routing layer limits (match PDK config.tcl defaults explicitly)
RT_MIN_LAYER: Metal2
RT_MAX_LAYER: TopMetal2

# Placement
PL_TARGET_DENSITY_PCT: 50
MAX_FANOUT_CONSTRAINT: 10

# CTS
CTS_CLK_MAX_WIRE_LENGTH: 0
CTS_DISTANCE_BETWEEN_BUFFERS: 0
CTS_SINK_CLUSTERING_SIZE: 20
CTS_SINK_CLUSTERING_MAX_DIAMETER: 60

# Repair After Placement
DESIGN_REPAIR_MAX_SLEW_PCT: 35
DESIGN_REPAIR_MAX_CAP_PCT: 30
DESIGN_REPAIR_MAX_WIRE_LENGTH: 0

# Repair After Routing
GRT_DESIGN_REPAIR_MAX_CAP_PCT: 20
GRT_DESIGN_REPAIR_MAX_SLEW_PCT: 20
GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH: 0
RUN_POST_GRT_DESIGN_REPAIR: true

# Margins
TOP_MARGIN_MULT: 1
BOTTOM_MARGIN_MULT: 1
LEFT_MARGIN_MULT: 6
RIGHT_MARGIN_MULT: 6
"""

IHP_SG13G2_DEFAULTS = {
    "clock_period": 10,       # ns (130nm, 100 MHz default)
    "die_width": 300.0,       # um
    "die_height": 300.0,      # um
    "clock_port": "clk",
}


_TEMPLATES = {
    "gf180": (GF180_CONFIG_TEMPLATE, GF180_DEFAULTS),
    "ihp_sg13g2": (IHP_SG13G2_CONFIG_TEMPLATE, IHP_SG13G2_DEFAULTS),
}


def get_config_template(pdk_config) -> tuple[str, dict]:
    """Return (template_string, defaults_dict) for the given PdkConfig.

    Selector key comes from pdk_config.librelane_config_template.
    Raises KeyError if the key is unknown.
    """
    key = pdk_config.librelane_config_template
    if key not in _TEMPLATES:
        available = ", ".join(sorted(_TEMPLATES))
        raise KeyError(
            f"No LibreLane config template for '{key}'. Available: {available}"
        )
    return _TEMPLATES[key]
