"""EDA project type detection.

Scans a directory for markers that indicate the type of EDA project:
LibreLane, ORFS, PDK development, or analog design. Returns structured
info including suggested skills for the project type.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EdaProjectInfo:
    """Detection result for an EDA project."""

    detected: bool = False
    project_type: str = ""  # "librelane", "orfs", "pdk", "analog"
    design_name: str = ""
    pdk: str = ""
    config_path: str = ""
    markers_found: list[str] = field(default_factory=list)
    suggested_skills: list[str] = field(default_factory=list)


# Skill suggestions per project type
_SKILL_MAP: dict[str, list[str]] = {
    "librelane": [
        "configure-librelane",
        "configure-pdn",
        "debug-drc",
        "debug-lvs",
        "debug-timing",
    ],
    "orfs": [
        "configure-pdn",
        "debug-drc",
        "debug-lvs",
        "debug-timing",
    ],
    "pdk": [
        "debug-drc",
        "debug-lvs",
        "port-design",
    ],
    "analog": [
        "xschem-simulate",
        "characterize-device",
        "debug-drc",
        "debug-lvs",
    ],
}


def detect_eda_project(root: Path) -> EdaProjectInfo:
    """Detect EDA project type from directory markers.

    Checks for LibreLane, ORFS, PDK, and analog project indicators.
    Returns EdaProjectInfo with detection results.
    """
    root = Path(root)
    info = EdaProjectInfo()

    # Check LibreLane: config.json with DESIGN_NAME and meta.version 2 or 3
    ll_result = _check_librelane(root)
    if ll_result:
        info.detected = True
        info.project_type = "librelane"
        info.design_name = ll_result["design"]
        info.config_path = ll_result["config_path"]
        info.markers_found.append(f"config.json ({ll_result['config_path']})")
        if ll_result.get("pdk"):
            info.pdk = ll_result["pdk"]

    # Check ORFS: config.mk with DESIGN_NAME or PLATFORM
    orfs_result = _check_orfs(root)
    if orfs_result and not info.detected:
        info.detected = True
        info.project_type = "orfs"
        info.design_name = orfs_result["design"]
        info.config_path = orfs_result["config_path"]
        info.markers_found.append(f"config.mk ({orfs_result['config_path']})")
        if orfs_result.get("platform"):
            info.pdk = orfs_result["platform"]
    elif orfs_result:
        info.markers_found.append(f"config.mk ({orfs_result['config_path']})")

    # Check PDK: libs.tech/ directory
    if (root / "libs.tech").is_dir():
        if not info.detected:
            info.detected = True
            info.project_type = "pdk"
            info.design_name = root.name
        info.markers_found.append("libs.tech/")

    # Check analog: xschemrc or .sch files
    has_xschemrc = (root / "xschemrc").exists() or (root / ".xschemrc").exists()
    has_sch = any(root.glob("*.sch"))
    if has_xschemrc or has_sch:
        if not info.detected:
            info.detected = True
            info.project_type = "analog"
            info.design_name = root.name
        if has_xschemrc:
            info.markers_found.append("xschemrc")
        if has_sch:
            info.markers_found.append("*.sch files")

    # PDK_ROOT env var as additional marker
    pdk_root = os.environ.get("PDK_ROOT")
    if pdk_root:
        info.markers_found.append(f"PDK_ROOT={pdk_root}")
        if not info.pdk and info.detected:
            # Try to infer PDK name from PDK_ROOT
            pdk_path = Path(pdk_root)
            if pdk_path.is_dir():
                info.pdk = pdk_path.name

    # Set suggested skills
    if info.project_type:
        info.suggested_skills = _SKILL_MAP.get(info.project_type, [])

    return info


def _check_librelane(root: Path) -> dict | None:
    """Check for LibreLane config.json."""
    config = root / "config.json"
    if not config.is_file():
        return None
    try:
        data = json.loads(config.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if "DESIGN_NAME" not in data:
        return None
    meta = data.get("meta", {})
    if meta.get("version") not in (2, 3):
        return None

    result = {
        "design": data["DESIGN_NAME"],
        "config_path": str(config),
    }

    # Try to find PDK from pdk:: keys
    pdk_keys = [k for k in data if k.startswith("pdk::")]
    if pdk_keys:
        # Extract PDK name from pattern like pdk::ihp-sg13g2*
        pdk_name = pdk_keys[0].replace("pdk::", "").rstrip("*")
        result["pdk"] = pdk_name

    return result


def _check_orfs(root: Path) -> dict | None:
    """Check for ORFS config.mk."""
    config = root / "config.mk"
    if not config.is_file():
        return None
    try:
        text = config.read_text()
    except OSError:
        return None
    if "DESIGN_NAME" not in text and "PLATFORM" not in text:
        return None

    result: dict = {"config_path": str(config)}

    # Extract DESIGN_NAME
    import re

    m = re.search(r"DESIGN_NAME\s*[?:]?=\s*(\S+)", text)
    if m:
        result["design"] = m.group(1)
    else:
        result["design"] = root.name

    # Extract PLATFORM
    m = re.search(r"PLATFORM\s*[?:]?=\s*(\S+)", text)
    if m:
        result["platform"] = m.group(1)

    return result
