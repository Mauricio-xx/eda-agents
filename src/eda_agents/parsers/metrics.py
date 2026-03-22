"""LibreLane metrics parser (state_in.json).

Extracts flow metrics from LibreLane run outputs: synthesis stats,
timing per corner (WNS/TNS), DRC counts, LVS results, routing metrics, power.
"""

from __future__ import annotations

import json
from pathlib import Path

from eda_agents.parsers.base import ImportItem


class LibreLaneMetricsParser:
    """Parse LibreLane state_in.json metrics into structured knowledge."""

    name = "librelane-metrics"

    def can_parse(self, path: Path) -> bool:
        path = Path(path)
        # Direct state_in.json file
        if path.is_file() and path.name == "state_in.json":
            return _has_metrics(path)
        # Run directory: scan for checker state_in.json files
        if path.is_dir():
            return any(_find_metrics_files(path))
        return False

    def parse(self, path: Path) -> list[ImportItem]:
        path = Path(path)

        if path.is_file():
            files = [path]
        else:
            files = sorted(_find_metrics_files(path))

        if not files:
            return []

        # Collect all metrics from all files, merging into one dict
        all_metrics: dict[str, float | int] = {}
        design_name = ""
        source_paths: list[str] = []

        for f in files:
            data = json.loads(f.read_text())
            metrics = data.get("metrics", {})
            all_metrics.update(metrics)
            source_paths.append(str(f))
            # Try to extract design name from paths or json_h
            if not design_name:
                design_name = _infer_design_name(data, f)

        if not design_name:
            design_name = "unknown"

        # Build structured markdown
        sections: list[str] = []
        sections.append(f"# EDA Metrics: {design_name}\n")
        sections.append(f"**Sources**: {len(source_paths)} metric file(s)\n")

        # Categorize metrics
        categories = _categorize_metrics(all_metrics)

        for cat_name, cat_metrics in categories:
            if not cat_metrics:
                continue
            sections.append(f"## {cat_name}\n")
            sections.append("| Metric | Value |")
            sections.append("|--------|-------|")
            for mk, mv in sorted(cat_metrics.items()):
                sections.append(f"| `{mk}` | {_fmt_metric(mv)} |")
            sections.append("")

        key = f"eda-metrics-{_slug(design_name)}"
        content = "\n".join(sections).strip()
        return [ImportItem(type="knowledge", key=key, content=content, source=", ".join(source_paths))]

    def describe(self) -> str:
        return "LibreLane state_in.json (synthesis, timing, DRC, LVS, routing, power metrics)"


def _has_metrics(path: Path) -> bool:
    try:
        data = json.loads(path.read_text())
        return isinstance(data.get("metrics"), dict)
    except (json.JSONDecodeError, OSError):
        return False


def _find_metrics_files(run_dir: Path) -> list[Path]:
    """Find state_in.json files with metrics in a run directory."""
    results = []
    for f in run_dir.rglob("state_in.json"):
        if _has_metrics(f):
            results.append(f)
    return results


def _infer_design_name(data: dict, path: Path) -> str:
    """Try to extract design name from state_in.json data or path."""
    # From json_h path: .../designs/<name>/runs/...
    for field in ("json_h", "nl", "sdc"):
        val = data.get(field, "")
        if val:
            parts = Path(val).parts
            if "designs" in parts:
                idx = parts.index("designs")
                if idx + 1 < len(parts):
                    return parts[idx + 1]

    # From path: .../designs/<name>/runs/...
    parts = path.parts
    if "designs" in parts:
        idx = parts.index("designs")
        if idx + 1 < len(parts):
            return parts[idx + 1]

    # From run directory name: try parent chain
    for parent in path.parents:
        if parent.name == "runs":
            return parent.parent.name
    return ""


def _categorize_metrics(metrics: dict) -> list[tuple[str, dict]]:
    """Group metrics by stage/category based on key prefixes."""
    cats: dict[str, dict] = {
        "Synthesis": {},
        "Timing": {},
        "DRC": {},
        "LVS": {},
        "Routing": {},
        "Power": {},
        "Other": {},
    }
    for k, v in metrics.items():
        kl = k.lower()
        if kl.startswith("design__instance") or kl.startswith("synthesis") or kl.startswith("design__inferred") or kl.startswith("design__lint"):
            cats["Synthesis"][k] = v
        elif "timing" in kl or "wns" in kl or "tns" in kl or "slack" in kl:
            cats["Timing"][k] = v
        elif "drc" in kl:
            cats["DRC"][k] = v
        elif "lvs" in kl:
            cats["LVS"][k] = v
        elif "route" in kl or "wire" in kl or "antenna" in kl:
            cats["Routing"][k] = v
        elif "power" in kl:
            cats["Power"][k] = v
        else:
            cats["Other"][k] = v

    return [(name, vals) for name, vals in cats.items() if vals]


def _fmt_metric(v: float | int) -> str:
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e12:
            return str(int(v))
        return f"{v:.4f}" if abs(v) < 100 else f"{v:.2f}"
    return str(v)


def _slug(name: str) -> str:
    return name.lower().replace("_", "-").replace(" ", "-")
