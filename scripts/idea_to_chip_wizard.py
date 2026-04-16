#!/usr/bin/env python3
"""Interactive wizard: NL idea -> chip (digital GDS or analog topology recommendation).

For novice users who want a guided flow from "I have an idea" to
either (a) a digital GDS file or (b) a topology + starter-specs
recommendation for the analog side. This is the S11 entry point —
not meant to be the only way to drive the pipeline, just the friendly
one.

Usage::

    # Interactive (asks domain, description, PDK, etc.)
    python scripts/idea_to_chip_wizard.py

    # One-shot digital (skips domain question)
    python scripts/idea_to_chip_wizard.py --digital \\
        --description "4-bit counter with enable" \\
        --design-name counter4 --pdk gf180mcu

    # One-shot analog recommendation
    python scripts/idea_to_chip_wizard.py --analog \\
        --description "low-noise biomedical 60 dB amplifier"

    # Digital dry run (no CC CLI / LibreLane actually invoked)
    python scripts/idea_to_chip_wizard.py --digital --dry-run \\
        --description "counter" --design-name c4

Exit codes:
    0   success
    1   user cancelled / validation failed
    2   downstream tool failure (missing PDK, no API key, etc.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _prompt(msg: str, default: str | None = None) -> str:
    """Simple stdin prompt with optional default."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{msg}{suffix}: ").strip()
    except EOFError:
        print("\n(aborted)")
        sys.exit(1)
    return answer or (default or "")


def _prompt_choice(msg: str, choices: list[str], default: str) -> str:
    """Prompt for one of a fixed set of choices."""
    while True:
        answer = _prompt(f"{msg} ({'/'.join(choices)})", default=default).lower()
        if answer in choices:
            return answer
        print(f"  please choose one of: {', '.join(choices)}")


def _resolve_pdk_root(pdk: str, explicit: str | None) -> str | None:
    """Best-effort: env PDK_ROOT if it matches, else a known local path."""
    if explicit:
        return explicit
    env_root = os.environ.get("PDK_ROOT")
    if env_root:
        return env_root
    guesses = {
        "gf180mcu": "/home/montanares/git/wafer-space-gf180mcu",
        "ihp_sg13g2": "/home/montanares/git/IHP-Open-PDK",
    }
    return guesses.get(pdk)


# ---------------------------------------------------------------------------
# Digital path
# ---------------------------------------------------------------------------


async def run_digital(args) -> int:
    from eda_agents.agents.idea_to_rtl import generate_rtl_draft, result_to_dict

    description = args.description or _prompt(
        "Describe the digital block in natural language"
    )
    if not description:
        print("  (need a description)")
        return 1

    design_name = args.design_name or _prompt(
        "Top module name (lowercase, no spaces)", default="mydesign"
    )
    pdk = args.pdk or _prompt_choice(
        "Target PDK", ["gf180mcu", "ihp_sg13g2"], default="gf180mcu"
    )
    work_dir = Path(args.work_dir or f"/tmp/idea_to_chip_{design_name}").resolve()
    pdk_root = _resolve_pdk_root(pdk, args.pdk_root)

    print()
    print(f"  description   : {description[:120]}{'...' if len(description) > 120 else ''}")
    print(f"  design_name   : {design_name}")
    print(f"  pdk           : {pdk}")
    print(f"  pdk_root      : {pdk_root or '(will use default)'}")
    print(f"  work_dir      : {work_dir}")
    print(f"  dry_run       : {args.dry_run}")
    print()
    if not args.yes:
        if _prompt_choice("Proceed?", ["y", "n"], default="y") != "y":
            print("aborted.")
            return 1

    try:
        result = await generate_rtl_draft(
            description=description,
            design_name=design_name,
            work_dir=work_dir,
            pdk=pdk,
            pdk_root=pdk_root,
            librelane_python=args.librelane_python,
            dry_run=args.dry_run,
            allow_dangerous=args.allow_dangerous,
            cli_path=args.cli_path,
            timeout_s=args.timeout,
            max_budget_usd=args.max_budget,
            skip_gl_sim=args.skip_gl_sim,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAIL: {type(exc).__name__}: {exc}")
        return 2

    print()
    print("=" * 60)
    print("Result")
    print("=" * 60)
    print(f"  success    : {result.success}")
    print(f"  all_passed : {result.all_passed}")
    print(f"  wall_time  : {result.wall_time_s:.1f}s")
    print(f"  cost_usd   : {result.cost_usd:.3f}")
    if result.gds_path:
        print(f"  gds        : {result.gds_path}")
    if result.gl_sim:
        gl = result.gl_sim
        print(f"  gl_synth   : {gl.get('post_synth', {}).get('success')}")
        print(f"  gl_pnr     : {gl.get('post_pnr', {}).get('success')}")
    if result.error:
        print(f"  error      : {result.error[:300]}")

    # Save full JSON next to work_dir.
    out = work_dir / "wizard_result.json"
    work_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result_to_dict(result), indent=2, default=str))
    print(f"\n  (saved: {out})")

    return 0 if result.all_passed else 2


