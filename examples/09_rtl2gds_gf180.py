#!/usr/bin/env python3
"""Digital RTL-to-GDS flow for GF180MCU designs.

Drives the full RTL-to-GDS pipeline via either ADK multi-agent
(OpenRouter/Gemini) or Claude Code CLI backend. Supports two
designs: fazyrv-hachure (primary, nix-shell) and systolic-mac
(CI fixture).

Usage:
    # Dry run (validate setup, no LLM calls, <5s)
    python examples/09_rtl2gds_gf180.py --dry-run

    # ADK backend with Gemini Flash
    python examples/09_rtl2gds_gf180.py \\
      --design fazyrv_hachure \\
      --backend adk \\
      --model google/gemini-3-flash-preview

    # Claude Code CLI backend (uses your CC subscription)
    python examples/09_rtl2gds_gf180.py \\
      --design fazyrv_hachure \\
      --backend cc_cli \\
      --allow-dangerous

    # Systolic MAC (CI fixture, faster)
    python examples/09_rtl2gds_gf180.py \\
      --design systolic_mac \\
      --backend adk \\
      --dry-run

Requires:
    pip install eda-agents[adk]          (for ADK backend)
    Claude Code CLI installed            (for cc_cli backend)
    scripts/fetch_digital_designs.sh     (clone target designs)
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

DESIGNS = {
    "fazyrv_hachure": "eda_agents.core.designs.fazyrv_hachure:FazyRvHachureDesign",
    "systolic_mac": "eda_agents.core.designs.systolic_mac_dft:SystolicMacDftDesign",
}


def load_design(name: str, macro: str = "frv_1"):
    """Load a DigitalDesign by name."""
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


async def run_dry(args):
    """Dry run: validate design + agent setup, no LLM/tool calls."""
    from eda_agents.agents.digital_adk_agents import ProjectManager

    design = load_design(args.design, macro=args.macro)

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

    design = load_design(args.design, macro=args.macro)

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


async def main():
    parser = argparse.ArgumentParser(
        description="Digital RTL-to-GDS flow for GF180MCU"
    )
    parser.add_argument(
        "--design", default="fazyrv_hachure",
        choices=list(DESIGNS),
        help="Target design (default: fazyrv_hachure)",
    )
    parser.add_argument(
        "--macro", default="frv_1",
        help="Macro subdirectory for fazyrv (default: frv_1). "
             "Use '' for chip-top.",
    )
    parser.add_argument(
        "--backend", default="adk",
        choices=["adk", "cc_cli"],
        help="Agent backend (default: adk)",
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

    if args.dry_run:
        await run_dry(args)
    else:
        await run_full(args)


if __name__ == "__main__":
    asyncio.run(main())
