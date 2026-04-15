"""Harness adapters for the bench runner.

A *callable adapter* is a small function that the runner can invoke to
execute one task. Each adapter takes a :class:`BenchTask` plus a working
directory and returns an :class:`AdapterResult`. The runner translates
that into a :class:`BenchResult`.

Adapters are intentionally **thin** — they wrap existing topology /
checks / harness code without re-implementing it. They never modify
``agents/analog_roles/``, ``agents/adk_harness.py``, or any
``topologies/sar_adc_*`` module (those are S6/S7 deliverables); they
only call into them.

The runner dispatches by ``BenchTask.harness``:

* ``dry_run``                — :func:`dry_run_adapter` (deterministic mock)
* ``analog_roles``           — :func:`analog_roles_adapter`
  (DryRunExecutor, no LLM calls — proves the DAG wiring works end-to-end
  without paying for a model)
* ``callable``               — uses ``inputs.callable`` dotted path,
  resolved via :func:`resolve_callable`. Lets task YAMLs point to one
  of the helpers below (``analytical_miller_design``,
  ``run_pre_sim_check`` …) without giving them magic strings.

Each helper that talks to ngspice tolerates a missing tool by reporting
``backend_used="ngspice-missing"`` and ``status=FAIL_INFRA``; the runner
maps that to ``SKIPPED`` rather than ``FAIL`` so the smoke summary stays
honest.
"""

from __future__ import annotations

import importlib
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from eda_agents.bench.models import BenchStatus, BenchTask


@dataclass
class AdapterResult:
    """Internal handoff between an adapter and the runner."""

    status: BenchStatus
    backend_used: str
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Free-form hooks for scoring. The runner reads ``compile`` and
    # ``sim_run`` to populate the corresponding score columns when the
    # task asks for them.
    compile_ok: bool | None = None
    sim_ok: bool | None = None
    raw_text: str = ""  # for regex_match scoring


# ---------------------------------------------------------------------------
# dry-run mock — pipeline smoke without any tool dependency
# ---------------------------------------------------------------------------


def dry_run_adapter(task: BenchTask, work_dir: Path) -> AdapterResult:
    """Always succeeds with deterministic synthetic metrics.

    Used to validate the runner pipeline end-to-end on CI hosts with no
    ngspice / Verilator / LibreLane available. ``inputs.fake_metrics``
    can override the canned numbers per task.
    """
    fake = task.inputs.get("fake_metrics") or {}
    metrics = {"Adc_dB": 60.0, "GBW_Hz": 5.0e6, "PM_deg": 65.0}
    metrics.update({k: float(v) for k, v in fake.items()})
    artifact = work_dir / "dry_run.txt"
    work_dir.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        "dry-run adapter: deterministic output.\n"
        f"task_id={task.id} family={task.family.value}\n"
    )
    return AdapterResult(
        status=BenchStatus.PASS,
        backend_used="dry-run",
        metrics=metrics,
        artifacts=[str(artifact)],
        compile_ok=True,
        sim_ok=True,
        raw_text="DRY_RUN_OK\n" + artifact.read_text(),
    )


# ---------------------------------------------------------------------------
# analog_roles DAG (DryRunExecutor)
# ---------------------------------------------------------------------------


