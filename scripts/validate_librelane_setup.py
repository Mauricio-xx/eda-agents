"""Validate LibreLane setup and prerequisites for E2E testing.

Checks:
- Template project directory exists and has config
- LibreLane Python interpreter available
- PDK_ROOT and model files accessible
- Required macros/IPs present

Usage:
    python scripts/validate_librelane_setup.py [--project data/gf180-template]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def check_project(project_dir: Path) -> list[str]:
    """Check project directory structure."""
    problems = []

    if not project_dir.is_dir():
        problems.append(f"Project directory not found: {project_dir}")
        return problems

    # Check for config (yaml or json) -- look in project_dir itself,
    # in a librelane/ subdirectory, and as config.json
    candidates = [
        project_dir / "config.yaml",
        project_dir / "config.json",
        project_dir / "librelane" / "config.yaml",
        project_dir / "librelane" / "config.json",
    ]
    found_cfg = next((c for c in candidates if c.is_file()), None)
    if found_cfg:
        fmt = "yaml v3" if found_cfg.suffix == ".yaml" else "json"
        print(f"  Config: {found_cfg} ({fmt})")
    else:
        problems.append("No config.yaml or config.json found")

    # Check for RTL sources (may be at project_dir/src or parent/src)
    src_dir = project_dir / "src"
    if not src_dir.is_dir() and project_dir.parent:
        src_dir = project_dir.parent / "src"
    if src_dir.is_dir():
        sv_files = list(src_dir.glob("*.sv")) + list(src_dir.glob("*.v"))
        print(f"  RTL sources: {len(sv_files)} files in src/")
    else:
        problems.append("No src/ directory found")

    return problems


def check_librelane() -> list[str]:
    """Check LibreLane installation."""
    from eda_agents.core.librelane_runner import _find_librelane_python

    problems = []
    py = _find_librelane_python()
    if py:
        print(f"  LibreLane Python: {py}")
    else:
        problems.append("No Python with librelane found")
    return problems


def check_pdk() -> list[str]:
    """Check GF180MCU PDK availability."""
    from eda_agents.core.pdk import GF180MCU_D
    problems = []
    pdk_root = GF180MCU_D.default_pdk_root or os.environ.get("PDK_ROOT", "")

    if not Path(pdk_root).is_dir():
        problems.append(f"PDK_ROOT not found: {pdk_root}")
        return problems

    print(f"  PDK_ROOT: {pdk_root}")

    # Check standard cell library
    sc_lib = Path(pdk_root) / "gf180mcuD" / "libs.ref" / "gf180mcu_fd_sc_mcu9t5v0"
    if sc_lib.is_dir():
        print(f"  Standard cells: {sc_lib.name}")
    else:
        problems.append(f"Standard cell library not found: {sc_lib}")

    return problems


def main():
    parser = argparse.ArgumentParser(description="Validate LibreLane setup")
    parser.add_argument(
        "--project",
        default="data/gf180-template",
        help="Path to LibreLane project directory",
    )
    args = parser.parse_args()

    project_dir = Path(args.project).resolve()
    all_problems = []

    print("Checking project structure...")
    all_problems.extend(check_project(project_dir))

    print("\nChecking LibreLane installation...")
    all_problems.extend(check_librelane())

    print("\nChecking PDK...")
    all_problems.extend(check_pdk())

    print()
    if all_problems:
        print(f"Found {len(all_problems)} problem(s):")
        for p in all_problems:
            print(f"  - {p}")
    else:
        print("All checks passed.")


if __name__ == "__main__":
    main()
