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

from pydantic import ValidationError

from eda_agents.bench.adapter_inputs import (
    AnalogRolesInputs,
    AnalyticalMillerInputs,
    DigitalAutoresearchInputs,
    DigitalFlowInputs,
    DryRunInputs,
    GlSimPostSynthInputs,
    LlmSpecToSizingInputs,
    PreSimGateInputs,
    Sar11bEnobInputs,
)
from eda_agents.bench.models import BenchStatus, BenchTask


def _format_validation_error(exc: ValidationError, adapter: str) -> str:
    """Turn a Pydantic ValidationError into a one-line bench-friendly message."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "")
        parts.append(f"inputs.{loc}: {msg}" if loc else msg)
    return f"{adapter}: typed inputs validation failed: " + " | ".join(parts)


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
    try:
        inputs = DryRunInputs.model_validate(task.inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="dry-run",
            errors=[_format_validation_error(exc, "dry_run_adapter")],
        )
    fake = inputs.fake_metrics or {}
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

    try:
        inputs = AnalogRolesInputs.model_validate(task.inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="analog_roles",
            errors=[_format_validation_error(exc, "analog_roles_adapter")],
        )
    try:
        spec = load_spec_from_string(inputs.spec_yaml)
    except Exception as exc:  # noqa: BLE001 — surface to runner
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="analog_roles",
            errors=[f"spec load failed: {type(exc).__name__}: {exc}"],
        )

    harness = AnalogRolesHarness(
        spec=spec,
        executor=DryRunExecutor(verbose=False),
        max_iterations=inputs.max_iterations,
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
# digital_autoresearch — real wrapper around DigitalAutoresearchRunner
# ---------------------------------------------------------------------------


def _resolve_bench_design_dir(raw: str) -> Path:
    """Resolve a ``design_dir`` from a task YAML.

    Uses the same repo-root heuristic as ``_bench_librelane_cache_root``
    (walk parents looking for ``bench/tasks/``) so YAMLs can use
    repo-relative paths without false matches against
    ``src/eda_agents/bench``.
    """
    p = Path(raw)
    if p.is_absolute():
        return p.resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "bench" / "tasks").is_dir():
            candidate = parent / raw
            if candidate.is_dir():
                return candidate.resolve()
    return p.resolve()


def digital_autoresearch_adapter(task: BenchTask, work_dir: Path) -> AdapterResult:
    """Exercise ``DigitalAutoresearchRunner`` on a :class:`GenericDesign`.

    Closes gap #4 (was a ``NOT_IMPLEMENTED`` stub before this session).
    The task YAML points at a LibreLane project dir and optionally at a
    mock FlowMetrics JSON file — when the fixture is supplied the
    adapter runs fully offline (no LLM, no LibreLane, no PDK), which
    keeps CI honest.

    Audit signal:

    * ``iterations_kept`` metric (``int``) — number of evaluations the
      greedy loop kept. ``>= 1`` means at least one eval produced a
      design that satisfied :meth:`DigitalDesign.check_validity`.
    * ``best_fom`` passed through for informational reporting.

    Skips cleanly when the design_dir is missing. When no mock fixture
    is supplied *and* the PDK/LibreLane/API key is not available, the
    underlying runner will short-circuit to a fallback proposal but
    the LibreLane step will fail — the adapter surfaces that as
    ``FAIL_INFRA`` → SKIPPED rather than marking the task as failed.
    """
    try:
        inputs = DigitalAutoresearchInputs.model_validate(task.inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[_format_validation_error(exc, "digital_autoresearch_adapter")],
        )

    import asyncio

    if not inputs.design_dir:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=["digital_autoresearch_adapter: inputs.design_dir is required"],
        )

    design_dir = _resolve_bench_design_dir(inputs.design_dir)
    if not design_dir.is_dir():
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[f"design_dir not found: {design_dir}"],
        )

    config_path = design_dir / "config.yaml"
    if not config_path.is_file():
        config_path = design_dir / "config.json"
    if not config_path.is_file():
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[f"neither config.yaml nor config.json under {design_dir}"],
        )

    from eda_agents.agents.digital_autoresearch import DigitalAutoresearchRunner
    from eda_agents.core.designs.generic import GenericDesign

    pdk_name = task.pdk or "gf180mcu"
    pdk_root = _resolve_librelane_pdk_root(pdk_name)

    # In mock mode we don't need LibreLane — but GenericDesign still
    # wants a PDK config for prompt metadata. Pass the requested PDK
    # name and let GenericDesign fall back to its default root when
    # none is reachable.
    try:
        design = GenericDesign(
            config_path=config_path,
            pdk_root=pdk_root,
            pdk_config=pdk_name,
        )
    except Exception as exc:  # noqa: BLE001
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[f"GenericDesign init failed: {type(exc).__name__}: {exc}"],
        )

    mock_path: Path | None = None
    if inputs.mock_metrics_path:
        mock_path = Path(inputs.mock_metrics_path)
        if not mock_path.is_absolute():
            mock_path = (design_dir / mock_path).resolve()
        if not mock_path.is_file():
            return AdapterResult(
                status=BenchStatus.FAIL_INFRA,
                backend_used="librelane",
                errors=[f"mock_metrics_path not found: {mock_path}"],
            )

    # When running for real (no mock), we need LibreLane + API key.
    # Short-circuit to SKIPPED if either is missing to avoid a long
    # subprocess cascade that we know will fail.
    if mock_path is None:
        import os as _os

        if pdk_root is None:
            return AdapterResult(
                status=BenchStatus.FAIL_INFRA,
                backend_used="librelane",
                errors=[
                    f"digital_autoresearch needs either mock_metrics_path or "
                    f"a working {pdk_name!r} PDK. Neither available."
                ],
            )
        if not _os.environ.get("OPENROUTER_API_KEY"):
            return AdapterResult(
                status=BenchStatus.FAIL_INFRA,
                backend_used="librelane",
                errors=[
                    "digital_autoresearch (real mode) needs OPENROUTER_API_KEY "
                    "to propose evaluations. Supply a mock_metrics_path for "
                    "offline / CI runs."
                ],
            )

    runner = DigitalAutoresearchRunner(
        design=design,
        budget=inputs.budget,
        use_mock_metrics=mock_path,
    )

    work_dir.mkdir(parents=True, exist_ok=True)

    # Live mode mutates design_dir/config.yaml via
    # LibreLaneRunner.modify_config (per eval). Snapshot the file
    # before running and restore it afterwards so repeated live runs
    # don't drift the committed design baseline between invocations.
    # Mock mode doesn't touch LibreLane, so the snapshot is a no-op
    # there but cheap enough to do unconditionally.
    config_snapshot = config_path.read_bytes()
    auto_res = None
    run_error: Exception | None = None
    try:
        auto_res = asyncio.run(runner.run(work_dir))
    except Exception as exc:  # noqa: BLE001
        run_error = exc
    finally:
        # Only write back when the file actually drifted — keeps
        # mtime stable for offline runs.
        if config_path.read_bytes() != config_snapshot:
            config_path.write_bytes(config_snapshot)

    if run_error is not None:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[
                f"DigitalAutoresearchRunner failed: "
                f"{type(run_error).__name__}: {run_error}"
            ],
        )
    assert auto_res is not None

    metrics: dict[str, Any] = {
        "iterations_kept": float(auto_res.kept),
        "iterations_total": float(auto_res.total_evals),
        "best_fom": float(auto_res.best_fom or 0.0),
        # Sum of LLM tokens consumed across all proposal calls; 0 on
        # mock runs or when the backend does not populate
        # ``response.usage`` (e.g. older litellm / stub adapters).
        "total_tokens": float(auto_res.total_tokens),
    }

    notes = [
        f"mode={'mock' if mock_path else 'live'}",
        f"kept={auto_res.kept}/{auto_res.total_evals}",
        f"best_valid={auto_res.best_valid}",
        f"tsv={auto_res.tsv_path}",
    ]

    # Audit: at least one kept evaluation means the runner found a
    # design point passing check_validity.
    passed = auto_res.kept >= 1 and auto_res.best_valid
    return AdapterResult(
        status=BenchStatus.PASS if passed else BenchStatus.FAIL_AUDIT,
        backend_used="librelane",
        metrics=metrics,
        artifacts=[auto_res.tsv_path] if auto_res.tsv_path else [],
        notes=notes,
        compile_ok=True,
        sim_ok=True,
        raw_text=notes[0],
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
    try:
        inputs = AnalyticalMillerInputs.model_validate(task.inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used=backend_label,
            errors=[_format_validation_error(exc, "analytical_miller_design")],
        )
    params = inputs.design_params
    designer = MillerOTADesigner(pdk=pdk_obj)
    try:
        design_kwargs: dict[str, Any] = {
            "gmid_input": params.gmid_input,
            "gmid_load": params.gmid_load,
            "L_input": params.L_input,
            "L_load": params.L_load,
            "Cc": params.Cc,
        }
        if params.Ibias is not None:
            design_kwargs["Ibias"] = params.Ibias
        result = designer.analytical_design(**design_kwargs)
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
        check_vds_polarity,
        parse_subcircuit,
    )

    try:
        inputs = PreSimGateInputs.model_validate(task.inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="dry-run",
            errors=[
                _format_validation_error(
                    exc, "run_pre_sim_gate_on_inline_netlist"
                )
            ],
        )
    fn_table = {
        "floating_nodes": check_floating_nodes,
        "bulk_connections": check_bulk_connections,
        "mirror_ratio": check_mirror_ratio,
        "bias_source": check_bias_source,
        "vds_polarity": check_vds_polarity,
    }
    fn = fn_table.get(inputs.gate)
    if fn is None:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="dry-run",
            errors=[
                f"gate {inputs.gate!r} not yet wired into run_pre_sim_gate_on_inline_netlist"
            ],
        )
    try:
        sc = parse_subcircuit(inputs.netlist, name=inputs.subckt)
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
        f"gate={inputs.gate} passed={res.passed}\n"
        + "\n".join(res.messages)
        + "\n"
    )
    detected_violation = not res.passed
    if inputs.expect_violation == detected_violation:
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
        notes=[
            f"expect_violation={inputs.expect_violation}, "
            f"detected={detected_violation}"
        ],
        compile_ok=True,
        sim_ok=True,
        raw_text=rep.read_text(),
    )


def _discover_cached_gl_sim_run(design_name: str) -> Path | None:
    """Find the most recent hardened LibreLane run produced by the bench.

    Looks under :func:`_bench_librelane_cache_root` for
    ``<design_name>/runs/<tag>/`` (the symlinks created by
    :func:`run_librelane_flow_task`, gap #5). Returns the most recent
    entry by mtime or ``None`` when the cache is empty.
    """
    root = _bench_librelane_cache_root()
    runs_dir = root / design_name / "runs"
    if not runs_dir.is_dir():
        return None
    candidates = [d for d in runs_dir.iterdir() if d.is_dir() or d.is_symlink()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0].resolve()


def run_gl_sim_post_synth(
    task: BenchTask, work_dir: Path
) -> AdapterResult:
    """Exercise ``core/stages/gl_sim_runner.py`` against a hardened run.

    Resolution order for the hardened LibreLane run directory:

    1. ``inputs.run_dir`` (explicit path in the YAML).
    2. ``EDA_AGENTS_GL_SIM_RUN_DIR`` environment variable.
    3. The newest symlink under
       ``bench/cache/librelane_runs/counter/runs/`` (published by
       :func:`run_librelane_flow_task` when gap #5's task runs).

    When none of those produces a usable LibreLane run, the adapter
    returns ``FAIL_INFRA`` so the runner can map it to SKIPPED. We never
    silently fake a GL sim PASS.

    Post-synth GL simulation is run against the ``counter`` design
    (via :class:`GenericDesign`) — that's the design the bench hardens
    and the one with an iverilog-compatible testbench under ``tb/``.
    """
    import os

    from eda_agents.core.designs.generic import GenericDesign
    from eda_agents.core.pdk import resolve_pdk
    from eda_agents.core.stages.gl_sim_runner import GlSimRunner
    from eda_agents.core.tool_environment import LocalToolEnvironment

    try:
        inputs = GlSimPostSynthInputs.model_validate(task.inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[_format_validation_error(exc, "run_gl_sim_post_synth")],
        )
    run_dir: Path | None
    if inputs.run_dir:
        run_dir = Path(inputs.run_dir)
    elif os.environ.get("EDA_AGENTS_GL_SIM_RUN_DIR"):
        run_dir = Path(os.environ["EDA_AGENTS_GL_SIM_RUN_DIR"])
    else:
        run_dir = _discover_cached_gl_sim_run("counter")

    if run_dir is None:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[
                "GL sim task found no hardened run: inputs.run_dir / "
                "EDA_AGENTS_GL_SIM_RUN_DIR / bench cache all empty. "
                "Hint: run the digital_counter_gf180 task first (gap #5)."
            ],
            notes=["bench did not harden a fresh design — see TODO"],
        )
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
    pdk_root_str = _resolve_librelane_pdk_root(pdk_name)
    if pdk_root_str is None:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[f"GL sim needs a PDK root for {pdk_name!r}; none found"],
        )
    pdk_root = Path(pdk_root_str)

    # Resolve the counter project dir via the bench cache heuristic and
    # wrap it in a GenericDesign so GlSimRunner can read its testbench.
    here = Path(__file__).resolve()
    design_dir: Path | None = None
    for parent in here.parents:
        candidate = parent / "bench" / "designs" / "counter_bench"
        if candidate.is_dir():
            design_dir = candidate
            break
    if design_dir is None:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=["counter design dir not found under bench/designs/"],
        )
    design = GenericDesign(
        config_path=design_dir / "config.yaml",
        pdk_root=pdk_root,
        pdk_config=pdk_name,
    )
    env = LocalToolEnvironment()
    runner = GlSimRunner(
        design=design,
        env=env,
        run_dir=run_dir,
        pdk_config=pdk,
        pdk_root=pdk_root,
        timeout_s=task.timeout_s,
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    stage_res = runner.run_post_synth()
    return AdapterResult(
        status=(
            BenchStatus.PASS if stage_res.success else BenchStatus.FAIL_SIM
        ),
        backend_used="librelane",
        metrics={"runtime_s": stage_res.run_time_s or 0.0},
        artifacts=[str(p) for p in stage_res.artifacts.values()],
        errors=[stage_res.error] if stage_res.error else [],
        notes=[f"stage={stage_res.stage.name if stage_res.stage else '?'}"],
        compile_ok=True,
        sim_ok=stage_res.success,
        raw_text=(stage_res.log_tail or "")[-2000:],
    )


# ---------------------------------------------------------------------------
# LLM spec-to-sizing adapter (gap #8)
# ---------------------------------------------------------------------------


_LLM_SYSTEM_PROMPT = (
    "You are an analog circuit designer. You will be given a spec for a "
    "two-stage Miller OTA and asked to propose design parameters. "
    "Respond ONLY with a JSON object — no prose, no markdown fences, no "
    "explanation. The JSON must have exactly these keys with numeric "
    "values in the stated ranges:\n"
    "  gmid_input:   5.0 to 25.0 (S/A)\n"
    "  gmid_load:    5.0 to 20.0 (S/A)\n"
    "  L_input:      1.3e-7 to 2e-6 (m)\n"
    "  L_load:       1.3e-7 to 2e-6 (m)\n"
    "  Cc:           1e-13 to 5e-12 (F)\n"
    "Choose values that make the spec targets reachable; do not argue."
)


def _parse_llm_json(text: str) -> dict[str, float]:
    """Extract the first JSON object from ``text`` and decode it.

    Keeps the adapter independent of whether the model wrapped the JSON
    in markdown fences / prose — we scan for the first ``{`` and last
    ``}`` and feed that to :func:`json.loads`.
    """
    import json

    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last < 0 or last <= first:
        raise ValueError(f"no JSON object found in LLM response: {text[:200]!r}")
    chunk = text[first : last + 1]
    data = json.loads(chunk)
    if not isinstance(data, dict):
        raise TypeError(f"LLM JSON was not an object, got {type(data).__name__}")
    return data


def _call_openrouter(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str, int]:
    """Single chat-completion call through OpenRouter's OpenAI-compatible API.

    Returns ``(content, total_tokens)`` where ``total_tokens`` is 0 when
    the backend did not populate ``response.usage``. Raises
    ``RuntimeError`` (not openai.XxxError / httpx.HTTPStatusError /
    ImportError) so the adapter can funnel every infra-level failure
    into one ``FAIL_INFRA`` branch.
    """
    import os

    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover — dep is in base install
        raise RuntimeError(f"openai not available: {exc}") from exc

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    # OpenRouter accepts model ids with or without the "openrouter/" prefix
    # depending on the caller; strip it for the direct API call.
    model_id = model.removeprefix("openrouter/") if model.startswith("openrouter/") else model

    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        resp = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        total_tokens = 0
        usage = getattr(resp, "usage", None)
        if usage is not None:
            total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        return resp.choices[0].message.content or "", total_tokens
    except Exception as exc:  # noqa: BLE001 — funnel to RuntimeError
        raise RuntimeError(
            f"OpenRouter call failed (model={model_id!r}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def llm_spec_to_sizing_adapter(
    task: BenchTask, work_dir: Path
) -> AdapterResult:
    """Ask an LLM to produce Miller OTA design params, then simulate the result.

    Pipeline: spec YAML -> chat-completion request (OpenRouter /
    Gemini Flash default) -> JSON of gmid/L/Cc knobs -> forward the
    values into :func:`analytical_miller_design` so the audit runs
    against real ngspice output.

    Skips gracefully via ``FAIL_INFRA`` when ``OPENROUTER_API_KEY`` is
    not set so CI hosts without a key stay green. The runner maps
    ``FAIL_INFRA`` to ``SKIPPED`` in the summary; we never silently
    fake a PASS.
    """
    try:
        inputs = LlmSpecToSizingInputs.model_validate(task.inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="llm+ngspice",
            errors=[_format_validation_error(exc, "llm_spec_to_sizing_adapter")],
        )

    work_dir.mkdir(parents=True, exist_ok=True)

    # S10g: optional skill injection. The S9 gap #8 task used a
    # hardcoded methodology-free system prompt; S10c declared that the
    # ``miller_ota`` topology's methodology lives in
    # ``analog.miller_ota_design`` + ``analog.gmid_sizing``. When
    # ``EDA_AGENTS_INJECT_SKILLS`` is enabled the adapter prepends the
    # rendered skills so the A/B actually exercises the S10c content.
    # Escape hatch ``EDA_AGENTS_INJECT_SKILLS=0`` restores the pre-S10c
    # behaviour for direct comparison.
    skills_prefix = ""
    import os as _os
    if _os.environ.get("EDA_AGENTS_INJECT_SKILLS", "1") != "0":
        from eda_agents.skills.registry import render_relevant_skills
        from eda_agents.topologies import get_topology_by_name

        try:
            topology = get_topology_by_name("miller_ota")
            rendered = render_relevant_skills(
                topology.relevant_skills(), topology
            )
            if rendered:
                skills_prefix = rendered + "\n\n"
        except Exception:  # noqa: BLE001 — never block the bench on skill lookup
            skills_prefix = ""

    system_prompt = f"{skills_prefix}{_LLM_SYSTEM_PROMPT}"

    try:
        raw, total_tokens = _call_openrouter(
            model=inputs.model,
            system_prompt=system_prompt,
            user_prompt=(
                "Spec (YAML):\n"
                + inputs.spec_yaml
                + "\nReturn the JSON object with the five sizing knobs."
            ),
            max_tokens=inputs.max_tokens,
            temperature=inputs.temperature,
        )
    except RuntimeError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used=f"llm/{inputs.model}",
            errors=[str(exc)],
            notes=[
                "LLM adapter skipped: missing API key or upstream error. "
                "Set OPENROUTER_API_KEY to enable."
            ],
        )
    response_path = work_dir / "llm_response.txt"
    response_path.write_text(raw, encoding="utf-8")

    try:
        design_params = _parse_llm_json(raw)
    except (ValueError, TypeError) as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_AUDIT,
            backend_used=f"llm/{inputs.model}",
            artifacts=[str(response_path)],
            errors=[f"LLM JSON parse failed: {exc}"],
            raw_text=raw,
        )

    # Validate the LLM's proposed sizing against the same typed contract
    # analytical_miller_design uses. If the model emitted out-of-range
    # numbers or missing keys, that is the MODEL failing the audit, not
    # infrastructure — surface FAIL_AUDIT rather than FAIL_INFRA.
    synthetic_inner_inputs = {
        "callable": "eda_agents.bench.adapters:analytical_miller_design",
        "design_params": design_params,
    }
    try:
        AnalyticalMillerInputs.model_validate(synthetic_inner_inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_AUDIT,
            backend_used=f"llm/{inputs.model}",
            artifacts=[str(response_path)],
            errors=[_format_validation_error(exc, "llm_spec_to_sizing_adapter")],
            notes=[
                f"LLM model={inputs.model} produced out-of-range sizing: "
                f"{design_params}"
            ],
            raw_text=raw,
        )
    synthetic_task_data = {
        **task.model_dump(mode="json"),
        "id": f"{task.id}__sizing",
        "harness": "callable",
        "expected_backend": "ngspice-osdi",
        "pdk": inputs.pdk or task.pdk or "ihp_sg13g2",
        "inputs": synthetic_inner_inputs,
    }
    synthetic_task = BenchTask.model_validate(synthetic_task_data)
    sim_dir = work_dir / "sim"
    sim_dir.mkdir(parents=True, exist_ok=True)
    sim_res = analytical_miller_design(synthetic_task, sim_dir)
    # Stitch the LLM artifacts into the result so the report shows both.
    skills_injected = skills_prefix != ""
    notes = sim_res.notes + [
        f"LLM model={inputs.model} params={design_params}",
        f"skills_injected={skills_injected}",
    ]
    metrics = dict(sim_res.metrics)
    # Surface the LLM cost on the BenchResult so the S10g A/B comparator
    # can read bloat + Pass@1 from the same JSON without re-parsing
    # the response file.
    metrics["total_tokens"] = float(total_tokens)
    metrics["skills_injected"] = 1.0 if skills_injected else 0.0
    return AdapterResult(
        status=sim_res.status,
        backend_used=f"llm+{sim_res.backend_used}",
        metrics=metrics,
        artifacts=[str(response_path), *sim_res.artifacts],
        errors=list(sim_res.errors),
        notes=notes,
        compile_ok=sim_res.compile_ok,
        sim_ok=sim_res.sim_ok,
        raw_text=sim_res.raw_text,
    )


# ---------------------------------------------------------------------------
# SAR ADC 11-bit ENOB measurement (gap #6)
# ---------------------------------------------------------------------------


def run_sar11_enob_measurement(
    task: BenchTask, work_dir: Path
) -> AdapterResult:
    """End-to-end: build the 11-bit SAR deck, simulate, extract ENOB.

    Exercises :class:`eda_agents.topologies.sar_adc_11bit.SARADC11BitTopology`
    against ngspice + PSP103 OSDI + Verilator. SPICE runtime is several
    minutes on typical hardware — the YAML sets ``timeout_s=900``.

    Skips gracefully when ngspice / openvaf / verilator are absent.
    """
    import asyncio
    import shutil as _shutil

    from eda_agents.core.spice_runner import SpiceRunner
    from eda_agents.topologies.sar_adc_11bit import SARADC11BitTopology

    try:
        inputs = Sar11bEnobInputs.model_validate(task.inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="ngspice-osdi",
            errors=[_format_validation_error(exc, "run_sar11_enob_measurement")],
        )

    missing = [t for t in ("ngspice", "openvaf", "verilator") if not _shutil.which(t)]
    if missing:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="ngspice-osdi",
            errors=[f"missing tools on PATH: {', '.join(missing)}"],
        )

    pdk_name = task.pdk or "ihp_sg13g2"
    topo = SARADC11BitTopology(pdk=pdk_name)
    params = dict(topo.default_params())
    # Allow the YAML to override individual topology knobs.
    params.update(inputs.topology_params or {})
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        cir = topo.generate_system_netlist(params, work_dir)
    except Exception as exc:  # noqa: BLE001
        return AdapterResult(
            status=BenchStatus.FAIL_COMPILE,
            backend_used="ngspice-osdi",
            errors=[f"generate_system_netlist failed: {type(exc).__name__}: {exc}"],
            compile_ok=False,
        )

    runner = SpiceRunner(preload_pdk_osdi=True)
    try:
        sp = asyncio.run(runner.run_async(cir, work_dir))
    except RuntimeError:
        # Under an existing event loop (shouldn't happen from bench) fall
        # back to the sync path.
        sp = runner.run(cir, work_dir=work_dir)

    artifacts = [str(cir)]
    notes: list[str] = []
    if not sp.success:
        return AdapterResult(
            status=BenchStatus.FAIL_SIM,
            backend_used="ngspice-osdi",
            artifacts=artifacts,
            errors=[sp.error or "ngspice failed"],
            compile_ok=True,
            sim_ok=False,
            raw_text=sp.stdout_tail or "",
        )

    metrics_raw = topo.extract_enob(work_dir)
    # Surface a clean metrics dict for audit + report. Use the canonical
    # key "ENOB" / "SNDR_dBc" (matching expected_metrics contract).
    metrics: dict[str, Any] = {}
    if "enob" in metrics_raw:
        metrics["ENOB"] = float(metrics_raw["enob"])
    if "sndr_dB" in metrics_raw:
        metrics["SNDR_dBc"] = float(metrics_raw["sndr_dB"])
    if "sfdr_dB" in metrics_raw:
        metrics["SFDR_dBc"] = float(metrics_raw["sfdr_dB"])
    if "thd_dB" in metrics_raw:
        metrics["THD_dBc"] = float(metrics_raw["thd_dB"])
    for pass_through in ("n_samples", "code_span", "unique_codes"):
        if pass_through in metrics_raw:
            metrics[pass_through] = float(metrics_raw[pass_through])
    notes.append(
        f"sar11_enob: ENOB={metrics.get('ENOB', float('nan')):.2f} "
        f"SNDR={metrics.get('SNDR_dBc', float('nan')):.1f} dB "
        f"codes={metrics.get('unique_codes', 0):.0f}"
    )
    return AdapterResult(
        status=BenchStatus.PASS,
        backend_used="ngspice-osdi",
        metrics=metrics,
        artifacts=artifacts,
        notes=notes,
        compile_ok=True,
        sim_ok=True,
        raw_text=sp.stdout_tail or "",
    )


# ---------------------------------------------------------------------------
# LibreLane RTL-to-GDS callable adapter (gap #5)
# ---------------------------------------------------------------------------


BENCH_LIBRELANE_CACHE_ENV = "EDA_AGENTS_BENCH_LIBRELANE_CACHE"


def _bench_librelane_cache_root() -> Path:
    """Root directory where the bench caches hardened LibreLane runs.

    Defaults to ``<worktree>/bench/cache/librelane_runs`` resolved from
    this file, so tests picked up by ``pytest`` from anywhere inside the
    repo still see the same cache as ``scripts/run_bench.py``. Overridable
    via ``EDA_AGENTS_BENCH_LIBRELANE_CACHE`` for CI.
    """
    import os

    override = os.environ.get(BENCH_LIBRELANE_CACHE_ENV)
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve()
    # Prefer the repo-root ``bench/`` (with ``bench/tasks/`` inside)
    # over the package-internal ``src/eda_agents/bench/`` that happens
    # to share the same name.
    for parent in here.parents:
        candidate = parent / "bench"
        if candidate.is_dir() and (candidate / "tasks").is_dir():
            return candidate / "cache" / "librelane_runs"
    return Path.cwd() / "bench" / "cache" / "librelane_runs"


def _resolve_librelane_pdk_root(pdk_name: str) -> str | None:
    """Find a PDK_ROOT that contains the requested PDK's LibreLane tech.

    Mirrors :func:`eda_agents.core.pdk.resolve_pdk_root` but widened to
    accept the wafer-space GF180 fork (the upstream LibreLane v3 config
    lives in the fork, not in ciel's stock install). Returns ``None``
    when no suitable root is found so the adapter can SKIP cleanly.
    """
    import os

    candidates: list[Path] = []
    env_val = os.environ.get("PDK_ROOT")
    if env_val:
        candidates.append(Path(env_val))
    if pdk_name.startswith("gf180"):
        candidates.extend([
            Path("/home/montanares/git/wafer-space-gf180mcu"),
            Path.home() / "pdks",
        ])
    elif pdk_name.startswith("ihp"):
        candidates.append(Path("/home/montanares/git/IHP-Open-PDK"))

    target_dir = "gf180mcuD" if pdk_name.startswith("gf180") else "ihp-sg13g2"
    for root in candidates:
        if (root / target_dir).is_dir():
            return str(root)
    return None


def run_librelane_flow_task(
    task: BenchTask, work_dir: Path
) -> AdapterResult:
    """Run a LibreLane RTL-to-GDS flow on a :class:`GenericDesign`.

    Closes gap #5. The task YAML points ``inputs.design_dir`` at a
    project directory containing a LibreLane ``config.yaml`` + ``rtl/``
    tree. The adapter spins up a fresh run tagged
    ``bench-<task.id>`` under that project's ``runs/`` directory, lets
    LibreLane harden through ``inputs.stop_after`` (default ``ROUTE``),
    and reports two structured metrics:

    * ``run_time_s`` — wall-clock flow time.
    * ``DRC_violations`` — parsed from the run's ``*.lyrdb`` files via
      ``LibreLaneRunner.read_drc``. ``-1`` means "no DRC report found",
      which the audit treats as out-of-range against ``{max: 0}``.

    When ``inputs.cache_run_dir=True`` (default) and the flow succeeds,
    the produced run directory is symlinked under
    ``bench/cache/librelane_runs/<design>/`` so :func:`run_gl_sim_post_synth`
    (gap #2) can pick it up without the bench needing to re-harden.

    Skips cleanly (``FAIL_INFRA`` → SKIPPED in summary) when LibreLane,
    its Python interpreter, or the PDK is not available.
    """
    try:
        inputs = DigitalFlowInputs.model_validate(task.inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[_format_validation_error(exc, "run_librelane_flow_task")],
        )

    from eda_agents.core.designs.generic import GenericDesign
    from eda_agents.core.librelane_runner import LibreLaneRunner

    pdk_name = task.pdk or "gf180mcu"
    pdk_root = _resolve_librelane_pdk_root(pdk_name)
    if pdk_root is None:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[
                f"LibreLane PDK root for {pdk_name!r} not found — set PDK_ROOT "
                "or install the GF180/IHP PDK. Skipping gap #5 gracefully."
            ],
        )

    # Resolve design_dir relative to the repo root (same heuristic as
    # _bench_librelane_cache_root — walk up looking for a repo root
    # whose ``bench/`` dir contains ``bench/tasks/``) so YAMLs can use
    # repo-relative paths without false matches against
    # ``src/eda_agents/bench``.
    design_dir = Path(inputs.design_dir)
    if not design_dir.is_absolute():
        here = Path(__file__).resolve()
        resolved = None
        for parent in here.parents:
            if (parent / "bench" / "tasks").is_dir():
                candidate = parent / inputs.design_dir
                if candidate.is_dir():
                    resolved = candidate.resolve()
                    break
        design_dir = resolved if resolved else design_dir.resolve()

    if not design_dir.is_dir():
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[f"design_dir not found: {design_dir}"],
        )

    config_path = design_dir / "config.yaml"
    if not config_path.is_file():
        config_path = design_dir / "config.json"
    if not config_path.is_file():
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[f"neither config.yaml nor config.json under {design_dir}"],
        )

    try:
        design = GenericDesign(
            config_path=config_path,
            pdk_root=pdk_root,
            pdk_config=pdk_name,
        )
    except Exception as exc:  # noqa: BLE001
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[f"GenericDesign init failed: {type(exc).__name__}: {exc}"],
        )

    # LibreLane v3 needs yosys >= 0.60 and recent OpenROAD — Ubuntu's
    # system packages are typically too old (yosys 0.43, openroad v2.0).
    # Reuse the digital_autoresearch helper so the bench behaves
    # identically to the autoresearch flow on the same host.
    from eda_agents.agents.digital_autoresearch import detect_nix_eda_tool_dirs

    import os as _os

    env_extra: dict[str, str] = {}
    nix_dirs = detect_nix_eda_tool_dirs()
    if nix_dirs:
        env_extra["PATH"] = ":".join(nix_dirs) + ":" + _os.environ.get("PATH", "")

    # LibreLane resolves the PDK variant from the ``PDK`` env var before
    # it consults the config file. Nudge it toward the target PDK so
    # the hardened run matches ``task.pdk`` instead of whatever the
    # host-global PDK was.
    if pdk_name.startswith("gf180"):
        env_extra["PDK"] = "gf180mcuD"
    elif pdk_name.startswith("ihp"):
        env_extra["PDK"] = "ihp-sg13g2"

    tag = f"bench-{task.id}"
    runner = LibreLaneRunner(
        project_dir=design.project_dir(),
        config_file=config_path.name,
        pdk_root=pdk_root,
        timeout_s=task.timeout_s,
        shell_wrapper=design.shell_wrapper(),
        env_extra=env_extra,
    )
    setup_problems = runner.validate_setup()
    if setup_problems:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[f"LibreLane setup: {'; '.join(setup_problems)}"],
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    flow = runner.run_flow(tag=tag, to=inputs.stop_after, overwrite=True)

    metrics: dict[str, Any] = {
        "run_time_s": float(flow.run_time_s or 0.0),
    }
    drc = runner.read_drc(flow.run_dir or None) if flow.run_dir else None
    if drc is not None:
        metrics["DRC_violations"] = float(drc.total_violations)

    artifacts: list[str] = []
    for path_attr in ("gds_path", "def_path", "netlist_path", "run_dir"):
        val = getattr(flow, path_attr, None)
        if val:
            artifacts.append(str(val))

    notes: list[str] = [f"librelane_tag={tag}"]
    if flow.run_dir:
        notes.append(f"run_dir={flow.run_dir}")
    if drc is not None:
        notes.append(
            f"DRC: {drc.total_violations} violations"
            if not drc.clean else "DRC clean"
        )

    if not flow.success:
        return AdapterResult(
            status=BenchStatus.FAIL_SIM,
            backend_used="librelane",
            metrics=metrics,
            artifacts=artifacts,
            errors=[flow.error or "LibreLane flow failed"],
            notes=notes,
            compile_ok=True,
            sim_ok=False,
            raw_text=(flow.log_tail or "")[-2000:],
        )

    # Publish the hardened run under the bench cache so downstream
    # tasks (gap #2 GL sim) can find it without touching the task YAMLs.
    if inputs.cache_run_dir and flow.run_dir:
        try:
            cache_root = _bench_librelane_cache_root()
            dest_dir = cache_root / design.project_name().replace("-", "_") / "runs"
            dest_dir.mkdir(parents=True, exist_ok=True)
            link = dest_dir / tag
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(Path(flow.run_dir).resolve())
            notes.append(f"cache_link={link}")
        except OSError as exc:
            # Caching is a convenience, not a contract — log but don't
            # downgrade the PASS.
            notes.append(f"cache_link_failed: {exc}")

    return AdapterResult(
        status=BenchStatus.PASS,
        backend_used="librelane",
        metrics=metrics,
        artifacts=artifacts,
        notes=notes,
        compile_ok=True,
        sim_ok=True,
        raw_text=(flow.log_tail or "")[-2000:],
    )


# ---------------------------------------------------------------------------
# Helper exposed to tests / runner
# ---------------------------------------------------------------------------


HARNESS_DISPATCH: dict[str, Callable[[BenchTask, Path], AdapterResult]] = {
    "dry_run": dry_run_adapter,
    "analog_roles": analog_roles_adapter,
    "callable": callable_adapter,
    "digital_autoresearch": digital_autoresearch_adapter,
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
    "digital_autoresearch_adapter",
    "dry_run_adapter",
    "llm_spec_to_sizing_adapter",
    "resolve_callable",
    "run_gl_sim_post_synth",
    "run_librelane_flow_task",
    "run_pre_sim_gate_on_inline_netlist",
    "run_sar11_enob_measurement",
    "run_task",
]
