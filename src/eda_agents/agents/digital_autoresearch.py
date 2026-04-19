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
)
from eda_agents.agents.phase_results import AutoresearchResult
from eda_agents.core.digital_design import DigitalDesign
from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.flow_stage import FlowStage
from eda_agents.core.stages.physical_slice_runner import STAGE_TO_LIBRELANE
from eda_agents.skills.registry import render_relevant_skills

logger = logging.getLogger(__name__)


def detect_nix_eda_tool_dirs() -> list[str]:
    """Scan /nix/store for LibreLane-compatible EDA tool binaries.

    LibreLane v3 requires yosys >= 0.60 and recent OpenROAD. System
    packages on Ubuntu are often too old. When a Nix installation is
    present on this machine, prefer its bin directories.

    Returns the first matching bin directory per tool, in the order
    yosys -> openroad -> magic -> netgen -> klayout. The caller is
    responsible for deciding how to prepend these to PATH (or pass them
    verbatim into a subprocess env). Returns an empty list on systems
    without Nix or without these tools.
    """
    import glob

    nix_dirs: list[str] = []
    for pattern in [
        "/nix/store/*-yosys-with-plugins-0.6*/bin",
        "/nix/store/*-openroad-202[56]*/bin",
        "/nix/store/*-magic-*/bin",
        "/nix/store/*-netgen-*/bin",
        "/nix/store/*-klayout-*/bin",
    ]:
        candidates = sorted(glob.glob(pattern), reverse=True)
        if candidates:
            nix_dirs.append(candidates[0])
    return nix_dirs

