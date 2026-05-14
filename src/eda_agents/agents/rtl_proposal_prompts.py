"""Prompt templates for RTL-aware and hybrid autoresearch strategies.

These are separated from ``digital_autoresearch.py`` for testability
and to keep the runner focused on loop logic, not prompt engineering.
"""

from __future__ import annotations

from pathlib import Path


def rtl_system_prompt(
    program_content: str,
    rtl_sources: dict[str, str],
    design_spec: str,
) -> str:
    """System prompt for ``strategy='rtl'``.

    Includes full RTL text and instructs the LLM to propose micro-
    optimizations as full-file rewrites in a JSON response.
    """
    rtl_block = _format_rtl_block(rtl_sources)

    return (
        "You are an RTL optimization engineer. Your program is below.\n\n"
        f"{program_content}\n\n"
        "## Design Specification\n"
        f"{design_spec}\n\n"
        "## Current RTL\n"
        f"{rtl_block}\n\n"
        "## Rules\n"
        "1. You MUST preserve the module name and port interface exactly.\n"
        "2. You MUST NOT add non-synthesizable constructs ($display, "
        "initial blocks, delays).\n"
        "3. Target: reduce area and power while maintaining timing closure.\n"
        "4. Techniques: resource sharing, shift-add instead of multiply, "
        "bitwidth reduction, register balancing, FSM encoding.\n"
        "5. IMPORTANT: your RTL changes must preserve functional "
        "correctness. A testbench will be run after your changes are "
        "applied. If it fails, the proposal is rejected.\n\n"
        "## Response Format\n"
        "Respond with ONLY a JSON object. No markdown fences, no commentary.\n"
        "```\n"
        '{\n'
        '  "rtl_changes": {\n'
        '    "src/file.v": "module ...\\n...\\nendmodule\\n"\n'
        '  },\n'
        '  "rationale": "one-line explanation of the optimization"\n'
        '}\n'
        "```\n"
        "The rtl_changes values must contain the COMPLETE file content "
        "(full rewrite, not a diff). Include ALL files, even unchanged ones."
    )


def hybrid_system_prompt(
    program_content: str,
    rtl_sources: dict[str, str],
    design_space: dict[str, list | tuple],
    design_spec: str,
) -> str:
    """System prompt for ``strategy='hybrid'``.

    Includes both RTL text and config design space. The LLM proposes
    both RTL changes and config knob values in a single response.
    """
    rtl_block = _format_rtl_block(rtl_sources)
    space_lines = _format_design_space(design_space)

    return (
        "You are a digital design optimizer. You can modify BOTH the RTL "
        "source code AND the flow configuration knobs. Your program is "
        "below.\n\n"
        f"{program_content}\n\n"
        "## Design Specification\n"
        f"{design_spec}\n\n"
        "## Current RTL\n"
        f"{rtl_block}\n\n"
        "## Flow Config Knobs\n"
        f"{space_lines}\n\n"
        "## Rules\n"
        "1. You MUST preserve the module name and port interface exactly.\n"
        "2. You MUST NOT add non-synthesizable constructs.\n"
        "3. Config knobs must be within the ranges listed above.\n"
        "4. You may change RTL only, config only, or both.\n"
        "5. Target: maximize FoM (see program above).\n"
        "6. Your RTL changes must preserve functional correctness. A "
        "testbench will verify behavior after changes are applied.\n\n"
        "## Response Format\n"
        "Respond with ONLY a JSON object:\n"
        "```\n"
        '{\n'
        '  "config": {"CLOCK_PERIOD": 50, "PL_TARGET_DENSITY_PCT": 65},\n'
        '  "rtl_changes": {\n'
        '    "src/file.v": "module ...\\n...\\nendmodule\\n"\n'
        '  },\n'
        '  "rationale": "one-line explanation"\n'
        '}\n'
        "```\n"
        "Omit config or rtl_changes if you don't want to change them."
    )


