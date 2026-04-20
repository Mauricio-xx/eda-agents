"""FastMCP server exposing eda-agents semantic tools.

Eight tools are registered:

* ``render_skill`` — render a named skill's prompt, optionally bound
  to a topology.
* ``list_skills`` — enumerate registered skills, optionally filtered by
  dotted prefix.
* ``evaluate_topology`` — run a SPICE evaluation through
  ``SpiceEvaluationHandler`` for a topology at the given parameters.
* ``describe_topology`` — return a topology's design-space, defaults,
  target specs and FoM formula so MCP clients can prepare a valid
  ``evaluate_topology`` call without Python introspection.
* ``run_autoresearch`` — drive ``AutoresearchRunner`` end-to-end on a
  registered topology and return the top-N designs. Lets MCP clients
  iterate without handholding. Backed by LiteLLM (any
  provider-agnostic model string); harness-based backends remain
  future work.
* ``generate_rtl_draft`` — drive the NL idea -> digital GDS pipeline
  (S11 Fase 0) via Claude Code CLI + LibreLane + post-flow gate-level
  simulation. Async, long-running; callers should pass a work_dir and
  a pdk_root.
* ``recommend_topology`` — map a natural-language analog idea to one
  of the registered topologies (S11 Fase 3) via LiteLLM + the
  ``analog.idea_to_topology`` skill. Provider-agnostic: any LiteLLM-
  routed model string works (``openrouter/``, ``zai/``, ``anthropic/``,
  ``gemini/`` …). Returns structured JSON with topology, rationale,
  starter specs and a confidence flag.
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
from eda_agents.agents.llm_client import call_llm as _call_llm
from eda_agents.agents.llm_client import validate_model_env as _validate_model_env
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


def _relevant_skills_as_names(topology: Any) -> list[str]:
    """Flatten ``relevant_skills()`` to a list of bare skill names.

    ``CircuitTopology.relevant_skills`` may return either strings or
    ``(name, kwargs)`` tuples. The MCP surface only needs the names.
    """
    try:
        entries = topology.relevant_skills()
    except Exception:  # noqa: BLE001 — metadata is optional
        return []
    names: list[str] = []
    for entry in entries or []:
        if isinstance(entry, tuple) and entry:
            names.append(str(entry[0]))
        elif isinstance(entry, str):
            names.append(entry)
    return names


@mcp.tool()
def describe_topology(name: str) -> dict[str, Any]:
    """Return design-space and metadata for a registered topology.

    Lets MCP clients discover what parameters a topology accepts —
    their ranges, default values, target specs, and FoM formula —
    without shelling out to Python for introspection. The output is a
    strict superset of what :func:`evaluate_topology` needs to be
    called successfully, plus human-readable prompt blocks.

    Parameters
    ----------
    name:
        Canonical topology name (from :func:`list_topology_names`).

    Returns
    -------
    dict
        On success::

            {
              "name": str,
              "design_space": {
                  var: {"min": float, "max": float, "default": float}
              },
              "default_params": {var: float},
              "description": str,          # prompt_description()
              "design_vars": str,          # design_vars_description()
              "specs": str,                # specs_description()
              "fom": str,                  # fom_description()
              "reference": str,            # reference_description()
              "exploration_hints": dict,
              "relevant_skills": list[str],
              "auxiliary_tools": str,
            }

        On unknown name::

            {"error": "Unknown topology ..."}
    """
    try:
        topology = get_topology_by_name(name)
    except KeyError as exc:
        return {"error": str(exc)}

    try:
        space = topology.design_space()
    except Exception as exc:  # noqa: BLE001 — surface misconfigured topologies
        return {"error": f"design_space() failed for {name!r}: {exc}"}

    try:
        defaults = topology.default_params()
    except Exception:  # noqa: BLE001 — fall back to midpoint per ABC default
        defaults = {n: (lo + hi) / 2.0 for n, (lo, hi) in space.items()}

    design_space: dict[str, dict[str, float]] = {}
    for var, (lo, hi) in space.items():
        entry: dict[str, float] = {"min": float(lo), "max": float(hi)}
        if var in defaults:
            entry["default"] = float(defaults[var])
        design_space[var] = entry

    def _safe(getter_name: str) -> str:
        getter = getattr(topology, getter_name, None)
        if getter is None:
            return ""
        try:
            return str(getter() or "")
        except Exception:  # noqa: BLE001 — optional prompt metadata
            return ""

    try:
        hints = dict(topology.exploration_hints() or {})
    except Exception:  # noqa: BLE001 — optional
        hints = {}

    return {
        "name": name,
        "design_space": design_space,
        "default_params": {k: float(v) for k, v in defaults.items()},
        "description": _safe("prompt_description"),
        "design_vars": _safe("design_vars_description"),
        "specs": _safe("specs_description"),
        "fom": _safe("fom_description"),
        "reference": _safe("reference_description"),
        "exploration_hints": hints,
        "relevant_skills": _relevant_skills_as_names(topology),
        "auxiliary_tools": _safe("auxiliary_tools_description"),
    }


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
    model: str = "openrouter/google/gemini-3-flash-preview",
    temperature: float = 0.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Map a NL analog idea to a registered topology (S11 Fase 3).

    Renders the ``analog.idea_to_topology`` skill as system prompt,
    packs the caller's description + optional constraints dict into a
    user prompt, asks the chosen LLM via LiteLLM, and parses the JSON
    response back into a structured dict.

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
        LiteLLM-routed model id. Default
        ``openrouter/google/gemini-3-flash-preview``. Any provider
        prefix supported by LiteLLM works; examples:

        * ``openrouter/google/gemini-3-flash-preview`` (needs
          ``OPENROUTER_API_KEY``)
        * ``zai/glm-4.6`` (needs ``ZAI_API_KEY``)
        * ``anthropic/claude-haiku-4-5`` (needs ``ANTHROPIC_API_KEY``)
        * ``gemini/gemini-2.5-flash`` (needs ``GEMINI_API_KEY``)
    temperature:
        Sampling temperature (default 0.0 for deterministic
        classification).
    dry_run:
        When True, render the prompt, validate the topology registry
        AND probe the provider env var for ``model`` without calling
        the LLM. Returns ``{"success": true, "dry_run": true,
        "env_ok": bool, "missing_keys": [...], ...}`` so MCP clients
        can sanity-check setup before spending tokens.

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
        failures: required env var missing, upstream HTTP error, JSON
        parse failure.
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
        try:
            env = _validate_model_env(model)
        except RuntimeError as exc:
            env = {"env_ok": False, "missing_keys": [], "error": str(exc)}
        return {
            "success": True,
            "dry_run": True,
            "model": model,
            "env_ok": env["env_ok"],
            "missing_keys": env["missing_keys"],
            "prompt_length": len(system_prompt) + len(user_prompt),
            "known_topologies": list_topology_names(),
        }

    try:
        raw, total_tokens = _call_llm(
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


def _sanitize_for_json(value: Any) -> Any:
    """Recursively coerce numpy scalars into plain Python types.

    ``AutoresearchRunner`` embeds numpy floats inside history / top_n
    entries via ``SpiceResult.measurements``. FastMCP serialises tool
    results with ``json.dumps`` which would reject those scalars, so
    we walk the structure once and call ``.item()`` where available.
    """
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_json(v) for v in value]
    item_attr = getattr(value, "item", None)
    if callable(item_attr) and not isinstance(value, (str, bytes)):
        try:
            return item_attr()
        except Exception:  # noqa: BLE001 — fall back to raw value
            return value
    return value


@mcp.tool()
async def run_autoresearch(
    topology_name: str,
    budget: int = 20,
    model: str = "openrouter/google/gemini-3-flash-preview",
    work_dir: str | None = None,
    top_n: int = 3,
    pdk: str | None = None,
    timeout_s: int = 3600,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Drive the greedy autoresearch loop for a registered topology.

    Exposes :class:`eda_agents.agents.autoresearch_runner.AutoresearchRunner`
    end-to-end so MCP clients (opencode TUI, Claude Code, …) can iterate
    a design exploration without handholding. Each call:

    1. Resolves the topology from its canonical name.
    2. Instantiates an ``AutoresearchRunner`` with ``model`` + ``budget``.
    3. Runs the greedy loop against SPICE for ``budget`` evaluations.
    4. Serialises the top-N designs, stats, and ``results.tsv`` path.

    The runner's resume-from-disk behaviour is preserved — pass the
    same ``work_dir`` twice to continue where a previous run stopped,
    with ``budget`` interpreted as "additional evaluations on top of
    the existing ``results.tsv``".

    Parameters
    ----------
    topology_name:
        Canonical topology name (from :func:`list_topology_names`).
    budget:
        Maximum SPICE evaluations. Default 20 keeps accidental calls
        cheap; raise explicitly for serious exploration.
    model:
        LiteLLM-routed model id for the proposal LLM (same prefix
        convention as :func:`recommend_topology`). Default is
        ``openrouter/google/gemini-3-flash-preview``.
    work_dir:
        Directory for ``program.md`` + ``results.tsv`` + ``eval_*``
        sub-dirs. When ``None``, a fresh tempdir is allocated per
        call (no resume).
    top_n:
        Number of top-FoM designs to return (default 3).
    pdk:
        Optional PDK name override; defaults to the topology's PDK.
    timeout_s:
        Hard wall-clock cap. Returns ``{"success": false, "error":
        "tool timeout ..."}`` if the greedy loop exceeds it.
    dry_run:
        When True, validate setup — topology registered, SpiceRunner
        constructible, model env var present — WITHOUT running
        evaluations. Returns ``{"success": true, "dry_run": true,
        "env_ok": bool, "missing_keys": [...], "pdk": str}``.

    Returns
    -------
    dict
        On success::

            {
              "success": true,
              "topology": str,
              "model": str,
              "pdk": str,
              "budget": int,
              "best_params": dict,
              "best_fom": float,
              "best_valid": bool,
              "total_evals": int,
              "kept": int,
              "discarded": int,
              "improvement_rate": float,
              "validity_rate": float,
              "total_tokens": int,
              "top_n": list[dict],   # sanitised, no history
              "tsv_path": str,
              "work_dir": str
            }

        On failure: ``{"success": false, "error": str}``. Typical
        failures: unknown topology, missing PDK/ngspice, missing env
        var, timeout.
    """
    if budget < 1:
        return {"success": False, "error": f"budget must be >= 1 (got {budget!r})"}
    if top_n < 1:
        return {"success": False, "error": f"top_n must be >= 1 (got {top_n!r})"}
    if timeout_s < 30:
        return {
            "success": False,
            "error": f"timeout_s must be >= 30 (got {timeout_s!r})",
        }

    try:
        topology = get_topology_by_name(topology_name)
    except KeyError as exc:
        return {"success": False, "error": str(exc)}

    # Probe model env up-front so dry_run and real runs share one code
    # path for this failure mode.
    try:
        env = _validate_model_env(model)
    except RuntimeError as exc:
        return {"success": False, "error": str(exc)}

    # Lazy import — SpiceRunner depends on PDK resolution which may
    # fail without $PDK_ROOT set. We want dry_run to surface that
    # error cleanly, not raise at import time.
    try:
        from eda_agents.core.spice_runner import SpiceRunner as _SpiceRunner

        _probe_runner = _SpiceRunner(pdk=pdk) if pdk else _SpiceRunner(
            pdk=getattr(topology, "pdk", None) or "ihp_sg13g2"
        )
        resolved_pdk = _probe_runner.pdk.name
    except Exception as exc:  # noqa: BLE001 — surface PDK/runner config errors
        return {
            "success": False,
            "error": f"SpiceRunner init failed: {exc}",
        }

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "topology": topology_name,
            "model": model,
            "pdk": resolved_pdk,
            "budget": budget,
            "env_ok": env["env_ok"],
            "missing_keys": env["missing_keys"],
        }

    if not env["env_ok"]:
        return {
            "success": False,
            "error": (
                f"missing env var(s) for model {model!r}: "
                f"{', '.join(env['missing_keys']) or '?'}"
            ),
        }

    # Resolve work_dir — fresh tempdir per call when caller omits one.
    import asyncio as _asyncio

    from eda_agents.agents.autoresearch_runner import AutoresearchRunner

    if work_dir is None:
        work_dir_path = Path(tempfile.mkdtemp(prefix="mcp_autoresearch_"))
    else:
        work_dir_path = Path(work_dir)
        work_dir_path.mkdir(parents=True, exist_ok=True)

    runner = AutoresearchRunner(
        topology=topology,
        model=model,
        budget=budget,
        pdk=pdk if pdk else None,
        top_n=top_n,
    )

    try:
        result = await _asyncio.wait_for(
            runner.run(work_dir_path), timeout=timeout_s,
        )
    except _asyncio.TimeoutError:
        return {
            "success": False,
            "error": f"tool timeout after {timeout_s}s",
            "work_dir": str(work_dir_path),
        }
    except Exception as exc:  # noqa: BLE001 — funnel runner errors
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "work_dir": str(work_dir_path),
        }

    payload = {
        "success": True,
        "topology": topology_name,
        "model": model,
        "pdk": resolved_pdk,
        "budget": budget,
        "best_params": result.best_params,
        "best_fom": float(result.best_fom),
        "best_valid": bool(result.best_valid),
        "total_evals": int(result.total_evals),
        "kept": int(result.kept),
        "discarded": int(result.discarded),
        "improvement_rate": float(result.improvement_rate),
        "validity_rate": float(result.validity_rate),
        "total_tokens": int(result.total_tokens),
        "top_n": _sanitize_for_json(result.top_n),
        "tsv_path": result.tsv_path,
        "work_dir": str(work_dir_path),
    }
    return _sanitize_for_json(payload)


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
