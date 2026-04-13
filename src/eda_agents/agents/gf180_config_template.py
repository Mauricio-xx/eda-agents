"""GF180MCU LibreLane config template for from-spec design generation.

Provides a minimal working YAML config template where only 4 fields
need filling: DESIGN_NAME, VERILOG_FILES, CLOCK_PORT, CLOCK_PERIOD.
Everything else is GF180MCU boilerplate validated against Phase 0
fazyrv-hachure runs.
"""

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

# Default values for template fields
GF180_DEFAULTS = {
    "clock_period": 50,       # ns (conservative, 20 MHz)
    "die_width": 300.0,       # um (small design default)
    "die_height": 300.0,      # um
    "clock_port": "clk",
}
