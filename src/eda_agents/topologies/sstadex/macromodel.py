"""Macromodel dataclasses for the SSTADEX hierarchical DSE port.

A ``Macromodel`` is a named block with ports, sub-instances (primitives
or nested macromodels), shared-node constraints, electrical parameters,
and a per-block parameter sweep (``macromodel_parameters``). It mirrors
the upstream ``sstadex.models.Macromodel`` data layout but trims the
physical-netlist generation paths down to what ``Testbench`` needs at
small-signal-AC time. Physical netlists (``gen_netlist_for_params``
etc.) are out of scope here: we are not invoking ngspice from inside
``dfs()`` -- validation goes through eda-agents' own ``SpiceRunner``
later in ``examples/17_sstadex_pareto_ihp.py``.

The flow chain
==============

::

    Library + GmIdLookup
        |
        v
    Primitive.set_port_voltages -> Primitive.build(lut)
        |
        v
    Macromodel.add_instance(name, primitive, net_map, ...)
        |
        v
    Testbench(dut=macromodel, elements=[...], tf=("VOUT","VINP"))
        |
        v
    Testbench.eval() -> sympy expression + numeric evaluation
        |
        v
    dfs() -> pd.DataFrame of valid configurations
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sympy import Symbol

from eda_agents.topologies.sstadex.primitives import Primitive


@dataclass
class NetlistInstance:
    """One instance of a primitive or nested macromodel inside a parent
    Macromodel. ``net_map`` maps the child block's port names to the
    parent's net names."""

    name: str
    block: Any  # Primitive | Macromodel
    net_map: dict[str, str]
    index: int = 0
    netlist_params: dict[Any, Any] | None = None

    def __post_init__(self) -> None:
        if self.netlist_params is None:
            self.netlist_params = {}