# Digital measurement columns for TSV logging
_DIGITAL_MEASUREMENT_COLS = [
    "wns_worst_ns",
    "cell_count",
    "die_area_um2",
    "power_mw",
    "wire_length_um",
]


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
        Run RTL simulation after lint for ``rtl``/``hybrid`` strategies.
        Defaults to True for RTL strategies when testbench exists.
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
        if backend == "cc_cli" and strategy == "flow":
            logger.warning(
                "backend='cc_cli' accepted but flow-only proposals still use litellm."
            )
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

        # Default run_rtl_sim: True for RTL strategies (if testbench exists)
        if run_rtl_sim is None:
            self.run_rtl_sim = strategy in ("rtl", "hybrid")
        else:
            self.run_rtl_sim = run_rtl_sim

        # Cumulative token counter populated by ``_propose_params`` from
        # the LLM backend's ``response.usage``. Reset at the start of
        # every ``run()`` so repeated calls don't leak across runs; the
        # field is surfaced on :class:`AutoresearchResult.total_tokens`.
        self._cumulative_tokens = 0

    # ------------------------------------------------------------------
    # program.md
    # ------------------------------------------------------------------

    def _generate_program(self) -> str:
        space = self.design.design_space()
        space_lines = []
        for name, values in space.items():
            if isinstance(values, list):
                space_lines.append(f"- {name}: one of {values}")
            elif isinstance(values, tuple) and len(values) == 2:
                space_lines.append(f"- {name}: [{values[0]}, {values[1]}]")

        from eda_agents.core.pdk import resolve_pdk as _resolve_pdk
        _pdk = self.design.pdk_config() or _resolve_pdk()

        return generate_program_content(
            domain_name=self.design.project_name(),
            pdk_display_name=_pdk.display_name,
            fom_description=self.design.fom_description(),
            specs_description=self.design.specs_description(),
            design_vars_description=self.design.design_vars_description(),
            design_space_lines="\n".join(space_lines),
            reference_description=self.design.reference_description(),
        )

    def _make_program_store(self, work_dir: Path) -> ProgramStore:
        return ProgramStore(work_dir, self._generate_program)

    def _make_tsv_logger(self, tsv_path: Path) -> TsvLogger:
        return TsvLogger(
            tsv_path=tsv_path,
            param_cols=list(self.design.design_space().keys()),
            measurement_cols=_DIGITAL_MEASUREMENT_COLS,
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

        return (
            f"{prefix}"
            f"You are an autonomous digital design optimizer. Your program "
            f"is defined below. Follow it exactly.\n\n"
            f"{program_content}\n\n"
            f"RESPONSE FORMAT: You must respond with ONLY a JSON object "
            f"containing the next design parameters to try. No explanation, "
            f"no markdown fences, no commentary. Just the raw JSON.\n"
            f"Example: {{{example}}}"
        )

    def _build_proposal_prompt(
        self,
        history: list[dict],
        best: dict | None,
        eval_num: int,
    ) -> str:
        parts = [f"Evaluation {eval_num}/{self.budget}.\n"]

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
        """Ask LLM to propose next design parameters."""
        import litellm

        prompt = self._build_proposal_prompt(history, best, eval_num)

        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt(program_content)},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1024,
            "temperature": 0.7,
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

    def _clamp_params(self, params: dict) -> dict[str, float | int]:
        """Validate and snap proposed params to the design space.

        Discrete lists: snap to nearest valid value.
        Continuous ranges: clamp to [lo, hi].
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
        """
        import os as _os

        nix_dirs = detect_nix_eda_tool_dirs()
        if nix_dirs:
            current_path = env_extra.get("PATH", _os.environ.get("PATH", ""))
            nix_prefix = ":".join(nix_dirs)
            env_extra["PATH"] = f"{nix_prefix}:{current_path}"
            logger.info("Prepended nix tools to PATH: %s", nix_prefix)

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

        warnings = lint_result.metrics.get("lint_warnings", 0)
        return True, None, warnings

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
            "temperature": 0.7,
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
            "temperature": 0.7,
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
        fom = self.design.compute_fom(metrics)
        valid, violations = self.design.check_validity(metrics)

        result: dict = {
            "eval": eval_num,
            "params": params,
            "success": True,
            "fom": fom,
            "valid": valid,
            "violations": violations,
            "wns_worst_ns": metrics.wns_worst_ns,
            "cell_count": metrics.synth_cell_count,
            "die_area_um2": metrics.die_area_um2,
            "power_mw": metrics.power_total_mw,
            "wire_length_um": metrics.wire_length_um,
            "run_dir": str(run_dir),
            "run_time_s": flow_result.run_time_s,
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

        metrics = FlowMetrics(**{
            k: v for k, v in data.items()
            if k in FlowMetrics.__dataclass_fields__
        })
        fom = self.design.compute_fom(metrics)
        valid, violations = self.design.check_validity(metrics)

        return {
            "eval": eval_num,
            "params": params,
            "success": True,
            "fom": fom,
            "valid": valid,
            "violations": violations,
            "wns_worst_ns": metrics.wns_worst_ns,
            "cell_count": metrics.synth_cell_count,
            "die_area_um2": metrics.die_area_um2,
            "power_mw": metrics.power_total_mw,
            "wire_length_um": metrics.wire_length_um,
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
            # Pre-proposal: restore best RTL for CC CLI (agent writes in-place)
            # ----------------------------------------------------------
            if snapshot_mgr and self.backend == "cc_cli":
                # Always restore config for CC CLI to prevent accidental changes
                snapshot_mgr.restore_best(
                    self.design.rtl_sources(),
                    config_path=self.design.librelane_config(),
                )

            # ----------------------------------------------------------
            # Propose
            # ----------------------------------------------------------
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
                        params = self._clamp_params(proposal.get("config", {}))
                    rtl_changes = proposal.get("rtl_changes", {})
                    rtl_rationale = proposal.get("rationale", "")
            except Exception as e:
                logger.warning("LLM proposal failed at eval %d: %s", eval_num, e)
                if self.strategy == "flow":
                    params = self._clamp_params(self.design.default_config())
                else:
                    # For RTL strategies, no fallback -- skip this eval
                    entry = {
                        "eval": eval_num, "params": {},
                        "success": False, "error": f"Proposal failed: {e}",
                        "fom": 0.0, "valid": False, "violations": [],
                        "status": "proposal_fail",
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
            # RTL simulation gate (rtl/hybrid, if testbench exists)
            # ----------------------------------------------------------
            if (
                self.strategy in ("rtl", "hybrid")
                and self.run_rtl_sim
                and self.design.testbench() is not None
                and not self.use_mock_metrics
            ):
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
                }
                history.append(entry)
                tsv_logger.append_row(entry)
                if snapshot_mgr:
                    snapshot_mgr.restore_best(self.design.rtl_sources())
                continue

            # Add RTL metadata to entry
            entry["rtl_rationale"] = rtl_rationale
            entry["rtl_hash"] = rtl_hash
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
