"""Netgen LVS report parser.

Parses Netgen LVS text reports to extract cell equivalence results,
device/net counts, pin mismatches, and the final match/no-match result.
"""

from __future__ import annotations

import re
from pathlib import Path

from eda_agents.parsers.base import ImportItem


class NetgenLvsParser:
    """Parse Netgen LVS .rpt files into structured summaries."""

    name = "netgen-lvs"

    def can_parse(self, path: Path) -> bool:
        path = Path(path)
        if path.is_file():
            name = path.name.lower()
            if "lvs" in name and path.suffix == ".rpt":
                return True
            if name == "lvs.netgen.json":
                return True
        if path.is_dir():
            return "lvs" in path.name.lower()
        return False

    def parse(self, path: Path) -> list[ImportItem]:
        path = Path(path)

        # If directory, find the .rpt file inside
        if path.is_dir():
            candidates = list(path.rglob("*.rpt"))
            candidates = [c for c in candidates if "lvs" in c.name.lower()]
            if not candidates:
                return []
            path = candidates[0]

        text = path.read_text(errors="replace")
        design_name = _infer_design(path)

        # Parse the report
        result = _parse_report(text)

        # Build markdown
        sections: list[str] = []
        sections.append(f"# LVS Summary: {design_name}\n")
        sections.append(f"**Source**: `{path}`")
        sections.append(f"**Final result**: **{result['final_result']}**\n")

        if result["top_cell"]:
            sections.append(f"**Top cell**: `{result['top_cell']}`")

        if result["device_counts"]:
            sections.append("\n## Device/Net Counts\n")
            sections.append("| Property | Circuit 1 | Circuit 2 |")
            sections.append("|----------|-----------|-----------|")
            for prop, (c1, c2) in result["device_counts"].items():
                match_mark = "" if c1 == c2 else " **MISMATCH**"
                sections.append(f"| {prop} | {c1} | {c2} |{match_mark}")
            sections.append("")

        if result["cell_results"]:
            sections.append("## Cell Comparison\n")
            eq_count = sum(1 for v in result["cell_results"].values() if v)
            neq_count = len(result["cell_results"]) - eq_count
            sections.append(f"- **Equivalent**: {eq_count}")
            sections.append(f"- **Not equivalent**: {neq_count}\n")
            if neq_count > 0:
                sections.append("### Mismatched Cells\n")
                for cell, eq in result["cell_results"].items():
                    if not eq:
                        sections.append(f"- `{cell}`")
                sections.append("")

        if result["pin_mismatches"]:
            sections.append("## Pin Mismatches\n")
            for cell, pins in result["pin_mismatches"].items():
                sections.append(f"- `{cell}`: {', '.join(pins)}")
            sections.append("")

        if result["warnings"]:
            sections.append("## Warnings\n")
            for w in result["warnings"][:20]:  # Cap at 20
                sections.append(f"- {w}")
            if len(result["warnings"]) > 20:
                sections.append(f"- ... and {len(result['warnings']) - 20} more")
            sections.append("")

        key = f"lvs-summary-{_slug(design_name)}"
        content = "\n".join(sections).strip()
        return [ImportItem(type="knowledge", key=key, content=content, source=str(path))]

    def describe(self) -> str:
        return "Netgen LVS .rpt report (cell equivalence, pin mismatches, final result)"


def _parse_report(text: str) -> dict:
    result = {
        "final_result": "unknown",
        "top_cell": "",
        "cell_results": {},  # cell_name -> bool (equivalent)
        "device_counts": {},  # property -> (circuit1, circuit2)
        "pin_mismatches": {},  # cell -> [pins]
        "warnings": [],
    }

    lines = text.splitlines()

    for i, line in enumerate(lines):
        # Final result
        if line.startswith("Final result:"):
            result["final_result"] = line.split(":", 1)[1].strip()
            continue

        # Device class equivalence
        m = _EQUIV_RE.match(line)
        if m:
            result["cell_results"][m.group(1).strip()] = True
            # Last equivalent cell is likely the top
            result["top_cell"] = m.group(1).strip()
            continue

        # Non-equivalent
        m = _NOT_EQUIV_RE.match(line)
        if m:
            result["cell_results"][m.group(1).strip()] = False
            continue

        # Device/net counts
        m = _COUNT_RE.match(line)
        if m:
            prop = m.group(1).strip()
            c1 = m.group(2).strip()
            c2 = m.group(3).strip()
            result["device_counts"][prop] = (c1, c2)
            continue

        # Pin mismatch warnings
        if "pin" in line.lower() and "mismatch" in line.lower():
            result["warnings"].append(line.strip())

        # Disconnected nodes
        if "disconnected" in line.lower():
            result["warnings"].append(line.strip())

    return result


_EQUIV_RE = re.compile(r"Device classes (.+?) and .+ are equivalent\.")
_NOT_EQUIV_RE = re.compile(r"Device classes (.+?) and .+ are NOT equivalent\.")
_COUNT_RE = re.compile(r"Number of (\w+):\s*(\d+)\s*\|\s*Number of \w+:\s*(\d+)")


def _infer_design(path: Path) -> str:
    parts = path.parts
    # Look for "designs/<name>" in path
    if "designs" in parts:
        idx = parts.index("designs")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    # Fallback: use grandparent directory name if it's not "reports"
    for parent in path.parents:
        if parent.name not in ("reports", "netgen-lvs") and parent.name != "":
            return parent.name
    return path.stem


def _slug(name: str) -> str:
    return name.lower().replace("_", "-").replace(" ", "-")
