"""FastMCP server exposing eda-agents semantic tools.

Six tools are registered:

* ``render_skill`` — render a named skill's prompt, optionally bound
  to a topology.
* ``list_skills`` — enumerate registered skills, optionally filtered by
  dotted prefix.
* ``evaluate_topology`` — run a SPICE evaluation through
  ``SpiceEvaluationHandler`` for a topology at the given parameters.
* ``generate_rtl_draft`` — drive the NL idea -> digital GDS pipeline
  (S11 Fase 0) via Claude Code CLI + LibreLane + post-flow gate-level
  simulation. Async, long-running; callers should pass a work_dir and
  a pdk_root.
* ``recommend_topology`` — map a natural-language analog idea to one
  of the registered topologies (S11 Fase 3) via OpenRouter + the
  ``analog.idea_to_topology`` skill. Returns structured JSON with
  topology, rationale, starter specs and a confidence flag.
* ``generate_analog_layout`` — drive the gLayout runner to emit a
  GDS for a primitive or composite on GF180 or SG13G2 (S11 Fase 4).
  Reuses ``GLayoutRunner`` so the heavy lifting is in a separate
  venv and blocks only through the subprocess.

The server defaults to the stdio transport used by MCP-aware clients
(Claude Code, Cursor, Zed). HTTP transports are opt-in and bind to
``127.0.0.1`` only — this spike does not implement authentication. See
``docs/mcp_spike_design.md``.
"""

from __future__ import annotations

import inspect
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from eda_agents.agents.handler import SpiceEvaluationHandler
from eda_agents.agents.idea_to_rtl import generate_rtl_draft as _generate_rtl_draft
from eda_agents.agents.idea_to_rtl import result_to_dict as _result_to_dict
from eda_agents.agents.openrouter_client import call_openrouter as _call_openrouter
from eda_agents.core.spice_runner import SpiceRunner
from eda_agents.skills import Skill
from eda_agents.skills.registry import get_skill
from eda_agents.skills.registry import list_skills as _list_skills
from eda_agents.topologies import get_topology_by_name, list_topology_names

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PDK = "ihp_sg13g2"

mcp = FastMCP(
    name="eda-agents",
    instructions=(
        "Semantic tools over eda-agents: skill rendering and SPICE-in-"
        "the-loop topology evaluation. Localhost-only; no auth on this "
        "spike — do not expose beyond the loopback interface."
    ),
)


def _prompt_fn_requires_topology(skill: Skill) -> bool:
    """True when the skill's ``prompt_fn`` has at least one required
    positional/keyword argument (i.e. a parameter with no default)."""
    if skill.prompt_fn is None:
        return False
    try:
        sig = inspect.signature(skill.prompt_fn)
    except (TypeError, ValueError):
        return False
    for param in sig.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.default is inspect.Parameter.empty:
            return True
    return False


@mcp.tool()
def render_skill(name: str, topology_name: str | None = None) -> str:
    """Render a registered skill's prompt.

    Parameters
    ----------
    name:
        Dotted skill name as registered in ``eda_agents.skills`` (e.g.
        ``"analog.miller_ota_design"``).
    topology_name:
        Optional canonical topology name (from
        ``list_topology_names``). When provided, the topology is
        instantiated and passed to the skill's ``prompt_fn``.

    Returns
    -------
    str
        The rendered prompt text on success. On error a string prefixed
        with ``"ERROR:"`` so LLM clients surface the failure inline
        instead of propagating a tool-call exception.
    """
    try:
        skill = get_skill(name)
    except KeyError as exc:
        return f"ERROR: {exc}"

    if topology_name is None:
        if _prompt_fn_requires_topology(skill):
            return (
                f"ERROR: skill {name!r} requires a topology argument; "
                "pass topology_name."
            )
        try:
            return skill.render()
        except TypeError as exc:
            return f"ERROR: {skill.name!r} rendering failed: {exc}"

    try:
        topology = get_topology_by_name(topology_name)
    except KeyError as exc:
        return f"ERROR: {exc}"

    try:
        return skill.render(topology)
    except TypeError as exc:
        return f"ERROR: {skill.name!r} rendering failed: {exc}"


@mcp.tool()
def list_skills(prefix: str | None = None) -> list[str]:
    """List registered skill names, optionally filtered by dotted prefix."""
    return [s.name for s in _list_skills(prefix=prefix)]


