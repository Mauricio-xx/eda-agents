"""Analog custom-composition loop — Claude-Code-driven synthesis of novel blocks.

Entry point for the S12-B Gap 5 arc: when
``recommend_topology`` returns ``confidence: low`` or
``topology: custom``, this loop tries to synthesise a working analog
block from SG13G2 / GF180-ready gLayout primitives by alternating
LLM proposals with ngspice verification and (optionally) KLayout
DRC / LVS.

Honest-fail is a **first-class outcome**: if the loop exhausts its
budget without converging, it returns an
:class:`AnalogCompositionResult` whose ``converged=False`` and
``honest_fail_reason`` captures the LLM's own diagnosis plus the
trajectory of iteration records. No fabricated "close enough"
verdicts.

The loop persists:

- ``<work_dir>/program.md`` — narrative log, one entry per stage.
- ``<work_dir>/iterations.jsonl`` — one JSON object per iteration for
  downstream parsing.
- ``<work_dir>/iter_<N>/`` — per-iteration artefacts (SPICE deck, sim
  output, GDS, DRC / LVS reports).

Design notes
------------

- **Pre-layout SPICE is the primary gate.** Layout generation +
  DRC + LVS are optional (``attempt_layout=True``) and run only after
  SPICE convergence. The MVP does not place sub-blocks into a single
  top-level GDS; it generates each sub-block's GDS via
  ``GLayoutRunner.generate_component`` and reports per-sub-block
  verdicts. The cross-sub-block placer is future work (see the S12-B
  plan file).
- **Budget discipline.** ``max_iterations`` and ``max_budget_usd``
  cap both wall-clock and LLM spend. The critique stage sees the
  remaining budget so it can choose ``honest_fail`` early rather than
  waste a turn.
- **No dependency on the SG13G2 opamp**. The primitives inventory
  surfaced via ``analog.custom_composition`` explicitly excludes
  opamp_twostage (Gap 4 blocker). The first bench target (4-bit
  current-steering DAC) only needs current_mirror + nmos.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from eda_agents.agents.openrouter_client import call_openrouter
from eda_agents.core.glayout_runner import GLayoutRunner
from eda_agents.core.spice_runner import SpiceResult, SpiceRunner
from eda_agents.skills.registry import get_skill

# OpenRouter pricing (USD / 1K tokens, averaged in+out). These are
# conservative estimates; callers can override via
# ``cost_per_1k_tokens`` if they want a tighter budget ceiling. The
# defaults cover the Gemini Flash + Claude Sonnet tiers.
_DEFAULT_COST_PER_1K_TOKENS = {
    "google/gemini-2.5-flash": 0.0003,
    "google/gemini-2.5-pro": 0.0025,
    "anthropic/claude-3.5-sonnet": 0.003,
    "anthropic/claude-sonnet-4.5": 0.003,
    "anthropic/claude-opus-4.5": 0.015,
}


@dataclass
class IterationRecord:
    """Per-iteration snapshot persisted to iterations.jsonl."""

    index: int
    composition: dict | None = None
    sizing: dict | None = None
    spice: dict | None = None
    layout: dict | None = None
    drc: dict | None = None
    lvs: dict | None = None
    critique: dict | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    elapsed_s: float = 0.0
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalogCompositionResult:
    """Outer result returned by :meth:`AnalogCompositionLoop.loop`."""

    success: bool
    converged: bool
    nl_description: str
    constraints: dict[str, Any]
    pdk: str
    iterations: list[IterationRecord] = field(default_factory=list)
    final_composition: dict | None = None
    final_sizing: dict | None = None
    final_spice: dict | None = None
    gds_paths: dict[str, str] = field(default_factory=dict)
    netlist_paths: dict[str, str] = field(default_factory=dict)
    drc_summary: dict | None = None
    lvs_summary: dict | None = None
    honest_fail_reason: str | None = None
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_time_s: float = 0.0
    work_dir: str = ""
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["iterations"] = [it.to_json() for it in self.iterations]
        return out


class AnalogCompositionLoop:
    """Orchestrator for the propose / size / simulate / critique loop."""

    SKILL_NAME = "analog.custom_composition"

    def __init__(
        self,
        *,
        pdk: str = "ihp_sg13g2",
        work_dir: str | Path,
        model: str = "google/gemini-2.5-flash",
        glayout_venv: str | None = None,
        spice_runner: SpiceRunner | None = None,
        max_iterations: int = 8,
        max_budget_usd: float = 10.0,
        cost_per_1k_tokens: float | None = None,
        attempt_layout: bool = True,
        attempt_drc_lvs: bool = False,
        temperature_propose: float = 0.4,
        temperature_size: float = 0.2,
        temperature_critique: float = 0.2,
        max_tokens_per_call: int = 4096,
    ):
        self.pdk = pdk
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.max_iterations = max_iterations
        self.max_budget_usd = max_budget_usd
        self.cost_per_1k_tokens = (
            cost_per_1k_tokens
            if cost_per_1k_tokens is not None
            else _DEFAULT_COST_PER_1K_TOKENS.get(model, 0.003)
        )
        self.attempt_layout = attempt_layout
        self.attempt_drc_lvs = attempt_drc_lvs
        self.temperature_propose = temperature_propose
        self.temperature_size = temperature_size
        self.temperature_critique = temperature_critique
        self.max_tokens_per_call = max_tokens_per_call

        self.glayout_runner = GLayoutRunner(
            glayout_venv=glayout_venv
            or "/home/montanares/personal_exp/eda-agents/.venv-glayout",
            pdk=pdk,
            timeout_s=600,
        )
        self.spice_runner = spice_runner or SpiceRunner(pdk=pdk, timeout_s=120)

        # One system prompt for the whole loop; the stage vocabulary
        # lives in the skill markdown bundle.
        self._system_prompt = get_skill(self.SKILL_NAME).render()

        # Persistence handles
        self._program_md = self.work_dir / "program.md"
        self._iterations_jsonl = self.work_dir / "iterations.jsonl"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def loop(
        self,
        nl_description: str,
        constraints: dict[str, Any] | None = None,
        *,
        max_iterations: int | None = None,
    ) -> AnalogCompositionResult:
        """Run the composition loop to convergence or honest-fail."""

        constraints = constraints or {}
        iterations_cap = max_iterations or self.max_iterations
        t_start = time.time()

        result = AnalogCompositionResult(
            success=False,
            converged=False,
            nl_description=nl_description,
            constraints=constraints,
            pdk=self.pdk,
            work_dir=str(self.work_dir),
        )

        self._log_program(
            f"# Analog composition loop\n\n"
            f"- NL: {nl_description}\n"
            f"- constraints: {json.dumps(constraints)}\n"
            f"- pdk: {self.pdk}\n"
            f"- model: {self.model}\n"
            f"- iterations_cap: {iterations_cap}\n"
            f"- budget_usd: {self.max_budget_usd:.2f}\n"
            f"- started: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t_start))}\n\n"
        )

        prior_iterations: list[IterationRecord] = []
        cumulative_tokens = 0
        cumulative_cost = 0.0

        composition: dict | None = None
        sizing: dict | None = None

        for i in range(iterations_cap):
            iter_dir = self.work_dir / f"iter_{i}"
            iter_dir.mkdir(exist_ok=True)
            it = IterationRecord(index=i)
            t_iter = time.time()

            # Budget guard before starting an iteration we can't finish.
            if self.max_budget_usd and cumulative_cost >= 0.9 * self.max_budget_usd:
                it.error = (
                    f"budget_near_limit: spent ${cumulative_cost:.2f} of "
                    f"${self.max_budget_usd:.2f}; aborting before iteration {i}"
                )
                self._persist_iteration(it)
                prior_iterations.append(it)
                result.honest_fail_reason = (
                    "budget exhausted before iteration could complete"
                )
                break

            # --- Propose composition ---
            try:
                if composition is None or (
                    prior_iterations
                    and (prior_iterations[-1].critique or {}).get("verdict")
                    == "patch"
                    and (prior_iterations[-1].critique or {}).get("patch", {}).get(
                        "composition"
                    )
                ):
                    composition, p_tokens = self._call_stage(
                        "propose_composition",
                        nl_description=nl_description,
                        constraints=constraints,
                        prior=prior_iterations,
                        temperature=self.temperature_propose,
                    )
                    cumulative_tokens += p_tokens
                    cost_incr = self._tokens_to_cost(p_tokens)
                    cumulative_cost += cost_incr
                    it.tokens += p_tokens
                    it.cost_usd += cost_incr
                it.composition = composition
            except Exception as e:
                it.error = f"propose_composition: {type(e).__name__}: {e}"
                self._persist_iteration(it)
                prior_iterations.append(it)
                break

            # --- Size sub-blocks (or apply last patch) ---
            try:
                last_patch = (
                    (prior_iterations[-1].critique or {}).get("patch", {}).get("sizing")
                    if prior_iterations
                    else None
                )
                if last_patch and sizing:
                    sizing = _merge_sizing(sizing, last_patch)
                else:
                    sizing, s_tokens = self._call_stage(
                        "size_sub_blocks",
                        nl_description=nl_description,
                        constraints=constraints,
                        composition=composition,
                        prior=prior_iterations,
                        temperature=self.temperature_size,
                    )
                    cumulative_tokens += s_tokens
                    cost_incr = self._tokens_to_cost(s_tokens)
                    cumulative_cost += cost_incr
                    it.tokens += s_tokens
                    it.cost_usd += cost_incr
                it.sizing = sizing
            except Exception as e:
                it.error = f"size_sub_blocks: {type(e).__name__}: {e}"
                self._persist_iteration(it)
                prior_iterations.append(it)
                break

            # --- Build SPICE deck + run ngspice ---
            try:
                deck_path = self._write_spice_deck(
                    composition, sizing, iter_dir, constraints
                )
                spice_res = self._run_spice(deck_path, iter_dir)
                it.spice = _spice_to_dict(spice_res, composition)
            except Exception as e:
                it.spice = {
                    "ran": False,
                    "error": f"{type(e).__name__}: {e}",
                }

            # --- Optional: layout + DRC/LVS on SPICE convergence ---
            pass_per_spec = (it.spice or {}).get("pass_per_spec", {})
            spice_all_pass = bool(pass_per_spec) and all(pass_per_spec.values())
            if self.attempt_layout and spice_all_pass:
                try:
                    layout_out = self._generate_layouts(composition, sizing, iter_dir)
                    it.layout = layout_out
                except Exception as e:
                    it.layout = {
                        "attempted": True,
                        "error": f"{type(e).__name__}: {e}",
                    }

            # Drc / lvs is explicitly gated behind attempt_drc_lvs because
            # the MVP top-level placer isn't there yet; we'd be verifying
            # each sub-block in isolation, which is redundant with their
            # own PDK tests.
            if self.attempt_drc_lvs and it.layout and it.layout.get("attempted"):
                it.drc = {"skipped": "MVP: sub-block-level DRC not enabled"}
                it.lvs = {"skipped": "MVP: sub-block-level LVS not enabled"}

            # --- Critique ---
            try:
                critique, c_tokens = self._call_stage(
                    "critique",
                    nl_description=nl_description,
                    constraints=constraints,
                    composition=composition,
                    sizing=sizing,
                    spice=it.spice,
                    layout=it.layout,
                    drc=it.drc,
                    lvs=it.lvs,
                    prior=prior_iterations,
                    budget_remaining_usd=max(
                        0.0, self.max_budget_usd - cumulative_cost
                    ),
                    iterations_remaining=iterations_cap - i - 1,
                    temperature=self.temperature_critique,
                )
                cumulative_tokens += c_tokens
                cost_incr = self._tokens_to_cost(c_tokens)
                cumulative_cost += cost_incr
                it.tokens += c_tokens
                it.cost_usd += cost_incr
                it.critique = critique
            except Exception as e:
                it.critique = {
                    "verdict": "honest_fail",
                    "rationale": f"critique stage failed: {type(e).__name__}: {e}",
                    "honest_fail_reason": "critique_error",
                }

            it.elapsed_s = round(time.time() - t_iter, 2)
            self._persist_iteration(it)
            prior_iterations.append(it)

            # --- Termination check ---
            verdict = (it.critique or {}).get("verdict")
            if verdict == "converged":
                result.converged = True
                result.success = True
                break
            if verdict == "honest_fail":
                result.honest_fail_reason = (
                    (it.critique or {}).get("honest_fail_reason")
                    or (it.critique or {}).get("rationale")
                    or "honest_fail"
                )
                break

        # --- Collate result ---
        result.iterations = prior_iterations
        result.total_tokens = cumulative_tokens
        result.total_cost_usd = round(cumulative_cost, 4)
        result.total_time_s = round(time.time() - t_start, 2)
        result.final_composition = composition
        result.final_sizing = sizing
        if prior_iterations:
            last = prior_iterations[-1]
            result.final_spice = last.spice
            if last.layout and last.layout.get("sub_block_gds"):
                result.gds_paths = dict(last.layout["sub_block_gds"])
                result.netlist_paths = dict(
                    last.layout.get("sub_block_netlists", {})
                )
            result.drc_summary = last.drc
            result.lvs_summary = last.lvs

        if not result.converged and not result.honest_fail_reason:
            result.honest_fail_reason = (
                f"loop exhausted {len(prior_iterations)}/{iterations_cap} "
                f"iterations without a 'converged' verdict"
            )

        (self.work_dir / "result.json").write_text(
            json.dumps(result.to_json(), indent=2, default=_json_default)
        )
        return result

    # ------------------------------------------------------------------
    # Stage helpers
    # ------------------------------------------------------------------

    def _call_stage(
        self,
        stage: str,
        *,
        nl_description: str,
        constraints: dict[str, Any],
        composition: dict | None = None,
        sizing: dict | None = None,
        spice: dict | None = None,
        layout: dict | None = None,
        drc: dict | None = None,
        lvs: dict | None = None,
        prior: list[IterationRecord] | None = None,
        budget_remaining_usd: float | None = None,
        iterations_remaining: int | None = None,
        temperature: float = 0.2,
    ) -> tuple[dict, int]:
        """Invoke OpenRouter for the named stage, return (payload, tokens)."""

        user_payload: dict[str, Any] = {
            "stage": stage,
            "nl_description": nl_description,
            "constraints": constraints,
            "pdk": self.pdk,
        }
        if composition is not None:
            user_payload["composition"] = composition
        if sizing is not None:
            user_payload["sizing"] = sizing
        if spice is not None:
            user_payload["spice"] = spice
        if layout is not None:
            user_payload["layout"] = layout
        if drc is not None:
            user_payload["drc"] = drc
        if lvs is not None:
            user_payload["lvs"] = lvs
        if prior:
            user_payload["prior_iteration_summaries"] = [
                {
                    "index": it.index,
                    "verdict": (it.critique or {}).get("verdict"),
                    "spice_pass_per_spec": (it.spice or {}).get(
                        "pass_per_spec"
                    ),
                    "spice_error": (it.spice or {}).get("error"),
                    "rationale": (it.critique or {}).get("rationale"),
                }
                for it in prior[-3:]  # last 3 for context
            ]
        if budget_remaining_usd is not None:
            user_payload["budget_remaining_usd"] = round(budget_remaining_usd, 4)
        if iterations_remaining is not None:
            user_payload["iterations_remaining"] = iterations_remaining

        user_payload["output_schema_reminder"] = (
            "Return ONE JSON object. For propose_composition: "
            "{composition, connectivity, testbench, target_specs}. "
            "For size_sub_blocks: {<sub_block_name>: {<param>: <value>}, ...}. "
            "For critique: {verdict, rationale, patch, honest_fail_reason}."
        )

        raw, tokens = call_openrouter(
            model=self.model,
            system_prompt=self._system_prompt,
            user_prompt=json.dumps(user_payload, indent=2),
            max_tokens=self.max_tokens_per_call,
            temperature=temperature,
        )
        payload = _parse_json_payload(raw)
        self._log_program(
            f"\n## iter stage={stage}\n"
            f"- tokens={tokens}\n"
            f"- payload_keys={list(payload.keys()) if isinstance(payload, dict) else 'non-dict'}\n"
        )
        return payload, tokens

    # ------------------------------------------------------------------
    # Deck + simulation
    # ------------------------------------------------------------------

    def _write_spice_deck(
        self,
        composition: dict,
        sizing: dict,
        iter_dir: Path,
        constraints: dict,
    ) -> Path:
        """Render a flat SPICE deck for the composition + testbench."""

        from eda_agents.core.pdk import get_pdk, netlist_lib_lines

        pdk_cfg = get_pdk(self.pdk)
        lib_lines = netlist_lib_lines(pdk_cfg)

        blocks = composition.get("composition", [])
        conn = composition.get("connectivity", [])
        tb = composition.get("testbench") or {}
        targets = composition.get("target_specs") or {}

        # Flatten connectivity into a port -> global-net map. Ports not
        # listed default to "<block>_<port>".
        port_to_net: dict[str, str] = {}
        for c in conn:
            a = c.get("from", "")
            b = c.get("to", "")
            # Whichever side names a global net wins; otherwise unify.
            port_to_net[a] = port_to_net.get(a, _canonical_net(a, b))
            port_to_net[b] = port_to_net[a]

        deck: list[str] = []
        deck.append(f"* Composition: {composition.get('name', 'custom')}")
        deck.append("")
        deck.extend(lib_lines)
        deck.append("")

        # Emit each sub-block as a device line. Current MVP supports
        # nmos / pmos / current_mirror / diff_pair / fvf / mimcap.
        for blk in blocks:
            name = blk.get("name")
            if not name:
                continue
            typ = (blk.get("type") or "").lower()
            blk_sizing = sizing.get(name, {}) if isinstance(sizing, dict) else {}
            deck.extend(
                _render_block_spice(
                    name=name,
                    typ=typ,
                    params=_coalesce_block_params(blk.get("params"), blk_sizing),
                    port_to_net=port_to_net,
                    pdk=self.pdk,
                )
            )
            deck.append("")

        # Testbench sources + measurements
        deck.append("* Testbench")
        for sig_name, src_spec in (tb.get("inputs") or {}).items():
            deck.append(f"* source for {sig_name}")
            deck.append(_render_source_line(sig_name, src_spec, port_to_net))
        deck.append("")

        control_lines = [".control"]
        analysis = (tb.get("analysis") or "op").lower()
        if analysis == "tran":
            step = tb.get("step", "1e-9")
            stop = tb.get("stop", "1e-6")
            control_lines.append(f"tran {step} {stop}")
        elif analysis == "ac":
            sweep = tb.get("sweep", "dec 20 1 1e9")
            control_lines.append(f"ac {sweep}")
        else:
            control_lines.append("op")

        for meas_line in tb.get("measurements") or []:
            control_lines.append(meas_line)

        control_lines.append("quit")
        control_lines.append(".endc")

        deck.extend(control_lines)
        deck.append(".end")

        out = iter_dir / "composition.cir"
        out.write_text("\n".join(deck) + "\n")

        # Stash the targets for the SPICE post-processing check
        (iter_dir / "target_specs.json").write_text(json.dumps(targets))

        return out

    def _run_spice(self, deck: Path, iter_dir: Path) -> SpiceResult:
        """Run ngspice on the deck; capture measurements + per-spec pass/fail."""
        targets_file = iter_dir / "target_specs.json"
        targets = (
            json.loads(targets_file.read_text()) if targets_file.is_file() else {}
        )
        res = self.spice_runner.run(deck, work_dir=iter_dir)
        # SpiceResult carries measurements; compare against targets.
        meas = getattr(res, "measurements", {}) or {}
        pass_per_spec: dict[str, bool] = {}
        for k, v in targets.items():
            if k not in meas:
                continue
            try:
                lhs = float(meas[k])
                rhs = float(v)
            except (TypeError, ValueError):
                continue
            if k.endswith("_max"):
                pass_per_spec[k] = lhs <= rhs
            elif k.endswith("_min"):
                pass_per_spec[k] = lhs >= rhs
            else:
                pass_per_spec[k] = abs(lhs - rhs) <= 0.1 * abs(rhs)
        res_dict = {
            "ran": True,
            "success": bool(getattr(res, "success", False)),
            "measurements": dict(meas),
            "pass_per_spec": pass_per_spec,
            "error": getattr(res, "error", None),
        }
        # Smuggle the dict onto the dataclass so _spice_to_dict can
        # hand it back; simpler than replumbing SpiceResult.
        res._extra = res_dict  # type: ignore[attr-defined]
        return res

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _generate_layouts(
        self,
        composition: dict,
        sizing: dict,
        iter_dir: Path,
    ) -> dict[str, Any]:
        """Generate each sub-block's GDS individually via GLayoutRunner.

        Does NOT compose sub-blocks into a single top-level GDS — that
        placer is future work. Returns per-sub-block GDS / netlist paths.
        """
        blocks = composition.get("composition", [])
        sub_block_gds: dict[str, str] = {}
        sub_block_netlists: dict[str, str] = {}
        errors: dict[str, str] = {}
        out_dir = iter_dir / "layouts"
        out_dir.mkdir(exist_ok=True)

        for blk in blocks:
            name = blk.get("name")
            if not name:
                continue
            typ = (blk.get("type") or "").lower()
            if typ not in {
                "nmos", "pmos", "mimcap", "diff_pair", "current_mirror", "fvf"
            }:
                errors[name] = f"unsupported type for layout: {typ}"
                continue
            params = _coalesce_block_params(
                blk.get("params"), sizing.get(name, {}) if isinstance(sizing, dict) else {}
            )
            sub_out = out_dir / name
            sub_out.mkdir(exist_ok=True)
            res = self.glayout_runner.generate_component(
                component=typ,
                params=params,
                output_dir=sub_out,
            )
            if res.success and res.gds_path:
                sub_block_gds[name] = res.gds_path
                if res.netlist_path:
                    sub_block_netlists[name] = res.netlist_path
            else:
                errors[name] = res.error or "unknown gLayout error"

        return {
            "attempted": True,
            "sub_block_gds": sub_block_gds,
            "sub_block_netlists": sub_block_netlists,
            "errors": errors,
            "top_placer_status": "not_implemented_mvp",
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _log_program(self, text: str) -> None:
        with self._program_md.open("a") as f:
            f.write(text)

    def _persist_iteration(self, it: IterationRecord) -> None:
        with self._iterations_jsonl.open("a") as f:
            f.write(json.dumps(it.to_json(), default=_json_default) + "\n")

    def _tokens_to_cost(self, tokens: int) -> float:
        return round((tokens / 1000.0) * self.cost_per_1k_tokens, 6)


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------


def _parse_json_payload(raw: str) -> dict:
    """Tolerant JSON extraction — strips markdown fences."""
    first = raw.find("{")
    last = raw.rfind("}")
    if first < 0 or last < 0 or last <= first:
        raise RuntimeError(
            f"LLM did not return a JSON object (first 200 chars: {raw[:200]!r})"
        )
    try:
        return json.loads(raw[first : last + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM JSON parse failed: {exc}") from exc


def _canonical_net(a: str, b: str) -> str:
    """Pick the canonical net name when two ports connect.

    Prefers the standard global names in a stable priority order so
    canonicalisation is deterministic regardless of argument order:
    GND > VSS (aliases) > VDD > VCM > IBIAS > VIN > VOUT.
    """
    priority = ["GND", "VSS", "VDD", "VCM", "IBIAS", "VIN", "VOUT"]
    seen: set[str] = set()
    for side in (a, b):
        for tok in side.split("."):
            seen.add(tok.strip().upper())
    for name in priority:
        if name in seen:
            # VSS aliased to GND for ngspice convenience.
            return "GND" if name in {"GND", "VSS"} else name
    return f"n_{a.replace('.', '_')}_{b.replace('.', '_')}"


def _render_block_spice(
    *,
    name: str,
    typ: str,
    params: dict,
    port_to_net: dict[str, str],
    pdk: str,
) -> list[str]:
    """Emit SPICE lines for a single sub-block.

    Maps the primitive type to the appropriate ngspice instance line.
    Sizes default to sensible SG13G2 values when params don't specify.
    """
    w = float(params.get("width", 2.0))
    l_ = float(params.get("length", 1.0 if typ in {"nmos", "pmos"} else 0.13))
    fingers = int(params.get("fingers", 1))
    mult = int(params.get("multipliers", 1))

    # For composite types, pick the transistor model based on the
    # block's declared device type (nfet default).
    device_type = (params.get("type") or "").lower()
    if typ in {"current_mirror", "diff_pair", "fvf"}:
        model_type = "pmos" if device_type in {"pmos", "pfet"} else "nmos"
    else:
        model_type = typ
    model = _resolve_model(model_type, pdk)

    def net(port: str) -> str:
        key = f"{name}.{port}"
        return port_to_net.get(key, _canonical_net(key, key))

    lines: list[str] = []
    if typ == "nmos":
        d = net("D")
        g = net("G")
        s = net("S")
        b = net("B") if f"{name}.B" in port_to_net else "GND"
        lines.append(
            f"X{name} {d} {g} {s} {b} {model} w={w}u l={l_}u nf={fingers} m={mult}"
        )
    elif typ == "pmos":
        d = net("D")
        g = net("G")
        s = net("S")
        b = net("B") if f"{name}.B" in port_to_net else "VDD"
        lines.append(
            f"X{name} {d} {g} {s} {b} {model} w={w}u l={l_}u nf={fingers} m={mult}"
        )
    elif typ == "mimcap":
        a = net("A")
        b = net("B")
        lines.append(
            f"X{name} {a} {b} {model} l={l_}u w={w}u"
        )
    elif typ == "current_mirror":
        vref = net("VREF")
        vcopy = net("VCOPY")
        vss = net("VSS") if f"{name}.VSS" in port_to_net else "GND"
        vb = net("VB") if f"{name}.VB" in port_to_net else vss
        # Unwrap to two MOSFET instances (ref diode + copy)
        lines.append(
            f"X{name}_ref {vref} {vref} {vss} {vb} {model} "
            f"w={w}u l={l_}u nf={fingers}"
        )
        lines.append(
            f"X{name}_out {vcopy} {vref} {vss} {vb} {model} "
            f"w={w}u l={l_}u nf={fingers} m={mult}"
        )
    elif typ == "diff_pair":
        vp = net("VP")
        vn = net("VN")
        op = net("VDD1") if f"{name}.VDD1" in port_to_net else net("OUT+")
        on = net("VDD2") if f"{name}.VDD2" in port_to_net else net("OUT-")
        tail = net("VTAIL") if f"{name}.VTAIL" in port_to_net else net("TAIL")
        b = net("B") if f"{name}.B" in port_to_net else "GND"
        lines.append(
            f"X{name}_L {op} {vp} {tail} {b} {model} w={w}u l={l_}u nf={fingers}"
        )
        lines.append(
            f"X{name}_R {on} {vn} {tail} {b} {model} w={w}u l={l_}u nf={fingers}"
        )
    elif typ == "fvf":
        vin = net("VIN")
        vout = net("VOUT")
        vbias = net("VBIAS") if f"{name}.VBIAS" in port_to_net else "VBIAS"
        vss = net("VSS") if f"{name}.VSS" in port_to_net else "GND"
        # Simplified FVF: M1 (input) + M2 (feedback), VDD=VBIAS ignoring well ties
        lines.append(
            f"X{name}_M1 {vout} {vin} {vss} {vss} {model} w={w}u l={l_}u nf={fingers}"
        )
        lines.append(
            f"X{name}_M2 {vin} {vout} {vbias} {vbias} "
            f"{_resolve_model('pmos', pdk)} w={w}u l={l_}u nf={fingers}"
        )
    else:
        lines.append(f"* UNSUPPORTED BLOCK: name={name} type={typ}")

    return lines


def _render_source_line(
    sig_name: str,
    spec: Any,
    port_to_net: dict[str, str],
) -> str:
    """Render a SPICE source line from the LLM's testbench input spec.

    Tolerant: accepts either a raw SPICE string (used verbatim) or a
    dict with ``{"type": "V|I|PWL", "value": ..., "net": "NET_NAME"}``.
    """
    if isinstance(spec, str):
        return spec
    if not isinstance(spec, dict):
        return f"* unsupported source spec for {sig_name}: {spec!r}"
    src_type = (spec.get("type") or "V").upper()
    net = spec.get("net") or port_to_net.get(sig_name, sig_name)
    ref = spec.get("ref", "GND")
    val = spec.get("value", 0.0)
    if src_type == "V":
        return f"V{sig_name} {net} {ref} {val}"
    if src_type == "I":
        return f"I{sig_name} {net} {ref} {val}"
    if src_type == "PWL":
        pwl = spec.get("pwl", "0 0")
        return f"V{sig_name} {net} {ref} PWL({pwl})"
    return f"* unsupported source type {src_type} for {sig_name}"


def _resolve_model(typ: str, pdk: str) -> str:
    """Map primitive type + PDK to the SPICE model name."""
    if pdk in {"ihp_sg13g2", "sg13g2"}:
        return {
            "nmos": "sg13_lv_nmos",
            "pmos": "sg13_lv_pmos",
            "mimcap": "cap_cmim",
        }.get(typ, "unknown_model")
    if pdk in {"gf180mcu", "gf180mcuD"}:
        return {
            "nmos": "nfet_03v3",
            "pmos": "pfet_03v3",
            "mimcap": "cap_mim_2p0fF",
        }.get(typ, "unknown_model")
    return "unknown_model"


def _coalesce_block_params(block_params: Any, sizing: dict) -> dict:
    """Merge LLM-provided block params with per-iteration sizing patches."""
    base = dict(block_params or {})
    base.update(sizing or {})
    return base


def _merge_sizing(current: dict, patch: dict) -> dict:
    """Apply a critique's sizing patch on top of the current sizing dict."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in current.items()}
    for sub, patch_params in patch.items():
        if isinstance(patch_params, dict):
            if sub in out and isinstance(out[sub], dict):
                out[sub].update(patch_params)
            else:
                out[sub] = dict(patch_params)
        else:
            out[sub] = patch_params
    return out


def _spice_to_dict(res: SpiceResult | Any, composition: dict | None) -> dict:
    """Convert SpiceResult into a JSON-serializable dict."""
    extra = getattr(res, "_extra", None)
    if extra:
        return extra
    out = {
        "ran": True,
        "success": bool(getattr(res, "success", False)),
        "measurements": dict(getattr(res, "measurements", {}) or {}),
        "error": getattr(res, "error", None),
    }
    return out


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "to_json"):
        return obj.to_json()
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return str(obj)


__all__ = [
    "AnalogCompositionLoop",
    "AnalogCompositionResult",
    "IterationRecord",
]
