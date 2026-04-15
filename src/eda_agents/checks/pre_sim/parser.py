"""Very small SPICE-ish parser for pre-sim gates.

This is intentionally limited to what the open-source stack emits and
what a human reasonably writes for an analog subcircuit under test:

  - ``.subckt`` / ``.ends`` pair.
  - MOSFETs prefixed ``M`` (GF180 style: ``M1 d g s b model w=...``).
  - Subcircuit instances prefixed ``X`` with the model / subckt name
    as the last positional token (IHP convention). We classify ``X``
    devices as ``nmos`` / ``pmos`` when the model name contains
    ``nmos`` / ``pmos`` / ``nch`` / ``pch`` — that is the ergonomic
    covered by both PDKs in use. Other ``X`` devices become the
    ``subckt`` kind.
  - Simple voltage / current sources (``V`` / ``I``), resistors
    (``R``) and capacitors (``C``).
  - Comments (``*`` at line start or ``$`` / ``;`` mid-line) are
    ignored; ``+`` line continuations are merged.

Anything more complex (hierarchies, ``.model``, ``.lib``, ``.tran``…)
is either ignored or handed to the real SPICE tool later; the pre-sim
gates only need the structural skeleton.
"""

from __future__ import annotations

import re

from eda_agents.checks.pre_sim.model import Device, Subcircuit

_PARAM_RE = re.compile(r"(\w+)\s*=\s*([^\s=]+)")


def parse_subcircuit(text: str, name: str | None = None) -> Subcircuit:
    """Parse the first (or named) ``.subckt ... .ends`` block.

    Parameters
    ----------
    text
        Full netlist text.
    name
        If given, select the subcircuit with this exact name. When
        ``None`` the first ``.subckt`` in the text is returned.
    """
    lines = _join_continuations(_strip_comments(text).splitlines())
    sub_name: str | None = None
    ports: tuple[str, ...] = ()
    devices: list[Device] = []
    in_target = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(".subckt"):
            tokens = line.split()
            # ".subckt name port1 port2 ... param=value"
            if len(tokens) < 2:
                continue
            candidate = tokens[1]
            body = tokens[2:]
            positional: list[str] = []
            for tok in body:
                if "=" in tok:
                    break
                positional.append(tok)
            if name is None or candidate == name:
                if sub_name is not None:
                    # Already parsed a matching subcircuit — ignore the rest.
                    break
                sub_name = candidate
                ports = tuple(positional)
                in_target = True
            continue
        if low.startswith(".ends"):
            if in_target:
                break
            continue
        if not in_target:
            continue
        dev = _parse_device(line)
        if dev is not None:
            devices.append(dev)
    if sub_name is None:
        raise ValueError("no .subckt block found in netlist text")
    return Subcircuit(name=sub_name, ports=ports, devices=tuple(devices))


def _strip_comments(text: str) -> str:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw
        if line.lstrip().startswith("*"):
            continue
        # strip trailing $ / ; comments
        for marker in ("$", ";"):
            idx = line.find(marker)
            if idx >= 0:
                line = line[:idx]
        out.append(line)
    return "\n".join(out)


def _join_continuations(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("+") and out:
            out[-1] = out[-1] + " " + stripped[1:].strip()
        else:
            out.append(line)
    return out


def _parse_device(line: str) -> Device | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("."):
        return None
    tokens = stripped.split()
    first = tokens[0]
    letter = first[0].upper()
    name = first

    # Split positional tokens from key=value pairs.
    positional: list[str] = []
    params: dict[str, str] = {}
    for tok in tokens[1:]:
        if "=" in tok:
            m = _PARAM_RE.match(tok)
            if m:
                params[m.group(1).lower()] = m.group(2)
        else:
            positional.append(tok)

    if letter == "M":
        # M<name> d g s b model [params]
        if len(positional) < 5:
            return None
        nodes = tuple(positional[:-1])[:4]
        model = positional[-1]
        kind = _guess_mos_kind(model)
        if kind is None:
            # unknown MOS model — conservatively classify as nmos.
            kind = "nmos"
        return Device(name=name, kind=kind, nodes=nodes, model=model, params=params)

    if letter == "X":
        # X<name> node1 ... nodeN model_or_subckt_name [params]
        if len(positional) < 2:
            return None
        model = positional[-1]
        nodes = tuple(positional[:-1])
        kind = _guess_mos_kind(model) or "subckt"
        # MOSFET-like subcircuits need 4 terminals; otherwise downgrade.
        if kind in ("nmos", "pmos") and len(nodes) < 4:
            kind = "subckt"
        return Device(name=name, kind=kind, nodes=nodes, model=model, params=params)

    if letter == "V":
        if len(positional) < 2:
            return None
        value = " ".join(positional[2:]) if len(positional) > 2 else ""
        return Device(
            name=name,
            kind="vsource",
            nodes=tuple(positional[:2]),
            params={**params, "value": value} if value else params,
        )

    if letter == "I":
        if len(positional) < 2:
            return None
        value = " ".join(positional[2:]) if len(positional) > 2 else ""
        return Device(
            name=name,
            kind="isource",
            nodes=tuple(positional[:2]),
            params={**params, "value": value} if value else params,
        )

    if letter == "R":
        if len(positional) < 2:
            return None
        value = positional[2] if len(positional) > 2 else ""
        return Device(
            name=name,
            kind="resistor",
            nodes=tuple(positional[:2]),
            params={**params, "value": value} if value else params,
        )

    if letter == "C":
        if len(positional) < 2:
            return None
        value = positional[2] if len(positional) > 2 else ""
        return Device(
            name=name,
            kind="capacitor",
            nodes=tuple(positional[:2]),
            params={**params, "value": value} if value else params,
        )

    if letter == "L":
        if len(positional) < 2:
            return None
        value = positional[2] if len(positional) > 2 else ""
        return Device(
            name=name,
            kind="inductor",
            nodes=tuple(positional[:2]),
            params={**params, "value": value} if value else params,
        )

    return None


def _guess_mos_kind(model: str) -> str | None:
    m = model.lower()
    if any(tag in m for tag in ("pmos", "pch", "pfet")):
        return "pmos"
    if any(tag in m for tag in ("nmos", "nch", "nfet")):
        return "nmos"
    return None
