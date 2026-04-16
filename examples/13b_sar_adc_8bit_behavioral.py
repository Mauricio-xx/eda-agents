"""SAR ADC 8-bit behavioural end-to-end demo (S7, Arcadia integration).

Drives ``SARADC8BitBehavioralTopology`` through a single evaluation:

  1. Checks that ngspice, openvaf, Verilator, and the XSPICE toolchain
     are available. Skips cleanly with an actionable message otherwise.
  2. Builds the behavioural comparator kit (compiles the XSPICE ``.cm``).
  3. Generates the SAR deck with the StrongARM swapped for
     ``ea_comparator_ideal``.
  4. Runs ``SpiceRunner.run_async`` with ``extra_codemodel`` wiring the
     compiled ``.cm`` and ``preload_pdk_osdi=True`` so the PSP103 OSDI
     is available next to the XSPICE primitives.
  5. Extracts ENOB / SNDR / SFDR / THD via
     :func:`eda_agents.tools.adc_metrics.compute_adc_metrics`.
  6. Prints the FoM and the validation verdict.

This demo exists to close the S6 handoff gap ("no executable SAR demo")
and to prove the behavioural path works end-to-end without any LLM
calls. Run with::

    PYTHONPATH=src python examples/13b_sar_adc_8bit_behavioral.py

It has no autoresearch loop and no LLM dependency.
"""

from __future__ import annotations

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
    print(f"[sar8-beh] {msg}", flush=True)


async def _run() -> int:
    missing = _tool_missing()
    if missing:
        print(
            f"SKIP: missing tools in PATH: {', '.join(missing)}.\n"
            "Install ngspice (>=44), openvaf, and Verilator or use the "
            "docker/xspice.Dockerfile image shipped in this repo.",
            file=sys.stderr,
        )
        return 0

    from eda_agents.core.spice_runner import SpiceRunner
    from eda_agents.topologies.sar_adc_8bit_behavioral import (
        SARADC8BitBehavioralTopology,
    )

    topo = SARADC8BitBehavioralTopology()
    params = topo.default_params()
    work_dir = Path("/tmp/eda_agents_sar8_beh_demo")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    _print_step(
        f"topology: {topo.topology_name()} on {topo.pdk.display_name}"
    )
    _print_step(f"params: {params}")

    try:
        cir = topo.generate_system_netlist(params, work_dir)
    except RuntimeError as exc:
        print(f"SKIP: {exc}", file=sys.stderr)
        return 0
    _print_step(f"deck written: {cir}")

    cm_path = topo.last_codemodel_path
    runner = SpiceRunner(
        extra_codemodel=[cm_path] if cm_path else None,
        preload_pdk_osdi=True,
    )
    result = await runner.run_async(cir, work_dir)
    if not result.success:
        print(f"FAIL: ngspice returned error: {result.error}", file=sys.stderr)
        return 1
    _print_step("ngspice OK")

    metrics = topo.extract_enob(work_dir)
    result.measurements.update(metrics)
    _print_step(f"measurements: { {k: result.measurements.get(k) for k in ['enob', 'sndr_dB', 'sfdr_dB', 'thd_dB', 'avg_idd']} }")

    fom = topo.compute_system_fom(result, params)
    valid, violations = topo.check_system_validity(result, params)

    print()
    print(f"FoM (Walden, higher=better): {fom:.2e}")
    print(f"Verdict : {'PASS' if valid else 'FAIL'}")
    for v in violations:
        print(f"  - {v}")
    return 0 if valid or fom > 0 else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
