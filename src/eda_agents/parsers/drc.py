"""Magic DRC report parser (streaming).

Parses Magic DRC .rpt files which can be millions of lines.
Extracts rule descriptions and violation counts per rule without
storing individual violation coordinates.
"""

from __future__ import annotations

import re
from pathlib import Path

from eda_agents.parsers.base import ImportItem


class MagicDrcParser:
    """Parse Magic DRC .rpt files into violation summaries."""

    name = "magic-drc"

    def can_parse(self, path: Path) -> bool:
        path = Path(path)
        if not path.is_file():
            return False
        if path.suffix != ".rpt":
            return False
        # Must be in a directory with "drc" in its name, or filename contains "drc"
        if "drc" in path.name.lower() or "drc" in path.parent.name.lower():
            return True
        # Peek at first few lines to see if it looks like a Magic DRC report
        try:
            with open(path) as f:
                lines = [f.readline() for _ in range(5)]
            # Magic DRC reports start with design name, then separator line
            return any("---" in line for line in lines)
        except OSError:
            return False

    def parse(self, path: Path) -> list[ImportItem]:
        path = Path(path)
        design_name = ""
        rules: dict[str, int] = {}
        current_rule = ""
        total_coords = 0

        # Streaming parse: never load entire file
        with open(path) as f:
            for line_num, line in enumerate(f):
                line = line.rstrip()

                # First non-empty line is design name
                if line_num == 0 and line.strip():
                    design_name = line.strip()
                    continue

                # Separator lines
                if line.startswith("---"):
                    continue

                # Coordinate lines (violations): digits/decimals with 'um'
                if _COORD_RE.match(line):
                    total_coords += 1
                    if current_rule:
                        rules[current_rule] = rules.get(current_rule, 0) + 1
                    continue

                # Footer: [INFO] COUNT: ...
                if line.startswith("[INFO]"):
                    continue

                # Rule description (anything else that's not blank)
                stripped = line.strip()
                if stripped and not _COORD_RE.match(stripped):
                    current_rule = stripped
                    if current_rule not in rules:
                        rules[current_rule] = 0

        if not design_name:
            design_name = path.stem.replace("drc_violations.", "").replace("drc.", "").replace(".magic", "")

        # Build markdown summary
        sections: list[str] = []
        sections.append(f"# DRC Summary: {design_name}\n")
        sections.append(f"**Source**: `{path}`\n")

        total_violations = sum(rules.values())
        sections.append(f"**Total violations**: {total_violations:,}")
        sections.append(f"**Unique rules**: {len(rules)}\n")

        if rules:
            # Sort by count descending
            sorted_rules = sorted(rules.items(), key=lambda x: x[1], reverse=True)
            sections.append("## Violations by Rule\n")
            sections.append("| Count | Rule |")
            sections.append("|------:|------|")
            for rule, count in sorted_rules:
                sections.append(f"| {count:,} | {rule} |")
            sections.append("")

        key = f"drc-summary-{_slug(design_name)}"
        content = "\n".join(sections).strip()
        return [ImportItem(type="knowledge", key=key, content=content, source=str(path))]

    def describe(self) -> str:
        return "Magic DRC .rpt report (violation counts per rule, streaming)"


# Match coordinate lines: optional whitespace, then decimal with 'um'
_COORD_RE = re.compile(r"^\s*[\d.]+um\s+[\d.]+um")


def _slug(name: str) -> str:
    return name.lower().replace("_", "-").replace(" ", "-")