def analog_roles_adapter(task: BenchTask, work_dir: Path) -> AdapterResult:
    """Run the 4-role DAG (S6) with the bundled DryRunExecutor.

    No LLM is invoked. The adapter exercises real harness wiring,
    iteration log, and skill rendering — failures here would mean the
    S6 DAG drifted, not that the bench is wrong. Because the bench
    *invokes* the harness (per Sesión 9 plan), this adapter is read-only
    on ``agents/analog_roles/``.
    """
    from eda_agents.agents.analog_roles import (
        AnalogRolesHarness,
        DryRunExecutor,
    )
    from eda_agents.specs import load_spec_from_string

    spec_yaml = task.inputs.get("spec_yaml")
    if not spec_yaml:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="analog_roles",
            errors=["analog_roles task missing inputs.spec_yaml"],
        )
    try:
        spec = load_spec_from_string(spec_yaml)
    except Exception as exc:  # noqa: BLE001 — surface to runner
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="analog_roles",
            errors=[f"spec load failed: {type(exc).__name__}: {exc}"],
        )

    harness = AnalogRolesHarness(
        spec=spec,
        executor=DryRunExecutor(verbose=False),
        max_iterations=task.inputs.get("max_iterations", 3),
    )
    output = harness.run()
    work_dir.mkdir(parents=True, exist_ok=True)
    log_path = work_dir / "iteration_log.yaml"
    harness.save_log(log_path)
    status = (
        BenchStatus.PASS if output.passed() else BenchStatus.FAIL_AUDIT
    )
    return AdapterResult(
        status=status,
        backend_used="analog_roles-dry",
        metrics={
            "iterations_used": output.iterations_used,
            "final_status": output.final_status,
        },
        artifacts=[str(log_path)],
        notes=[f"final_status={output.final_status}"],
        compile_ok=True,
        sim_ok=output.passed(),
        raw_text=log_path.read_text(),
    )


# ---------------------------------------------------------------------------
# callable dispatch — task YAMLs reference dotted paths to helpers
# ---------------------------------------------------------------------------


def resolve_callable(dotted: str) -> Callable[..., AdapterResult]:
    """Resolve ``module.path:func`` (or ``module.path.func``) to a callable.

    Restricted to ``eda_agents.bench.adapters`` to avoid arbitrary code
    execution from the task YAMLs.
    """
    if ":" in dotted:
        mod_name, func_name = dotted.split(":", 1)
    else:
        mod_name, _, func_name = dotted.rpartition(".")
    if not mod_name.startswith("eda_agents.bench.adapters"):
        raise ValueError(
            f"callable {dotted!r} must live under eda_agents.bench.adapters"
        )
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, func_name)
    if not callable(fn):
        raise TypeError(f"{dotted} is not callable")
    return fn


def callable_adapter(task: BenchTask, work_dir: Path) -> AdapterResult:
    """Dispatch to the callable named by ``inputs.callable``."""
    dotted = task.inputs.get("callable")
    if not dotted:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="callable",
            errors=["callable task missing inputs.callable"],
        )
    try:
        fn = resolve_callable(dotted)
    except Exception as exc:  # noqa: BLE001
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="callable",
            errors=[f"resolve_callable failed: {type(exc).__name__}: {exc}"],
        )
    return fn(task, work_dir)


# ---------------------------------------------------------------------------
# Concrete callables — the only place that touches real EDA tools.
# ---------------------------------------------------------------------------