# ---------------------------------------------------------------------------
# Analog path (topology recommendation only — sizing + layout are separate
# arcs; see docs/idea_to_chip_s11.md for the full picture).
# ---------------------------------------------------------------------------


async def run_analog(args) -> int:
    description = args.description or _prompt(
        "Describe the analog block (gain, bandwidth, noise, supply, etc.)"
    )
    if not description:
        print("  (need a description)")
        return 1

    constraints_raw = args.constraints or _prompt(
        "Numeric constraints as 'key=value,key=value' (optional)",
        default="",
    )
    constraints: dict[str, float | int] = {}
    if constraints_raw:
        for pair in constraints_raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                print(f"  skipping malformed constraint: {pair!r}")
                continue
            k, v = pair.split("=", 1)
            try:
                constraints[k.strip()] = float(v.strip())
            except ValueError:
                print(f"  non-numeric constraint value skipped: {pair!r}")

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("\n  OPENROUTER_API_KEY not set — need a key to call the recommender.")
        print("  (export OPENROUTER_API_KEY=... then rerun)")
        return 2

    from eda_agents.mcp.server import recommend_topology

    payload = recommend_topology(
        description=description,
        constraints=constraints or None,
        model=args.model,
        dry_run=args.dry_run,
    )

    print()
    print("=" * 60)
    print("Topology recommendation")
    print("=" * 60)
    if not payload.get("success"):
        print(f"  FAIL: {payload.get('error')}")
        return 2
    if payload.get("dry_run"):
        print("  (dry run — no OpenRouter call)")
        print(f"  prompt_length    : {payload['prompt_length']} chars")
        print(f"  known_topologies : {', '.join(payload['known_topologies'])}")
        return 0

    print(f"  topology         : {payload['topology']}")
    print(f"  confidence       : {payload['confidence']}")
    print(f"  valid_topology   : {payload['valid_topology']}")
    print(f"  rationale        : {payload['rationale']}")
    if payload.get("starter_specs"):
        print("  starter_specs    :")
        for k, v in payload["starter_specs"].items():
            print(f"    {k} = {v}")
    if payload.get("notes"):
        print(f"  notes            : {payload['notes']}")
    print(f"  model            : {payload['model']}  (tokens: {payload['total_tokens']})")

    if payload["confidence"] == "low" or payload["topology"] == "custom":
        print()
        print("  CONFIDENCE LOW — mapping is uncertain. Do NOT commit to")
        print("  the recommended sizing without a human expert in the loop.")
    else:
        print()
        print("  Next step: use `evaluate_topology` MCP tool or run the")
        print("  existing autoresearch loop over the recommended topology")
        print("  to find a sizing point. See docs/idea_to_chip_s11.md.")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def amain() -> int:
    parser = argparse.ArgumentParser(
        description="Interactive wizard for the S11 idea-to-chip pipeline."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--digital", action="store_true")
    mode.add_argument("--analog", action="store_true")

    parser.add_argument("--description", default=None)
    parser.add_argument("--design-name", default=None)
    parser.add_argument("--pdk", default=None)
    parser.add_argument("--pdk-root", default=None)
    parser.add_argument("--work-dir", default=None)
    parser.add_argument(
        "--librelane-python",
        default="/home/montanares/git/librelane/.venv/bin/python",
    )
    parser.add_argument("--cli-path", default="claude")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--max-budget", type=float, default=None)
    parser.add_argument("--allow-dangerous", action="store_true")
    parser.add_argument("--skip-gl-sim", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip the interactive 'proceed?' confirmation")

    # Analog-only
    parser.add_argument("--constraints", default=None,
                        help="Analog: key=value,key=value numeric constraints")
    parser.add_argument("--model", default="google/gemini-2.5-flash")

    args = parser.parse_args()

    if args.digital:
        domain = "digital"
    elif args.analog:
        domain = "analog"
    else:
        print("=" * 60)
        print("eda-agents idea-to-chip wizard")
        print("=" * 60)
        print("Digital flow ends at a signoff-clean GDS (CC CLI + LibreLane).")
        print("Analog flow ends at a topology recommendation from the")
        print("registered set (miller_ota, aa_ota, gf180_ota, strongarm_comp,")
        print("sar_adc_{7,8,11}bit) plus starter specs.")
        print()
        domain = _prompt_choice(
            "Which domain?", ["digital", "analog"], default="digital"
        )

    if domain == "digital":
        return await run_digital(args)
    return await run_analog(args)


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