def rtl_proposal_prompt(
    history: list[dict],
    best: dict | None,
    eval_num: int,
    budget: int,
) -> str:
    """User-turn prompt for RTL/hybrid proposals.

    Shows metrics history, best entry info, and remaining budget.
    """
    parts = [f"Evaluation {eval_num}/{budget}.\n"]

    if best:
        parts.append(
            f"Current best (eval #{best['eval']}): "
            f"FoM={best['fom']:.2e}, valid={best['valid']}\n"
        )
        if best.get("rtl_rationale"):
            parts.append(f"Best RTL change: {best['rtl_rationale']}\n")
        parts.append(
            f"Measurements: WNS={best.get('wns_worst_ns', '?')}ns, "
            f"cells={best.get('cell_count', '?')}, "
            f"area={best.get('die_area_um2', '?')}um2, "
            f"power={best.get('power_mw', '?')}mW\n"
        )
    else:
        parts.append("No valid design found yet. Start with the current RTL.\n")

    if history:
        parts.append("\nHistory (last 15):\n")
        for h in history[-15:]:
            status = h.get("status", "kept" if h.get("kept") else "discarded")
            valid = "valid" if h.get("valid") else "INVALID"
            rationale = h.get("rtl_rationale", "")
            rat_str = f" -- {rationale}" if rationale else ""
            parts.append(
                f"  #{h['eval']}: FoM={h['fom']:.2e} {valid} "
                f"({status}){rat_str}\n"
            )

    parts.append(
        f"\nPropose the next optimization. "
        f"Budget remaining: {budget - eval_num + 1}."
    )
    return "".join(parts)


def cc_cli_rtl_prompt(
    design_name: str,
    design_spec: str,
    optimization_goal: str,
    rtl_file_paths: list[Path],
    current_metrics: dict | None = None,
    pdk_root: str | None = None,
    history: list[dict] | None = None,
    eval_num: int | None = None,
    budget: int | None = None,
) -> str:
    """Prompt for CC CLI agent in strategy='rtl' mode.

    The agent modifies ONLY RTL files. Config is off-limits.

    ``history`` (with ``eval_num`` and ``budget`` for the header), when
    provided, surfaces prior evaluations as a queryable Markdown table
    plus an explicit already-tried params list. The Instructions block
    grows a leading anti-duplicate warning so the agent treats the list
    as a hard constraint rather than informational context.
    """
    rtl_paths_str = "\n".join(f"  - {p}" for p in rtl_file_paths)

    metrics_section = ""
    if current_metrics:
        metrics_section = (
            "## Current Metrics\n"
            f"  WNS: {current_metrics.get('wns_worst_ns', '?')} ns\n"
            f"  Cells: {current_metrics.get('cell_count', '?')}\n"
            f"  Area: {current_metrics.get('die_area_um2', '?')} um2\n"
            f"  Power: {current_metrics.get('power_mw', '?')} mW\n\n"
        )

    history_section = _format_history_section(history, eval_num, budget)
    dedup_warning = ""
    if history_section:
        dedup_warning = (
            "**Avoid duplicates: do NOT propose params combinations listed in "
            "\"Prior Evaluations\" above.** The harness rejects duplicates "
            "as wasted budget.\n\n"
        )

    return (
        f"# RTL Optimization: {design_name}\n\n"
        f"## Goal\n{optimization_goal}\n\n"
        f"## Specification\n{design_spec}\n\n"
        f"## RTL Files (ONLY these may be modified)\n{rtl_paths_str}\n\n"
        f"{history_section}"
        f"{metrics_section}"
        "## Instructions\n"
        f"{dedup_warning}"
        "1. Read the RTL files listed above.\n"
        "2. Analyze for area/power optimization opportunities.\n"
        "3. Apply RTL modifications. Preserve module name and port interface.\n"
        "4. Run `verilator --lint-only -sv <files>` to verify syntax.\n"
        "5. Report what you changed and why.\n\n"
        "CRITICAL RULES:\n"
        "- Do NOT modify any config files (config.yaml, config.json, etc.)\n"
        "- Do NOT run LibreLane or any synthesis/place-route tools.\n"
        "- Do NOT change the module name or port list.\n"
        "- ONLY modify .v (Verilog) files.\n"
        "- If a testbench exists (tb/tb_*.v), run it after your changes:\n"
        "    iverilog -o /tmp/sim.out -sv src/*.v tb/tb_*.v && vvp /tmp/sim.out\n"
        "  If the testbench fails, revert your changes.\n\n"
        "Techniques to consider: resource sharing, shift-add instead of "
        "multiply, bitwidth reduction, FSM re-encoding, register merging, "
        "clock gating, pipeline balancing.\n\n"
        "When done, output a JSON summary:\n"
        '{"rtl_changes_applied": true, "rationale": "what you changed"}\n\n'
        "End with: DONE\n"
    )


