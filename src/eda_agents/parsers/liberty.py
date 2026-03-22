"""Liberty .lib header parser.

Reads only the header portion of Liberty files (before cell definitions)
to extract library name, PVT corner, units, and default limits.
Directory mode scans all .lib files and produces a corner summary.
"""

from __future__ import annotations

import re
from pathlib import Path

from eda_agents.parsers.base import ImportItem


class LibertyParser:
    """Parse Liberty .lib file headers into structured knowledge."""

    name = "liberty"

    def can_parse(self, path: Path) -> bool:
        path = Path(path)
        if path.is_file():
            return path.suffix == ".lib"
        if path.is_dir():
            return any(path.glob("*.lib")) or any(path.rglob("*.lib"))
        return False

    def parse(self, path: Path) -> list[ImportItem]:
        path = Path(path)

        if path.is_file():
            return self._parse_single(path)
        else:
            return self._parse_directory(path)

    def _parse_single(self, path: Path) -> list[ImportItem]:
        header = _read_header(path)
        info = _parse_header(header)
        if not info["library_name"]:
            return []

        sections: list[str] = []
        sections.append(f"# Liberty Library: {info['library_name']}\n")
        sections.append(f"**Source**: `{path}`\n")

        if info["corner"]:
            sections.append(f"**Corner**: {info['corner']}")
        sections.append(f"**Process**: {info.get('nom_process', '?')}")
        sections.append(f"**Voltage**: {info.get('nom_voltage', '?')} V")
        sections.append(f"**Temperature**: {info.get('nom_temperature', '?')} C\n")

        if info["units"]:
            sections.append("## Units\n")
            for unit_name, unit_val in info["units"].items():
                sections.append(f"- **{unit_name}**: {unit_val}")
            sections.append("")

        if info["defaults"]:
            sections.append("## Default Limits\n")
            for k, v in sorted(info["defaults"].items()):
                sections.append(f"- **{k}**: {v}")
            sections.append("")

        key = f"liberty-corners-{_slug(info['library_name'])}"
        content = "\n".join(sections).strip()
        return [ImportItem(type="knowledge", key=key, content=content, source=str(path))]

    def _parse_directory(self, path: Path) -> list[ImportItem]:
        lib_files = sorted(path.rglob("*.lib"))
        if not lib_files:
            return []

        corners: list[dict] = []
        lib_family = ""

        for lf in lib_files:
            header = _read_header(lf)
            info = _parse_header(header)
            if info["library_name"]:
                corners.append(info | {"path": str(lf)})
                if not lib_family:
                    # Extract family name by removing corner suffix
                    lib_family = _extract_family(info["library_name"])

        if not corners:
            return []

        if not lib_family:
            lib_family = path.name

        sections: list[str] = []
        sections.append(f"# Liberty Corner Summary: {lib_family}\n")
        sections.append(f"**Source**: `{path}`")
        sections.append(f"**Libraries found**: {len(corners)}\n")

        sections.append("## Available Corners\n")
        sections.append("| Library | Process | Voltage | Temperature | Corner |")
        sections.append("|---------|---------|---------|-------------|--------|")
        for c in corners:
            sections.append(
                f"| `{c['library_name']}` "
                f"| {c.get('nom_process', '?')} "
                f"| {c.get('nom_voltage', '?')} V "
                f"| {c.get('nom_temperature', '?')} C "
                f"| {c.get('corner', '?')} |"
            )
        sections.append("")

        # Show units from first library (assumed same across corners)
        if corners[0].get("units"):
            sections.append("## Units (from first library)\n")
            for unit_name, unit_val in corners[0]["units"].items():
                sections.append(f"- **{unit_name}**: {unit_val}")
            sections.append("")

        key = f"liberty-corners-{_slug(lib_family)}"
        content = "\n".join(sections).strip()
        sources = ", ".join(c["path"] for c in corners[:5])
        if len(corners) > 5:
            sources += f" (+{len(corners) - 5} more)"
        return [ImportItem(type="knowledge", key=key, content=content, source=sources)]

    def describe(self) -> str:
        return "Liberty .lib header (library name, PVT corner, units, default limits)"


def _read_header(path: Path, max_lines: int = 100) -> str:
    """Read header of a Liberty file (stops before cell definitions)."""
    lines: list[str] = []
    try:
        with open(path, errors="replace") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                # Stop when we hit the first cell definition
                stripped = line.strip()
                if stripped.startswith("cell (") or stripped.startswith("cell("):
                    break
                lines.append(line)
    except OSError:
        pass
    return "".join(lines)


def _parse_header(header: str) -> dict:
    """Extract structured info from a Liberty header."""
    info: dict = {
        "library_name": "",
        "corner": "",
        "nom_process": "",
        "nom_voltage": "",
        "nom_temperature": "",
        "units": {},
        "defaults": {},
    }

    # Library name
    m = re.search(r"library\s*\(\s*(\S+)\s*\)", header)
    if m:
        info["library_name"] = m.group(1)
        info["corner"] = _infer_corner(m.group(1))

    # Key-value pairs: key : value ;
    for m in re.finditer(r"(\w+)\s*:\s*(.+?)\s*;", header):
        key = m.group(1)
        value = m.group(2).strip().strip('"')
        if key == "nom_process":
            info["nom_process"] = value
        elif key == "nom_voltage":
            info["nom_voltage"] = value
        elif key == "nom_temperature":
            info["nom_temperature"] = value
        elif key.endswith("_unit"):
            info["units"][key] = value
        elif key.startswith("default_"):
            info["defaults"][key] = value

    # Capacitive load unit (special syntax)
    m = re.search(r"capacitive_load_unit\s*\(\s*(.+?)\s*\)", header)
    if m:
        info["units"]["capacitive_load_unit"] = m.group(1)

    return info


def _infer_corner(lib_name: str) -> str:
    """Infer PVT corner from library name patterns."""
    name_lower = lib_name.lower()
    parts = []

    if "typ" in name_lower:
        parts.append("typical")
    elif "ff" in name_lower or "fast" in name_lower:
        parts.append("fast-fast")
    elif "ss" in name_lower or "slow" in name_lower:
        parts.append("slow-slow")
    elif "sf" in name_lower:
        parts.append("slow-fast")
    elif "fs" in name_lower:
        parts.append("fast-slow")

    # Voltage
    m = re.search(r"(\d+p\d+)v", name_lower)
    if m:
        parts.append(m.group(1).replace("p", ".") + "V")

    # Temperature
    m = re.search(r"(n?\d+)c", name_lower)
    if m:
        temp = m.group(1).replace("n", "-")
        parts.append(f"{temp}C")

    return " / ".join(parts) if parts else ""


def _extract_family(lib_name: str) -> str:
    """Extract library family name by removing corner suffixes."""
    # Common pattern: sg13g2_stdcell_typ_1p20V_25C -> sg13g2_stdcell
    # Remove known corner tokens
    name = lib_name
    name = re.sub(r"_(typ|ff|ss|sf|fs|slow|fast)", "", name, flags=re.I)
    name = re.sub(r"_\d+p\d+V", "", name, flags=re.I)
    name = re.sub(r"_n?\d+C", "", name, flags=re.I)
    name = name.rstrip("_")
    return name


def _slug(name: str) -> str:
    return name.lower().replace("_", "-").replace(" ", "-")
