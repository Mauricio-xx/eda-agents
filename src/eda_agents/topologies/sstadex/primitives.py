"""Primitive library for the SSTADEX hierarchical DSE port.

Mirrors the upstream SSTADEx primitive contract (``simplediffpair``,
``simplecurrentmirror``, ``simplecurrentsource``, ``simplecommonsource``)
as a single-file dataclass schema. Each primitive carries:

  * Identity (``name``, ``transistor_type``)
  * Pin order + small-signal branch list (used by ``Testbench.eval``)
  * LUT sweep configuration (``vgs_sweep``, ``vds_sweep`` magnitudes,
    ``lengths_um``)
  * Bias current ``il`` (per-instance, not per-branch)
  * Port objects with externally settable DC voltages
  * Engine-facing dicts ``parameters`` (small-signal: gm, Ro, cgg, ...)
    and ``outputs`` (sizing: W, L) — populated by ``build()``

The Library class is a minimal registry that hands out **fresh**
primitive instances on each ``get()`` call, so callers can safely set
distinct port voltages on each branch without cross-talk.

Net mapping
===========

Each primitive declares ``small_signal_branches`` as a list of dicts
``{name, vd, vg, vs}``. ``Macromodel`` stamps these into the MNA system
by reading the parent's ``net_map`` and rewriting (vd, vg, vs) into the
parent's nets. For each branch, two MNA elements appear:

  * VCCS ``G_<branch_name>`` from drain to source, controlled by gate
    relative to source. Symbol naming: ``g_gm_<instance>_<branch>``.
  * Resistor ``R_<branch_name>`` between drain and source, value
    ``R_gds_<instance>_<branch>``.

This matches upstream's SPICE-emitter convention so the same
``parameter_map`` (e.g. ``{Symbol('g_gm_xdp_m2'): Symbol('g_gm_xdp_m1'),
...}``) drops in unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.core.pdk import PdkConfig
from eda_agents.topologies.sstadex.characterization import (
    characterize_primitive,
)


PortRole = str  # "input" | "output" | "bias" | "supply" | "internal"


@dataclass
class Port:
    """A primitive port with externally settable DC voltage."""

    name: str
    role: PortRole
    description: str = ""
    dc_voltage: Any = None  # float | np.ndarray | None

    def set_voltage(self, v: Any) -> None:
        self.dc_voltage = v

    def __repr__(self) -> str:
        v = self.dc_voltage
        if v is None:
            vstr = "unset"
        elif isinstance(v, np.ndarray):
            vstr = f"array({v.size})"
        else:
            vstr = f"{float(v):.3f}V"
        return f"Port({self.name!r}, role={self.role}, dc={vstr})"


@dataclass
class Primitive:
    """Base primitive. Subclasses fill in the ``build()`` logic.

    Layout philosophy mirrors upstream: each primitive class is a thin
    descriptor of (ports, branches, lut_config, biasing convention); the
    LUT sweep itself is performed by ``characterize_primitive`` from
    ``characterization.py``.
    """

    name: str
    transistor_type: str            # "nmos" | "pmos"
    pin_order: list[str]
    subckt_name: str
    ports: dict[str, Port]
    small_signal_branches: list[dict[str, str]]
    lengths_um: list[float]
    lut_w_ref: float = 10e-6
    il: float = 100e-6              # Total bias current (A) — meaning depends on subclass.
    pdk: PdkConfig | str | None = None
    description: str = ""

    # Engine-facing — populated by build().
    parameters: dict[Any, np.ndarray] = field(default_factory=dict)
    outputs: dict[Any, np.ndarray] = field(default_factory=dict)
    interface_variables: dict[str, np.ndarray] = field(default_factory=dict)

    # ----- port helpers -----

    def set_port_voltage(self, port_name: str, voltage: Any) -> None:
        if port_name not in self.ports:
            raise KeyError(
                f"Port '{port_name}' not found in primitive '{self.name}'. "
                f"Known ports: {list(self.ports)}"
            )
        self.ports[port_name].set_voltage(voltage)

    def set_port_voltages(self, voltages: dict[str, Any]) -> None:
        for name, v in voltages.items():
            self.set_port_voltage(name, v)

    # ----- characterization (override per primitive) -----

    def build(self, lut: GmIdLookup | None = None) -> pd.DataFrame:
        """Run LUT sweep at the current port voltages. Subclass-specific.

        Subclasses define how port voltages map to (vgs, vds) sweep
        arrays and to per-branch bias currents.
        """
        raise NotImplementedError(
            f"Primitive '{self.name}' must implement build()."
        )

    # ----- introspection -----

    def summary(self) -> str:
        lines = [f"Primitive {self.name!r} ({self.transistor_type})"]
        for p in self.ports.values():
            lines.append(f"  {p}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Concrete primitives
# ---------------------------------------------------------------------------


def _diffpair(name: str = "simplediffpair", transistor_type: str = "nmos") -> Primitive:
    return Primitive(
        name=name,
        transistor_type=transistor_type,
        pin_order=["VINP", "VINN", "VOUTP", "VOUTN", "VTAIL"],
        subckt_name=name,
        ports={
            "VINP":  Port("VINP",  "input",  "Positive differential input"),
            "VINN":  Port("VINN",  "input",  "Negative differential input"),
            "VOUTP": Port("VOUTP", "output", "Positive output"),
            "VOUTN": Port("VOUTN", "output", "Negative output"),
            "VTAIL": Port("VTAIL", "bias",   "Tail current source node"),
        },
        small_signal_branches=[
            {"name": "m1", "vd": "VOUTP", "vg": "VINP", "vs": "VTAIL"},
            {"name": "m2", "vd": "VOUTN", "vg": "VINN", "vs": "VTAIL"},
        ],
        lengths_um=[0.4, 0.8, 1.6, 3.2, 6.4],
        description="Simple differential pair (single transistor per branch).",
    )


def _current_mirror(name: str = "simplecurrentmirror", transistor_type: str = "pmos") -> Primitive:
    return Primitive(
        name=name,
        transistor_type=transistor_type,
        pin_order=["VINP", "VINN", "VOUTP", "VOUTN", "VDD"],
        subckt_name=name,
        ports={
            "VINP":  Port("VINP",  "input",  "Positive input"),
            "VINN":  Port("VINN",  "input",  "Negative input"),
            "VOUTP": Port("VOUTP", "output", "Positive output"),
            "VOUTN": Port("VOUTN", "output", "Negative output"),
            "VDD":   Port("VDD",   "bias",   "Source node (VDD for PMOS)"),
        },
        small_signal_branches=[
            {"name": "m1", "vd": "VOUTP", "vg": "VINP", "vs": "VDD"},
            {"name": "m2", "vd": "VINP",  "vg": "VINP", "vs": "VDD"},
        ],
        lengths_um=[0.4, 0.8, 1.6, 3.2, 6.4],
        description="Simple current mirror, PMOS default (m2 diode-connected).",
    )


def _current_source(name: str = "simplecurrentsource", transistor_type: str = "nmos") -> Primitive:
    return Primitive(
        name=name,
        transistor_type=transistor_type,
        pin_order=["VINP", "VINN", "VOUTP", "VOUTN", "VSS"],
        subckt_name=name,
        ports={
            "VINP":  Port("VINP",  "input",  "Gate input"),
            "VINN":  Port("VINN",  "input",  "Gate input"),
            "VOUTP": Port("VOUTP", "output", "Mirrored output"),
            "VOUTN": Port("VOUTN", "output", "Bias output"),
            "VSS":   Port("VSS",   "supply", "Source node (VSS for NMOS)"),
        },
        small_signal_branches=[
            {"name": "m1", "vd": "VOUTP", "vg": "VINP", "vs": "VSS"},
            {"name": "m2", "vd": "VOUTN", "vg": "VINN", "vs": "VSS"},
        ],
        lengths_um=[0.4, 0.8, 1.6, 3.2, 6.4],
        description="NMOS current source / bias generator.",
    )


def _common_source(name: str = "simplecommonsource", transistor_type: str = "pmos") -> Primitive:
    return Primitive(
        name=name,
        transistor_type=transistor_type,
        pin_order=["VIN", "VOUT", "VDD"],
        subckt_name=name,
        ports={
            "VIN":  Port("VIN",  "input",  "Gate input"),
            "VOUT": Port("VOUT", "output", "Drain output"),
            "VDD":  Port("VDD",  "supply", "Source node (VDD for PMOS)"),
        },
        small_signal_branches=[
            {"name": "m1", "vd": "VOUT", "vg": "VIN", "vs": "VDD"},
        ],
        lengths_um=[0.4, 0.8, 1.6, 3.2, 6.4],
        description="PMOS common-source amplifier.",
    )


# ---------------------------------------------------------------------------
# Build dispatch — port-voltage -> characterization sweep arrays
# ---------------------------------------------------------------------------


def _normalize_array(v: Any) -> np.ndarray:
    arr = np.atleast_1d(np.asarray(v, dtype=float))
    return arr


def build_diffpair(prim: Primitive, lut: GmIdLookup) -> pd.DataFrame:
    """Each branch carries ``il/2``. (vds, vgs) come from
    ``(VOUTP - VTAIL, VINP - VTAIL)``. ``VOUTP``/``VINP`` may be
    scalars; ``VTAIL`` may be an array."""
    voutp = _normalize_array(prim.ports["VOUTP"].dc_voltage)
    vinp = _normalize_array(prim.ports["VINP"].dc_voltage)
    vtail = _normalize_array(prim.ports["VTAIL"].dc_voltage)
    # Outer over voutp/vinp magnitudes; if either is a single point, just sweep vtail.
    vds = (voutp[:, None] - vtail[None, :]).ravel()
    vgs = (vinp[:, None] - vtail[None, :]).ravel()
    df = characterize_primitive(
        lut, prim.transistor_type,
        lengths_um=prim.lengths_um,
        vgs_sweep=vgs,
        vds_sweep=vds,
        id_target_per_branch=prim.il / 2.0,
    )
    return df


def build_current_mirror(prim: Primitive, lut: GmIdLookup) -> pd.DataFrame:
    """PMOS mirror: each branch carries ``il/2``. (vds, vgs) come from
    ``(VOUTP - VDD, VINP - VDD)`` in magnitude; sign handled by the
    characterizer."""
    voutp = _normalize_array(prim.ports["VOUTP"].dc_voltage)
    vinp = _normalize_array(prim.ports["VINP"].dc_voltage)
    vdd = _normalize_array(prim.ports["VDD"].dc_voltage)
    # For PMOS: V_SD = VDD - VOUTP (magnitude), V_SG = VDD - VINP.
    if prim.transistor_type == "pmos":
        vds = (vdd[:, None] - voutp[None, :]).ravel()
        vgs = (vdd[:, None] - vinp[None, :]).ravel()
    else:
        vds = (voutp[:, None] - vdd[None, :]).ravel()
        vgs = (vinp[:, None] - vdd[None, :]).ravel()
    df = characterize_primitive(
        lut, prim.transistor_type,
        lengths_um=prim.lengths_um,
        vgs_sweep=vgs,
        vds_sweep=vds,
        id_target_per_branch=prim.il / 2.0,
    )
    return df


def build_current_source(prim: Primitive, lut: GmIdLookup) -> pd.DataFrame:
    """NMOS current source with a diode-connected bias. Each branch
    carries the full ``il``. The output column ``width_m1``/``width_m2``
    is set to W from the sweep; ``vgs_cs`` exposes the actual VGS used
    so callers can wire the bias voltage hierarchy."""
    voutp = _normalize_array(prim.ports["VOUTP"].dc_voltage)
    vinp = _normalize_array(prim.ports["VINP"].dc_voltage)
    vss = _normalize_array(prim.ports["VSS"].dc_voltage)
    # NMOS magnitudes:
    vds = (voutp[:, None] - vss[None, :]).ravel()
    vgs = (vinp[:, None] - vss[None, :]).ravel()
    df = characterize_primitive(
        lut, prim.transistor_type,
        lengths_um=prim.lengths_um,
        vgs_sweep=vgs,
        vds_sweep=vds,
        id_target_per_branch=prim.il,
    )
    # Upstream convention: expose width_m1 and width_m2 separately so
    # the macromodel can flatten them as distinct outputs.
    df = df.rename(columns={"width": "width_m1"})
    df["width_m2"] = df["width_m1"]
    df["vgs_cs"] = df["vgs"]
    return df


def build_common_source(prim: Primitive, lut: GmIdLookup) -> pd.DataFrame:
    """PMOS common source. Single branch, full ``il``."""
    vout = _normalize_array(prim.ports["VOUT"].dc_voltage)
    vin = _normalize_array(prim.ports["VIN"].dc_voltage)
    vdd = _normalize_array(prim.ports["VDD"].dc_voltage)
    if prim.transistor_type == "pmos":
        vds = (vdd[:, None] - vout[None, :]).ravel()
        vgs = (vdd[:, None] - vin[None, :]).ravel()
    else:
        vds = (vout[:, None] - vdd[None, :]).ravel()
        vgs = (vin[:, None] - vdd[None, :]).ravel()
    df = characterize_primitive(
        lut, prim.transistor_type,
        lengths_um=prim.lengths_um,
        vgs_sweep=vgs,
        vds_sweep=vds,
        id_target_per_branch=prim.il,
    )
    return df


# Mapping of primitive name -> (factory fn, build fn).
_PRIMITIVE_REGISTRY: dict[str, tuple[Any, Any]] = {
    "simplediffpair":      (_diffpair,        build_diffpair),
    "simplecurrentmirror": (_current_mirror,  build_current_mirror),
    "simplecurrentsource": (_current_source,  build_current_source),
    "simplecommonsource":  (_common_source,   build_common_source),
}


def _bind_build(prim: Primitive, build_fn) -> Primitive:
    """Bind a build method onto a primitive instance."""

    def _runner(lut: GmIdLookup) -> pd.DataFrame:
        return build_fn(prim, lut)

    prim.build = _runner  # type: ignore[attr-defined]
    return prim


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


@dataclass
class Library:
    """Technology-aware registry of primitive factories.

    A ``Library`` is bound to a single ``GmIdLookup`` (which carries the
    PDK + LUT directory). Each ``get(name, ...)`` returns a **fresh**
    primitive instance with a bound ``build()`` method; modifying the
    returned primitive's ports does not affect future ``get()`` calls.

    Mirrors the upstream ``sstadex.models.Library`` semantics so the
    user-facing call patterns are the same. We deliberately drop the
    JSON/folder loader: every primitive is registered as a Python
    callable, which keeps the surface area minimal and avoids the
    file-loader complexity that upstream needs for arbitrary primitive
    extensions.
    """

    name: str
    lut: GmIdLookup
    _factories: dict[str, tuple[Any, Any]] = field(
        default_factory=lambda: dict(_PRIMITIVE_REGISTRY)
    )

    def get(self, primitive_name: str, **overrides) -> Primitive:
        """Return a fresh primitive instance bound to this library's
        LUT. ``overrides`` may include ``il`` (bias current) and any
        attribute of the primitive (``lengths_um``, ``transistor_type``,
        ...).
        """
        if primitive_name not in self._factories:
            raise KeyError(
                f"Primitive '{primitive_name}' not registered in "
                f"library '{self.name}'. Known: {list(self._factories)}"
            )
        factory, build_fn = self._factories[primitive_name]
        prim = factory()
        prim.pdk = self.lut.pdk
        for k, v in overrides.items():
            if k in ("il", "lengths_um", "transistor_type"):
                setattr(prim, k, v)
            else:
                # Forward unknown overrides as port voltages, if name matches.
                if k in prim.ports:
                    prim.set_port_voltage(k, v)
                else:
                    raise KeyError(
                        f"Cannot apply override '{k}' to primitive "
                        f"'{primitive_name}'."
                    )
        _bind_build(prim, build_fn)
        return prim

    def register(self, name: str, factory, build_fn) -> None:
        """Register an additional primitive class. ``factory`` returns
        a bare ``Primitive`` (ports + branches), ``build_fn`` is a
        callable ``(prim, lut) -> pd.DataFrame``."""
        self._factories[name] = (factory, build_fn)

    def list(self) -> list[str]:
        return list(self._factories)
