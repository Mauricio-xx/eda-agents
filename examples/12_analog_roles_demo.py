"""Walk a Miller OTA spec through the 4-role analog DAG.

This demo runs without any LLM calls. It exercises:

  - ``BlockSpec`` loaded from a YAML literal.
  - ``AnalogRolesHarness`` with the bundled ``DryRunExecutor`` so the
    DAG is observed end-to-end (Librarian -> Architect -> Designer ->
    Verifier) and the iteration log is persisted to disk.
  - The pre-sim structural gates (``check_floating_nodes``,
    ``check_bulk_connections``, ``check_mirror_ratio``,
    ``check_bias_source``) over a hand-written subcircuit netlist so
    a reviewer can see how the Verifier would gate a real netlist
    before spending SPICE budget.

Run::

    PYTHONPATH=src python examples/12_analog_roles_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from eda_agents.agents.analog_roles import (
    AnalogRolesHarness,
    DryRunExecutor,
)
from eda_agents.checks.pre_sim import parse_subcircuit, run_all
from eda_agents.specs import load_spec_from_string
from eda_agents.topologies.ota_miller import MillerOTATopology

SPEC_YAML = """\
block: miller_ota
process: ihp_sg13g2
supply:
  vdd: 1.2
  vss: 0.0
specs:
  dc_gain:      {min: 60.0, unit: dB}
  gbw:          {min: 10.0e6, unit: Hz}
  phase_margin: {min: 60.0, unit: deg}
  power:        {max: 1.0, unit: mW}
corners: [TT_27, FF_m40, SS_125]
notes: |
  Behavioural primitives available: filter_1st (RC LPF), opamp_1p
  (single-pole). Use them in the architect's testbench to validate
  the load condition before the designer's transistor netlist.
"""

# Hand-written Miller OTA-style subcircuit purely for the pre-sim gate
# demo. Bulks are explicit, mirror ratios are 1:1 and 8:1, and the
# bias net is anchored by a diode-connected NMOS so the gates pass.
DEMO_NETLIST = """\
* miller-ota-style subcircuit, structural gates demo
.subckt miller_ota inp inn out vdd vss vbn
* input pair (PMOS), tail bias from vbp
Mtail vtail vbp vdd vdd sg13_lv_pmos w=4u l=0.5u nf=4 m=1
M1 d1 inp vtail vdd sg13_lv_pmos w=2u l=0.35u nf=2 m=1
M2 d2 inn vtail vdd sg13_lv_pmos w=2u l=0.35u nf=2 m=1
* current mirror load (NMOS)
M3 d1 d1   vss vss sg13_lv_nmos w=1u l=0.5u nf=1 m=1
M4 d2 d1   vss vss sg13_lv_nmos w=1u l=0.5u nf=1 m=1
* second stage CS amp + Miller cap
M5 out d2  vss vss sg13_lv_nmos w=8u l=0.5u nf=8 m=1
Mload out vbp vdd vdd sg13_lv_pmos w=8u l=0.5u nf=8 m=1
Cmiller d2 out 1p
* bias network (diode-connected NMOS sets vbn; vbp is external bias port)
Ibias vbp vss 50u
Mdio  vbp vbp vdd vdd sg13_lv_pmos w=2u l=0.5u nf=2 m=1
Mb    vbn vbn vss vss sg13_lv_nmos w=1u l=0.5u nf=1 m=1
Iref  vbn vss 20u
.ends miller_ota
"""


def _print_step(msg: str) -> None:
    print(f"[roles-demo] {msg}", flush=True)


def main() -> int:
    spec = load_spec_from_string(SPEC_YAML)
    topology = MillerOTATopology()
    harness = AnalogRolesHarness(
        spec=spec,
        executor=DryRunExecutor(verbose=False),
        topology=topology,
        max_iterations=3,
    )

    _print_step(
        f"running 4-role DAG for block '{spec.block}' on process "
        f"'{spec.process}' (supply {spec.supply.vdd} V)"
    )
    output = harness.run()

    log_path = Path("/tmp/eda_agents_analog_roles/iteration_log.yaml")
    harness.save_log(log_path)
    _print_step(f"iteration log saved to {log_path}")

    print()
    print(f"Session : {output.session_id}")
    print(f"Block   : {output.block}")
    print(f"Verdict : {output.final_status}")
    print(f"Iters   : {output.iterations_used}")
    print()

    print("Role timeline:")
    for r in output.role_results:
        marker = "ok " if r.success else "x  "
        print(f"  [{marker}] {r.role.value:<10s} {r.summary}")
    print()

    _print_step("running pre-sim structural gates against a demo netlist")
    sub = parse_subcircuit(DEMO_NETLIST, name="miller_ota")
    declared_ratios = {
        # M3 vs M4 are 1:1 mirror, so the ratio is 1.0.
        ("M3", "M4"): 1.0,
    }
    results = run_all(sub, declared_ratios=declared_ratios)
    print()
    for res in results:
        marker = "OK  " if res.passed else (
            "WARN" if res.severity == "warn" else "FAIL"
        )
        print(f"  [{marker}] {res.name}")
        for msg in res.messages:
            print(f"        - {msg}")

    failures = [r for r in results if not r.passed and r.severity == "error"]
    if failures or output.final_status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
