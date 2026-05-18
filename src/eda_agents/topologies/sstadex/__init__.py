"""SSTADEX hierarchical analog DSE port (Code-a-Chip VLSI26 #16,
MIT license, github.com/lild4d4/SSTADEx pinned at b5bef194c6).

The upstream framework drives XSCHEM + MNA + mosplot to build the
small-signal symbolic transfer function and explore the Pareto front
of sized analog blocks. This eda-agents port replaces:

  * the upstream LUT engine (mosplot) with eda-agents' own
    ``GmIdLookup`` reading the PSP103 .npz files shipped via
    ``ihp-gmid-kit`` -- no extra LUT format, no extra runtime.
  * the upstream MNA engine (Symbolic-modified-nodal-analysis) with a
    small in-house sympy MNA module (``symbolic_mna.py``) that builds
    the admittance matrix from a list of small-signal elements and
    solves it once per testbench.
  * the upstream folder-based primitive loader (``primitive.json`` +
    ``build.py``) with a dataclass schema where the four upstream
    primitives are baked-in factories.

The public surface preserves upstream's user-facing call shape
(``Library.get`` → primitive ports → ``primitive.build()`` →
``Macromodel.add_instance`` → ``Testbench`` → ``dfs``) so notebook-style
SSTADEx scripts can be ported to eda-agents with no method renames.

Example
=======

::

    import numpy as np
    from sympy import Symbol
    from eda_agents.core.gmid_lookup import GmIdLookup
    from eda_agents.topologies.sstadex import (
        Library, Macromodel, Testbench, VoltageSource, Resistor, dfs,
    )

    lut = GmIdLookup(pdk="ihp_sg13g2")
    lib = Library(name="ihp_sg13g2", lut=lut)
    diffpair = lib.get("simplediffpair", il=20e-6)
    diffpair.set_port_voltages({
        "VINP": 0.9, "VINN": 0.9,
        "VOUTP": 1.0, "VOUTN": 1.0,
        "VTAIL": np.linspace(0.2, 0.9, 10),
    })
    # ... build macromodel, testbench, dfs ...
"""

from eda_agents.topologies.sstadex.characterization import (
    characterize_primitive,
)
from eda_agents.topologies.sstadex.dfs import (
    ExplorationResult,
    dfs,
)
from eda_agents.topologies.sstadex.macromodel import (
    Macromodel,
    NetlistInstance,
)
from eda_agents.topologies.sstadex.primitives import (
    Library,
    Port,
    Primitive,
)
from eda_agents.topologies.sstadex.symbolic_mna import (
    Capacitor as MnaCapacitor,
    CurrentSource as MnaCurrentSource,
    Resistor as MnaResistor,
    VCCS,
    VoltageSource as MnaVoltageSource,
    build_system,
    solve_system,
    transfer_function,
)
from eda_agents.topologies.sstadex.testbench import (
    BenchElement,
    Capacitor,
    CurrentSource,
    Resistor,
    Test,
    Testbench,
    VoltageSource,
)

__all__ = [
    # symbolic MNA primitives
    "VCCS",
    "MnaCapacitor",
    "MnaCurrentSource",
    "MnaResistor",
    "MnaVoltageSource",
    "build_system",
    "solve_system",
    "transfer_function",
    # primitives + library
    "Port",
    "Primitive",
    "Library",
    "characterize_primitive",
    # macromodel
    "Macromodel",
    "NetlistInstance",
    # testbench
    "BenchElement",
    "VoltageSource",
    "CurrentSource",
    "Resistor",
    "Capacitor",
    "Test",
    "Testbench",
    # explorer
    "ExplorationResult",
    "dfs",
]