def cc_cli_hybrid_prompt(
    design_name: str,
    design_spec: str,
    optimization_goal: str,
    rtl_file_paths: list[Path],
    config_path: Path,
    current_metrics: dict | None = None,
    best_metrics: dict | None = None,
    pdk_root: str | None = None,
    pdk_name: str | None = None,
    history: list[dict] | None = None,
    eval_num: int | None = None,
    budget: int | None = None,
) -> str:
    """Full prompt for ClaudeCodeHarness in hybrid cc_cli mode.

    The CC CLI agent reads/writes files directly, so we give it
    paths, not content.

    ``pdk_name``, when provided, is rendered as ``PDK=<name>`` in the
    Environment section. Pass ``PdkConfig.librelane_pdk_name`` (e.g.
    ``"gf180mcuD"``, ``"ihp-sg13g2"``) so the agent exports the value
    the active PDK actually expects.

    ``history`` (with ``eval_num`` and ``budget`` for the header), when
    provided, surfaces prior evaluations as a queryable Markdown table
    plus an explicit already-tried params list. The Instructions block
    grows a leading anti-duplicate warning so the agent treats the list
    as a hard constraint rather than informational context.
    """
    rtl_paths_str = "\n".join(f"  - {p}" for p in rtl_file_paths)

    metrics_section = ""
    if current_metrics:
        metrics_section = (
            "## Current Metrics\n"
            f"  WNS: {current_metrics.get('wns_worst_ns', '?')} ns\n"
            f"  Cells: {current_metrics.get('cell_count', '?')}\n"
            f"  Area: {current_metrics.get('die_area_um2', '?')} um2\n"
            f"  Power: {current_metrics.get('power_mw', '?')} mW\n\n"
        )

    pdk_note = ""
    if pdk_root:
        pdk_lines = [f"PDK_ROOT={pdk_root}"]
        if pdk_name:
            pdk_lines.append(f"PDK={pdk_name}")
        pdk_note = (
            "\n## Environment\n"
            + "\n".join(pdk_lines)
            + "\nAlways export these before running any flow command.\n"
        )

    history_section = _format_history_section(history, eval_num, budget)
    dedup_warning = ""
    if history_section:
        dedup_warning = (
            "**Avoid duplicates: do NOT propose params combinations listed in "
            "\"Prior Evaluations\" above.** The harness rejects duplicates "
            "as wasted budget.\n\n"
        )

    return (
        f"# RTL Optimization: {design_name}\n\n"
        f"## Goal\n{optimization_goal}\n\n"
        f"## Specification\n{design_spec}\n\n"
        f"## RTL Files\n{rtl_paths_str}\n\n"
        f"## Config\n  {config_path}\n\n"
        f"{history_section}"
        f"{metrics_section}"
        f"{pdk_note}\n"
        "## Instructions\n"
        f"{dedup_warning}"
        "1. Read the current RTL files listed above.\n"
        "2. Analyze for area/power/timing optimization opportunities.\n"
        "3. Apply RTL modifications (preserve module name and ports).\n"
        "4. Run `verilator --lint-only -sv <files>` to verify.\n"
        "5. To change any flow knob (CLOCK_PERIOD, "
        "PL_TARGET_DENSITY_PCT, DIE_AREA, or any other LibreLane "
        "safe-list key), use Edit on the config file shown in "
        "## Config above, then Read it back to confirm the change "
        "is on disk. Stating the intent in the rationale is NOT "
        "enough; only on-disk edits take effect on the next "
        "LibreLane run.\n"
        "6. Report what you changed and why.\n\n"
        "IMPORTANT: Do NOT run LibreLane. Only modify RTL and config.\n"
        "The caller will run the flow after you finish.\n\n"
        "When done, output a JSON summary:\n"
        '{"rtl_changes_applied": true, '
        '"config_changes_applied": true|false, '
        '"config_knobs_on_disk": {"CLOCK_PERIOD": <value-from-yaml>, '
        '"PL_TARGET_DENSITY_PCT": <value-from-yaml>}, '
        '"rationale": "what you changed and why"}\n'
        "Populate ``config_knobs_on_disk`` by re-reading the config "
        "file after your edits; the runner cross-checks the values "
        "against the on-disk YAML.\n\n"
        "End with: DONE\n"
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _format_rtl_block(rtl_sources: dict[str, str]) -> str:
    """Format RTL sources into a markdown code block."""
    parts = []
    for name, content in rtl_sources.items():
        lines = content.rstrip().split("\n")
        parts.append(f"### {name} ({len(lines)} lines)")
        parts.append(f"```verilog\n{content.rstrip()}\n```")
    return "\n\n".join(parts)


def _format_design_space(design_space: dict[str, list | tuple]) -> str:
    """Format design space into bullet points."""
    lines = []
    for name, values in design_space.items():
        if isinstance(values, list):
            lines.append(f"- {name}: one of {values}")
        elif isinstance(values, tuple) and len(values) == 2:
            lines.append(f"- {name}: [{values[0]}, {values[1]}]")
    return "\n".join(lines) if lines else "(no config knobs)"


def _format_params_compact(params: dict | None) -> str:
    """Serialize a params dict as ``key=value,key=value`` (sorted by key).

    Sorting gives duplicates a stable visual signature so the agent can
    spot recurring combinations at a glance. Returns ``"(empty)"`` for
    missing or empty params.
    """
    if not params:
        return "(empty)"
    parts = []
    for key in sorted(params.keys()):
        val = params[key]
        if isinstance(val, float):
            val_str = f"{val:g}"
        else:
            val_str = str(val)
        parts.append(f"{key}={val_str}")
    return ",".join(parts)


def _format_history_section(
    history: list[dict] | None,
    eval_num: int | None,
    budget: int | None,
) -> str:
    """Render prior evaluations as a queryable Markdown section.

    Returns an empty string when ``history`` is ``None`` or empty so
    callers stay backwards-compatible. Truncates to last 15 entries to
    bound prompt size.
    """
    if not history:
        return ""

    recent = history[-15:]
    if eval_num is not None and budget is not None:
        header = (
            f"## Prior Evaluations (eval {eval_num} of {budget}; "
            "do NOT repeat any params combination listed below)"
        )
    else:
        header = (
            "## Prior Evaluations (do NOT repeat any params combination "
            "listed below)"
        )

    lines = [header, ""]
    lines.append("| # | params | FoM | valid | status | rationale |")
    lines.append("|---|--------|-----|-------|--------|-----------|")

    for h in recent:
        n = h.get("eval", "?")
        params_str = _format_params_compact(h.get("params"))
        fom = h.get("fom")
        if fom is None:
            fom_str = "n/a"
        else:
            try:
                fom_str = f"{float(fom):.1e}"
            except (TypeError, ValueError):
                fom_str = "n/a"
        valid = h.get("valid")
        if valid is True:
            valid_str = "yes"
        elif valid is False:
            valid_str = "no"
        else:
            valid_str = "n/a"
        status_str = h.get("status") or (
            "kept" if h.get("kept") else "discarded"
        )
        rationale = (h.get("rtl_rationale") or "").replace("|", "\\|")[:50]
        # Escape pipes in params/status to keep Markdown table intact.
        params_str_safe = params_str.replace("|", "\\|")
        status_safe = str(status_str).replace("|", "\\|")
        lines.append(
            f"| {n} | {params_str_safe} | {fom_str} | {valid_str} | "
            f"{status_safe} | {rationale} |"
        )

    seen = set()
    tried = []
    for h in recent:
        ps = _format_params_compact(h.get("params"))
        if ps and ps != "(empty)" and ps not in seen:
            seen.add(ps)
            tried.append(ps)
    lines.append("")
    lines.append(
        f"Already-tried params combinations: {tried}. The harness rejects "
        "duplicates as wasted budget; choose params not in this list."
    )
    lines.append("")
    return "\n".join(lines) + "\n"