@mcp.tool()
async def evaluate_topology(
    topology_name: str,
    params: dict[str, float],
    pdk: str = DEFAULT_PDK,
) -> dict[str, Any]:
    """Run a single SPICE evaluation for the named topology.

    The call is backed by ``SpiceEvaluationHandler.evaluate`` so it
    honours the same analytical pre-filter and measurement pipeline the
    in-tree runners use. Budget is forced to one evaluation per call —
    clients driving exploration should issue repeated calls instead of
    expecting server-side state.

    Returns a JSON-serialisable dict with the evaluation result shape
    (``params``, ``eval_mode``, ``fom``, ``valid``, ``violations``,
    ``analytical``, ``spice``). On unknown topology or runner failure
    the dict contains an ``"error"`` key.
    """
    try:
        topology = get_topology_by_name(topology_name)
    except KeyError as exc:
        return {"error": str(exc)}

    try:
        runner = SpiceRunner(pdk=pdk)
    except Exception as exc:  # surface PDK/runner misconfig to caller
        return {"error": f"SpiceRunner init failed: {exc}"}

    with tempfile.TemporaryDirectory(prefix="eda_mcp_eval_") as tmp:
        work_dir = Path(tmp)
        handler = SpiceEvaluationHandler(
            topology=topology,
            runner=runner,
            work_dir=work_dir,
            max_evals=1,
        )
        result = await handler.evaluate(params)

    return {
        "topology": topology_name,
        "pdk": pdk,
        "params": result.params,
        "eval_mode": result.eval_mode,
        "fom": result.fom,
        "valid": result.valid,
        "violations": list(result.violations),
        "analytical": dict(result.analytical),
        "spice": dict(result.spice) if result.spice else {},
    }


