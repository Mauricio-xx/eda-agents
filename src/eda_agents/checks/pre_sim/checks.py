"""Structural pre-sim gates for analog subcircuits.

Each function is pure (``Subcircuit -> CheckResult``) and has no side
effects. ``run_all`` bundles the default set for the harness.
"""

from __future__ import annotations

from collections.abc import Mapping

from eda_agents.checks.pre_sim.model import CheckResult, Device, Subcircuit


def check_floating_nodes(sc: Subcircuit) -> CheckResult:
    """Flag internal nets that touch fewer than two device terminals.

    Subcircuit ports are exempt (they are driven externally). Supplies
    are exempt when they appear: a supply rail connected once inside
    a subckt is legitimate.
    """
    external = {p.lower() for p in sc.ports}
    issues: list[str] = []
    for net, conns in sc.net_to_devices().items():
        if net.lower() in external:
            continue
        if sc.is_supply(net):
            continue
        if len(conns) < 2:
            who = conns[0][0].name if conns else "<unknown>"
            issues.append(f"net '{net}' has only one connection ({who})")
    return CheckResult(
        name="floating_nodes",
        passed=not issues,
        severity="error",
        messages=tuple(issues),
    )


def check_bulk_connections(sc: Subcircuit) -> CheckResult:
    """NMOS bulk to VSS/0/source, PMOS bulk to VDD/source."""
    issues: list[str] = []
    for dev in sc.devices:
        if not dev.is_mosfet:
            continue
        bulk = dev.bulk
        source = dev.source
        if bulk is None or source is None:
            issues.append(f"{dev.name} is missing drain/gate/source/bulk nodes")
            continue
        b = bulk.lower()
        s = (source or "").lower()
        if dev.kind == "nmos":
            if b not in ("0", "gnd", "vss", "vssa", "vssd", s):
                issues.append(
                    f"{dev.name} (nmos) bulk '{bulk}' not tied to VSS/gnd or source"
                )
        elif dev.kind == "pmos":
            if b not in ("vdd", "vdda", "vddd", "vcc", s):
                issues.append(
                    f"{dev.name} (pmos) bulk '{bulk}' not tied to VDD or source"
                )
    return CheckResult(
        name="bulk_connections",
        passed=not issues,
        severity="error",
        messages=tuple(issues),
    )


def check_mirror_ratio(
    sc: Subcircuit,
    declared_ratios: Mapping[tuple[str, str], float] | None = None,
    tol: float = 0.05,
) -> CheckResult:
    """Effective width ratios of gate-sharing transistor pairs.

    If ``declared_ratios`` is provided as a map ``(ref, mirror) ->
    ratio``, the gate compares the structural width ratio against the
    declared one and flags mismatches > ``tol``. When no declarations
    are given, the gate simply verifies that both devices in a gate-
    sharing pair expose a positive effective width — a zero means
    the sizing is missing or nonsensical.
    """
    issues: list[str] = []
    declared = dict(declared_ratios or {})
    pairs_seen: set[tuple[str, str]] = set()
    by_gate: dict[tuple[str, str], list[Device]] = {}
    for dev in sc.devices:
        if not dev.is_mosfet or dev.gate is None:
            continue
        by_gate.setdefault((dev.kind, dev.gate), []).append(dev)
    for (kind, gate), devices in by_gate.items():
        if len(devices) < 2:
            continue
        # Use the first device as reference for width-ratio sanity.
        ref = devices[0]
        ref_w = ref.width_m()
        if ref_w <= 0:
            issues.append(f"{ref.name} (gate '{gate}') has non-positive effective width")
            continue
        for mirror in devices[1:]:
            mir_w = mirror.width_m()
            if mir_w <= 0:
                issues.append(
                    f"{mirror.name} (gate '{gate}') has non-positive effective width"
                )
                continue
            ratio = mir_w / ref_w
            key = (ref.name, mirror.name)
            pairs_seen.add(key)
            if key in declared:
                want = declared[key]
                if want <= 0:
                    issues.append(
                        f"declared ratio for {key} must be positive, got {want}"
                    )
                    continue
                if abs(ratio - want) / want > tol:
                    issues.append(
                        f"mirror {mirror.name}/{ref.name}: W-ratio {ratio:.3g} "
                        f"differs from declared {want:.3g} by > {tol:.0%}"
                    )
    for key in declared:
        if key not in pairs_seen:
            issues.append(
                f"declared mirror {key} not found as gate-sharing MOSFET pair"
            )
    return CheckResult(
        name="mirror_ratio",
        passed=not issues,
        severity="warn",
        messages=tuple(issues),
    )


