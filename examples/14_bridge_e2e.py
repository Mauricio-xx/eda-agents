"""End-to-end demo of the EDA bridge (Sesión 8).

What this exercises:

  1. ``eda_agents.bridge.JobRegistry`` — UUID-keyed JSON registry
     persisted under ``~/.cache/eda_agents/jobs/`` (or ``--jobs-dir``).
  2. ``eda_agents.bridge.SimulationResult`` — Pydantic v2 result wrapping
     ``SpiceRunner`` output.
  3. ``eda_agents.bridge.KLayoutOps`` — KLayout DRC facade (skipped if
     no GDS provided; KLayout-only signoff on IHP per the upstream
     Magic blocker).

Two execution paths:

  - GF180MCU: full SPICE + DRC if a GDS is provided.
  - IHP-SG13G2: SPICE only. KLayout DRC against IHP would require the
    ihp-sg13g2 KLayout deck path; that is intentionally not wired here
    because the upstream Magic blocker means full layout signoff for
    IHP is gated on KLayout-only flow that the bridge does not own.

The demo is **executable**: with ngspice + IHP PDK installed it
performs a real AC analysis and reports DC gain / GBW / phase margin.
Auditor-friendly output: every reported number is the parsed
``.meas`` value, never a heuristic estimate.

Usage::

    PYTHONPATH=src python examples/14_bridge_e2e.py
    PYTHONPATH=src python examples/14_bridge_e2e.py --pdk ihp_sg13g2
    PYTHONPATH=src python examples/14_bridge_e2e.py --pdk gf180mcu
    PYTHONPATH=src python examples/14_bridge_e2e.py --gds path.gds --top TOPCELL
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from eda_agents.bridge.jobs import JobRegistry, JobStatus
from eda_agents.bridge.klayout_ops import KLayoutOps
from eda_agents.bridge.models import (
    BridgeResult,
    ExecutionStatus,
    SimulationResult,
)


def _print_step(msg: str) -> None:
    print(f"[bridge-e2e] {msg}", flush=True)


def _spice_via_runner(cir_path: Path, work_dir: Path, pdk: str) -> SimulationResult:
    """Bridge facade over SpiceRunner.

    Returns a ``SimulationResult`` so the JobRegistry can persist it.
    The infra-error vs. tool-error distinction follows the same rule as
    XschemRunner: a missing binary or PDK -> ERROR; a non-zero ngspice
    exit -> FAILURE.
    """
    from eda_agents.core.spice_runner import SpiceRunner

    runner = SpiceRunner(pdk=pdk)
    missing = runner.validate_pdk()
    if missing:
        return SimulationResult(
            status=ExecutionStatus.ERROR,
            netlist=str(cir_path),
            errors=[f"PDK files missing: {p}" for p in missing],
            metadata={"pdk": pdk},
        )
    sp = runner.run(cir_path, work_dir=work_dir)
    if not sp.success:
        return SimulationResult(
            status=ExecutionStatus.FAILURE,
            netlist=str(cir_path),
            errors=[sp.error or "ngspice failed"],
            duration_s=sp.sim_time_s,
            metadata={"pdk": pdk, "stdout_tail": sp.stdout_tail[-1500:]},
        )
    measurements: dict[str, float] = {}
    for k, v in sp.measurements.items():
        if v is not None:
            measurements[k] = float(v)
    if sp.Adc_dB is not None:
        measurements["Adc_dB"] = float(sp.Adc_dB)
    if sp.GBW_Hz is not None:
        measurements["GBW_Hz"] = float(sp.GBW_Hz)
    if sp.PM_deg is not None:
        measurements["PM_deg"] = float(sp.PM_deg)
    if sp.power_uW is not None:
        measurements["power_uW"] = float(sp.power_uW)
    return SimulationResult(
        status=ExecutionStatus.SUCCESS,
        netlist=str(cir_path),
        measurements=measurements,
        duration_s=sp.sim_time_s,
        metadata={"pdk": pdk},
    )


def _build_miller_ota_deck(work_dir: Path, pdk: str) -> Path:
    """Generate a sane Miller OTA deck via the existing topology."""
    from eda_agents.topologies.miller_ota import MillerOTADesigner

    designer = MillerOTADesigner(pdk=pdk)
    # Use the legacy GBW-driven sizing (no Ibias override) — that path
    # is what examples/01_miller_ota_sweep.py exercises and is known to
    # converge cleanly on both PDKs. The demo measures the bridge, not
    # the OTA, so we want a deck that produces sane numbers, not the
    # one that wins QoR.
    result = designer.analytical_design(
        gmid_input=12.0,
        gmid_load=10.0,
        L_input=1.0e-6,
        L_load=1.0e-6,
        Cc=1.0e-12,
    )
    return designer.generate_netlist(result, work_dir)


def _audit_simulation(sim: SimulationResult) -> tuple[bool, list[str]]:
    """Audit step. We refuse to call a run "good" just because rc==0.

    The plan handoff is explicit: ``FoM > 0`` is not a green light. We
    enforce three structural sanity checks against the parsed
    measurements before declaring the bridge demo successful.
    """
    notes: list[str] = []
    ok = True
    if sim.status is not ExecutionStatus.SUCCESS:
        return False, [f"sim status was {sim.status.value}"]
    m = sim.measurements
    if "Adc_dB" not in m or m["Adc_dB"] < 20:
        ok = False
        notes.append(
            f"DC gain looks bogus: Adc_dB={m.get('Adc_dB')!r} (expected > 20 dB)"
        )
    else:
        notes.append(f"DC gain plausible: {m['Adc_dB']:.1f} dB")
    if "GBW_Hz" not in m or m["GBW_Hz"] < 1e3:
        ok = False
        notes.append(
            f"GBW looks bogus: GBW_Hz={m.get('GBW_Hz')!r} (expected > 1 kHz)"
        )
    else:
        notes.append(f"GBW plausible: {m['GBW_Hz']:.2e} Hz")
    if "PM_deg" not in m:
        notes.append("PM not parsed (some decks omit it; treat as warning)")
    else:
        notes.append(f"PM: {m['PM_deg']:.1f} deg")
    return ok, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdk",
        default="ihp_sg13g2",
        choices=["ihp_sg13g2", "gf180mcu"],
        help="PDK for the SPICE leg of the demo.",
    )
    parser.add_argument(
        "--gds",
        type=Path,
        default=None,
        help="Optional GDS for KLayout DRC. Currently exercised for GF180 only.",
    )
    parser.add_argument(
        "--top",
        default=None,
        help="Top cell name for KLayout DRC (auto if omitted).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("/tmp/eda_agents_bridge_e2e"),
        help="Working dir for netlist + jobs.",
    )
    args = parser.parse_args()

    if args.work_dir.exists():
        shutil.rmtree(args.work_dir)
    args.work_dir.mkdir(parents=True)
    netlist_dir = args.work_dir / "netlist"
    jobs_dir = args.work_dir / "jobs"

    if not shutil.which("ngspice"):
        print("SKIP: ngspice not in PATH", file=sys.stderr)
        return 0

    # -- 1. Generate netlist via existing topology layer ------------------
    _print_step(f"PDK = {args.pdk}")
    try:
        cir_path = _build_miller_ota_deck(netlist_dir, args.pdk)
    except Exception as exc:  # noqa: BLE001 — surface to user
        print(f"FAIL: deck generation failed: {exc}", file=sys.stderr)
        return 2
    _print_step(f"deck written: {cir_path}")

    # -- 2. Submit ngspice job through the bridge JobRegistry -------------
    registry = JobRegistry(jobs_dir=jobs_dir, max_workers=2)
    try:
        sim_job = registry.submit(
            _spice_via_runner,
            cir_path=cir_path,
            work_dir=netlist_dir,
            pdk=args.pdk,
            kind="ngspice-ac",
            metadata={"pdk": args.pdk},
        )
        _print_step(f"submitted ngspice job {sim_job}")
        sim_record = registry.wait(sim_job, timeout=240)
        if sim_record is None:
            print("FAIL: simulation job vanished from registry", file=sys.stderr)
            return 2
        if sim_record.status is JobStatus.ERROR:
            print(
                f"FAIL: simulation infra error: {sim_record['errors']}",
                file=sys.stderr,
            )
            return 2
        # Re-hydrate the typed result from the dict the runner stored.
        sim_payload = sim_record.get("result") or {}
        sim = SimulationResult.model_validate(sim_payload)
        _print_step(f"simulation status: {sim.status.value}")
        _print_step(
            "measurements: "
            + ", ".join(f"{k}={v}" for k, v in sim.measurements.items())
        )

        # Persist the typed result alongside the registry record.
        sim.save_json(args.work_dir / "simulation.json")

        # -- 3. Audit before declaring success ------------------------------
        ok, notes = _audit_simulation(sim)
        for n in notes:
            _print_step(f"audit: {n}")

        # -- 4. Optional KLayout DRC ----------------------------------------
        drc_summary: BridgeResult | None = None
        if args.gds:
            if args.pdk == "ihp_sg13g2":
                _print_step(
                    "DRC requested but PDK is IHP — KLayout deck not wired "
                    "for IHP in this demo (Magic blocker tracking applies "
                    "to upstream signoff, not to KLayoutOps directly). "
                    "Skipping DRC."
                )
            else:
                if not shutil.which("klayout"):
                    _print_step("klayout missing — skipping DRC")
                else:
                    klayout = KLayoutOps()
                    drc_summary = klayout.run_drc(
                        gds_path=args.gds,
                        run_dir=args.work_dir / "drc",
                        top_cell=args.top,
                    )
                    _print_step(f"DRC status: {drc_summary.status.value}")
                    _print_step(f"DRC summary: {drc_summary.output}")
                    drc_summary.save_json(args.work_dir / "drc.json")
        else:
            _print_step("no --gds provided; DRC step skipped (expected)")
    finally:
        registry.shutdown()

    print()
    print("=" * 60)
    print(f"sim status      : {sim.status.value}")
    print(f"audit verdict   : {'PASS' if ok else 'FAIL'}")
    if drc_summary is not None:
        print(f"DRC status      : {drc_summary.status.value}")
    print(f"jobs persisted  : {jobs_dir}")
    print(f"sim json        : {args.work_dir / 'simulation.json'}")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
