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
# digital_autoresearch — stub until the gap-closure session lands a real one
# ---------------------------------------------------------------------------


_DIGITAL_AUTORESEARCH_NOT_IMPLEMENTED = (
    "NOT_IMPLEMENTED: digital_autoresearch adapter is a stub. "
    "The RTL-to-GDS greedy exploration (examples/10_digital_autoresearch.py) "
    "is not yet wired into the bench runner. Scheduled for the post-merge "
    "S9-gap-closure session (tier 2). Returns SKIPPED, not FAIL_INFRA, so "
    "the summary accounts for it as deliberately unimplemented."
)


def digital_autoresearch_adapter(task: BenchTask, work_dir: Path) -> AdapterResult:
    """Placeholder adapter for digital autoresearch tasks.

    Tasks whose harness is ``digital_autoresearch`` resolve here today.
    The adapter returns :class:`BenchStatus.SKIPPED` with an explicit
    ``NOT_IMPLEMENTED`` note rather than :class:`BenchStatus.FAIL_INFRA`
    so the summary does not conflate "no hardened run available" with
    "this feature is not built yet". The real implementation will land
    in the gap-closure session next.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    note_path = work_dir / "NOT_IMPLEMENTED.txt"
    note_path.write_text(_DIGITAL_AUTORESEARCH_NOT_IMPLEMENTED + "\n")
    return AdapterResult(
        status=BenchStatus.SKIPPED,
        backend_used="librelane",
        artifacts=[str(note_path)],
        notes=[_DIGITAL_AUTORESEARCH_NOT_IMPLEMENTED],
        compile_ok=None,
        sim_ok=None,
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

    try:
        inputs = GlSimPostSynthInputs.model_validate(task.inputs)
    except ValidationError as exc:
        return AdapterResult(
            status=BenchStatus.FAIL_INFRA,
            backend_used="librelane",
            errors=[_format_validation_error(exc, "run_gl_sim_post_synth")],
        )
    run_dir_str = inputs.run_dir or os.environ.get(
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
) -> str:
    """Single chat-completion call through OpenRouter's OpenAI-compatible API.

    Raises ``RuntimeError`` (not openai.XxxError / httpx.HTTPStatusError /
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
        return resp.choices[0].message.content or ""
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
    try:
        raw = _call_openrouter(
            model=inputs.model,
            system_prompt=_LLM_SYSTEM_PROMPT,
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
    notes = sim_res.notes + [
        f"LLM model={inputs.model} params={design_params}"
    ]
    return AdapterResult(
        status=sim_res.status,
        backend_used=f"llm+{sim_res.backend_used}",
        metrics=dict(sim_res.metrics),
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
    "run_pre_sim_gate_on_inline_netlist",
    "run_sar11_enob_measurement",
    "run_task",
]
