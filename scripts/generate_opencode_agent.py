#!/usr/bin/env python3
"""Generate an opencode agent stub from a registered eda-agents skill.

Usage::

    python scripts/generate_opencode_agent.py \\
        --skill analog.idea_to_topology \\
        --name analog-topology-recommender \\
        --description "Map an analog idea to a registered topology." \\
        --mcp-tools "recommend_topology,describe_topology,list_skills,render_skill" \\
        [--topology miller_ota] \\
        [--output .opencode/agent/analog-topology-recommender.md]

The emitted file is a starting point — the curated agents shipped at
``.opencode/agent/*.md`` add a short "operational loop" block that goes
beyond the raw skill body. Use this helper to bootstrap new agents, then
hand-edit.

Design goal: emit a valid opencode agent file (YAML frontmatter + body)
that:

* Whitelists ONLY the requested MCP tools (``eda-agents_<name>``) and
  disables every built-in shell / editor tool by default.
* Inherits the global opencode model — pass ``--model`` to override.
* Renders the skill body as the system prompt. Topology-bound skills
  must be paired with ``--topology``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from textwrap import indent

REPO_ROOT = Path(__file__).resolve().parent.parent

# Built-in opencode tools we default to OFF for scoped agents. Callers
# that want a tool available (e.g. ``read`` / ``write`` for authoring
# flows) pass ``--builtin-tools read,write,edit``.
BUILTIN_TOOLS = (
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "webfetch",
    "task",
    "todowrite",
)


def _parse_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _frontmatter(
    description: str,
    mode: str,
    temperature: float,
    model: str | None,
    builtin_enabled: list[str],
    mcp_tools: list[str],
) -> str:
    lines = [
        "---",
        f"description: {description.strip()}",
        f"mode: {mode}",
        f"temperature: {temperature}",
    ]
    if model:
        lines.append(f"model: {model}")
    lines.append("tools:")
    for name in BUILTIN_TOOLS:
        enabled = name in builtin_enabled
        lines.append(f"  {name}: {'true' if enabled else 'false'}")
    for tool in mcp_tools:
        full = tool if tool.startswith("eda-agents_") else f"eda-agents_{tool}"
        lines.append(f'  "{full}": true')
    lines.append("---")
    return "\n".join(lines)


def _render_skill_body(skill_name: str, topology_name: str | None) -> str:
    # Imported inline so --help works without pulling the full stack.
    from eda_agents.skills.registry import get_skill

    skill = get_skill(skill_name)
    if topology_name is None:
        return skill.render()

    from eda_agents.topologies import get_topology_by_name

    topology = get_topology_by_name(topology_name)
    return skill.render(topology)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate an opencode agent stub from an eda-agents skill.",
    )
    parser.add_argument(
        "--skill", required=True, help="Dotted skill name (e.g. analog.idea_to_topology)"
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Agent name (filename stem; will become .opencode/agent/<name>.md)",
    )
    parser.add_argument(
        "--description",
        required=True,
        help="Single-line description surfaced in `opencode agent list`.",
    )
    parser.add_argument(
        "--mcp-tools",
        default="",
        help=(
            "Comma-separated MCP tool names to whitelist. Names may be bare "
            "(`recommend_topology`) — the `eda-agents_` prefix is added "
            "automatically — or fully qualified."
        ),
    )
    parser.add_argument(
        "--builtin-tools",
        default="",
        help=(
            "Comma-separated built-in opencode tools to ENABLE "
            "(defaults disable all: bash, read, write, edit, glob, grep, "
            "webfetch, task, todowrite)."
        ),
    )
    parser.add_argument(
        "--topology",
        help="Canonical topology name for skills whose prompt_fn needs it.",
    )
    parser.add_argument(
        "--mode",
        default="all",
        choices=("all", "primary", "subagent"),
        help="Agent mode (default: all).",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.2, help="Sampling temperature (default 0.2)."
    )
    parser.add_argument(
        "--model",
        help=(
            "Override opencode model (provider/model). Omit to inherit the "
            "global opencode default."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Output file. Defaults to .opencode/agent/<name>.md relative to "
            "the repo root."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing agent file.",
    )
    args = parser.parse_args(argv)

    try:
        body = _render_skill_body(args.skill, args.topology)
    except KeyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except TypeError as exc:
        print(
            f"ERROR: skill {args.skill!r} requires a topology (pass --topology): {exc}",
            file=sys.stderr,
        )
        return 2

    mcp_tools = _parse_list(args.mcp_tools)
    builtin_enabled = _parse_list(args.builtin_tools)
    for name in builtin_enabled:
        if name not in BUILTIN_TOOLS:
            print(
                f"ERROR: unknown built-in tool {name!r}. "
                f"Allowed: {', '.join(BUILTIN_TOOLS)}",
                file=sys.stderr,
            )
            return 2

    output = args.output or (REPO_ROOT / ".opencode" / "agent" / f"{args.name}.md")
    if output.exists() and not args.force:
        print(
            f"ERROR: {output} exists; pass --force to overwrite.",
            file=sys.stderr,
        )
        return 3

    front = _frontmatter(
        description=args.description,
        mode=args.mode,
        temperature=args.temperature,
        model=args.model,
        builtin_enabled=builtin_enabled,
        mcp_tools=mcp_tools,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(front + "\n\n" + body.rstrip() + "\n", encoding="utf-8")
    try:
        rel: Path | str = output.relative_to(REPO_ROOT)
    except ValueError:
        rel = output
    print(f"Wrote {rel}")
    print(
        indent(
            "Next: hand-edit the body to add an 'OPERATIONAL LOOP' section "
            "describing how the agent should sequence the whitelisted MCP tools.",
            "  ",
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