def check_bias_source(sc: Subcircuit) -> CheckResult:
    """Every current-source MOSFET gate must reach a bias source.

    A MOSFET is considered "current-source-like" when its drain is
    not the same as its gate (i.e. it is not diode-connected) and
    its gate is not an external port. For each such gate net we
    require that at least one of the following is true:

      - a voltage source drives the net,
      - a current source drives the net,
      - the net is tied to a diode-connected transistor
        (drain == gate) somewhere in the subcircuit.
    """
    external = {p.lower() for p in sc.ports}
    net_map = sc.net_to_devices()
    bias_nets: set[str] = set()
    for dev in sc.devices:
        if dev.is_mosfet and dev.drain == dev.gate and dev.gate is not None:
            bias_nets.add(dev.gate.lower())
        if dev.kind in ("vsource", "isource"):
            for n in dev.nodes:
                bias_nets.add(n.lower())

    issues: list[str] = []
    for dev in sc.devices:
        if not dev.is_mosfet:
            continue
        gate = dev.gate
        if gate is None:
            continue
        if gate.lower() in external:
            continue
        if dev.drain == gate:  # diode-connected: self-bias.
            continue
        siblings = net_map.get(gate, [])
        # Treat gate as a signal node (not a bias) when another MOSFET's
        # drain ties into the same net -- that is a normal cascode /
        # second-stage hand-off and does not need a separate bias source.
        signal_path = any(
            d.is_mosfet and d.drain == gate and d is not dev
            for d, _ in siblings
        )
        if signal_path:
            continue
        if gate.lower() in bias_nets:
            continue
        # Accept if another MOSFET on the same gate is diode-connected.
        diode_connected = any(
            d.is_mosfet and d.drain == d.gate and d is not dev
            for d, _ in siblings
        )
        if diode_connected:
            continue
        # Accept if a voltage/current source directly drives this net.
        sourced = any(d.kind in ("vsource", "isource") for d, _ in siblings)
        if sourced:
            continue
        issues.append(
            f"{dev.name} gate net '{gate}' has no bias source or diode-connected anchor"
        )
    return CheckResult(
        name="bias_source",
        passed=not issues,
        severity="error",
        messages=tuple(issues),
    )


def check_testbench_pin_match(
    definition: Subcircuit, instantiation: Device
) -> CheckResult:
    """DUT instance port arity must match the subcircuit definition.

    ``instantiation`` is expected to be a ``kind='subckt'`` (or a MOS-
    kind whose model matches the definition name) device parsed from
    the testbench. We do not infer net semantics — just arity and
    model name.
    """
    issues: list[str] = []
    if instantiation.model.lower() != definition.name.lower():
        issues.append(
            f"instance '{instantiation.name}' references model "
            f"'{instantiation.model}' but definition is '{definition.name}'"
        )
    want = len(definition.ports)
    got = len(instantiation.nodes)
    if want != got:
        issues.append(
            f"instance '{instantiation.name}' has {got} terminals but "
            f"definition '{definition.name}' declares {want} ports "
            f"({', '.join(definition.ports)})"
        )
    return CheckResult(
        name="testbench_pin_match",
        passed=not issues,
        severity="error",
        messages=tuple(issues),
    )


def check_vds_polarity(sc: Subcircuit) -> CheckResult:
    """Flag MOSFETs whose source pin lands on the wrong supply rail.

    Structural proxy for a drain-source swap: an NMOS with ``source``
    wired directly to a VDD alias, or a PMOS with ``source`` wired
    directly to a VSS/ground alias, almost always means the author
    swapped the drain and source positions in the instance line.

    The gate operates on static net names; it cannot catch Vds
    inversions that only manifest from the DC operating point
    (e.g. input-referred voltage swings driving source above drain at
    specific bias). Those are the domain of simulation, not a
    pre-sim structural pass. But it covers the obvious copy-paste
    error where an NMOS drain accidentally lands on VDD while source
    lands on an output node, a pattern bench gap #7 reproduces on a
    StrongARM input pair.
    """
    vdd_aliases = {"vdd", "vdda", "vddd", "vcc", "vdd33", "vcca", "vccad"}
    vss_aliases = {"0", "gnd", "vss", "vssa", "vssd"}
    issues: list[str] = []
    for dev in sc.devices:
        if not dev.is_mosfet:
            continue
        src = (dev.source or "").lower()
        if not src:
            continue
        if dev.kind == "nmos" and src in vdd_aliases:
            issues.append(
                f"{dev.name} (nmos) source '{dev.source}' on a VDD rail "
                f"— drain-source swap suspected"
            )
        elif dev.kind == "pmos" and src in vss_aliases:
            issues.append(
                f"{dev.name} (pmos) source '{dev.source}' on a VSS/ground rail "
                f"— drain-source swap suspected"
            )
    return CheckResult(
        name="vds_polarity",
        passed=not issues,
        severity="error",
        messages=tuple(issues),
    )


def run_all(
    sc: Subcircuit,
    declared_ratios: Mapping[tuple[str, str], float] | None = None,
) -> list[CheckResult]:
    """Run the default pre-sim gates over ``sc``.

    ``check_testbench_pin_match`` is intentionally omitted here; it
    takes a second argument (the DUT instantiation) and is expected
    to be called by the verifier once the testbench has been parsed.
    """
    return [
        check_floating_nodes(sc),
        check_bulk_connections(sc),
        check_mirror_ratio(sc, declared_ratios=declared_ratios),
        check_bias_source(sc),
        check_vds_polarity(sc),
    ]