def analytical_miller_design(task: BenchTask, work_dir: Path) -> AdapterResult:
    """Generate a Miller OTA via the analytical designer + run AC sim.

    Used by spec-to-topology / end-to-end tasks. The deck generator is
    ``topologies/miller_ota.py`` (read-only); SPICE goes through
    ``core/spice_runner.py`` (the parser bug there was fixed in S8).
    """
    from eda_agents.core.pdk import resolve_pdk
    from eda_agents.core.spice_runner import SpiceRunner
    from eda_agents.topologies.miller_ota import MillerOTADesigner

    pdk_name = task.pdk or "ihp_sg13g2"
    if not shutil.which("ngspice"):
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="ngspice-missing",
            errors=["ngspice binary not on PATH"],
        )
    pdk_obj = resolve_pdk(pdk_name)
    backend_label = "ngspice-osdi" if pdk_obj.has_osdi() else "ngspice"
    inputs = task.inputs.get("design_params") or {}
    designer = MillerOTADesigner(pdk=pdk_obj)
    try:
        result = designer.analytical_design(
            gmid_input=inputs.get("gmid_input", 12.0),
            gmid_load=inputs.get("gmid_load", 10.0),
            L_input=inputs.get("L_input", 1.0e-6),
            L_load=inputs.get("L_load", 1.0e-6),
            Cc=inputs.get("Cc", 1.0e-12),
        )
    except Exception as exc:  # noqa: BLE001
        return AdapterResult(
            status=BenchStatus.ERROR,
            backend_used=backend_label,
            errors=[f"analytical_design crashed: {type(exc).__name__}: {exc}"],
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    cir = designer.generate_netlist(result, work_dir)
    runner = SpiceRunner(pdk=pdk_name)
    sp = runner.run(cir, work_dir=work_dir)
    artifacts = [str(cir)]
    metrics: dict[str, Any] = {}
    notes: list[str] = []
    raw = sp.stdout_tail or ""
    if not sp.success:
        return AdapterResult(
            status=BenchStatus.FAIL_SIM,
            backend_used=backend_label,
            metrics=metrics,
            artifacts=artifacts,
            errors=[sp.error or "ngspice failed"],
            notes=notes,
            compile_ok=True,  # the deck generator did its job
            sim_ok=False,
            raw_text=raw,
        )
    if sp.Adc_dB is not None:
        metrics["Adc_dB"] = float(sp.Adc_dB)
    if sp.GBW_Hz is not None:
        metrics["GBW_Hz"] = float(sp.GBW_Hz)
    if sp.PM_deg is not None:
        metrics["PM_deg"] = float(sp.PM_deg)
    if sp.power_uW is not None:
        metrics["power_uW"] = float(sp.power_uW)
    notes.append(
        f"ngspice ok in {sp.sim_time_s:.2f}s; "
        f"measurements={sorted(metrics)}"
    )
    return AdapterResult(
        status=BenchStatus.PASS,
        backend_used=backend_label,
        metrics=metrics,
        artifacts=artifacts,
        notes=notes,
        compile_ok=True,
        sim_ok=True,
        raw_text=raw,
    )


def run_pre_sim_gate_on_inline_netlist(
    task: BenchTask, work_dir: Path
) -> AdapterResult:
    """Run a single pre-sim gate against an inline subcircuit text.

    Used by bugfix tasks. ``inputs.netlist`` is the SPICE text;
    ``inputs.subckt`` is the subcircuit name; ``inputs.gate`` selects
    the check (``floating_nodes`` / ``bulk_connections`` /
    ``mirror_ratio`` / ``bias_source``); ``inputs.expect_violation``
    is True if a violation is the *correct* outcome.
    """
    from eda_agents.checks.pre_sim import (
        check_bias_source,
        check_bulk_connections,
        check_floating_nodes,
        check_mirror_ratio,
        parse_subcircuit,
    )

    gate = task.inputs.get("gate")
    netlist = task.inputs.get("netlist")
    subckt = task.inputs.get("subckt")
    expect_violation = bool(task.inputs.get("expect_violation", False))
    if not (gate and netlist and subckt):
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="dry-run",
            errors=[
                "pre-sim task requires inputs.gate, inputs.netlist, inputs.subckt"
            ],
        )
    fn_table = {
        "floating_nodes": check_floating_nodes,
        "bulk_connections": check_bulk_connections,
        "mirror_ratio": check_mirror_ratio,
        "bias_source": check_bias_source,
    }
    fn = fn_table.get(gate)
    if fn is None:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="dry-run",
            errors=[f"unknown gate: {gate}"],
        )
    try:
        sc = parse_subcircuit(netlist, name=subckt)
    except Exception as exc:  # noqa: BLE001
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="dry-run",
            errors=[f"parse_subcircuit failed: {type(exc).__name__}: {exc}"],
        )
    res = fn(sc)
    work_dir.mkdir(parents=True, exist_ok=True)
    rep = work_dir / "gate_report.txt"
    rep.write_text(
        f"gate={gate} passed={res.passed}\n"
        + "\n".join(res.messages)
        + "\n"
    )
    detected_violation = not res.passed
    if expect_violation == detected_violation:
        status = BenchStatus.PASS
    else:
        status = BenchStatus.FAIL_AUDIT
    return AdapterResult(
        status=status,
        backend_used="dry-run",
        metrics={
            "violations": len(res.messages),
            "passed_gate": res.passed,
        },
        artifacts=[str(rep)],
        notes=[f"expect_violation={expect_violation}, detected={detected_violation}"],
        compile_ok=True,
        sim_ok=True,
        raw_text=rep.read_text(),
    )


