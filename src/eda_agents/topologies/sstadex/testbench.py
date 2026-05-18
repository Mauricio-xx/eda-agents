"""Testbench + Test schema for the SSTADEX hierarchical DSE port.

A ``Testbench`` wraps a ``Macromodel`` (the DUT) and a list of bench
elements (independent sources, optional resistors / capacitors) that
form the small-signal AC environment. ``Testbench.eval()`` calls the
symbolic MNA solver and returns a sympy expression for the requested
transfer function ``V[output_node] / input_signal``.

``Test`` records the testbench-driven specification (name, optimization
goal, conditions, target macromodel parameter, output processing
recipe). The composed-spec path (``out_def={"divide": (...)}``) and
frequency-domain post-processing (``"frec"``, ``"pm"``, ``"diff"``,
``"eval"``) match upstream's ``Test.out_def`` vocabulary so the
``dfs()`` explorer can route specs through the same branches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sympy as sym

from eda_agents.topologies.sstadex.macromodel import Macromodel
from eda_agents.topologies.sstadex.symbolic_mna import (
    Capacitor as MnaCapacitor,
    CurrentSource as MnaCurrentSource,
    Resistor as MnaResistor,
    VoltageSource as MnaVoltageSource,
    transfer_function,
)


# ---------------------------------------------------------------------------
# Bench elements -- user-facing wrappers; converted to MNA elements at
# Testbench.eval() time so the user never has to think about node sign
# conventions or auxiliary-variable indexing.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchElement:
    name: str


@dataclass(frozen=True)
class VoltageSource(BenchElement):
    """``Vname n+ n- value``. ``value`` can be a number or a sympy
    symbol — the testbench's ``parameter_map`` substitutes it at eval
    time."""

    nplus: str
    nminus: str
    value: Any


@dataclass(frozen=True)
class CurrentSource(BenchElement):
    """``Iname n+ n- value`` with SPICE polarity (positive value pulls
    current out of n+)."""

    nplus: str
    nminus: str
    value: Any


@dataclass(frozen=True)
class Resistor(BenchElement):
    n1: str
    n2: str
    value: Any


@dataclass(frozen=True)
class Capacitor(BenchElement):
    n1: str
    n2: str
    value: Any


def _to_mna(el: BenchElement):
    if isinstance(el, VoltageSource):
        return MnaVoltageSource(name=el.name, nplus=el.nplus, nminus=el.nminus, value=el.value)
    if isinstance(el, CurrentSource):
        return MnaCurrentSource(name=el.name, nplus=el.nplus, nminus=el.nminus, value=el.value)
    if isinstance(el, Resistor):
        return MnaResistor(name=el.name, n1=el.n1, n2=el.n2, value=el.value)
    if isinstance(el, Capacitor):
        return MnaCapacitor(name=el.name, n1=el.n1, n2=el.n2, value=el.value)
    raise TypeError(f"Unknown bench element type: {type(el).__name__}")


# ---------------------------------------------------------------------------
# Test record
# ---------------------------------------------------------------------------


@dataclass
class Test:
    """Per-spec record produced by ``Testbench.make_test``.

    Out-def vocabulary (mirrors upstream):
      * ``{"eval": tf_expr}`` — direct DC evaluation of the symbolic TF
      * ``{"frec": tf_expr}`` — find -3 dB bandwidth from the TF
      * ``{"pm": tf_expr}`` — phase margin (degrees) at unity gain
      * ``{"diff": ...}`` — differential evaluation (rare)
      * ``{"divide": [num_test, den_test]}`` — composed spec: ratio of
        two already-defined tests' values
    """

    name: str = ""
    tf: Any = None
    netlist: str = ""
    parametros: dict = field(default_factory=dict)
    variables: dict = field(default_factory=dict)
    out_def: dict = field(default_factory=dict)
    composed: int = 0
    lamd: Any = None
    target_param: Any = ""
    only_up: bool = False
    opt_goal: str = "max"
    conditions: dict = field(default_factory=dict)
    testbench: Any = None  # back-pointer to Testbench

    def __post_init__(self) -> None:
        if not self.out_def:
            self.out_def = {"eval": self.tf}


# ---------------------------------------------------------------------------
# Testbench
# ---------------------------------------------------------------------------


@dataclass
class Testbench:
    """Symbolic-AC testbench around a ``Macromodel``.

    ``tf=(output_node, input_node_or_source_name)`` selects the
    transfer function. The input handle can be either:

      * A node name (``"VINP"``) — the TF numerator is the
        symbolic V at that node, divided by an external 1 V drive.
      * A source name (e.g. ``"V_p"`` matching one of ``elements``) —
        the TF numerator is ``V[output_node]`` and the denominator is
        the *symbol* the source's ``value`` carries. The user is
        expected to drive that symbol to 1 via ``parameter_map`` (the
        upstream convention).
    """

    name: str
    dut: Macromodel
    view: str = "small_signal"
    elements: list[BenchElement] = field(default_factory=list)
    tf: tuple[str, str] | list[str] | None = None
    parameter_map: dict[Any, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    extra_spice: dict[str, str] | None = None

    # Cached symbolic expression (resolved once per call to .eval()).
    _cached_expr: Any = None

    # ------------------------------------------------------------------

    def eval(
        self,
        *,
        ground: str = "VSS",
        s_symbol: sym.Symbol | None = None,
        simplify: bool = False,
    ) -> sym.Expr:
        """Build the small-signal network and return the symbolic
        ``V[output] / input_signal`` expression.

        The result is the same expression every call as long as the
        DUT instance graph hasn't changed; ``dfs()`` should call
        ``eval()`` once per spec and then numerically substitute the
        primitive parameters via ``sympy.lambdify``.
        """
        if self.tf is None:
            raise ValueError(f"Testbench '{self.name}' has no tf set.")

        output_node, input_handle = self.tf[0], self.tf[1]

        # Collect MNA elements from the DUT (recursive) + bench.
        elements = list(self.dut.small_signal_elements())
        for el in self.elements:
            elements.append(_to_mna(el))

        # Resolve input_signal: if the handle matches an
        # element-by-name (typically a VoltageSource), use its symbolic
        # value. Otherwise treat the handle as a node and apply a unit
        # drive via the implicit input_signal=1 convention.
        input_signal: Any = None
        for el in self.elements:
            if isinstance(el, VoltageSource) and el.nplus == input_handle:
                input_signal = el.value
                break
            if isinstance(el, VoltageSource) and el.name == input_handle:
                input_signal = el.value
                break
        if input_signal is None:
            # Fallback: treat input_handle as a node, denom = 1.
            input_signal = sym.Integer(1)

        return transfer_function(
            elements,
            output_node,
            input_signal,
            ground=ground,
            s_symbol=s_symbol,
            simplify=simplify,
        )

    # ------------------------------------------------------------------

    def make_test(
        self,
        *,
        name: str,
        opt_goal: str = "max",
        conditions: dict | None = None,
        out_def: dict | None = None,
        composed: int = 0,
        lamd: Any = None,
        target_param: Any = "",
        only_up: bool = False,
    ) -> Test:
        """Bind a ``Test`` to this testbench. ``conditions`` follows
        upstream: ``{"min": [floor1, ...]}`` or ``{"max": [...]}`` (and
        unions thereof)."""
        tf_expr = self.tf if composed else None  # actual eval at dfs() time
        test = Test(
            name=name,
            tf=tf_expr,
            netlist=self.name,
            parametros=dict(self.parameter_map),
            variables=dict(self.variables),
            out_def=out_def or {"eval": tf_expr},
            composed=composed,
            lamd=lamd,
            target_param=target_param,
            only_up=only_up,
            opt_goal=opt_goal,
            conditions=conditions or {},
            testbench=self,
        )
        return test
