r"""ORFS config.mk parser.

Parses OpenROAD-flow-scripts Makefile-style configuration files.
Handles export VAR = value, +=, ?=, and line continuations with \.
Groups variables by category and preserves inline comments as rationale.
"""

from __future__ import annotations

import re
from pathlib import Path

from eda_agents.parsers.base import ImportItem


class OrfsConfigParser:
    """Parse ORFS config.mk into structured knowledge."""

    name = "orfs-config"

    def can_parse(self, path: Path) -> bool:
        path = Path(path)
        if not path.is_file():
            return False
        if path.name != "config.mk":
            return False
        try:
            text = path.read_text()
        except OSError:
            return False
        return "DESIGN_NAME" in text or "PLATFORM" in text

    def parse(self, path: Path) -> list[ImportItem]:
        path = Path(path)
        text = path.read_text()
        variables = _parse_makefile(text)

        design = variables.get("DESIGN_NAME", ("", "", ""))[0]
        platform = variables.get("PLATFORM", ("", "", ""))[0]

        if not design:
            design = path.parent.name

        # Build markdown
        sections: list[str] = []
        sections.append(f"# ORFS Configuration: {design}\n")
        sections.append(f"**Source**: `{path}`")
        if platform:
            sections.append(f"**Platform**: `{platform}`")
        sections.append("")

        # Group by category
        categories = _categorize(variables)
        for cat_name, cat_vars in categories:
            if not cat_vars:
                continue
            sections.append(f"## {cat_name}\n")
            for var_name, (value, operator, comment) in sorted(cat_vars.items()):
                op_str = f" ({operator})" if operator != "=" else ""
                line = f"- **{var_name}**{op_str}: `{value}`"
                if comment:
                    line += f" -- {comment}"
                sections.append(line)
            sections.append("")

        key = f"orfs-config-{_slug(design)}"
        content = "\n".join(sections).strip()
        return [ImportItem(type="knowledge", key=key, content=content, source=str(path))]

    def describe(self) -> str:
        return "ORFS config.mk (design variables, platform, floorplan, libraries)"


def _parse_makefile(text: str) -> dict[str, tuple[str, str, str]]:
    """Parse Makefile variable assignments.

    Returns dict of var_name -> (value, operator, comment).
    Handles line continuations with \\.
    """
    variables: dict[str, tuple[str, str, str]] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # Handle line continuations
        while line.rstrip().endswith("\\") and i + 1 < len(lines):
            line = line.rstrip()[:-1] + " " + lines[i + 1].strip()
            i += 1

        # Strip comments, but save inline comment
        comment = ""
        comment_match = re.search(r"\s+#\s*(.+)$", line)
        if comment_match:
            comment = comment_match.group(1).strip()
            line = line[: comment_match.start()]

        # Skip full-line comments and blanks
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        # Match: [export] VAR op value
        m = _VAR_RE.match(stripped)
        if m:
            var_name = m.group(1)
            operator = m.group(2)
            value = m.group(3).strip()

            if operator == "+=" and var_name in variables:
                old_val, _, old_comment = variables[var_name]
                value = f"{old_val} {value}"
                comment = comment or old_comment

            variables[var_name] = (value, operator, comment)

        i += 1

    return variables


_VAR_RE = re.compile(r"(?:export\s+)?(\w+)\s*([?+:]?=)\s*(.*)")


def _categorize(
    variables: dict[str, tuple[str, str, str]],
) -> list[tuple[str, dict[str, tuple[str, str, str]]]]:
    """Group variables by category."""
    cats: dict[str, dict[str, tuple[str, str, str]]] = {
        "Process": {},
        "Design": {},
        "Libraries": {},
        "Floorplan": {},
        "Placement": {},
        "Power": {},
        "Routing": {},
        "Timing": {},
        "Checks": {},
        "Other": {},
    }

    for var_name, val_tuple in variables.items():
        vn = var_name.upper()
        if vn in ("PLATFORM", "PROCESS", "TECH_LEF", "PDK"):
            cats["Process"][var_name] = val_tuple
        elif vn in ("DESIGN_NAME", "VERILOG_FILES", "SDC_FILE", "DESIGN_NICKNAME"):
            cats["Design"][var_name] = val_tuple
        elif "LIB" in vn or "LEF" in vn or "CELL" in vn or "DONT_USE" in vn:
            cats["Libraries"][var_name] = val_tuple
        elif vn.startswith("FP_") or "CORE_UTIL" in vn or "DIE_AREA" in vn or "CORE_AREA" in vn:
            cats["Floorplan"][var_name] = val_tuple
        elif "PLACE" in vn or "DENSITY" in vn or vn.startswith("GPL_") or vn.startswith("DPL_"):
            cats["Placement"][var_name] = val_tuple
        elif "PDN" in vn or "POWER" in vn or "VDD" in vn or "VSS" in vn:
            cats["Power"][var_name] = val_tuple
        elif "ROUTE" in vn or "GRT_" in vn or "DRT_" in vn or "WIRE" in vn:
            cats["Routing"][var_name] = val_tuple
        elif "CLOCK" in vn or "TNS" in vn or "WNS" in vn or "SLACK" in vn or "PERIOD" in vn or "SYNTH" in vn:
            cats["Timing"][var_name] = val_tuple
        elif "DRC" in vn or "LVS" in vn or "CHECK" in vn or "FILL" in vn:
            cats["Checks"][var_name] = val_tuple
        else:
            cats["Other"][var_name] = val_tuple

    return [(name, vals) for name, vals in cats.items() if vals]


def _slug(name: str) -> str:
    return name.lower().replace("_", "-").replace(" ", "-")