def run_gl_sim_post_synth(
    task: BenchTask, work_dir: Path
) -> AdapterResult:
    """Exercise ``core/stages/gl_sim_runner.py`` against a hardened run.

    The hardened LibreLane run directory comes from
    ``inputs.run_dir`` or, if absent, the env var
    ``EDA_AGENTS_GL_SIM_RUN_DIR``. If neither is set or the dir does
    not look like a LibreLane run, the adapter returns ``FAIL_INFRA``
    so the runner can map it to ``SKIPPED`` — we never silently fake
    a GL sim PASS.
    """
    import os

    from eda_agents.core.designs.systolic_mac_dft import SystolicMacDftDesign
    from eda_agents.core.pdk import resolve_pdk, resolve_pdk_root
    from eda_agents.core.stages.gl_sim_runner import GlSimRunner
    from eda_agents.core.tool_environment import ToolEnvironment

    run_dir_str = task.inputs.get("run_dir") or os.environ.get(
        "EDA_AGENTS_GL_SIM_RUN_DIR"
    )
    if not run_dir_str:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[
                "GL sim task needs inputs.run_dir or EDA_AGENTS_GL_SIM_RUN_DIR; "
                "no hardened LibreLane run available"
            ],
            notes=["bench did not harden a fresh design — see TODO"],
        )
    run_dir = Path(run_dir_str)
    if not (run_dir / "final" / "pnl").is_dir() and not list(
        run_dir.glob("*-yosys-synthesis")
    ):
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[
                f"{run_dir} does not look like a LibreLane run "
                "(no final/pnl/ and no *-yosys-synthesis/)"
            ],
        )
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="iverilog-missing",
            errors=["iverilog/vvp not on PATH"],
        )
    pdk_name = task.pdk or "gf180mcu"
    pdk = resolve_pdk(pdk_name)
    pdk_root = resolve_pdk_root(pdk)
    design = SystolicMacDftDesign()
    env = ToolEnvironment()
    runner = GlSimRunner(
        design=design,
        env=env,
        run_dir=run_dir,
        pdk_config=pdk,
        pdk_root=pdk_root,
        timeout_s=task.timeout_s,
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    stage_res = runner.run_post_synth(work_dir=work_dir)
    return AdapterResult(
        status=(
            BenchStatus.PASS if stage_res.success else BenchStatus.FAIL_SIM
        ),
        backend_used="librelane",
        metrics={"runtime_s": stage_res.duration_s or 0.0},
        artifacts=[str(p) for p in (stage_res.artifacts or [])],
        errors=[stage_res.error] if stage_res.error else [],
        notes=[f"stage={stage_res.stage.value if stage_res.stage else '?'}"],
        compile_ok=True,
        sim_ok=stage_res.success,
        raw_text=(stage_res.stdout_tail or "")[-2000:],
    )


# ---------------------------------------------------------------------------
# Helper exposed to tests / runner
# ---------------------------------------------------------------------------


HARNESS_DISPATCH: dict[str, Callable[[BenchTask, Path], AdapterResult]] = {
    "dry_run": dry_run_adapter,
    "analog_roles": analog_roles_adapter,
    "callable": callable_adapter,
}


def run_task(task: BenchTask, work_dir: Path) -> AdapterResult:
    """Top-level dispatch — picks the adapter from ``task.harness``."""
    fn = HARNESS_DISPATCH.get(task.harness.value)
    if fn is None:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used=task.expected_backend.value,
            errors=[
                f"no adapter registered for harness {task.harness.value!r} "
                f"(available: {sorted(HARNESS_DISPATCH)})"
            ],
        )
    t0 = time.monotonic()
    res = fn(task, work_dir)
    res.notes.append(f"adapter_runtime_s={time.monotonic() - t0:.2f}")
    return res


__all__ = [
    "AdapterResult",
    "HARNESS_DISPATCH",
    "analog_roles_adapter",
    "analytical_miller_design",
    "callable_adapter",
    "dry_run_adapter",
    "resolve_callable",
    "run_gl_sim_post_synth",
    "run_pre_sim_gate_on_inline_netlist",
    "run_task",
]
