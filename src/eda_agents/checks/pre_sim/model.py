"""Lightweight dataclasses for structural netlist analysis.

These types are deliberately minimal. They describe only what the
pre-sim gates consult: device kind, terminal nets, and a few sized
parameters. Anything richer (models, bins, subsubcircuits) is out of
scope here and is handled by the real SPICE tools downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


DEVICE_KINDS = (
    "nmos",
    "pmos",
    "vsource",
    "isource",
    "resistor",
    "capacitor",
    "inductor",
    "subckt",
)


@dataclass(frozen=True)
class Device:
    """A single device instantiation line, already classified."""

    name: str
    kind: str
    nodes: tuple[str, ...]
    model: str = ""
    params: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in DEVICE_KINDS:
            raise ValueError(
                f"Device '{self.name}' has unknown kind '{self.kind}'. "
                f"Expected one of {DEVICE_KINDS}."
            )

    @property
    def is_mosfet(self) -> bool:
        return self.kind in ("nmos", "pmos")

    @property
    def drain(self) -> str | None:
        return self.nodes[0] if self.is_mosfet and len(self.nodes) >= 4 else None

    @property
    def gate(self) -> str | None:
        return self.nodes[1] if self.is_mosfet and len(self.nodes) >= 4 else None

    @property
    def source(self) -> str | None:
        return self.nodes[2] if self.is_mosfet and len(self.nodes) >= 4 else None

    @property
    def bulk(self) -> str | None:
        return self.nodes[3] if self.is_mosfet and len(self.nodes) >= 4 else None

    def width_m(self) -> float:
        """Effective width expressed as ``W * m * nf`` (best-effort).

        Returns 0.0 when the device has no W parameter.
        """
        w = _numeric(self.params.get("w") or self.params.get("W"))
        if w <= 0:
            return 0.0
        m = _numeric(self.params.get("m") or self.params.get("M") or 1.0)
        nf = _numeric(self.params.get("nf") or self.params.get("NF") or 1.0)
        mult = _numeric(self.params.get("multi") or self.params.get("MULTI") or 1.0)
        return w * max(m, 1.0) * max(nf, 1.0) * max(mult, 1.0)


@dataclass(frozen=True)
class Subcircuit:
    """A parsed subcircuit: ports + device list."""

    name: str
    ports: tuple[str, ...]
    devices: tuple[Device, ...]

    def supply_nets(self) -> set[str]:
        """Heuristic supply net identifiers (vdd/vss/gnd + aliases)."""
        supplies = {"0", "gnd", "vss", "vssa", "vssd"}
        supplies |= {"vdd", "vdda", "vddd", "vcc"}
        # Any net reachable only from a single voltage source with
        # the other terminal at 0/gnd behaves like a supply too; we
        # leave that richer reasoning out on purpose.
        return supplies

    def is_supply(self, net: str) -> bool:
        return net.lower() in self.supply_nets()

    def net_to_devices(self) -> dict[str, list[tuple[Device, int]]]:
        """Map net -> list of (device, terminal_index)."""
        out: dict[str, list[tuple[Device, int]]] = {}
        for dev in self.devices:
            for i, node in enumerate(dev.nodes):
                out.setdefault(node, []).append((dev, i))
        return out


@dataclass(frozen=True)
class CheckResult:
    """Structured result of a single pre-sim gate."""

    name: str
    passed: bool
    severity: str  # "error" | "warn"
    messages: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.passed

    def summary(self) -> str:
        tag = "PASS" if self.passed else ("WARN" if self.severity == "warn" else "FAIL")
        return f"[{tag}] {self.name}"


def _numeric(raw: str | float | int | None) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().lower().replace("µ", "u")
    if not s:
        return 0.0
    # Strip SI suffix (f, p, n, u, m, k, meg, g, t) with forgiving regex.
    suffix_map = {
        "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3,
        "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
    }
    num_part = s
    suffix_val = 1.0
    for suffix, mult in sorted(suffix_map.items(), key=lambda kv: -len(kv[0])):
        if num_part.endswith(suffix):
            num_part = num_part[: -len(suffix)]
            suffix_val = mult
            break
    try:
        return float(num_part) * suffix_val
    except ValueError:
        return 0.0
