"""Autonomous greedy exploration loop for digital RTL-to-GDS flows.

Mirrors ``AutoresearchRunner`` (analog/SPICE) but evaluates by running
LibreLane partial flows instead of SPICE simulations.  Shares the
``ProgramStore`` / ``TsvLogger`` infrastructure from
``_autoresearch_core`` to persist state, resume, and log results.

The evaluation loop:
    1. LLM proposes flow config overrides (JSON)
    2. Config is written to the design project
    3. ``LibreLaneRunner.run_flow(to=stop_after)`` executes
    4. ``FlowMetrics.from_librelane_run_dir`` extracts metrics
    5. ``design.compute_fom(metrics)`` scores the result
    6. Greedy keep/discard

Per-eval cost is 10-100x higher than analog SPICE (~5-20 min vs ~2s),
so the default budget is low (5) and ``stop_after=ROUTE`` skips signoff
during exploration.  Full signoff is only run on kept designs at the end
(not yet implemented — Phase 4/6 territory).

Design space handling differs from analog:
- Analog: continuous (lo, hi) ranges with float clamping.
- Digital: discrete lists (non-monotonic response) with nearest-value
  snapping.  ``DigitalDesign.design_space()`` returns
  ``dict[str, list | tuple]``.

Usage:
    runner = DigitalAutoresearchRunner(
        design=FazyRvHachureDesign(),
        model="openrouter/anthropic/claude-haiku-4.5",
        budget=5,
    )
    result = await runner.run(work_dir=Path("digital_results"))

Mock mode (no LibreLane, for testing):
    runner = DigitalAutoresearchRunner(
        design=FazyRvHachureDesign(),
        model="test-model",
        budget=3,
        use_mock_metrics=Path("fixtures/mock_flow_metrics.json"),
    )
    result = await runner.run(work_dir=Path("test_results"))
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from pathlib import Path

from eda_agents.agents._autoresearch_core import (
    ProgramStore,
    TsvLogger,
    extract_json_from_response,
    generate_program_content,
    proposal_temperature,
)
from eda_agents.agents.phase_results import AutoresearchResult
from eda_agents.core.digital_design import DigitalDesign
from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.flow_stage import FlowStage
from eda_agents.core.stage_results import StageResults
from eda_agents.core.stages.physical_slice_runner import STAGE_TO_LIBRELANE
from eda_agents.skills.registry import render_relevant_skills

logger = logging.getLogger(__name__)


def _detect_librelane_venv_pythonpath() -> list[str]:
    """Return ``site-packages`` dirs of the LibreLane venv (or empty).

    Yosys-with-plugins from ``nix-eda`` embeds a CPython that does NOT
    inherit site-packages from the active venv. When LibreLane invokes
    ``yosys -y pyosys/json_header.py`` the embedded interpreter cannot
    import ``click`` or ``ys_common``, and the
    ``Yosys.JsonHeader`` step crashes with a ``ModuleNotFoundError``
    before any LibreLane run directory is produced.

    The fix is to prepend the LibreLane venv's site-packages dirs to
    ``PYTHONPATH`` so the embedded interpreter sees the venv's
    packages. We probe the same Python interpreter
    :func:`LibreLaneRunner._find_librelane_python` discovers and
    derive its venv layout.

    Returns an empty list if the venv cannot be located so callers
    can short-circuit gracefully.
    """
    import sys as _sys
    from pathlib import Path as _Path

    try:
        from eda_agents.core.librelane_runner import _find_librelane_python
    except ImportError:
        return []

    py = _find_librelane_python()
    if not py:
        return []

    # Do NOT resolve(): venv ``bin/python`` is a symlink chain that
    # ends at the system interpreter, which would jump out of the
    # venv and lose the site-packages we are trying to find.
    py_path = _Path(py)
    if not py_path.is_symlink() and not py_path.exists():
        return []
    venv_root = py_path.parent.parent  # <venv>/bin/python -> <venv>/
    if not (venv_root / "pyvenv.cfg").is_file():
        return []

    pyver = f"python{_sys.version_info.major}.{_sys.version_info.minor}"
    candidates = [
        venv_root / "lib" / pyver / "site-packages",
        venv_root / "local" / "lib" / pyver / "dist-packages",
        venv_root / "lib" / "python3" / "dist-packages",
        venv_root / "lib" / pyver / "dist-packages",
    ]
    return [str(p) for p in candidates if p.is_dir()]


def _pick_latest_openroad(candidates: list[str]) -> str | None:
    """Choose the newest openroad bin dir by ``YYYY-MM-DD`` suffix.

    Hash-based reverse-sort picks alphabetically; that is uncorrelated
    with the build date and routinely lands on a stale 2025-06 build
    when 2026-02 is also installed. The newer build matters because
    LibreLane's STAMidPnR / STAPostPNR Tcl scripts use the
    ``est::`` namespace introduced after the 2025-06 cutoff
    (``corner.tcl line 44 invalid command name "est::check_corner_wire_cap"``).
    Prefers the no-``-env`` flavour at a given date so the openroad
    bin does not drag a python3.13 site-packages prefix into PATH
    that conflicts with the python3.12 LibreLane venv.
    """
    import re

    if not candidates:
        return None

    dated: list[tuple[str, int, str]] = []
    for c in candidates:
        m = re.search(r"openroad-(\d{4}-\d{2}-\d{2})", c)
        date_key = m.group(1) if m else "0000-00-00"
        # Prefer non-env at same date: env flavour bundles a python
        # interpreter we do not want on PATH.
        env_penalty = 1 if "-python3-" in c else 0
        dated.append((date_key, env_penalty, c))
    dated.sort(reverse=True, key=lambda t: (t[0], -t[1]))
    return dated[0][2]


def _pick_latest_opensta(candidates: list[str]) -> str | None:
    """Choose the newest opensta bin dir by ``-version`` probe.

    Nix store hashes carry no version information, so the only way to
    rank candidates deterministically is to invoke ``sta -version``.
    We need 2.7.0+ for ``corner.tcl``'s
    ``report_checks -group_path_count`` flag; 2.6.0 fails STAPrePNR
    immediately with ``not a known keyword or flag``.
    """
    import os
    import subprocess

    if not candidates:
        return None

    ranked: list[tuple[tuple[int, ...], str]] = []
    for c in candidates:
        sta_bin = os.path.join(c, "sta")
        if not os.path.isfile(sta_bin):
            continue
        try:
            out = subprocess.run(
                [sta_bin, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            raw = (out.stdout or out.stderr).strip().splitlines()[0]
            parts = raw.split(".")
            ver = tuple(int(p) for p in parts if p.isdigit())
        except (subprocess.SubprocessError, OSError, ValueError):
            ver = (0, 0, 0)
        ranked.append((ver, c))
    if not ranked:
        return None
    ranked.sort(reverse=True, key=lambda t: t[0])
    return ranked[0][1]


def detect_nix_eda_tool_dirs() -> list[str]:
    """Scan /nix/store for LibreLane-compatible EDA tool binaries.

    LibreLane v3 requires yosys >= 0.60 and a recent OpenROAD. System
    packages on Ubuntu are often too old. When a Nix installation is
    present on this machine, prefer its bin directories.

    Returns the bin directory per tool, in the order
    yosys -> openroad -> opensta -> magic -> netgen -> klayout. The
    caller is responsible for deciding how to prepend these to PATH
    (or pass them verbatim into a subprocess env). Returns an empty
    list on systems without Nix or without these tools.

    Picker is tool-specific:

    * ``openroad`` is selected by the YYYY-MM-DD date in the store
      path (see :func:`_pick_latest_openroad`). Hash-reverse-sort is
      undefined w.r.t. version, and an old 2025-06 build is missing
      the ``est::`` Tcl namespace LibreLane's STAMidPnR uses.
    * ``opensta`` is selected by ``sta -version`` probe (see
      :func:`_pick_latest_opensta`). 2.6.0 lacks
      ``report_checks -group_path_count`` (STAPrePNR Tcl error);
      2.7.0+ is required.
    * Other tools currently only have a single candidate per host;
      reverse-sorted alphabetic pick is fine.
    """
    import glob

    nix_dirs: list[str] = []

    # yosys
    yosys_cands = sorted(
        glob.glob("/nix/store/*-yosys-with-plugins-0.6*/bin"),
        reverse=True,
    )
    if yosys_cands:
        nix_dirs.append(yosys_cands[0])

    # openroad: prefer newest YYYY-MM-DD, prefer non-env flavour at
    # the same date.
    openroad_cands = sorted(
        glob.glob("/nix/store/*-openroad*/bin"),
    )
    chosen = _pick_latest_openroad(openroad_cands)
    if chosen:
        nix_dirs.append(chosen)

    # opensta: pick highest version via -version probe (need 2.7.0+).
    opensta_cands = sorted(glob.glob("/nix/store/*-opensta*/bin"))
    chosen = _pick_latest_opensta(opensta_cands)
    if chosen:
        nix_dirs.append(chosen)

    # magic, netgen, klayout: single candidate per host on the
    # current nix-eda lock; alphabetic reverse-sort is safe.
    for pattern in [
        "/nix/store/*-magic-*/bin",
        "/nix/store/*-netgen-*/bin",
        "/nix/store/*-klayout-*/bin",
    ]:
        candidates = sorted(glob.glob(pattern), reverse=True)
        if candidates:
            nix_dirs.append(candidates[0])

    return nix_dirs

class DigitalAutoresearchRunner:
    """Autonomous greedy exploration for digital RTL-to-GDS flows.

    Parameters
    ----------
    design : DigitalDesign
        Design to optimize (defines design space, FoM, config paths).
    model : str
        LiteLLM model identifier for the proposal LLM.
    budget : int
        Maximum number of LibreLane evaluations.
    stop_after : FlowStage or None
        Stop after this stage.  Default: ``None`` (full flow including
        RCX, STA post-PnR, DRC, LVS, GDS).  Pass a ``FlowStage``
        to stop early for faster exploration, but metrics will be
        estimates, not post-RCX values.
    dedup : bool
        Reject proposals whose parameters exactly match a prior eval.
    use_mock_metrics : Path or None
        If set, load FlowMetrics from this JSON file instead of running
        LibreLane.  For testing only.
    top_n : int
        Number of top designs to return.
    strategy : str
        Optimization strategy: ``"flow"`` (config-only, default),
        ``"rtl"`` (RTL micro-edits), ``"hybrid"`` (RTL + config).
    run_rtl_sim : bool
        Run cocotb / iverilog RTL simulation after lint, regardless
        of strategy. Defaults to ``True``: every digital design is
        required to ship a testbench (``DigitalDesign.testbench`` is
        abstract), so the gate runs by default. Pass ``False``
        explicitly to skip cocotb during fast-iteration loops.
    """

    def __init__(
        self,
        design: DigitalDesign,
        model: str = "openrouter/anthropic/claude-haiku-4.5",
        budget: int = 5,
        stop_after: FlowStage | None = None,
        dedup: bool = True,
        use_mock_metrics: Path | None = None,
        top_n: int = 3,
        backend: str = "adk",
        strategy: str = "flow",
        run_rtl_sim: bool | None = None,
        allow_dangerous: bool = False,
        cli_path: str = "claude",
        litellm_model: str = "openrouter/google/gemini-2.5-flash",
        litellm_allow_bash: bool = False,
        opencode_cli_path: str = "opencode",
        opencode_model: str | None = None,
    ):
        if backend not in ("adk", "cc_cli", "litellm", "opencode"):
            raise ValueError(
                f"Unknown backend: {backend!r}."
                " Use 'adk', 'cc_cli', 'litellm', or 'opencode'."
            )
        if strategy not in ("flow", "rtl", "hybrid"):
            raise ValueError(f"Unknown strategy: {strategy!r}. Use 'flow', 'rtl', or 'hybrid'.")
        self.design = design
        self.model = model
        self.budget = budget
        self.stop_after = stop_after  # None = full flow
        self.dedup = dedup
        self.use_mock_metrics = use_mock_metrics
        self.top_n = top_n
        self.backend = backend
        self.strategy = strategy
        self.allow_dangerous = allow_dangerous
        self.cli_path = cli_path
        self.litellm_model = litellm_model
        self.litellm_allow_bash = litellm_allow_bash
        self.opencode_cli_path = opencode_cli_path
        self.opencode_model = opencode_model

        # ``DigitalDesign.testbench`` is mandatory, so the RTL sim
        # gate runs by default for every strategy (flow / rtl /
        # hybrid). Callers opt out explicitly with ``run_rtl_sim=False``.
        self.run_rtl_sim = True if run_rtl_sim is None else run_rtl_sim

        # Measurement columns are sourced from the design (domain
        # concern). The TSV header and per-eval rows use this exact
        # ordering; designs override ``measurement_columns()`` to
        # extend the default PPA set with domain-specific metrics.
        self.measurement_cols = list(self.design.measurement_columns())

        # Cumulative token counter populated by ``_propose_params`` from
        # the LLM backend's ``response.usage``. Reset at the start of
        # every ``run()`` so repeated calls don't leak across runs; the
        # field is surfaced on :class:`AutoresearchResult.total_tokens`.
        self._cumulative_tokens = 0

    # ------------------------------------------------------------------
    # program.md
    # ------------------------------------------------------------------

    def _generate_program(self) -> str:
        from eda_agents.core.pdk import resolve_pdk as _resolve_pdk
        _pdk = self.design.pdk_config() or _resolve_pdk()

        return generate_program_content(
            domain_name=self.design.project_name(),
            pdk_display_name=_pdk.display_name,
            fom_description=self.design.fom_description(),
            specs_description=self.design.specs_description(),
            design_vars_description=self.design.design_vars_description(),
            reference_description=self.design.reference_description(),
        )

    def _make_program_store(self, work_dir: Path) -> ProgramStore:
        return ProgramStore(work_dir, self._generate_program)

    def _make_tsv_logger(self, tsv_path: Path) -> TsvLogger:
        return TsvLogger(
            tsv_path=tsv_path,
            param_cols=list(self.design.design_space().keys()),
            measurement_cols=self.measurement_cols,
        )

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _system_prompt(self, program_content: str) -> str:
        """Build the system prompt: skills (S10c) + program.md + response suffix.

        Design-declared skills are rendered first so the methodology
        framing arrives before the run-local strategy in ``program.md``.
        Gated by ``EDA_AGENTS_INJECT_SKILLS``: set to ``"0"`` to fall
        back to the pre-S10c prompt.

        The response-format guidance tells the LLM that the listed
        design space is a *starting hint*, not a cage: it is free to
        propose any LibreLane safe-listed config key and let the
        downstream loop's safety check accept or reject it. The only
        immutables are the spec assertions
        (:meth:`DigitalDesign.check_validity`) and the FoM
        (:meth:`DigitalDesign.compute_fom`); both live in the design
        subclass and are evaluation criteria, not action-space
        levers.
        """
        space = self.design.design_space()
        example_keys = list(space.keys())
        example = ", ".join(f'"{k}": ...' for k in example_keys)

        skills_block = ""
        if os.environ.get("EDA_AGENTS_INJECT_SKILLS", "1") != "0":
            skills_block = render_relevant_skills(
                self.design.relevant_skills(), self.design
            )
        prefix = f"{skills_block}\n\n" if skills_block else ""

        # Surface the LibreLane safe-list so the LLM knows what other
        # keys it is free to touch. We import lazily to avoid a top-of-
        # file cycle with the runner module.
        try:
            from eda_agents.core.librelane_runner import SAFE_CONFIG_KEYS
            safe_keys = sorted(SAFE_CONFIG_KEYS)
        except ImportError:
            safe_keys = []
        safe_list = ", ".join(safe_keys) if safe_keys else "(unavailable)"

        return (
            f"{prefix}"
            f"You are an autonomous digital design optimizer. Your program "
            f"is defined below. Follow it exactly.\n\n"
            f"{program_content}\n\n"
            f"DESIGN-SPACE POLICY: the variables listed under '## Design "
            f"Space' above are a starting hint, not a cage. Treat them as "
            f"the bounds the optimizer pre-knows. You are free to:\n"
            f"  * Propose any value within the listed range.\n"
            f"  * Propose ANY OTHER key from the LibreLane safe-list "
            f"(below). The runner will write the value into config.yaml; "
            f"unsafe keys are dropped with a warning, valid keys take "
            f"effect on the next eval.\n"
            f"  * In hybrid/rtl strategies, edit RTL files directly via "
            f"the tools your harness provides.\n"
            f"The ONLY immutables are: the spec (what must hold for a "
            f"valid design, encoded in ``check_validity``) and the FoM "
            f"definition (what we are maximising, encoded in "
            f"``compute_fom``). Both live in the design class; do not "
            f"try to redefine them from inside a proposal.\n\n"
            f"LibreLane safe-listed config keys: {safe_list}\n\n"
            f"RESPONSE FORMAT: You must respond with ONLY a JSON object "
            f"containing the next design parameters to try. No "
            f"explanation, no markdown fences, no commentary. Just the "
            f"raw JSON. Keys not from the design space are fine as long "
            f"as they are in the LibreLane safe-list.\n"
            f"Example: {{{example}}}"
        )

    def _build_proposal_prompt(
        self,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> str:
        parts = [f"Evaluation {eval_num}/{self.budget}.\n"]

        # Anti-centroid anchor cue: when eval 1 was seeded with the
        # project's baseline (status flag ``seed`` on the first
        # history entry), tell the LLM explicitly so it proposes a
        # delta instead of redundantly guessing the baseline again.
        if (
            eval_num == 2
            and history
            and history[0].get("seed")
        ):
            parts.append(
                "Eval 1 is the project baseline read from "
                "config.yaml; propose a delta, not the same point.\n"
            )

        if best:
            parts.append(
                f"Current best (eval #{best['eval']}): "
                f"FoM={best['fom']:.2e}, valid={best['valid']}\n"
                f"Params: {json.dumps(best['params'], indent=2)}\n"
                f"Measurements: WNS={best.get('wns_worst_ns', '?')}ns, "
                f"cells={best.get('cell_count', '?')}, "
                f"area={best.get('die_area_um2', '?')}um2, "
                f"power={best.get('power_mw', '?')}mW\n"
            )
        else:
            parts.append("No valid design found yet. Start exploring.\n")

        if history:
            parts.append("\nHistory (last 20):\n")
            for h in history[-20:]:
                status = h.get("status", "kept" if h.get("kept") else "discarded")
                valid = "valid" if h.get("valid") else "INVALID"
                violations = h.get("violations", [])
                viol_str = f" [{', '.join(violations)}]" if violations else ""
                parts.append(
                    f"  #{h['eval']}: FoM={h['fom']:.2e} {valid}{viol_str} "
                    f"({status}) -- {json.dumps(h['params'])}\n"
                )

        parts.append(
            f"\nPropose the next design parameters as a JSON object. "
            f"Budget remaining: {self.budget - eval_num + 1}."
        )

        return "".join(parts)

    async def _propose_params(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict[str, float | int]:
        """Ask LLM to propose next design parameters.

        Dispatches by ``self.backend`` so flow-strategy proposals run on
        the same engine the user picked for the rest of the loop:

        * ``cc_cli``   -> Claude Code CLI (subscription, Opus 4.7 default).
        * ``opencode`` -> opencode CLI (any provider, e.g. gpt-5.3-codex).
        * ``adk`` / ``litellm`` -> the legacy LiteLLM ``acompletion`` path.

        Without this dispatch, a user who selects ``--backend opencode
        --model openai/gpt-5.3-codex`` ends up with proposals routed to
        OpenAI directly via LiteLLM, bypassing the opencode CLI and its
        OAuth-managed subscription. Same shape with ``cc_cli``: the
        proposal silently runs on whatever LiteLLM model defaulted to,
        not on the Claude subscription the user paid for.
        """
        if self.backend == "cc_cli":
            return await self._propose_params_via_cc_cli(
                program_content, history, best, eval_num
            )
        if self.backend == "opencode":
            return await self._propose_params_via_opencode(
                program_content, history, best, eval_num
            )

        # backend in {"adk", "litellm"}: LiteLLM ``acompletion`` path
        import litellm

        prompt = self._build_proposal_prompt(history, best, eval_num)

        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt(program_content)},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1024,
            "temperature": proposal_temperature(self.model),
        }

        try:
            response = await litellm.acompletion(
                **kwargs, response_format={"type": "json_object"}
            )
        except Exception as e:
            err_str = f"{type(e).__name__}: {e}"
            if "response_format" in err_str or "UnsupportedParams" in err_str:
                logger.info(
                    "response_format not supported by %s, retrying without",
                    self.model,
                )
                response = await litellm.acompletion(**kwargs)
            else:
                raise

        usage = getattr(response, "usage", None)
        if usage is not None:
            total = getattr(usage, "total_tokens", None)
            if total is None and isinstance(usage, dict):
                total = usage.get("total_tokens")
            if total:
                self._cumulative_tokens += int(total)

        content = response.choices[0].message.content or ""
        content = extract_json_from_response(content)
        params = json.loads(content)

        return self._clamp_params(params)

    def _build_flow_proposal_prompt(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> str:
        """Concatenate system + user prompts for CLI-harness consumption.

        ClaudeCodeHarness and OpenCodeHarness do not expose a separate
        system-prompt slot; both accept a single prompt string. We
        glue ``_system_prompt`` and ``_build_proposal_prompt`` together
        and append a hard instruction telling the agent to emit JSON
        only and not touch the working tree (the harness still loads
        an editing-capable agent; the prompt is the only guardrail).
        """
        sys_prompt = self._system_prompt(program_content)
        user_prompt = self._build_proposal_prompt(history, best, eval_num)
        return (
            f"{sys_prompt}\n\n"
            f"---\n\n"
            f"{user_prompt}\n\n"
            f"IMPORTANT: Respond with ONLY the raw JSON object. No "
            f"commentary, no markdown fences, no tool calls. Do not "
            f"edit any files in the working directory. Your entire "
            f"response must be parseable by ``json.loads`` as-is."
        )

    async def _propose_params_via_cc_cli(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict[str, float | int]:
        """Flow-strategy proposal via the Claude Code CLI subscription.

        Mirrors :meth:`_propose_cc_cli` (rtl/hybrid) but for the
        ``flow`` strategy: we only need a JSON object matching the
        design space, no file edits, no lint. The harness still runs a
        full Claude agent (Opus 4.7 by default), so the prompt has to
        steer it to JSON-only output. The ``result_text`` is parsed
        with :func:`extract_json_from_response` and clamped via
        :meth:`_clamp_params`.
        """
        from eda_agents.agents.claude_code_harness import ClaudeCodeHarness

        prompt = self._build_flow_proposal_prompt(
            program_content, history, best, eval_num
        )

        harness = ClaudeCodeHarness(
            prompt=prompt,
            work_dir=self.design.project_dir(),
            allow_dangerous=self.allow_dangerous,
            cli_path=self.cli_path,
            timeout_s=300,
            max_budget_usd=2.0,
        )

        result = await harness.run()
        if not result.success:
            raise RuntimeError(
                f"CC CLI flow proposal failed: {result.error or 'unknown'}"
            )

        text = result.result_text or ""
        content = extract_json_from_response(text)
        params = json.loads(content)
        return self._clamp_params(params)

    async def _propose_params_via_opencode(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict[str, float | int]:
        """Flow-strategy proposal via the opencode CLI.

        Same shape as :meth:`_propose_params_via_cc_cli`. Uses
        ``self.opencode_model`` (e.g. ``openai/gpt-5.3-codex``) under
        the opencode CLI so codex-class models run through the
        opencode OAuth, NOT through LiteLLM ``acompletion`` (which
        would short-circuit straight to OpenAI's API and ignore the
        subscription).
        """
        from eda_agents.agents.opencode_harness import OpenCodeHarness

        prompt = self._build_flow_proposal_prompt(
            program_content, history, best, eval_num
        )

        harness = OpenCodeHarness(
            prompt=prompt,
            work_dir=self.design.project_dir(),
            model=self.opencode_model,
            cli_path=self.opencode_cli_path,
            timeout_s=300,
        )

        result = await harness.run()
        if not result.success:
            raise RuntimeError(
                f"OpenCode flow proposal failed: {result.error or 'unknown'}"
            )

        text = result.result_text or ""
        content = extract_json_from_response(text)
        params = json.loads(content)
        return self._clamp_params(params)

    def _clamp_params(self, params: dict) -> dict[str, float | int]:
        """Coerce proposed params for downstream consumption.

        Behaviour by key class:

        * **Declared design-space keys**: clamp / snap to the
          declared range. Discrete lists snap to the nearest valid
          value; continuous ``(lo, hi)`` tuples clamp to the bound.
          A missing value gets filled from ``default_config`` (or
          the lower bound if the default is also absent).
        * **Undeclared keys** (anything the LLM proposes that is not
          in ``design.design_space()``): pass through untouched.

        The pass-through is intentional. The autoresearch policy is
        "design space is a starting hint; the model has full
        flexibility to experiment with any LibreLane safe-listed
        config key". The downstream
        ``LibreLaneRunner.modify_config`` call validates each key
        against ``SAFE_CONFIG_KEYS`` and either accepts it (writes
        to ``config.yaml``) or rejects it (caught by the eval loop's
        ``ValueError`` handler at ``_evaluate``). Either outcome is
        useful feedback for the LLM; silently dropping undeclared
        keys was the broken behaviour.
        """
        space = self.design.design_space()
        default = self.design.default_config()
        clean: dict[str, float | int] = {}

        for name, values in space.items():
            val = params.get(name)
            if val is None:
                clean[name] = default.get(name, values[0] if isinstance(values, list) else values[0])
                continue

            val = float(val)

            if isinstance(values, list):
                # Snap to nearest value in the discrete list
                clean[name] = min(values, key=lambda v: abs(v - val))
            elif isinstance(values, tuple) and len(values) == 2:
                lo, hi = values
                clean[name] = max(lo, min(hi, val))
            else:
                clean[name] = val

        # Pass-through any key the LLM proposed that is not in the
        # declared design space. modify_config will safety-check each
        # one and reject (with a logged warning) anything outside
        # SAFE_CONFIG_KEYS.
        for name, val in params.items():
            if name in space or name in clean:
                continue
            clean[name] = val

        return clean

    # ------------------------------------------------------------------
    # RTL-aware methods (strategy='rtl' and 'hybrid')
    # ------------------------------------------------------------------

    @staticmethod
    def _prepend_nix_tools(env_extra: dict[str, str]) -> None:
        """Prepend nix-provided EDA tools to PATH if system versions are old.

        LibreLane v3 requires yosys >= 0.60 and a recent OpenROAD.
        System packages may be outdated. Auto-detect and prepend
        nix-store tool directories when available.

        Also injects ``PYTHONPATH`` pointing at the LibreLane venv's
        ``site-packages``: Yosys (from Nix) embeds its own CPython that
        runs ``pyosys/json_header.py`` and imports ``click``,
        ``ys_common`` from the venv. Without this, the
        ``Yosys.JsonHeader`` step crashes with ``ModuleNotFoundError:
        No module named 'click'`` before producing any
        ``runs/<tag>/`` artefact. This mirrors the PYTHONPATH prefix
        the from-spec idea-loop prompt embeds (``tool_defs.py:984``)
        for the agent's own Bash tool calls; autoresearch invokes
        LibreLane directly, so the env must carry the same prefix.
        """
        import os as _os

        nix_dirs = detect_nix_eda_tool_dirs()
        if nix_dirs:
            current_path = env_extra.get("PATH", _os.environ.get("PATH", ""))
            nix_prefix = ":".join(nix_dirs)
            env_extra["PATH"] = f"{nix_prefix}:{current_path}"
            logger.info("Prepended nix tools to PATH: %s", nix_prefix)

        venv_paths = _detect_librelane_venv_pythonpath()
        if venv_paths:
            current = env_extra.get(
                "PYTHONPATH", _os.environ.get("PYTHONPATH", "")
            )
            prefix = ":".join(venv_paths)
            env_extra["PYTHONPATH"] = (
                f"{prefix}:{current}" if current else prefix
            )
            logger.info(
                "Prepended LibreLane venv site-packages to PYTHONPATH: %s",
                prefix,
            )

    def _read_rtl_sources(self) -> dict[str, str]:
        """Read current RTL files into {relative_path: content}."""
        result: dict[str, str] = {}
        project_dir = self.design.project_dir()
        for src in self.design.rtl_sources():
            if src.is_file():
                try:
                    rel = str(src.resolve().relative_to(project_dir.resolve()))
                except ValueError:
                    rel = src.name
                result[rel] = src.read_text()
        return result

    def _validate_rtl_proposal(self, proposal: dict) -> tuple[bool, str]:
        """Check that a proposal has the expected structure.

        Returns (ok, error_message). Validates:
        - rtl_changes is a dict with string values
        - Module name is preserved in each changed file
        """
        rtl_changes = proposal.get("rtl_changes", {})
        if not isinstance(rtl_changes, dict):
            return False, "rtl_changes must be a dict"

        # Check module name preservation
        import re
        current_rtl = self._read_rtl_sources()
        for fname, new_content in rtl_changes.items():
            if not isinstance(new_content, str):
                return False, f"rtl_changes[{fname!r}] must be a string"
            # Find module name in current RTL
            if fname in current_rtl:
                old_modules = re.findall(
                    r"module\s+(\w+)", current_rtl[fname]
                )
                new_modules = re.findall(r"module\s+(\w+)", new_content)
                if old_modules and new_modules and old_modules[0] != new_modules[0]:
                    return False, (
                        f"Module name changed in {fname}: "
                        f"{old_modules[0]} -> {new_modules[0]}"
                    )
        return True, ""

    def _apply_rtl_and_lint(
        self,
        proposal: dict,
        snapshot_mgr,
        eval_num: int,
    ) -> tuple[bool, str | None, int]:
        """Apply RTL changes, run lint. Returns (ok, error, lint_warnings).

        1. Restore best RTL state
        2. Apply proposed RTL changes
        3. Run RtlLintRunner
        4. Return result
        """
        from eda_agents.core.stages.rtl_lint_runner import RtlLintRunner
        from eda_agents.core.tool_environment import LocalToolEnvironment

        rtl_changes = proposal.get("rtl_changes", {})

        # Restore to best-known state
        config_path = (
            self.design.librelane_config()
            if self.strategy == "hybrid" else None
        )
        snapshot_mgr.restore_best(
            self.design.rtl_sources(), config_path=config_path
        )

        # Apply new RTL
        if rtl_changes:
            snapshot_mgr.apply_rtl_changes(rtl_changes)

        # Lint
        env = LocalToolEnvironment()
        linter = RtlLintRunner(design=self.design, env=env)
        lint_result = linter.run()

        if not lint_result.success:
            error = lint_result.error or "lint failed"
            log = lint_result.log_tail or ""
            return False, f"{error}\n{log[:500]}", 0

        # ``StageResult`` exposes lint metrics via ``metrics_delta`` (the
        # canonical name across the digital flow stages). The previous
        # version reached for ``lint_result.metrics`` which does not
        # exist on the dataclass and crashed the very first hybrid eval
        # for every non-cc_cli backend (opencode hit it on demo_goertzel
        # FP32; cc_cli skips this code path because the agent already
        # wrote files and the call site lints inline).
        warnings = lint_result.metrics_delta.get("lint_warnings", 0)
        return True, None, int(warnings)

    async def _propose_rtl(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict:
        """LLM proposal for strategy='rtl'. Returns dict with rtl_changes."""
        import litellm

        from eda_agents.agents.rtl_proposal_prompts import (
            rtl_proposal_prompt,
            rtl_system_prompt,
        )

        sys_prompt = rtl_system_prompt(
            program_content,
            self._read_rtl_sources(),
            self.design.specification(),
        )
        user_prompt = rtl_proposal_prompt(history, best, eval_num, self.budget)

        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 4096,
            "temperature": proposal_temperature(self.model),
        }

        try:
            response = await litellm.acompletion(
                **kwargs, response_format={"type": "json_object"}
            )
        except Exception as e:
            err_str = f"{type(e).__name__}: {e}"
            if "response_format" in err_str or "UnsupportedParams" in err_str:
                response = await litellm.acompletion(**kwargs)
            else:
                raise

        content = response.choices[0].message.content or ""
        content = extract_json_from_response(content)
        return json.loads(content)

    async def _propose_hybrid(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict:
        """LLM proposal for strategy='hybrid'. Returns dict with config + rtl_changes."""
        import litellm

        from eda_agents.agents.rtl_proposal_prompts import (
            hybrid_system_prompt,
            rtl_proposal_prompt,
        )

        sys_prompt = hybrid_system_prompt(
            program_content,
            self._read_rtl_sources(),
            self.design.design_space(),
            self.design.specification(),
        )
        user_prompt = rtl_proposal_prompt(history, best, eval_num, self.budget)

        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 4096,
            "temperature": proposal_temperature(self.model),
        }

        try:
            response = await litellm.acompletion(
                **kwargs, response_format={"type": "json_object"}
            )
        except Exception as e:
            err_str = f"{type(e).__name__}: {e}"
            if "response_format" in err_str or "UnsupportedParams" in err_str:
                response = await litellm.acompletion(**kwargs)
            else:
                raise

        content = response.choices[0].message.content or ""
        content = extract_json_from_response(content)
        return json.loads(content)

    async def _propose_cc_cli(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict:
        """CC CLI proposal for rtl/hybrid strategies.

        The CC CLI agent reads RTL from disk, proposes and writes changes,
        runs lint, and outputs a JSON summary.  We then read the modified
        files to determine what changed.
        """
        from eda_agents.agents.claude_code_harness import ClaudeCodeHarness
        from eda_agents.agents.rtl_proposal_prompts import (
            cc_cli_hybrid_prompt,
            cc_cli_rtl_prompt,
            rtl_proposal_prompt,
        )

        # Build the proposal context
        user_context = rtl_proposal_prompt(history, best, eval_num, self.budget)

        optimization_goal = (
            f"{self.design.fom_description()}\n\n"
            f"Constraints: {self.design.specs_description()}\n\n"
            f"Current evaluation: {user_context}"
        )

        pdk_root = None
        if hasattr(self.design, "pdk_root") and self.design.pdk_root():
            pdk_root = str(self.design.pdk_root())

        metrics = (
            {
                "wns_worst_ns": best.get("wns_worst_ns"),
                "cell_count": best.get("cell_count"),
                "die_area_um2": best.get("die_area_um2"),
                "power_mw": best.get("power_mw"),
            } if best else None
        )

        if self.strategy == "rtl":
            prompt = cc_cli_rtl_prompt(
                design_name=self.design.project_name(),
                design_spec=self.design.specification(),
                optimization_goal=optimization_goal,
                rtl_file_paths=self.design.rtl_sources(),
                current_metrics=metrics,
                pdk_root=pdk_root,
            )
        else:
            prompt = cc_cli_hybrid_prompt(
                design_name=self.design.project_name(),
                design_spec=self.design.specification(),
                optimization_goal=optimization_goal,
                rtl_file_paths=self.design.rtl_sources(),
                config_path=self.design.librelane_config(),
                current_metrics=metrics,
                pdk_root=pdk_root,
            )

        harness = ClaudeCodeHarness(
            prompt=prompt,
            work_dir=self.design.project_dir(),
            allow_dangerous=self.allow_dangerous,
            cli_path=self.cli_path,
            timeout_s=600,  # 10 min per proposal
            max_budget_usd=2.0,
        )

        result = await harness.run()

        if not result.success:
            raise RuntimeError(
                f"CC CLI proposal failed: {result.error or 'unknown'}"
            )

        # The agent wrote files directly. Read back what changed.
        rtl_changes: dict[str, str] = {}
        for src in self.design.rtl_sources():
            if src.is_file():
                try:
                    rel = str(
                        src.resolve().relative_to(
                            self.design.project_dir().resolve()
                        )
                    )
                except ValueError:
                    rel = src.name
                rtl_changes[rel] = src.read_text()

        # Try to extract rationale from the agent output
        rationale = "CC CLI agent proposal"
        text = result.result_text or ""
        try:
            # Look for JSON in the output
            summary = json.loads(extract_json_from_response(text))
            rationale = summary.get("rationale", rationale)
        except (json.JSONDecodeError, ValueError):
            # Extract any line that looks like a rationale
            for line in text.split("\n"):
                if "rationale" in line.lower() or "changed" in line.lower():
                    rationale = line.strip()[:200]
                    break

        proposal: dict = {
            "rtl_changes": rtl_changes,
            "rationale": rationale,
        }

        # For hybrid, also check if config was modified
        if self.strategy == "hybrid":
            proposal["config"] = {}  # agent may have modified config directly

        return proposal

    # ------------------------------------------------------------------
    # LiteLLM / OpenCode proposals (same prompt, different harness)
    # ------------------------------------------------------------------

    async def _propose_litellm(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict:
        """LiteLLMAgentHarness proposal for rtl/hybrid strategies."""
        from eda_agents.agents.litellm_harness import LiteLLMAgentHarness
        from eda_agents.agents.rtl_proposal_prompts import (
            cc_cli_hybrid_prompt,
            cc_cli_rtl_prompt,
            rtl_proposal_prompt,
        )

        user_context = rtl_proposal_prompt(history, best, eval_num, self.budget)
        optimization_goal = (
            f"{self.design.fom_description()}\n\n"
            f"Constraints: {self.design.specs_description()}\n\n"
            f"Current evaluation: {user_context}"
        )

        pdk_root = None
        if hasattr(self.design, "pdk_root") and self.design.pdk_root():
            pdk_root = str(self.design.pdk_root())

        metrics = (
            {
                "wns_worst_ns": best.get("wns_worst_ns"),
                "cell_count": best.get("cell_count"),
                "die_area_um2": best.get("die_area_um2"),
                "power_mw": best.get("power_mw"),
            }
            if best
            else None
        )

        if self.strategy == "rtl":
            prompt = cc_cli_rtl_prompt(
                design_name=self.design.project_name(),
                design_spec=self.design.specification(),
                optimization_goal=optimization_goal,
                rtl_file_paths=self.design.rtl_sources(),
                current_metrics=metrics,
                pdk_root=pdk_root,
            )
        else:
            prompt = cc_cli_hybrid_prompt(
                design_name=self.design.project_name(),
                design_spec=self.design.specification(),
                optimization_goal=optimization_goal,
                rtl_file_paths=self.design.rtl_sources(),
                config_path=self.design.librelane_config(),
                current_metrics=metrics,
                pdk_root=pdk_root,
            )

        harness = LiteLLMAgentHarness(
            prompt=prompt,
            work_dir=self.design.project_dir(),
            model=self.litellm_model,
            timeout_s=600,
            max_budget_usd=2.0,
            allow_bash=self.litellm_allow_bash,
        )

        result = await harness.run()

        if not result.success:
            raise RuntimeError(
                f"LiteLLM proposal failed: {result.error or 'unknown'}"
            )

        return self._extract_rtl_changes(result.result_text)

    async def _propose_opencode(
        self,
        program_content: str,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> dict:
        """OpenCodeHarness proposal for rtl/hybrid strategies."""
        from eda_agents.agents.opencode_harness import OpenCodeHarness
        from eda_agents.agents.rtl_proposal_prompts import (
            cc_cli_hybrid_prompt,
            cc_cli_rtl_prompt,
            rtl_proposal_prompt,
        )

        user_context = rtl_proposal_prompt(history, best, eval_num, self.budget)
        optimization_goal = (
            f"{self.design.fom_description()}\n\n"
            f"Constraints: {self.design.specs_description()}\n\n"
            f"Current evaluation: {user_context}"
        )

        pdk_root = None
        if hasattr(self.design, "pdk_root") and self.design.pdk_root():
            pdk_root = str(self.design.pdk_root())

        metrics = (
            {
                "wns_worst_ns": best.get("wns_worst_ns"),
                "cell_count": best.get("cell_count"),
                "die_area_um2": best.get("die_area_um2"),
                "power_mw": best.get("power_mw"),
            }
            if best
            else None
        )

        if self.strategy == "rtl":
            prompt = cc_cli_rtl_prompt(
                design_name=self.design.project_name(),
                design_spec=self.design.specification(),
                optimization_goal=optimization_goal,
                rtl_file_paths=self.design.rtl_sources(),
                current_metrics=metrics,
                pdk_root=pdk_root,
            )
        else:
            prompt = cc_cli_hybrid_prompt(
                design_name=self.design.project_name(),
                design_spec=self.design.specification(),
                optimization_goal=optimization_goal,
                rtl_file_paths=self.design.rtl_sources(),
                config_path=self.design.librelane_config(),
                current_metrics=metrics,
                pdk_root=pdk_root,
            )

        harness = OpenCodeHarness(
            prompt=prompt,
            work_dir=self.design.project_dir(),
            model=self.opencode_model,
            timeout_s=600,
            cli_path=self.opencode_cli_path,
        )

        result = await harness.run()

        if not result.success:
            raise RuntimeError(
                f"OpenCode proposal failed: {result.error or 'unknown'}"
            )

        return self._extract_rtl_changes(result.result_text)

    def _extract_rtl_changes(self, result_text: str) -> dict:
        """Read back RTL files from disk and extract rationale from agent output.

        Shared by _propose_litellm and _propose_opencode — mirrors the logic
        in _propose_cc_cli without duplicating it.
        """
        rtl_changes: dict[str, str] = {}
        for src in self.design.rtl_sources():
            if src.is_file():
                try:
                    rel = str(
                        src.resolve().relative_to(
                            self.design.project_dir().resolve()
                        )
                    )
                except ValueError:
                    rel = src.name
                rtl_changes[rel] = src.read_text()

        rationale = "agent proposal"
        try:
            summary = json.loads(extract_json_from_response(result_text))
            rationale = summary.get("rationale", rationale)
        except (json.JSONDecodeError, ValueError):
            for line in result_text.split("\n"):
                if "rationale" in line.lower() or "changed" in line.lower():
                    rationale = line.strip()[:200]
                    break

        proposal: dict = {"rtl_changes": rtl_changes, "rationale": rationale}
        if self.strategy == "hybrid":
            proposal["config"] = {}
        return proposal

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def _evaluate(
        self,
        params: dict[str, float | int],
        work_dir: Path,
        eval_num: int,
    ) -> dict:
        """Run LibreLane flow and extract metrics."""
        # Mock mode: load metrics from fixture file
        if self.use_mock_metrics:
            return self._evaluate_mock(params, eval_num)

        from eda_agents.core.librelane_runner import LibreLaneRunner
        from eda_agents.core.pdk import resolve_pdk

        # Apply config overrides
        config_path = self.design.librelane_config()

        # Resolve PDK from design (GenericDesign binds it) or env fallback.
        # Never rely on inherited PDK/PDK_ROOT -- we inject both explicitly
        # to avoid the layer-conflict bug seen when a GF180 run picked up
        # an inherited PDK=ihp-sg13g2 from the parent shell.
        pdk_cfg = self.design.pdk_config() or resolve_pdk()
        pdk_root_str = str(self.design.pdk_root() or "")

        env_extra: dict[str, str] = {
            "PDK": pdk_cfg.librelane_pdk_name,
        }
        if pdk_root_str:
            env_extra["PDK_ROOT"] = pdk_root_str

        logger.info(
            "[eval %s] PDK=%s PDK_ROOT=%s (design=%s)",
            eval_num, env_extra["PDK"],
            env_extra.get("PDK_ROOT", "<unset>"),
            self.design.project_name(),
        )

        # Ensure nix-provided yosys (0.62+) is on PATH if system yosys is old
        self._prepend_nix_tools(env_extra)

        # Flags come from two layers: PDK-level (e.g. ``--manual-pdk``
        # for GF180MCU) plus design-level (e.g. fazyrv skips KLayout
        # and Magic DRC because its leo/gf180mcu LibreLane pin has a
        # broken deck). Keep both, PDK flags first so a design can
        # append overrides deterministically.
        design_flags = (
            list(self.design.librelane_extra_flags())
            if hasattr(self.design, "librelane_extra_flags")
            else []
        )
        combined_flags = list(pdk_cfg.librelane_extra_flags) + design_flags

        runner = LibreLaneRunner(
            project_dir=self.design.project_dir(),
            config_file=config_path.name,
            pdk_root=pdk_root_str,
            timeout_s=1800,
            shell_wrapper=self.design.shell_wrapper(),
            env_extra=env_extra,
            extra_flags=combined_flags,
        )

        # Write exploration params to config
        for key, value in params.items():
            try:
                runner.modify_config(key, value)
            except ValueError:
                logger.warning("Key %s not in SAFE_CONFIG_KEYS, skipping", key)

        # Also apply design-specific overrides
        for key, value in self.design.flow_config_overrides().items():
            runner.modify_config(key, value, force=True)

        # Determine the LibreLane stop step
        if self.stop_after is not None and self.stop_after in STAGE_TO_LIBRELANE:
            _, to_step = STAGE_TO_LIBRELANE[self.stop_after]
        else:
            to_step = None  # full flow (including RCX, STA post, DRC, LVS, GDS)

        tag = f"eval_{eval_num:03d}"
        flow_result = runner.run_flow(tag=tag, to=to_step)

        if not flow_result.success:
            return {
                "eval": eval_num,
                "params": params,
                "success": False,
                "error": flow_result.error or "LibreLane flow failed",
                "fom": 0.0,
                "valid": False,
                "violations": [],
                "status": "crash",
            }

        # Extract metrics from the run dir
        run_dir = Path(flow_result.run_dir) if flow_result.run_dir else None
        if run_dir is None or not run_dir.is_dir():
            return {
                "eval": eval_num,
                "params": params,
                "success": False,
                "error": "No run directory found after flow",
                "fom": 0.0,
                "valid": False,
                "violations": [],
                "status": "crash",
            }

        # Gate-level simulation gates (signoff-blocking). Post-synth
        # runs first — a functionally broken netlist never reaches PnR
        # scoring. Post-PnR with SDF annotation runs next; SDF warnings
        # are non-blocking (counted in metrics), functional FAIL / no
        # PASS marker is. Skipped silently when the design has no
        # iverilog testbench or the PDK has no stdcell model glob.
        gl_synth = self._run_gl_sim(run_dir, eval_num, pdk_cfg, mode="post_synth")
        if gl_synth is not None and not gl_synth["success"]:
            return {
                "eval": eval_num,
                "params": params,
                "success": False,
                "error": gl_synth["error"],
                "fom": 0.0,
                "valid": False,
                "violations": [],
                "status": "gl_sim_post_synth_fail",
                "run_dir": str(run_dir),
                "gl_sim_log_tail": gl_synth["log_tail"],
            }

        gl_pnr = self._run_gl_sim(run_dir, eval_num, pdk_cfg, mode="post_pnr")
        if gl_pnr is not None and not gl_pnr["success"]:
            return {
                "eval": eval_num,
                "params": params,
                "success": False,
                "error": gl_pnr["error"],
                "fom": 0.0,
                "valid": False,
                "violations": [],
                "status": "gl_sim_post_pnr_fail",
                "run_dir": str(run_dir),
                "gl_sim_log_tail": gl_pnr["log_tail"],
            }

        metrics = FlowMetrics.from_librelane_run_dir(run_dir)
        stage_results = StageResults(
            eval_idx=eval_num,
            params=params,
            work_dir=work_dir,
            flow_metrics=metrics,
            run_dir=run_dir,
            gl_sim_post_synth=gl_synth,
            gl_sim_post_pnr=gl_pnr,
        )
        measurements = self.design.extract_measurements(stage_results)
        fom = self.design.compute_fom(measurements)
        valid, violations = self.design.check_validity(measurements)

        result: dict = {
            "eval": eval_num,
            "params": params,
            "success": True,
            "fom": fom,
            "valid": valid,
            "violations": violations,
            "run_dir": str(run_dir),
            "run_time_s": flow_result.run_time_s,
            **measurements,
        }
        if gl_synth is not None:
            result["gl_sim_post_synth_ok"] = gl_synth["success"]
            result["gl_sim_post_synth_time_s"] = gl_synth["run_time_s"]
        if gl_pnr is not None:
            result["gl_sim_post_pnr_ok"] = gl_pnr["success"]
            result["gl_sim_post_pnr_time_s"] = gl_pnr["run_time_s"]
            result["gl_sim_sdf_warnings"] = gl_pnr.get("sdf_warnings", 0)
        return result

    def _run_gl_sim(
        self,
        run_dir: Path,
        eval_num: int,
        pdk_cfg,
        *,
        mode: str,
    ) -> dict | None:
        """Run post-synth or post-PnR GL sim against a LibreLane run dir.

        ``mode`` selects :meth:`GlSimRunner.run_post_synth` (``"post_synth"``)
        or :meth:`GlSimRunner.run_post_pnr` (``"post_pnr"``). Returns a
        dict with ``success``/``error``/``log_tail``/``run_time_s`` (and
        ``sdf_warnings`` for ``post_pnr``), or ``None`` when GL sim is
        not applicable (no testbench, no stdcell glob, no PDK root).
        """
        tb = self.design.testbench()
        if tb is None or tb.driver != "iverilog":
            return None
        if not pdk_cfg.stdcell_verilog_models_glob and not self.design.gl_sim_cells_glob():
            logger.info(
                "[eval %s] GL sim (%s) skipped: no stdcell_verilog_models_glob "
                "for PDK %s",
                eval_num, mode, pdk_cfg.name,
            )
            return None

        from eda_agents.core.pdk import resolve_pdk_root
        from eda_agents.core.stages.gl_sim_runner import GlSimRunner
        from eda_agents.core.tool_environment import LocalToolEnvironment

        try:
            pdk_root = resolve_pdk_root(
                pdk_cfg,
                explicit_root=(
                    str(self.design.pdk_root()) if self.design.pdk_root() else None
                ),
            )
        except ValueError as exc:
            logger.warning(
                "[eval %s] GL sim (%s) skipped: %s", eval_num, mode, exc
            )
            return None

        runner = GlSimRunner(
            design=self.design,
            env=LocalToolEnvironment(),
            run_dir=run_dir,
            pdk_config=pdk_cfg,
            pdk_root=pdk_root,
        )
        if mode == "post_synth":
            stage_result = runner.run_post_synth()
        elif mode == "post_pnr":
            stage_result = runner.run_post_pnr()
        else:
            raise ValueError(f"Unknown GL sim mode {mode!r}")

        out: dict = {
            "success": stage_result.success,
            "error": stage_result.error or "",
            "log_tail": stage_result.log_tail,
            "run_time_s": stage_result.run_time_s,
        }
        if mode == "post_pnr":
            out["sdf_warnings"] = int(
                stage_result.metrics_delta.get("gl_sim_sdf_warnings", 0)
            )
        return out

    def _evaluate_mock(self, params: dict, eval_num: int) -> dict:
        """Load metrics from a JSON fixture instead of running LibreLane."""
        raw = json.loads(self.use_mock_metrics.read_text())

        # Support both flat dict and list-of-dicts (one per eval)
        if isinstance(raw, list):
            idx = (eval_num - 1) % len(raw)
            data = raw[idx]
        else:
            data = raw

        # Mock fixtures use ``FlowMetrics`` field names directly
        # (e.g. ``synth_cell_count``); wrap them in a synthetic
        # ``StageResults`` so the dict-based design API still applies.
        # Designs that read fields beyond ``flow_metrics`` (lint, sim,
        # cocotb sidecars) will see ``None`` here, which is the
        # correct semantic for a mock LibreLane-only path.
        metrics = FlowMetrics(**{
            k: v for k, v in data.items()
            if k in FlowMetrics.__dataclass_fields__
        })
        stage_results = StageResults(
            eval_idx=eval_num,
            params=params,
            work_dir=self.use_mock_metrics.parent,
            flow_metrics=metrics,
        )
        measurements = self.design.extract_measurements(stage_results)
        fom = self.design.compute_fom(measurements)
        valid, violations = self.design.check_validity(measurements)

        return {
            "eval": eval_num,
            "params": params,
            "success": True,
            "fom": fom,
            "valid": valid,
            "violations": violations,
            **measurements,
        }

    # ------------------------------------------------------------------
    # Dedup
    # ------------------------------------------------------------------

    def _is_duplicate(
        self, params: dict, history: list[dict], rtl_hash: str = ""
    ) -> bool:
        """Check if params (+ RTL hash for rtl/hybrid) match a prior eval."""
        if not self.dedup:
            return False
        for h in history:
            if self.strategy == "flow":
                if h["params"] == params:
                    return True
            else:
                if h["params"] == params and h.get("rtl_hash", "") == rtl_hash:
                    return True
        return False

    # ------------------------------------------------------------------
    # Format helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_digital_best(entry: dict) -> str:
        """Format the Current Best section body for digital metrics."""
        params_str = json.dumps(entry["params"], indent=2)
        text = (
            f"Eval #{entry['eval']}: FoM={entry['fom']:.2e}\n"
            f"Parameters:\n```json\n{params_str}\n```\n"
            f"Measurements: WNS={entry.get('wns_worst_ns', '?')}ns, "
            f"cells={entry.get('cell_count', '?')}, "
            f"area={entry.get('die_area_um2', '?')}um2, "
            f"power={entry.get('power_mw', '?')}mW, "
            f"wire={entry.get('wire_length_um', '?')}um"
        )
        if entry.get("rtl_rationale"):
            text += f"\nRTL change: {entry['rtl_rationale']}"
        if entry.get("rtl_files_changed"):
            text += f"\nFiles modified: {', '.join(entry['rtl_files_changed'])}"
        return text

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, work_dir: Path) -> AutoresearchResult:
        """Run the autonomous exploration loop.

        Mirrors ``AutoresearchRunner.run()`` with digital-specific
        evaluation and discrete design space handling.  Supports three
        strategies: ``flow`` (config-only), ``rtl`` (RTL edits), and
        ``hybrid`` (RTL + config).
        """
        work_dir.mkdir(parents=True, exist_ok=True)

        # Reset the per-run LLM token counter so repeated ``run()`` calls
        # on the same instance report their own totals.
        self._cumulative_tokens = 0

        program_store = self._make_program_store(work_dir)
        program_store.init()

        tsv_path = work_dir / "results.tsv"
        tsv_logger = self._make_tsv_logger(tsv_path)

        history, best, start_eval = tsv_logger.load_history()
        if not history:
            tsv_logger.write_header()
        kept_count = sum(1 for h in history if h.get("kept"))

        end_eval = start_eval + self.budget - 1

        # Initialize RTL snapshot manager for rtl/hybrid strategies
        snapshot_mgr = None
        if self.strategy in ("rtl", "hybrid"):
            from eda_agents.agents.rtl_snapshot_manager import RtlSnapshotManager

            rtl_sources = self.design.rtl_sources()
            if not rtl_sources:
                raise ValueError(
                    f"strategy='{self.strategy}' requires design.rtl_sources() "
                    f"to return a non-empty list of RTL file paths"
                )
            snapshot_mgr = RtlSnapshotManager(work_dir, self.design.project_dir())
            # Always snapshot config for CC CLI (agent may accidentally modify it)
            # For litellm backend, only snapshot config for hybrid strategy
            snapshot_config = (
                self.strategy == "hybrid"
                or self.backend == "cc_cli"
            )
            config_path = (
                self.design.librelane_config() if snapshot_config else None
            )
            snapshot_mgr.init_from_originals(rtl_sources, config_path=config_path)

        logger.info(
            "DigitalAutoresearch: %s, model=%s, budget=%d (evals %d-%d), "
            "stop_after=%s, strategy=%s",
            self.design.project_name(),
            self.model,
            self.budget,
            start_eval,
            end_eval,
            self.stop_after.name if self.stop_after else "FULL",
            self.strategy,
        )

        for eval_num in range(start_eval, end_eval + 1):
            t0 = time.monotonic()

            program_content = program_store.read()
            proposal = {}
            params: dict[str, float | int] = {}
            rtl_changes: dict[str, str] = {}
            rtl_rationale = ""

            # ----------------------------------------------------------
            # Anti-centroid seed: eval 1 = baseline from LibreLane config
            # ----------------------------------------------------------
            # When the very first evaluation of a fresh run targets the
            # ``flow`` strategy, source the parameters from
            # :meth:`DigitalDesign.baseline_params` (the project's
            # ``config.yaml``) instead of an LLM proposal. The LLM then
            # sees a real reference point in the history block from
            # eval 2 onward, instead of guessing in a vacuum where it
            # tends to anchor on the centroid of the design-space tuple.
            is_seed_eval = False
            if (
                eval_num == 1
                and not history
                and self.strategy == "flow"
            ):
                # Probe ``baseline_params`` directly (not the clamped
                # form) so an empty config produces an empty seed.
                # ``_clamp_params({})`` would otherwise back-fill from
                # ``default_config`` and silently re-introduce the
                # centroid bias.
                raw_baseline = self.design.baseline_params()
                if raw_baseline:
                    seed_params = self._clamp_params(raw_baseline)
                    logger.info(
                        "Seeded eval 1 with baseline params from "
                        "config: %s",
                        seed_params,
                    )
                    params = seed_params
                    rtl_rationale = "baseline from librelane config"
                    is_seed_eval = True
                else:
                    logger.info(
                        "Eval 1: no usable baseline from %s; falling "
                        "back to LLM proposal",
                        self.design.librelane_config(),
                    )

            # ----------------------------------------------------------
            # Pre-proposal: restore best RTL for CC CLI (agent writes in-place)
            # ----------------------------------------------------------
            if snapshot_mgr and self.backend == "cc_cli":
                # Always restore config for CC CLI to prevent accidental changes
                snapshot_mgr.restore_best(
                    self.design.rtl_sources(),
                    config_path=self.design.librelane_config(),
                )

            # ----------------------------------------------------------
            # Propose (skipped on the baseline-seeded eval 1)
            # ----------------------------------------------------------
            if not is_seed_eval:
                try:
                    if self.strategy == "flow":
                        params = await self._propose_params(
                            program_content, history, best, eval_num
                        )
                    elif self.strategy in ("rtl", "hybrid"):
                        if self.backend == "cc_cli":
                            proposal = await self._propose_cc_cli(
                                program_content, history, best, eval_num
                            )
                        elif self.backend == "litellm":
                            proposal = await self._propose_litellm(
                                program_content, history, best, eval_num
                            )
                        elif self.backend == "opencode":
                            proposal = await self._propose_opencode(
                                program_content, history, best, eval_num
                            )
                        elif self.strategy == "rtl":
                            proposal = await self._propose_rtl(
                                program_content, history, best, eval_num
                            )
                        else:
                            proposal = await self._propose_hybrid(
                                program_content, history, best, eval_num
                            )
                        if self.strategy == "hybrid":
                            params = self._clamp_params(
                                proposal.get("config", {})
                            )
                        rtl_changes = proposal.get("rtl_changes", {})
                        rtl_rationale = proposal.get("rationale", "")
                except Exception as e:
                    logger.warning(
                        "LLM proposal failed at eval %d: %s", eval_num, e
                    )
                    if self.strategy == "flow":
                        # Prefer baseline_params over default_config so
                        # the fallback never silently re-introduces the
                        # centroid bias the seed was designed to remove.
                        # Probe the raw return so an empty baseline
                        # falls cleanly through to default_config.
                        raw_baseline = self.design.baseline_params()
                        if raw_baseline:
                            params = self._clamp_params(raw_baseline)
                        else:
                            params = self._clamp_params(
                                self.design.default_config()
                            )
                    else:
                        # For RTL strategies, no fallback -- skip this eval
                        entry = {
                            "eval": eval_num, "params": {},
                            "success": False,
                            "error": f"Proposal failed: {e}",
                            "fom": 0.0, "valid": False, "violations": [],
                            "status": "proposal_fail",
                            "seed": False,
                        }
                        history.append(entry)
                        tsv_logger.append_row(entry)
                        continue

            # ----------------------------------------------------------
            # Dedup check
            # ----------------------------------------------------------
            rtl_hash = ""
            if self.strategy in ("rtl", "hybrid") and snapshot_mgr:
                rtl_hash = snapshot_mgr.content_hash(self.design.rtl_sources())

            if self._is_duplicate(params, history, rtl_hash=rtl_hash):
                logger.info("Eval %d: duplicate, skipping", eval_num)
                entry = {
                    "eval": eval_num, "params": params,
                    "success": False, "error": "duplicate",
                    "fom": 0.0, "valid": False, "violations": [],
                    "status": "dedup", "rtl_hash": rtl_hash,
                    "seed": is_seed_eval,
                }
                history.append(entry)
                tsv_logger.append_row(entry)
                continue

            # ----------------------------------------------------------
            # RTL apply + lint gate (rtl/hybrid only)
            # ----------------------------------------------------------
            if self.strategy in ("rtl", "hybrid") and snapshot_mgr:
                if rtl_changes:
                    # Validate proposal structure
                    valid_prop, prop_err = self._validate_rtl_proposal(proposal)
                    if not valid_prop:
                        entry = {
                            "eval": eval_num, "params": params,
                            "success": False, "error": f"Invalid proposal: {prop_err}",
                            "fom": 0.0, "valid": False, "violations": ["proposal_invalid"],
                            "status": "proposal_fail",
                            "rtl_rationale": rtl_rationale,
                        }
                        history.append(entry)
                        tsv_logger.append_row(entry)
                        continue

                    if self.backend == "cc_cli":
                        # CC CLI agent already wrote files; just lint-verify
                        from eda_agents.core.stages.rtl_lint_runner import RtlLintRunner
                        from eda_agents.core.tool_environment import LocalToolEnvironment
                        lint_result = RtlLintRunner(
                            design=self.design, env=LocalToolEnvironment()
                        ).run()
                        lint_ok = lint_result.success
                        lint_err = lint_result.error if not lint_ok else None
                    else:
                        # litellm backend: restore best, apply, lint
                        lint_ok, lint_err, _ = self._apply_rtl_and_lint(
                            proposal, snapshot_mgr, eval_num
                        )
                    if not lint_ok:
                        entry = {
                            "eval": eval_num, "params": params,
                            "success": False, "error": f"Lint failed: {lint_err}",
                            "fom": 0.0, "valid": False, "violations": ["lint_fail"],
                            "status": "lint_fail",
                            "rtl_rationale": rtl_rationale,
                        }
                        history.append(entry)
                        tsv_logger.append_row(entry)
                        program_store.update_learning(
                            f"Eval #{eval_num}: lint fail -- {rtl_rationale}"
                        )
                        snapshot_mgr.restore_best(self.design.rtl_sources())
                        continue

                    # Update RTL hash after applying changes
                    rtl_hash = snapshot_mgr.content_hash(self.design.rtl_sources())

            # ----------------------------------------------------------
            # RTL simulation gate — runs for every strategy when
            # ``run_rtl_sim`` is on and we are not in mock mode.
            # ``DigitalDesign.testbench`` is abstract, so the spec is
            # always present.
            # ----------------------------------------------------------
            if self.run_rtl_sim and not self.use_mock_metrics:
                from eda_agents.core.stages.rtl_sim_runner import RtlSimRunner
                from eda_agents.core.tool_environment import LocalToolEnvironment

                sim_result = RtlSimRunner(
                    design=self.design, env=LocalToolEnvironment()
                ).run()
                if not sim_result.success:
                    sim_err = sim_result.error or "simulation failed"
                    entry = {
                        "eval": eval_num, "params": params,
                        "success": False,
                        "error": f"RTL sim failed: {sim_err}",
                        "fom": 0.0, "valid": False,
                        "violations": ["sim_fail"],
                        "status": "sim_fail",
                        "rtl_rationale": rtl_rationale,
                    }
                    history.append(entry)
                    tsv_logger.append_row(entry)
                    program_store.update_learning(
                        f"Eval #{eval_num}: sim fail -- {rtl_rationale}"
                    )
                    if snapshot_mgr:
                        snapshot_mgr.restore_best(self.design.rtl_sources())
                    continue

            # ----------------------------------------------------------
            # Evaluate (LibreLane flow)
            # ----------------------------------------------------------
            try:
                entry = await self._evaluate(params, work_dir, eval_num)
            except Exception as e:
                logger.error(
                    "Eval %d CRASHED: %s\n%s",
                    eval_num,
                    e,
                    traceback.format_exc(),
                )
                entry = {
                    "eval": eval_num, "params": params,
                    "success": False, "error": str(e),
                    "fom": 0.0, "valid": False, "violations": [],
                    "status": "crash",
                    "seed": is_seed_eval,
                }
                history.append(entry)
                tsv_logger.append_row(entry)
                if snapshot_mgr:
                    snapshot_mgr.restore_best(self.design.rtl_sources())
                continue

            # Add RTL + seed metadata to entry
            entry["rtl_rationale"] = rtl_rationale
            entry["rtl_hash"] = rtl_hash
            entry["seed"] = is_seed_eval
            if rtl_changes:
                entry["rtl_files_changed"] = list(rtl_changes.keys())

            # ----------------------------------------------------------
            # Keep or discard
            # ----------------------------------------------------------
            if entry["success"] and entry["valid"] and (
                best is None or entry["fom"] > best["fom"]
            ):
                entry["kept"] = True
                entry["status"] = "kept"
                best = entry.copy()
                kept_count += 1

                # Update snapshots on keep
                if snapshot_mgr:
                    config_path = (
                        self.design.librelane_config()
                        if self.strategy == "hybrid" else None
                    )
                    snapshot_mgr.update_best(
                        self.design.rtl_sources(), config_path=config_path
                    )

                program_store.update_best(entry, self._format_digital_best)

                insight = (
                    f"Eval #{eval_num}: FoM improved to {entry['fom']:.2e} "
                    f"(WNS={entry.get('wns_worst_ns', '?')}ns, "
                    f"cells={entry.get('cell_count', '?')}) "
                    f"with {json.dumps(entry['params'])}"
                )
                if rtl_rationale:
                    insight += f" -- RTL: {rtl_rationale}"
                program_store.update_learning(insight)

                logger.info(
                    "Eval %d: KEPT (FoM=%.2e, WNS=%sns, cells=%s, area=%sum2)",
                    eval_num,
                    entry["fom"],
                    entry.get("wns_worst_ns", "?"),
                    entry.get("cell_count", "?"),
                    entry.get("die_area_um2", "?"),
                )
            else:
                entry["kept"] = False
                entry["status"] = "discarded"

                # Rollback RTL on discard
                if snapshot_mgr:
                    snapshot_mgr.restore_best(self.design.rtl_sources())

                if not entry["success"]:
                    reason = f"Eval #{eval_num}: crash -- {entry.get('error', 'unknown')}"
                    entry["status"] = "crash"
                elif not entry["valid"]:
                    viols = ", ".join(entry.get("violations", []))
                    reason = f"Eval #{eval_num}: invalid ({viols})"
                else:
                    reason = (
                        f"Eval #{eval_num}: valid but FoM={entry['fom']:.2e} "
                        f"< best {best['fom']:.2e}"
                    )
                if rtl_rationale:
                    reason += f" -- RTL: {rtl_rationale}"

                if eval_num % 3 == 0 or not entry["success"]:
                    program_store.update_learning(reason)

                logger.debug(
                    "Eval %d: %s (fom=%.2e, valid=%s)",
                    eval_num,
                    entry["status"],
                    entry["fom"],
                    entry["valid"],
                )

            history.append(entry)
            tsv_logger.append_row(entry)

            elapsed = time.monotonic() - t0
            logger.debug("Eval %d took %.1fs", eval_num, elapsed)

        # Top-N
        valid_entries = sorted(
            [h for h in history if h.get("valid") and h.get("success")],
            key=lambda x: x["fom"],
            reverse=True,
        )
        top_n = valid_entries[: self.top_n]

        if best is None:
            all_sorted = sorted(history, key=lambda x: x["fom"], reverse=True)
            fallback = all_sorted[0] if all_sorted else {"params": {}, "fom": 0.0}
            return AutoresearchResult(
                best_params=fallback.get("params", {}),
                best_fom=fallback.get("fom", 0.0),
                best_valid=False,
                total_evals=len(history),
                kept=kept_count,
                discarded=len(history) - kept_count,
                top_n=[],
                history=history,
                tsv_path=str(tsv_path),
                total_tokens=self._cumulative_tokens,
            )

        return AutoresearchResult(
            best_params=best["params"],
            best_fom=best["fom"],
            best_valid=True,
            total_evals=len(history),
            kept=kept_count,
            discarded=len(history) - kept_count,
            top_n=top_n,
            history=history,
            tsv_path=str(tsv_path),
            total_tokens=self._cumulative_tokens,
        )
