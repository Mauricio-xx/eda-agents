"""LibreLane config.json parser.

Extracts design parameters, physical configuration, PDN settings,
PDK-specific overrides, and flow stages from LibreLane config files.
"""

from __future__ import annotations

import json
from pathlib import Path

from eda_agents.parsers.base import ImportItem


class LibreLaneConfigParser:
    """Parse LibreLane config.json into structured knowledge."""

    name = "librelane-config"

    def can_parse(self, path: Path) -> bool:
        path = Path(path)
        if path.name != "config.json":
            return False
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return False
        if "DESIGN_NAME" not in data:
            return False
        meta = data.get("meta", {})
        return meta.get("version") in (2, 3)

    def parse(self, path: Path) -> list[ImportItem]:
        path = Path(path)
        data = json.loads(path.read_text())
        design = data["DESIGN_NAME"]
        meta = data.get("meta", {})

        sections: list[str] = []
        sections.append(f"# LibreLane Configuration: {design}\n")
        sections.append(f"**Source**: `{path}`\n")
        sections.append(f"**Config version**: {meta.get('version', 'unknown')}\n")

        # Flow stages
        flow = meta.get("flow", [])
        if flow:
            sections.append("## Flow Stages\n")
            for i, stage in enumerate(flow, 1):
                sections.append(f"{i}. `{stage}`")
            sections.append("")

        # Design parameters
        design_keys = _extract_category(data, _DESIGN_PARAMS)
        if design_keys:
            sections.append("## Design Parameters\n")
            for k, v in design_keys:
                sections.append(f"- **{k}**: `{_fmt_val(v)}`")
            sections.append("")

        # Physical configuration
        phys_keys = _extract_category(data, _PHYSICAL_PARAMS)
        if phys_keys:
            sections.append("## Physical Configuration\n")
            for k, v in phys_keys:
                sections.append(f"- **{k}**: `{_fmt_val(v)}`")
            sections.append("")

        # PDN configuration
        pdn_keys = _extract_category(data, _PDN_PARAMS)
        if pdn_keys:
            sections.append("## PDN Configuration\n")
            for k, v in pdn_keys:
                sections.append(f"- **{k}**: `{_fmt_val(v)}`")
            sections.append("")

        # Timing
        timing_keys = _extract_category(data, _TIMING_PARAMS)
        if timing_keys:
            sections.append("## Timing\n")
            for k, v in timing_keys:
                sections.append(f"- **{k}**: `{_fmt_val(v)}`")
            sections.append("")

        # All remaining top-level keys (not meta, not categorized, not pdk::)
        categorized = _DESIGN_PARAMS | _PHYSICAL_PARAMS | _PDN_PARAMS | _TIMING_PARAMS
        remaining = [
            (k, v)
            for k, v in data.items()
            if k not in categorized
            and k != "meta"
            and k != "DESIGN_NAME"
            and not k.startswith("pdk::")
        ]
        if remaining:
            sections.append("## Other Settings\n")
            for k, v in remaining:
                sections.append(f"- **{k}**: `{_fmt_val(v)}`")
            sections.append("")

        # PDK-specific overrides
        pdk_overrides = {k: v for k, v in data.items() if k.startswith("pdk::")}
        if pdk_overrides:
            sections.append("## PDK-Specific Overrides\n")
            for pdk_key, overrides in pdk_overrides.items():
                sections.append(f"### `{pdk_key}`\n")
                if isinstance(overrides, dict):
                    for k, v in overrides.items():
                        sections.append(f"- **{k}**: `{_fmt_val(v)}`")
                else:
                    sections.append(f"- Value: `{_fmt_val(overrides)}`")
                sections.append("")

        key = f"librelane-config-{_slug(design)}"
        content = "\n".join(sections).strip()
        return [ImportItem(type="knowledge", key=key, content=content, source=str(path))]

    def describe(self) -> str:
        return "LibreLane config.json (design parameters, flow stages, PDN, PDK overrides)"


# -- Key categories --

_DESIGN_PARAMS = {
    "DESIGN_NAME",
    "VERILOG_FILES",
    "CLOCK_PORT",
    "CLOCK_NET",
    "CLOCK_PERIOD",
    "SDC_FILE",
}

_PHYSICAL_PARAMS = {
    "FP_SIZING",
    "DIE_AREA",
    "CORE_AREA",
    "FP_CORE_UTIL",
    "PL_TARGET_DENSITY_PCT",
    "CORE_UTILIZATION",
    "PLACE_DENSITY",
    "GPL_CELL_PADDING",
    "DPL_CELL_PADDING",
}

_PDN_PARAMS = {
    "FP_PDN_VPITCH",
    "FP_PDN_HPITCH",
    "FP_PDN_VOFFSET",
    "FP_PDN_HOFFSET",
    "FP_PDN_VWIDTH",
    "FP_PDN_HWIDTH",
    "FP_PDN_CORE_RING",
}

_TIMING_PARAMS = {
    "CLOCK_PERIOD",
    "PL_RESIZER_HOLD_SLACK_MARGIN",
    "PL_RESIZER_HOLD_MAX_BUFFER_PERCENT",
    "TNS_END_PERCENT",
    "SYNTH_MAX_FANOUT",
}


def _extract_category(data: dict, keys: set[str]) -> list[tuple[str, object]]:
    return [(k, data[k]) for k in sorted(keys) if k in data and k != "DESIGN_NAME"]


def _fmt_val(v: object) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if v is None:
        return "null"
    return str(v)


def _slug(name: str) -> str:
    return name.lower().replace("_", "-").replace(" ", "-")
