"""KLayout DRC .lyrdb report parser.

Parses KLayout results database XML files into structured
violation summaries, following the EdaImporter protocol.
"""

from __future__ import annotations

from pathlib import Path

from eda_agents.core.klayout_drc import parse_lyrdb
from eda_agents.parsers.base import ImportItem


class KLayoutDrcParser:
    """Parse KLayout DRC .lyrdb files into violation summaries."""

    name = "klayout-drc"

    def can_parse(self, path: Path) -> bool:
        path = Path(path)
        if not path.is_file():
            return False
        return path.suffix == ".lyrdb"

    def parse(self, path: Path) -> list[ImportItem]:
        path = Path(path)
        rules = parse_lyrdb(path)

        total_violations = sum(rules.values())
        design_name = path.stem

        # Strip common suffixes from design name
        for suffix in ("_drc", "_comp", "_main"):
            if design_name.endswith(suffix):
                design_name = design_name[: -len(suffix)]
                break

        sections: list[str] = []
        sections.append(f"# KLayout DRC Summary: {design_name}\n")
        sections.append(f"**Source**: `{path}`\n")
        sections.append(f"**Total violations**: {total_violations:,}")
        sections.append(f"**Unique rules**: {len(rules)}\n")

        if rules:
            sorted_rules = sorted(
                rules.items(), key=lambda x: x[1], reverse=True
            )
            sections.append("## Violations by Rule\n")
            sections.append("| Count | Rule |")
            sections.append("|------:|------|")
            for rule, count in sorted_rules:
                sections.append(f"| {count:,} | {rule} |")
            sections.append("")

        key = f"klayout-drc-{_slug(design_name)}"
        content = "\n".join(sections).strip()
        return [
            ImportItem(
                type="knowledge",
                key=key,
                content=content,
                source=str(path),
            )
        ]

    def describe(self) -> str:
        return "KLayout DRC .lyrdb report (violation counts per rule, XML)"


def _slug(name: str) -> str:
    return name.lower().replace("_", "-").replace(" ", "-")