@mcp.tool()
async def generate_rtl_draft(
    description: str,
    design_name: str,
    work_dir: str,
    pdk: str = "gf180mcu",
    pdk_root: str | None = None,
    complexity: str = "simple",
    dry_run: bool = True,
    skip_gl_sim: bool = False,
    librelane_python: str = "python3",
    timeout_s: int = 3600,
    cli_path: str = "claude",
    max_budget_usd: float | None = None,
    model: str | None = None,
    tb_framework: str = "iverilog",
    loop_budget: int = 1,
    per_turn_timeout_s: int | None = None,
) -> dict[str, Any]:
    """Run the NL idea -> digital GDS pipeline (S11 Fase 0).

    On ``dry_run=True`` (default) the server only builds the agent
    prompt and validates PDK-root resolution, returning quickly.
    Clients that want the real flow must pass ``dry_run=False`` AND
    accept that the call blocks for minutes / hours while Claude Code
    authors RTL, runs LibreLane, and completes gate-level simulation.

    Parameters
    ----------
    description:
        Natural-language description of the digital block (e.g.
        "4-bit synchronous up-counter with enable and async active-low
        reset").
    design_name:
        Target top-module name. Drives filenames and LibreLane
        ``DESIGN_NAME``.
    work_dir:
        Directory where the agent writes ``src/``, ``tb/``,
        ``config.yaml``, and ``runs/``. Created if absent.
    pdk:
        PDK name (``gf180mcu`` or ``ihp_sg13g2``). Defaults to
        ``gf180mcu``.
    pdk_root:
        Explicit PDK root. When ``None``, falls back to ``$PDK_ROOT``
        or the PDK's ``default_pdk_root``.
    complexity:
        One of ``simple|medium|complex``. Accepted today but a
        single-shot pipeline is used regardless; reserved for the
        Fase 1 iterative loop.
    dry_run:
        When True (default), validate setup + build the prompt only.
    skip_gl_sim:
        When True, skip the post-flow post-synth + post-PnR GL sim.
        Keeps callers honest: defaults to running both stages when
        the flow succeeds.
    librelane_python, timeout_s, cli_path, max_budget_usd, model:
        Pass-throughs (see
        :func:`eda_agents.agents.idea_to_rtl.generate_rtl_draft`).
    tb_framework:
        ``"iverilog"`` (default) or ``"cocotb"``. Swaps Phase 2.5 of
        the from-spec prompt between the plain-Verilog TB + iverilog
        and the cocotb + Makefile path guided by the
        ``digital.cocotb_testbench`` skill.
    loop_budget:
        Iterative idea-to-chip loop budget. ``1`` (default) runs the
        S11 single-shot path. ``> 1`` dispatches to
        ``IdeaToRTLLoop`` which feeds critique back between turns;
        the result includes a ``loop_result`` block with per-turn
        diagnostics.
    per_turn_timeout_s:
        Per-loop-turn wall-clock cap (60..14400 s). Only consulted
        when ``loop_budget > 1``. ``None`` (default) lets each turn
        use the full ``timeout_s``; set when a single runaway turn
        must not be allowed to consume the entire wall-clock budget.

    Returns
    -------
    dict
        JSON-serialisable result: ``success``, ``all_passed``,
        ``prompt_length``, ``work_dir``, ``gds_path`` (when produced),
        ``run_dir``, ``gl_sim`` verdict, ``cost_usd``, ``error``,
        and ``loop_result`` when ``loop_budget > 1``.
    """
    if complexity not in ("simple", "medium", "complex"):
        return {
            "success": False,
            "error": (
                f"unknown complexity {complexity!r}; "
                "allowed: simple, medium, complex"
            ),
        }
    if tb_framework not in ("iverilog", "cocotb"):
        return {
            "success": False,
            "error": (
                f"unknown tb_framework {tb_framework!r}; "
                "allowed: iverilog, cocotb"
            ),
        }
    if not 1 <= loop_budget <= 20:
        return {
            "success": False,
            "error": (
                f"loop_budget {loop_budget!r} out of range 1..20"
            ),
        }
    if per_turn_timeout_s is not None and not 60 <= per_turn_timeout_s <= 14400:
        return {
            "success": False,
            "error": (
                f"per_turn_timeout_s {per_turn_timeout_s!r} "
                "out of range 60..14400"
            ),
        }
    try:
        result = await _generate_rtl_draft(
            description=description,
            design_name=design_name,
            work_dir=work_dir,
            pdk=pdk,
            pdk_root=pdk_root,
            complexity=complexity,  # type: ignore[arg-type]
            dry_run=dry_run,
            skip_gl_sim=skip_gl_sim,
            librelane_python=librelane_python,
            timeout_s=timeout_s,
            cli_path=cli_path,
            max_budget_usd=max_budget_usd,
            model=model,
            tb_framework=tb_framework,
            loop_budget=loop_budget,
            per_turn_timeout_s=per_turn_timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — surface all failures to caller
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return _result_to_dict(result)


@mcp.tool()
def recommend_topology(
    description: str,
    constraints: dict[str, Any] | None = None,
    model: str = "google/gemini-2.5-flash",
    temperature: float = 0.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Map a NL analog idea to a registered topology (S11 Fase 3).

    Renders the ``analog.idea_to_topology`` skill as system prompt,
    packs the caller's description + optional constraints dict into a
    user prompt, asks OpenRouter (Gemini Flash by default), and parses
    the JSON response back into a structured dict.

    Parameters
    ----------
    description:
        Natural-language description of the desired analog block
        (e.g. "low-noise 1 kHz amplifier for a biomedical sensor,
        60 dB gain, 45 deg phase margin").
    constraints:
        Optional dict of numeric specs / PDK hints to prepend verbatim
        to the user message. Keys that look like specs (Adc, GBW, PM,
        power_uW, ENOB, fs_Hz) are most useful — the skill prompts
        the LLM to emit starter_specs in the same shape.
    model:
        OpenRouter model id. Defaults to ``google/gemini-2.5-flash``.
    temperature:
        Sampling temperature (default 0.0 for deterministic
        classification).
    dry_run:
        When True, render the prompt and validate the topology
        registry without calling the LLM. Returns
        ``{"success": true, "dry_run": true, ...}`` with the
        rendered prompt length — useful for MCP clients that just
        want to probe the tool shape.

    Returns
    -------
    dict
        On success: ``{"success": true, "topology": str,
        "rationale": str, "starter_specs": dict, "confidence": str,
        "notes": str, "model": str, "total_tokens": int,
        "valid_topology": bool}``. ``valid_topology`` is False when
        the LLM returned a name that isn't in the registry and isn't
        the string "custom" — the caller decides whether to retry.

        On failure: ``{"success": false, "error": str}``. Typical
        failures: OPENROUTER_API_KEY missing, upstream HTTP error,
        JSON parse failure.
    """
    try:
        skill = get_skill("analog.idea_to_topology")
    except KeyError as exc:
        return {"success": False, "error": f"skill not registered: {exc}"}

    system_prompt = skill.render()

    user_lines = [f"Description:\n{description.strip()}"]
    if constraints:
        user_lines.append("Numeric constraints:")
        for k, v in constraints.items():
            user_lines.append(f"  - {k} = {v}")
    user_lines.append("Return ONLY the JSON object as specified.")
    user_prompt = "\n\n".join(user_lines)

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "model": model,
            "prompt_length": len(system_prompt) + len(user_prompt),
            "known_topologies": list_topology_names(),
        }

    try:
        raw, total_tokens = _call_openrouter(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1024,
            temperature=temperature,
        )
    except RuntimeError as exc:
        return {"success": False, "error": str(exc)}

    # Parse JSON from the response, tolerating markdown fences.
    first = raw.find("{")
    last = raw.rfind("}")
    if first < 0 or last < 0 or last <= first:
        return {
            "success": False,
            "error": (
                f"LLM did not return a JSON object "
                f"(first 200 chars: {raw[:200]!r})"
            ),
            "raw": raw[-2000:],
        }
    try:
        payload = json.loads(raw[first : last + 1])
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "error": f"LLM JSON parse failed: {exc}",
            "raw": raw[-2000:],
        }
    if not isinstance(payload, dict):
        return {
            "success": False,
            "error": f"LLM JSON was not an object, got {type(payload).__name__}",
            "raw": raw[-2000:],
        }

    topology = str(payload.get("topology", "")).strip() or "custom"
    known = set(list_topology_names())
    valid_topology = topology in known or topology == "custom"

    return {
        "success": True,
        "topology": topology,
        "rationale": str(payload.get("rationale", "")),
        "starter_specs": payload.get("starter_specs", {}) or {},
        "confidence": str(payload.get("confidence", "low")),
        "notes": str(payload.get("notes", "")),
        "valid_topology": valid_topology,
        "model": model,
        "total_tokens": total_tokens,
    }


@mcp.tool()
async def generate_analog_layout(
    pdk: str,
    component: str,
    params: dict[str, Any],
    output_dir: str,
    glayout_venv: str | None = None,
    timeout_s: int = 600,
) -> dict[str, Any]:
    """Generate an analog layout via the gLayout runner (S11 Fase 4).

    Parameters
    ----------
    pdk:
        PDK key — ``gf180mcu`` or ``ihp_sg13g2``.
    component:
        Canonical component name. Supported on both PDKs:
        ``nmos``, ``pmos``, ``mimcap``, ``diff_pair``,
        ``current_mirror``, ``fvf``. ``opamp_twostage`` is GF180-only
        today (the SG13G2 port is WIP upstream).
    params:
        Dimensions + counts passed through to the gLayout generator.
        Typical keys: ``width`` (um), ``length`` (um), ``fingers``,
        plus composite-specific keys (``multipliers``, ``type`` for
        current mirrors, the opamp's nested dimension tuples).
    output_dir:
        Absolute directory where the GDS + netlist land.
    glayout_venv:
        Path to ``.venv-glayout``. When ``None``, falls back to the
        eda-agents default relative path; callers whose cwd is a
        worktree without the venv should pass an absolute path.
    timeout_s:
        Hard timeout for the subprocess (default 10 min).

    Returns
    -------
    dict
        ``{"success": bool, "gds_path": str | None, "netlist_path":
        str | None, "top_cell": str, "component": str,
        "run_time_s": float, "error": str | None}``.
    """
    # Import lazily so the MCP server stays importable even if
    # GLayoutRunner adds heavy deps later.
    from eda_agents.core.glayout_runner import GLayoutRunner

    # The async wrapper offloads the blocking subprocess to a thread.
    def _run_blocking() -> dict[str, Any]:
        runner_kwargs = {"timeout_s": timeout_s, "pdk": pdk}
        if glayout_venv:
            runner_kwargs["glayout_venv"] = glayout_venv
        runner = GLayoutRunner(**runner_kwargs)
        result = runner.generate_component(
            component=component,
            params=params,
            output_dir=output_dir,
        )
        return {
            "success": result.success,
            "gds_path": result.gds_path,
            "netlist_path": result.netlist_path,
            "top_cell": result.top_cell,
            "component": result.component,
            "run_time_s": result.run_time_s,
            "error": result.error,
        }

    import asyncio as _asyncio

    return await _asyncio.to_thread(_run_blocking)


@mcp.tool()
async def explore_custom_topology(
    description: str,
    constraints: dict[str, Any] | None = None,
    pdk: str = "ihp_sg13g2",
    max_iterations: int = 8,
    max_budget_usd: float = 10.0,
    model: str = "google/gemini-2.5-flash",
    output_dir: str | None = None,
    attempt_layout: bool = True,
    timeout_s: int = 1800,
) -> dict[str, Any]:
    """Run the custom-composition loop for a novel analog block (S12-B Gap 5).

    Entry point when ``recommend_topology`` returned
    ``confidence: low`` or ``topology: custom``. Launches the
    propose -> size -> simulate -> critique loop from
    :class:`eda_agents.agents.analog_composition_loop.AnalogCompositionLoop`
    and returns the :class:`AnalogCompositionResult` as a dict.

    **Honest-fail is a first-class outcome** — a return with
    ``converged: false`` and a populated ``honest_fail_reason`` is
    considered successful tool execution.

    Parameters
    ----------
    description:
        Natural-language description of the desired block (e.g.
        ``"4-bit current-steering DAC, 1 uA LSB, differential output"``).
    constraints:
        Optional numeric constraints (``supply_v``, ``inl_lsb_max`` …)
        passed verbatim into the loop's prompt.
    pdk:
        ``ihp_sg13g2`` or ``gf180mcu``. Default IHP.
    max_iterations:
        Upper bound on outer iterations (default 8).
    max_budget_usd:
        USD budget ceiling across LLM calls. The loop aborts early
        once ``cumulative_cost >= 0.9 * max_budget_usd``.
    model:
        OpenRouter model id (default Gemini Flash).
    output_dir:
        Work directory for ``program.md`` + ``iterations.jsonl`` +
        per-iteration artefacts. When ``None``, uses a fresh tempdir.
    attempt_layout:
        When True, sub-block layouts are generated per iteration once
        SPICE passes all target specs. DRC / LVS on the top-level
        composition is NOT attempted yet (MVP — placer is future work).
    timeout_s:
        Hard subprocess timeout (default 30 min).

    Returns
    -------
    dict
        The serialised :class:`AnalogCompositionResult`. On tool-level
        failure (module import, PDK lookup), returns
        ``{"success": false, "error": str}``.
    """
    from eda_agents.agents.analog_composition_loop import AnalogCompositionLoop

    def _run_blocking() -> dict[str, Any]:
        import tempfile

        work_dir = output_dir or tempfile.mkdtemp(prefix="custom_composition_")
        loop = AnalogCompositionLoop(
            pdk=pdk,
            work_dir=work_dir,
            model=model,
            max_iterations=max_iterations,
            max_budget_usd=max_budget_usd,
            attempt_layout=attempt_layout,
        )
        result = loop.loop(description, constraints=constraints or {})
        return result.to_json()

    import asyncio as _asyncio

    try:
        return await _asyncio.wait_for(
            _asyncio.to_thread(_run_blocking), timeout=timeout_s,
        )
    except _asyncio.TimeoutError:
        return {
            "success": False,
            "converged": False,
            "error": f"tool timeout after {timeout_s}s",
        }
    except Exception as exc:
        return {
            "success": False,
            "converged": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_server(
    transport: str = "stdio",
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Run the MCP server.

    ``transport`` defaults to ``"stdio"`` which matches the MCP convention
    used by Claude Code / Cursor / Zed. Passing
    ``"streamable-http"`` binds an HTTP endpoint on ``host`` / ``port``;
    ``host`` is forced to ``127.0.0.1`` unless the caller overrides it,
    since the spike does not authenticate requests.
    """
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    if host != DEFAULT_HOST:
        logger.warning(
            "Binding MCP HTTP transport to %s — spike has no auth; "
            "use 127.0.0.1 unless you know what you are doing.",
            host,
        )
    mcp.run(transport=transport, host=host, port=port)


if __name__ == "__main__":
    run_server()
