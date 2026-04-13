#!/usr/bin/env python3
"""Digital RTL-to-GDS flow for GF180MCU designs.

Three entry modes from most to least friction:

  Mode 1 - Expert (named design with Python wrapper):
    python examples/09_rtl2gds_gf180.py \\
      --design fazyrv_hachure --backend cc_cli --allow-dangerous

  Mode 2 - Bring your config (no Python class needed):
    python examples/09_rtl2gds_gf180.py \\
      --config /path/to/project/config.yaml \\
      --pdk-root /path/to/gf180mcu \\
      --backend cc_cli --allow-dangerous

  Mode 3 - From spec (idea to GDS, CC CLI only):
    python examples/09_rtl2gds_gf180.py \\
      --spec "4-bit synchronous counter with enable and async reset" \\
      --pdk-root /path/to/gf180mcu \\
      --backend cc_cli --allow-dangerous \\
      --work-dir /tmp/my_counter

  Dry run (any mode, no LLM calls, <5s):
    python examples/09_rtl2gds_gf180.py --dry-run
    python examples/09_rtl2gds_gf180.py --dry-run --spec "counter"

Requires:
    pip install eda-agents[adk]          (for ADK backend)
    Claude Code CLI installed            (for cc_cli backend)
    scripts/fetch_digital_designs.sh     (for --design mode)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

DEFAULT_MODEL = "google/gemini-3-flash-preview"


def parse_fom_weights(raw: str | None) -> dict[str, float] | None:
    """Parse FoM weights from CLI string like 'timing=1.0,area=0.5,power=0.3'."""
    if not raw:
        return None
    weights = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            print(f"Invalid FoM weight format: {pair!r}. Expected key=value.")
            sys.exit(1)
        key, val = pair.split("=", 1)
        key = key.strip()
        key_map = {"timing": "timing_w", "area": "area_w", "power": "power_w"}
        key = key_map.get(key, key)
        if key not in ("timing_w", "area_w", "power_w"):
            print(f"Unknown FoM weight: {key!r}. Valid: timing, area, power")
            sys.exit(1)
        weights[key] = float(val)
    return weights


DESIGNS = {
    "fazyrv_hachure": "eda_agents.core.designs.fazyrv_hachure:FazyRvHachureDesign",
    "systolic_mac": "eda_agents.core.designs.systolic_mac_dft:SystolicMacDftDesign",
}


def load_design_named(name: str, macro: str = "frv_1"):
    """Mode 1: Load a DigitalDesign by registered name."""
    if name not in DESIGNS:
        print(f"Unknown design: {name}")
        print(f"Available: {', '.join(sorted(DESIGNS))}")
        sys.exit(1)

    module_path, class_name = DESIGNS[name].rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    if name == "fazyrv_hachure":
        return cls(macro=macro)
    return cls()


def load_design_from_config(
    config_path: str,
    pdk_root: str | None,
    fom_weights: dict[str, float] | None = None,
):
    """Mode 2: Create a GenericDesign from a LibreLane config file."""
    from eda_agents.core.designs.generic import GenericDesign

    return GenericDesign(
        config_path=config_path,
        pdk_root=pdk_root,
        fom_weights=fom_weights,
    )


def resolve_design(args):
    """Resolve the design from args (Mode 1, 2, or 3)."""
    if args.spec:
        return None  # Mode 3 doesn't use a DigitalDesign object
    fom_weights = parse_fom_weights(getattr(args, "fom_weights", None))
    if args.config:
        return load_design_from_config(args.config, args.pdk_root, fom_weights)
    return load_design_named(args.design, macro=args.macro)


def check_env(backend: str, model: str):
    """Validate environment for the chosen backend."""
    issues = []

    if backend == "adk":
        if model.startswith("openrouter/") or "/" in model:
            if not os.environ.get("OPENROUTER_API_KEY"):
                issues.append("OPENROUTER_API_KEY not set")
        elif model.startswith("gemini"):
            if not os.environ.get("GOOGLE_API_KEY"):
                issues.append("GOOGLE_API_KEY not set")
        try:
            from google.adk.agents import LlmAgent  # noqa: F401
        except ImportError:
            issues.append("google-adk not installed. Run: pip install eda-agents[adk]")

    elif backend == "cc_cli":
        import shutil
        if not shutil.which("claude"):
            npm_claude = Path.home() / ".npm-global" / "bin" / "claude"
            if not npm_claude.is_file():
                issues.append(
                    "Claude Code CLI not found. Install: "
                    "npm install -g @anthropic-ai/claude-code"
                )

    return issues


# ---------------------------------------------------------------------------
# Mode 3: From-spec execution
# ---------------------------------------------------------------------------


async def run_from_spec(args):
    """Mode 3: Generate design from natural language spec via CC CLI."""
    from eda_agents.agents.claude_code_harness import ClaudeCodeHarness
    from eda_agents.agents.tool_defs import build_from_spec_prompt

    if not args.pdk_root:
        print("--pdk-root is required for --spec mode")
        sys.exit(1)

    work_dir = Path(args.work_dir) if args.work_dir else Path("rtl2gds_from_spec")

    print("=" * 60)
    print("RTL-to-GDS From Spec" + (" (Dry Run)" if args.dry_run else ""))
    print("=" * 60)
    print(f"  Spec:      {args.spec}")
    print(f"  Work dir:  {work_dir}")
    print(f"  PDK root:  {args.pdk_root}")
    print("  Backend:   cc_cli (forced for --spec)")

    prompt = build_from_spec_prompt(
        spec=args.spec,
        work_dir=str(work_dir),
        pdk_root=args.pdk_root,
    )

    if args.dry_run:
        print(f"\n  Prompt length: {len(prompt)} chars")
        print("  Prompt preview:")
        for line in prompt.split("\n")[:10]:
            print(f"    {line}")
        print("    ...")
        print("\n  PASS (dry run)")
        return

    # Check env
    issues = check_env("cc_cli", args.model)
    if issues:
        print("\n  Environment issues:")
        for issue in issues:
            print(f"    - {issue}")
        sys.exit(1)

    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "src").mkdir(exist_ok=True)

    harness = ClaudeCodeHarness(
        prompt=prompt,
        work_dir=work_dir,
        allow_dangerous=args.allow_dangerous,
        cli_path=args.cli_path,
        timeout_s=3600,
        max_budget_usd=args.max_budget,
    )

    print("\n  Launching CC CLI agent...\n")
    t0 = time.monotonic()
    result = await harness.run()
    elapsed = time.monotonic() - t0

    print("\n" + "=" * 60)
    print("Results")
    print("=" * 60)
    print(f"  Success:   {result.success}")
    print(f"  Wall time: {elapsed:.1f}s")
    print(f"  Turns:     {result.num_turns}")
    print(f"  Cost:      ${result.total_cost_usd:.4f}")

    if result.error:
        print(f"  Error:     {result.error[:300]}")

    # Show agent output (truncated)
    if result.result_text:
        lines = result.result_text.strip().split("\n")
        print(f"\n  Agent output ({len(lines)} lines):")
        for line in lines[-30:]:
            print(f"    {line}")

    # Save results
    results_file = work_dir / "from_spec_results.json"
    results_file.write_text(json.dumps({
        "spec": args.spec,
        "success": result.success,
        "wall_time_s": elapsed,
        "num_turns": result.num_turns,
        "cost_usd": result.total_cost_usd,
        "error": result.error,
        "result_text": result.result_text[-2000:] if result.result_text else "",
    }, indent=2))
    print(f"\n  Results saved: {results_file}")


# ---------------------------------------------------------------------------
# Mode 1+2: Design-based execution (expert or GenericDesign)
# ---------------------------------------------------------------------------


async def run_dry(args):
    """Dry run: validate design + agent setup, no LLM/tool calls."""
    from eda_agents.agents.digital_adk_agents import ProjectManager

    design = resolve_design(args)

    print("=" * 60)
    print("RTL-to-GDS Dry Run")
    print("=" * 60)

    # Validate design clone
    problems = design.validate_clone()
    if problems:
        print("\n  Design issues:")
        for p in problems:
            print(f"    - {p}")
        if not args.force:
            sys.exit(1)

    mode = "config" if args.config else "expert"
    print(f"  Mode:      {mode}")
    print(f"  Design:    {design.project_name()}")
    print(f"  Spec:      {design.specs_description()}")
    print(f"  FoM:       {design.fom_description()}")
    print(f"  Backend:   {args.backend}")
    print(f"  Model:     {args.model}")

    pm = ProjectManager(
        design=design,
        model=args.model,
        backend=args.backend,
        allow_dangerous=args.allow_dangerous,
        cli_path=args.cli_path,
        max_budget_usd=args.max_budget,
    )

    work_dir = Path(args.output) if args.output else Path("rtl2gds_results")
    result = await pm.run(work_dir, dry_run=True)

    if args.backend == "cc_cli":
        print(f"\n  Prompt length: {result.get('prompt_length', 0)} chars")
        print(f"  CLI path:     {result.get('cli_path', 'N/A')}")
    else:
        print(f"\n  Master:       {result.get('master_agent', 'N/A')}")
        sub_agents = result.get("sub_agent_names", result.get("sub_agents", []))
        print(f"  Sub-agents:   {', '.join(str(s) for s in sub_agents)}")

    print("\n  PASS (dry run)")


async def run_full(args):
    """Full run: execute the RTL-to-GDS flow."""
    from eda_agents.agents.digital_adk_agents import ProjectManager

    design = resolve_design(args)

    print("=" * 60)
    print("RTL-to-GDS Full Run")
    print("=" * 60)

    # Validate design
    problems = design.validate_clone()
    if problems:
        print("\n  Design issues:")
        for p in problems:
            print(f"    - {p}")
        if not args.force:
            sys.exit(1)

    # Check environment
    issues = check_env(args.backend, args.model)
    if issues:
        print("\n  Environment issues:")
        for issue in issues:
            print(f"    - {issue}")
        sys.exit(1)

    mode = "config" if args.config else "expert"
    print(f"  Mode:      {mode}")
    print(f"  Design:    {design.project_name()}")
    print(f"  Backend:   {args.backend}")
    print(f"  Model:     {args.model}")

    work_dir = Path(args.output) if args.output else Path("rtl2gds_results")
    print(f"  Output:    {work_dir}")

    if args.allow_dangerous:
        env_gate = os.environ.get("EDA_AGENTS_ALLOW_DANGEROUS") == "1"
        if env_gate:
            print("  Dangerous: ENABLED (double-gated)")
        else:
            print("  Dangerous: constructor=True but env var not set")

    pm = ProjectManager(
        design=design,
        model=args.model,
        backend=args.backend,
        allow_dangerous=args.allow_dangerous,
        cli_path=args.cli_path,
        max_budget_usd=args.max_budget,
    )

    print("\n  Launching...\n")
    t0 = time.monotonic()

    try:
        result = await pm.run(work_dir)

        elapsed = time.monotonic() - t0

        print("\n" + "=" * 60)
        print("Results")
        print("=" * 60)
        print(f"  Design:      {result.get('design', 'N/A')}")
        print(f"  Wall time:   {elapsed:.1f}s")

        if args.backend == "cc_cli":
            print(f"  Success:     {result.get('success', 'N/A')}")
            print(f"  Verdict:     {result.get('verdict', 'N/A')}")
            print(f"  CLI turns:   {result.get('num_turns', 'N/A')}")
            print(f"  Cost:        ${result.get('cost_usd', 0):.4f}")
            if result.get("wns_ns") is not None:
                print(f"  WNS:         {result['wns_ns']:+.3f} ns")
            if result.get("error"):
                print(f"  Error:       {result['error'][:200]}")
        else:
            # ADK output
            output = result.get("agent_output", "")
            if output:
                lines = output.strip().split("\n")
                print(f"\n  Agent output ({len(lines)} lines):")
                for line in lines[:30]:
                    print(f"    {line}")
                if len(lines) > 30:
                    print(f"    ... ({len(lines) - 30} more lines)")

        # Save results
        results_file = work_dir / "rtl2gds_results.json"
        results_file.parent.mkdir(parents=True, exist_ok=True)
        serializable = {
            k: v for k, v in result.items()
            if k not in ("agent_output", "prompt")
        }
        serializable["wall_time_s"] = elapsed
        results_file.write_text(json.dumps(serializable, indent=2, default=str))
        print(f"\n  Results saved: {results_file}")

    except Exception as e:
        print(f"\n  FAIL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    parser = argparse.ArgumentParser(
        description="Digital RTL-to-GDS flow for GF180MCU (3 entry modes)"
    )

    # Entry mode (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--design", default=None,
        choices=list(DESIGNS),
        help="Mode 1: Named design with Python wrapper",
    )
    mode_group.add_argument(
        "--config", default=None,
        help="Mode 2: Path to LibreLane config (YAML/JSON). "
             "Creates a GenericDesign automatically.",
    )
    mode_group.add_argument(
        "--spec", default=None,
        help="Mode 3: Natural language circuit spec. Agent writes RTL + "
             "config from scratch. CC CLI only.",
    )

    parser.add_argument(
        "--pdk-root", default=None,
        help="Explicit PDK_ROOT path (required for --config and --spec)",
    )
    parser.add_argument(
        "--fom-weights", default=None,
        help="FoM weights as key=value pairs: timing=1.0,area=0.5,power=0.3 "
             "(Mode 2 only, passed to GenericDesign)",
    )
    parser.add_argument(
        "--macro", default="frv_1",
        help="Macro subdirectory for fazyrv (default: frv_1)",
    )
    parser.add_argument(
        "--backend", default="adk",
        choices=["adk", "cc_cli"],
        help="Agent backend (default: adk). --spec forces cc_cli.",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"LLM model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output directory (default: rtl2gds_results)",
    )
    parser.add_argument(
        "--work-dir", default=None,
        help="Working directory for --spec mode (agent writes files here)",
    )
    parser.add_argument(
        "--max-budget", type=float, default=None,
        help="Max budget in USD for CC CLI backend",
    )
    parser.add_argument(
        "--allow-dangerous", action="store_true",
        help="Enable --dangerously-skip-permissions for CC CLI "
             "(also requires EDA_AGENTS_ALLOW_DANGEROUS=1)",
    )
    parser.add_argument(
        "--cli-path", default="claude",
        help="Path to claude CLI binary (default: claude)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate setup without running LLM agents",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Continue even if design validation fails",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    # Default to fazyrv_hachure if no mode specified
    if not args.design and not args.config and not args.spec:
        args.design = "fazyrv_hachure"

    # --spec forces cc_cli backend
    if args.spec and args.backend != "cc_cli":
        args.backend = "cc_cli"

    # Mode 3: from-spec path (separate flow)
    if args.spec:
        await run_from_spec(args)
        return

    # Mode 1 or 2: design-based path
    if args.dry_run:
        await run_dry(args)
    else:
        await run_full(args)


if __name__ == "__main__":
    asyncio.run(main())
