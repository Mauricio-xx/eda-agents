"""Symbolic Modified Nodal Analysis (MNA) for small-signal AC circuits.

This module is the analytical engine behind ``Testbench.eval()``. It
takes a list of small-signal elements (resistors, capacitors, voltage
sources, current sources, and voltage-controlled current sources) and
builds the augmented MNA system

    [ Y   B ] [ V   ]   [ I_inj ]
    [ C   D ] [ I_v ] = [ E_vs  ]

with everything kept as sympy expressions. Solving the system returns a
mapping ``node -> sympy_expression`` from which any transfer function
can be read off as ``V[output_node] / V[input_node]`` after substituting
the parameter map.

Why MNA instead of pure nodal analysis: voltage sources cannot be
expressed as admittances. MNA introduces a per-source auxiliary current
variable and a row that imposes ``V[n+] - V[n-] = source_value``. This
gives a uniform stamp for every primitive in the SSTADEX schema.

Performance note: sympy solves the system symbolically once per
``Testbench`` build. Per-iteration speed comes from ``sympy.lambdify``
of the resulting symbolic expression, which is what ``dfs()`` does
downstream. A 1-stage OTA with three primitives + four testbench
elements produces a ~12 x 12 MNA matrix that sympy solves in a few
seconds; the lambdified expression then evaluates on 1e4+ LUT points
in a fraction of a second.

The SSTADEx upstream
====================

The upstream SSTADEx project (Code-a-Chip VLSI26 #16, MIT license,
github.com/lild4d4/SSTADEx pinned at b5bef194c6) ships with a parallel
machinery that runs XSCHEM to author a schematic, exports a SPICE
netlist, and feeds it to a Python MNA package
(github.com/lild4d4/Symbolic-modified-nodal-analysis) to derive the TF.
That stack is offline by design for our port: the schematic editor is
heavy, the netlist roundtrip drags in PDK-specific transistor models,
and the MNA library is a thin wrapper around the same equations
implemented here. Authoring the small-signal network directly from
``Testbench.elements`` plus the primitives' branch list is far simpler
and works identically on IHP SG13G2 and GF180MCU. See
``docs/sstadex_port.md`` for the full deviation from upstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sympy as sym


# ---------------------------------------------------------------------------
# Element dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Resistor:
    """Linear resistor between two nodes. ``value`` is the resistance
    (sympy expression or numeric); admittance is ``1/value``."""

    name: str
    n1: str
    n2: str
    value: Any


@dataclass(frozen=True)
class Capacitor:
    """Linear capacitor between two nodes. ``value`` is capacitance;
    admittance is ``s * value`` in the Laplace domain."""

    name: str
    n1: str
    n2: str
    value: Any


@dataclass(frozen=True)
class VoltageSource:
    """Independent voltage source. Adds an auxiliary current unknown
    and a constraint row ``V[nplus] - V[nminus] = value``."""

    name: str
    nplus: str
    nminus: str
    value: Any


@dataclass(frozen=True)
class CurrentSource:
    """Independent current source. SPICE-style: positive ``value`` means
    current flows from ``nplus`` into the external circuit (i.e. is
    pulled out of ``nplus``) and back into ``nminus``."""

    name: str
    nplus: str
    nminus: str
    value: Any


@dataclass(frozen=True)
class VCCS:
    """Voltage-controlled current source.

    Drives a current of ``gm * (V[ctrl_p] - V[ctrl_n])`` from node
    ``n_d`` toward node ``n_s``. Used to model the ``gm`` action of
    a MOSFET small-signal model: ``n_d`` is the drain net, ``n_s`` the
    source net, ``ctrl_p`` the gate, ``ctrl_n`` the source.
    """

    name: str
    n_d: str
    n_s: str
    ctrl_p: str
    ctrl_n: str
    gm: Any


# Convenience union type for type hints.
Element = Resistor | Capacitor | VoltageSource | CurrentSource | VCCS


# ---------------------------------------------------------------------------
# MNA build + solve
# ---------------------------------------------------------------------------


@dataclass
class MnaSystem:
    """Built MNA system before solve, exposed for unit tests and debug."""

    nodes: list[str]
    voltage_sources: list[VoltageSource]
    s_symbol: sym.Symbol
    Y: sym.Matrix
    rhs: sym.Matrix
    node_index: dict[str, int] = field(default_factory=dict)
    vs_index: dict[str, int] = field(default_factory=dict)


def _collect_nodes(
    elements: list[Element], ground: str
) -> tuple[list[str], dict[str, int]]:
    """Return the list of unknown node names (ground excluded) and an
    index mapping. Node order is deterministic by first appearance so
    sympy solve output is stable across runs."""
    seen: list[str] = []
    for el in elements:
        node_attrs = []
        if isinstance(el, (Resistor, Capacitor)):
            node_attrs = [el.n1, el.n2]
        elif isinstance(el, (VoltageSource, CurrentSource)):
            node_attrs = [el.nplus, el.nminus]
        elif isinstance(el, VCCS):
            node_attrs = [el.n_d, el.n_s, el.ctrl_p, el.ctrl_n]
        for net in node_attrs:
            if net != ground and net not in seen:
                seen.append(net)
    return seen, {net: i for i, net in enumerate(seen)}


def build_system(
    elements: list[Element],
    ground: str = "VSS",
    s_symbol: sym.Symbol | None = None,
) -> MnaSystem:
    """Build the symbolic MNA system from a list of elements.

    Parameters
    ----------
    elements
        Small-signal elements making up the circuit. Mixed types
        allowed; the order does not matter.
    ground
        Reference node. All KCL equations except for this node go into
        ``Y``. References to ``ground`` in any element are treated as
        ``V = 0`` (the row/column is excluded).
    s_symbol
        Laplace variable. If ``None``, a fresh ``sym.Symbol("s")`` is
        created. Pass an explicit symbol to share it with downstream
        code that substitutes ``s = 0`` for DC analysis.

    Returns
    -------
    MnaSystem
        Contains ``Y`` (square sympy matrix) and ``rhs`` (column matrix),
        plus indexing helpers. Call ``solve_system(...)`` to get the
        node voltage map.
    """
    if s_symbol is None:
        s_symbol = sym.Symbol("s")

    nodes, node_index = _collect_nodes(elements, ground)
    vsources = [el for el in elements if isinstance(el, VoltageSource)]
    vs_index = {vs.name: i for i, vs in enumerate(vsources)}

    n_nodes = len(nodes)
    n_vs = len(vsources)
    size = n_nodes + n_vs

    Y = sym.zeros(size, size)
    rhs = sym.zeros(size, 1)

    def _stamp(matrix: sym.Matrix, row: int, col: int, value: Any) -> None:
        matrix[row, col] = matrix[row, col] + sym.sympify(value)

    def _node_idx(name: str) -> int | None:
        if name == ground:
            return None
        if name not in node_index:
            raise KeyError(
                f"Node '{name}' not seen during _collect_nodes. "
                "Ensure all elements reference the same node names."
            )
        return node_index[name]

    for el in elements:
        if isinstance(el, Resistor):
            # Admittance 1/R between n1 and n2.
            admittance = 1 / sym.sympify(el.value)
            _stamp_passive(Y, el.n1, el.n2, admittance, _node_idx)

        elif isinstance(el, Capacitor):
            admittance = s_symbol * sym.sympify(el.value)
            _stamp_passive(Y, el.n1, el.n2, admittance, _node_idx)

        elif isinstance(el, CurrentSource):
            # SPICE convention: positive value pulls current out of
            # nplus and pushes it into nminus. KCL "currents leaving"
            # sees +I at nplus and -I at nminus.
            value = sym.sympify(el.value)
            ip = _node_idx(el.nplus)
            im = _node_idx(el.nminus)
            if ip is not None:
                rhs[ip, 0] = rhs[ip, 0] - value
            if im is not None:
                rhs[im, 0] = rhs[im, 0] + value

        elif isinstance(el, VoltageSource):
            # Add aux current row + constraint row.
            vs_row = n_nodes + vs_index[el.name]
            ip = _node_idx(el.nplus)
            im = _node_idx(el.nminus)

            if ip is not None:
                _stamp(Y, ip, vs_row, 1)
                _stamp(Y, vs_row, ip, 1)
            if im is not None:
                _stamp(Y, im, vs_row, -1)
                _stamp(Y, vs_row, im, -1)

            rhs[vs_row, 0] = rhs[vs_row, 0] + sym.sympify(el.value)

        elif isinstance(el, VCCS):
            # Stamp:  current g*(V[ctrl_p] - V[ctrl_n]) flows from n_d to n_s.
            g = sym.sympify(el.gm)
            id_idx = _node_idx(el.n_d)
            is_idx = _node_idx(el.n_s)
            cp_idx = _node_idx(el.ctrl_p)
            cn_idx = _node_idx(el.ctrl_n)

            if id_idx is not None:
                if cp_idx is not None:
                    _stamp(Y, id_idx, cp_idx, g)
                if cn_idx is not None:
                    _stamp(Y, id_idx, cn_idx, -g)
            if is_idx is not None:
                if cp_idx is not None:
                    _stamp(Y, is_idx, cp_idx, -g)
                if cn_idx is not None:
                    _stamp(Y, is_idx, cn_idx, g)

        else:
            raise TypeError(f"Unknown MNA element type: {type(el).__name__}")

    return MnaSystem(
        nodes=nodes,
        voltage_sources=vsources,
        s_symbol=s_symbol,
        Y=Y,
        rhs=rhs,
        node_index=node_index,
        vs_index=vs_index,
    )


def _stamp_passive(
    Y: sym.Matrix,
    n1: str,
    n2: str,
    admittance: Any,
    node_idx_fn,
) -> None:
    """Standard 4-corner stamp for a 2-terminal admittance."""
    i1 = node_idx_fn(n1)
    i2 = node_idx_fn(n2)
    if i1 is not None:
        Y[i1, i1] = Y[i1, i1] + admittance
    if i2 is not None:
        Y[i2, i2] = Y[i2, i2] + admittance
    if i1 is not None and i2 is not None:
        Y[i1, i2] = Y[i1, i2] - admittance
        Y[i2, i1] = Y[i2, i1] - admittance


def solve_system(
    system: MnaSystem, *, simplify: bool = False
) -> dict[str, sym.Expr]:
    """Solve ``Y * x = rhs`` symbolically. Returns ``{node: V_expr}``.

    The ground node always maps to ``0``. Voltage-source auxiliary
    currents are NOT returned; callers that need them can use
    ``system.Y.LUsolve(system.rhs)`` directly and pick by index.

    ``simplify=False`` (default) returns raw expressions from LUsolve.
    sympy's full ``simplify`` is exponential in symbolic size and is the
    bottleneck on circuits with 10+ MOSFETs; ``sympy.lambdify`` cancels
    common factors at compile time and gives identical numerics, so the
    raw form is what ``Testbench.eval`` consumes. Pass ``simplify=True``
    for human-readable closed-form output (slow on large systems).
    """
    # sympy's LUsolve handles parametric systems; faster than .solve()
    # for the modest sizes we see (n <= ~30).
    x = system.Y.LUsolve(system.rhs)

    result: dict[str, sym.Expr] = {}
    for net, idx in system.node_index.items():
        expr = x[idx, 0]
        if simplify:
            expr = sym.simplify(expr)
        result[net] = expr
    return result


def transfer_function(
    elements: list[Element],
    output_node: str,
    input_signal: Any,
    *,
    ground: str = "VSS",
    s_symbol: sym.Symbol | None = None,
    simplify: bool = False,
) -> sym.Expr:
    """Convenience wrapper: build, solve, return ``V[output_node] /
    input_signal`` as a sympy expression.

    Parameters
    ----------
    elements
        MNA element list.
    output_node
        Name of the node whose voltage forms the numerator.
    input_signal
        Either a sympy symbol or a numeric value representing the
        applied input. The ratio ``V[output_node] / input_signal``
        is returned. For ``Testbench.eval`` we normally pass the
        symbol that names the input voltage source's ``value`` field
        (e.g. ``Symbol("V_p")``); then substituting ``V_p = 1`` in
        the resulting expression yields the gain.
    ground, s_symbol
        Passed through to ``build_system``.

    Notes
    -----
    For the SSTADEx 1-stage OTA gain testbench the call is

    ``transfer_function(elements, "VOUT", sym.Symbol("V_p"))``

    and the resulting expression collapses to
    ``gm_xdp * (R_xdp || R_xcm)`` after substituting ``s = 0`` and the
    matched-pair parameter map ``gm_xdp_m2 = gm_xdp_m1``, etc.
    """
    system = build_system(elements, ground=ground, s_symbol=s_symbol)
    voltages = solve_system(system, simplify=simplify)
    if output_node not in voltages:
        if output_node == ground:
            return sym.Integer(0)
        raise KeyError(
            f"Output node '{output_node}' not present in solved system. "
            f"Known nodes: {sorted(voltages)}"
        )
    expr = voltages[output_node] / sym.sympify(input_signal)
    return sym.simplify(expr) if simplify else expr