@dataclass
class Macromodel:
    """Hierarchical analog block.

    Attributes match the upstream contract where the engine cares
    (ports, outputs, electrical_parameters, macromodel_parameters,
    submacromodels, primitives, instances, interface_variables,
    shared_nodes, propagated_conditions, derived_metrics,
    specifications, opt_specifications, num_level_exp, is_primitive,
    run_pareto). Everything XSCHEM/SPICE-emit-related is dropped.
    """

    name: str
    ports: list[str] = field(default_factory=list)
    electrical_parameters: dict[str, Any] = field(default_factory=dict)
    outputs: list[Symbol] = field(default_factory=list)
    macromodel_parameters: dict[Symbol, np.ndarray] = field(default_factory=dict)

    primitives: list[Primitive] = field(default_factory=list)
    submacromodels: list["Macromodel"] = field(default_factory=list)
    instances: list[NetlistInstance] = field(default_factory=list)

    interface_variables: list[str] = field(default_factory=list)
    shared_nodes: dict[str, list[str]] = field(default_factory=dict)

    propagated_conditions: dict[str, list[dict]] = field(
        default_factory=lambda: {"direct": [], "derived": []}
    )
    derived_metrics: dict[str, Any] = field(default_factory=dict)
    submacro_condition_rules: dict[Any, list[dict]] = field(default_factory=dict)

    specifications: list[Any] = field(default_factory=list)  # Test
    opt_specifications: list[Any] = field(default_factory=list)

    num_level_exp: int = -1   # -1 = recursive; 1 = leaf (single-level)
    is_primitive: bool = False
    run_pareto: bool = False
    ext_mask: np.ndarray | None = None

    # Populated by dfs() so parent macromodels can read child outputs.
    output_results: dict[Symbol, np.ndarray] = field(default_factory=dict)
    interface_results: dict[str, np.ndarray] = field(default_factory=dict)
    its_final: bool = False
    flattened_params: dict[Any, dict[Any, np.ndarray]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Instance management
    # ------------------------------------------------------------------

    def add_instance(
        self,
        name: str,
        block: Any,
        net_map: dict[str, str],
        index: int = 0,
        netlist_params: dict[Any, Any] | None = None,
    ) -> None:
        self.instances.append(
            NetlistInstance(
                name=name,
                block=block,
                net_map=net_map,
                index=index,
                netlist_params=netlist_params or {},
            )
        )

    def hasPrimitive(self) -> bool:
        return len(self.primitives) > 0

    # ------------------------------------------------------------------
    # Condition application
    # ------------------------------------------------------------------

    def evaluate_derived_metric(self, metric_name: str, df: pd.DataFrame):
        if metric_name not in self.derived_metrics:
            raise KeyError(
                f"Macromodel '{self.name}' has no derived metric '{metric_name}'."
            )
        rule = self.derived_metrics[metric_name]
        if callable(rule):
            return rule(df)
        if isinstance(rule, dict) and callable(rule.get("expr")):
            return rule["expr"](df)
        raise TypeError(
            f"Derived metric '{metric_name}' in macromodel '{self.name}' must "
            "be a callable or a dict with a callable 'expr'."
        )

    def apply_propagated_conditions(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter ``df`` by the macromodel's ``propagated_conditions``.

        Supports ``kind == "range"`` (column min/max), ``"allowed_values"``
        (column membership), ``"metric"`` (derived metric callable on the
        DataFrame), and ``"expression"`` (callable on the DataFrame).
        """
        if df is None or len(df.index) == 0:
            return df

        filtered = df.copy()
        for cond in self.propagated_conditions.get("direct", []):
            kind = cond.get("kind", "range")
            col = cond.get("column")
            # Accept either a sympy Symbol or a string for the column
            # reference -- _primitive_to_columns stores stringified
            # versions of the same Symbol keys, so we resolve both.
            if col is None:
                continue
            if col in filtered.columns:
                col_used = col
            else:
                col_name = col.name if hasattr(col, "name") else str(col)
                if col_name in filtered.columns:
                    col_used = col_name
                else:
                    continue
            if kind == "range":
                limits = cond.get("condition", {})
                if "min" in limits:
                    filtered = filtered[filtered[col_used] >= limits["min"]]
                if "max" in limits:
                    filtered = filtered[filtered[col_used] <= limits["max"]]
            elif kind == "allowed_values":
                values = np.asarray(cond.get("values", []))
                filtered = filtered[filtered[col_used].isin(values)]

        for cond in self.propagated_conditions.get("derived", []):
            kind = cond.get("kind", "metric")
            if kind == "metric":
                series = self.evaluate_derived_metric(cond["metric"], filtered)
            elif kind == "expression":
                expr = cond.get("expr")
                if not callable(expr):
                    raise TypeError(
                        f"Derived expression condition in macromodel "
                        f"'{self.name}' must be a callable."
                    )
                series = expr(filtered)
            else:
                continue
            limits = cond.get("condition", {})
            series = np.asarray(series)
            if "min" in limits:
                mask = series >= limits["min"]
                filtered = filtered[mask]
                series = series[mask]
            if "max" in limits:
                mask = series <= limits["max"]
                filtered = filtered[mask]

        return filtered

    # ------------------------------------------------------------------
    # Small-signal element generation
    # ------------------------------------------------------------------

    def small_signal_elements(self) -> list[Any]:
        """Flatten the instance tree into a list of small-signal MNA
        elements. Each primitive instance's branches produce one VCCS
        and one resistor; nested macromodels recurse and the parent's
        ``net_map`` is composed with the child's ports.

        Returns instances of ``symbolic_mna.{VCCS, Resistor}`` keyed by
        symbol names ``g_gm_<instance>_<branch>`` and
        ``R_gds_<instance>_<branch>``.
        """
        from eda_agents.topologies.sstadex.symbolic_mna import VCCS, Resistor

        elements: list[Any] = []
        for inst in self.instances:
            block = inst.block
            net_map = inst.net_map
            if isinstance(block, Primitive):
                for br in block.small_signal_branches:
                    vd = net_map[br["vd"]]
                    vg = net_map[br["vg"]]
                    vs = net_map[br["vs"]]
                    gm_sym = Symbol(f"g_gm_{inst.name}_{br['name']}")
                    R_sym = Symbol(f"R_gds_{inst.name}_{br['name']}")
                    elements.append(
                        VCCS(
                            name=f"G_gm_{inst.name}_{br['name']}",
                            n_d=vd, n_s=vs,
                            ctrl_p=vg, ctrl_n=vs,
                            gm=gm_sym,
                        )
                    )
                    elements.append(
                        Resistor(
                            name=f"R_gds_{inst.name}_{br['name']}",
                            n1=vd, n2=vs, value=R_sym,
                        )
                    )
            elif isinstance(block, Macromodel):
                # Recurse and rewrite nested net names through net_map.
                nested = block.small_signal_elements()
                for el in nested:
                    elements.append(_rewrite_element_nets(el, net_map, block.ports))
            else:
                raise TypeError(
                    f"Unsupported instance block type: {type(block).__name__}"
                )
        return elements


def _rewrite_element_nets(el: Any, parent_net_map: dict[str, str], child_ports: list[str]) -> Any:
    """Rewrite an MNA element's nets through the parent's net_map.

    Child ports listed in ``child_ports`` are rewritten using
    ``parent_net_map[child_port]``; internal nets (not in child_ports)
    are left untouched (they remain unique within their child's scope
    because the upstream convention names internal nets after the
    instance, e.g. ``N1`` for diff-pair tail rebroadcast).
    """
    from eda_agents.topologies.sstadex.symbolic_mna import (
        VCCS, Resistor, Capacitor, VoltageSource, CurrentSource,
    )

    def remap(net: str) -> str:
        return parent_net_map.get(net, net)

    if isinstance(el, VCCS):
        return VCCS(
            name=el.name,
            n_d=remap(el.n_d),
            n_s=remap(el.n_s),
            ctrl_p=remap(el.ctrl_p),
            ctrl_n=remap(el.ctrl_n),
            gm=el.gm,
        )
    if isinstance(el, Resistor):
        return Resistor(name=el.name, n1=remap(el.n1), n2=remap(el.n2), value=el.value)
    if isinstance(el, Capacitor):
        return Capacitor(name=el.name, n1=remap(el.n1), n2=remap(el.n2), value=el.value)
    if isinstance(el, VoltageSource):
        return VoltageSource(
            name=el.name,
            nplus=remap(el.nplus),
            nminus=remap(el.nminus),
            value=el.value,
        )
    if isinstance(el, CurrentSource):
        return CurrentSource(
            name=el.name,
            nplus=remap(el.nplus),
            nminus=remap(el.nminus),
            value=el.value,
        )
    raise TypeError(f"Cannot rewrite nets for element of type {type(el).__name__}")
