"""SAR ADC 11-bit design_reference greedy demo (S7).

Exercises :class:`SARADC11BitTopology` end-to-end. Because the 11-bit
flow is marked as ``design_reference`` (not silicon-validated), this
demo keeps the scope tight:

  - Runs the default design point plus two perturbations (larger CDAC,
    larger input pair) and compares the reported ENOB / SNDR / FoM.
  - Prints the ``check_system_validity`` verdict per point so reviewers
    can see which robustness gates bite first at 11 bits.
  - No autoresearch loop, no LLM calls. The intent is to prove the
    pipeline works and to anchor the FoM number.

Gracefully skips when ngspice / openvaf / Verilator are missing.

    PYTHONPATH=src python examples/13_sar_adc_11bit.py [--pdk ihp_sg13g2]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path


def _tool_missing() -> list[str]:
    missing: list[str] = []
    for tool in ("ngspice", "openvaf", "verilator"):
        if not shutil.which(tool):
            missing.append(tool)
    return missing


def _print_step(msg: str) -> None:
    print(f"[sar11] {msg}", flush=True)


def _perturbations(default: dict[str, float]) -> list[tuple[str, dict[str, float]]]:
    larger_cdac = dict(default)
    larger_cdac["cdac_C_unit_fF"] = min(2 * default["cdac_C_unit_fF"], 200.0)
    bigger_comp = dict(default)
    bigger_comp["comp_W_input_um"] = min(2 * default["comp_W_input_um"], 64.0)
    bigger_comp["comp_L_input_um"] = min(2 * default["comp_L_input_um"], 2.0)
    return [
        ("default", default),
        ("larger-cdac", larger_cdac),
        ("bigger-input-pair", bigger_comp),
    ]


async def _eval(runner, topo, tag: str, params: dict[str, float]):
    work_dir = Path(f"/tmp/eda_agents_sar11_{tag}")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        cir = topo.generate_system_netlist(params, work_dir)
    except RuntimeError as exc:
        print(f"[{tag}] SKIP: {exc}", file=sys.stderr)
        return None, []
    result = await runner.run_async(cir, work_dir)
    if not result.success:
        print(f"[{tag}] SPICE failed: {result.error}", file=sys.stderr)
        return None, []
    metrics = topo.extract_enob(work_dir)
    result.measurements.update(metrics)
    fom = topo.compute_system_fom(result, params)
    valid, violations = topo.check_system_validity(result, params)
    return (fom, valid, metrics), violations


async def _run(args) -> int:
    missing = _tool_missing()
    if missing:
        print(
            f"SKIP: missing tools in PATH: {', '.join(missing)}.",
            file=sys.stderr,
        )
        return 0

    from eda_agents.core.spice_runner import SpiceRunner
    from eda_agents.topologies.sar_adc_11bit import SARADC11BitTopology

    topo = SARADC11BitTopology(pdk=args.pdk)
    _print_step(f"topology: {topo.topology_name()} on {topo.pdk.display_name}")
    _print_step(
        "design_reference=True (not silicon-validated). "
        "IHP layout path blocked upstream."
    )

    runner = SpiceRunner(preload_pdk_osdi=True)
    rows: list[tuple[str, dict, tuple | None, list[str]]] = []
    for tag, params in _perturbations(topo.default_params()):
        _print_step(f"evaluating '{tag}'")
        result, violations = await _eval(runner, topo, tag, params)
        rows.append((tag, params, result, violations))

    print()
    print(f"{'tag':<20s} {'ENOB':>7s} {'SNDR[dB]':>10s} {'FoM':>10s} {'valid':>7s}")
    for tag, params, result, violations in rows:
        if result is None:
            print(f"{tag:<20s} {'-':>7s} {'-':>10s} {'-':>10s} {'SKIP':>7s}")
            continue
        fom, valid, metrics = result
        enob = metrics.get("enob", 0.0)
        sndr = metrics.get("sndr_dB", 0.0)
        print(
            f"{tag:<20s} {enob:>7.2f} {sndr:>10.2f} {fom:>10.2e} "
            f"{('PASS' if valid else 'FAIL'):>7s}"
        )
        for v in violations:
            print(f"    - {v}")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--pdk",
        default=None,
        choices=[None, "ihp_sg13g2", "gf180mcu"],
        help="PDK override (default: resolve_pdk default).",
    )
    return ap.parse_args(argv)


def main() -> int:
    args = _parse_args(sys.argv[1:])
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
