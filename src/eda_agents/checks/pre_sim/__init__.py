"""Pre-simulation structural gates for analog subcircuits.

These gates are ported conceptually (not verbatim) from the analog-agents
``pre-sim-checklists`` set. They operate on a parsed ``Subcircuit``
object rather than on raw SPICE text so the harness can run them
without re-parsing and tests can exercise the check logic with hand-
built fixtures.

Current checks:

  - ``check_floating_nodes``       — every internal net has at least
    two terminals.
  - ``check_bulk_connections``     — PMOS bulk to VDD/source, NMOS
    bulk to VSS/source.
  - ``check_mirror_ratio``         — gate-sharing transistor pairs
    have a sane effective-width ratio (flags suspected mismatches).
  - ``check_bias_source``          — every gate net reaches a voltage
    source or a diode-connected device.
  - ``check_testbench_pin_match``  — DUT instantiation port arity
    matches the subcircuit definition.

Convenience::

    from eda_agents.checks.pre_sim import parse_subcircuit, run_all
    sc = parse_subcircuit(netlist_text, name="miller_ota")
    for res in run_all(sc):
        print(res)
"""

from eda_agents.checks.pre_sim.model import (
    CheckResult,
    Device,
    Subcircuit,
)
from eda_agents.checks.pre_sim.parser import parse_subcircuit
from eda_agents.checks.pre_sim.checks import (
    check_bias_source,
    check_bulk_connections,
    check_floating_nodes,
    check_mirror_ratio,
    check_testbench_pin_match,
    check_vds_polarity,
    run_all,
)

__all__ = [
    "CheckResult",
    "Device",
    "Subcircuit",
    "check_bias_source",
    "check_bulk_connections",
    "check_floating_nodes",
    "check_mirror_ratio",
    "check_testbench_pin_match",
    "check_vds_polarity",
    "parse_subcircuit",
    "run_all",
]
