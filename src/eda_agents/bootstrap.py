"""Install eda-agents project assets into a downstream cwd.

Ships as the ``eda-init`` console script. After
``pip install eda-agents[mcp]`` a student runs ``eda-init`` in their
project directory to materialise ``opencode.json``, ``.mcp.json``,
``.opencode/agent/*.md`` and ``.claude/agents/*.md`` from the package's
templates, skipping files that already exist unless ``--force`` is
passed.
"""

from __future__ import annotations

import argparse
from importlib.resources import files as _files
from pathlib import Path

_TEMPLATES = _files("eda_agents") / "templates"


def _copy_file(src, dst: Path, *, force: bool, label: str) -> bool:
    """Copy one template file. Returns True if it wrote, False if skipped."""
    if dst.exists() and not force:
        print(f"  skip   {label}: {dst} already exists (use --force to overwrite)")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"  write  {label}: {dst}")
    return True


def _copy_dir(src_dir, dst_dir: Path, *, force: bool, label: str) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src_dir.iterdir(), key=lambda e: e.name):
        if entry.is_file() and entry.name.endswith(".md"):
            _copy_file(entry, dst_dir / entry.name, force=force, label=label)


def init_project(target: Path | None = None, *, force: bool = False) -> None:
    """Populate ``target`` (default: cwd) with the shipped templates."""
    target_path = Path(target) if target else Path.cwd()
    target_path.mkdir(parents=True, exist_ok=True)

    print(f"Installing eda-agents project assets into {target_path}")
    _copy_file(
        _TEMPLATES / "opencode.json",
        target_path / "opencode.json",
        force=force,
        label="opencode config",
    )
    _copy_file(
        _TEMPLATES / "mcp.json",
        target_path / ".mcp.json",
        force=force,
        label="Claude Code MCP config",
    )
    _copy_dir(
        _TEMPLATES / "opencode_agents",
        target_path / ".opencode" / "agent",
        force=force,
        label="opencode agent",
    )
    _copy_dir(
        _TEMPLATES / "claude_agents",
        target_path / ".claude" / "agents",
        force=force,
        label="Claude Code agent",
    )

    print()
    print("Done. Next steps:")
    print(f"  cd {target_path}")
    print("  opencode --agent gf180-docker-digital")
    print("  # or")
    print("  claude   # picks up .mcp.json + .claude/agents automatically")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="eda-init",
        description=(
            "Install the eda-agents opencode / Claude Code templates into "
            "the current project. Safe by default: existing files are kept "
            "unless --force is given."
        ),
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Target directory (default: current working directory).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files that already exist.",
    )
    args = parser.parse_args()
    init_project(target=args.target, force=args.force)


if __name__ == "__main__":
    main()
