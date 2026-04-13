#!/usr/bin/env python3
"""Validate that the digital RTL-to-GDS environment is ready.

Checks tool availability, design clones, LibreLane discoverability,
and PDK access. Warns on missing optional tools but does not fail
(exit 0 unless a critical component is missing).

Usage:
    python scripts/validate_digital_flow.py
    python scripts/validate_digital_flow.py --design fazyrv_hachure
    python scripts/validate_digital_flow.py --verbose
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DESIGNS_DIR = Path(
    os.environ.get("EDA_AGENTS_DIGITAL_DESIGNS_DIR", "/home/montanares/git")
)

REQUIRED_DESIGNS = {
    "fazyrv_hachure": "gf180mcu-fazyrv-hachure",
    "systolic_mac": "Systolic_MAC_with_DFT",
}

PRECHECK_REPO = "gf180mcu-precheck"


def check_tool(name: str, cmd: list[str] | None = None) -> tuple[bool, str]:
    """Check if a tool is available. Returns (found, version_or_error)."""
    path = shutil.which(name)
    if not path:
        return False, "not found"
    if cmd is None:
        cmd = [name, "--version"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
        )
        version = proc.stdout.strip().split("\n")[0] or proc.stderr.strip().split("\n")[0]
        return True, version
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return True, f"found at {path} (version unknown)"


def check_design(name: str, repo_name: str) -> tuple[bool, str]:
    """Check if a design repo is cloned."""
    design_dir = DESIGNS_DIR / repo_name
    if not design_dir.is_dir():
        return False, f"not found at {design_dir}"
    # Check for config file
    for cfg in ["config.yaml", "config.json"]:
        candidates = list(design_dir.rglob(cfg))
        if candidates:
            return True, f"{design_dir} ({len(candidates)} config(s))"
    return True, f"{design_dir} (no config found, may be OK)"


def check_librelane() -> tuple[bool, str]:
    """Check if LibreLane is discoverable."""
    # Try the known venv path first
    venv_py = "/home/montanares/git/librelane/.venv/bin/python"
    if Path(venv_py).is_file():
        try:
            proc = subprocess.run(
                [venv_py, "-c", "import librelane; print(librelane.__version__)"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                return True, f"venv: {proc.stdout.strip()}"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Try system python
    try:
        proc = subprocess.run(
            ["python3", "-c", "import librelane; print(librelane.__version__)"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return True, f"system: {proc.stdout.strip()}"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Check for nix-shell availability (fazyrv-hachure path)
    fazyrv_dir = DESIGNS_DIR / "gf180mcu-fazyrv-hachure"
    if (fazyrv_dir / "shell.nix").is_file() or (fazyrv_dir / "flake.nix").is_file():
        return True, f"nix-shell at {fazyrv_dir} (v3.0.0.dev45 expected)"

    return False, "not found (install or clone with nix-shell)"


def check_pdk() -> tuple[bool, str]:
    """Check GF180MCU PDK availability."""
    pdk_root = os.environ.get("PDK_ROOT", "")
    if pdk_root:
        gf180_path = Path(pdk_root) / "gf180mcuD"
        if gf180_path.is_dir():
            return True, f"{gf180_path}"

    # Check wafer-space clone
    ws_pdk = DESIGNS_DIR / "wafer-space-gf180mcu"
    if ws_pdk.is_dir():
        return True, f"wafer-space: {ws_pdk}"

    # Check fazyrv's per-project clone
    fazyrv_pdk = DESIGNS_DIR / "gf180mcu-fazyrv-hachure" / "gf180mcu"
    if fazyrv_pdk.is_dir():
        return True, f"fazyrv clone: {fazyrv_pdk}"

    return False, "GF180MCU PDK not found"


def main():
    parser = argparse.ArgumentParser(
        description="Validate digital RTL-to-GDS environment"
    )
    parser.add_argument(
        "--design", choices=list(REQUIRED_DESIGNS) + ["all"],
        default="all",
        help="Which design to check (default: all)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show detailed output",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Digital RTL-to-GDS Environment Validation")
    print("=" * 60)

    warnings = []
    errors = []

    # 1. Tools
    print("\n--- EDA Tools ---")
    tools = [
        ("verilator", None),
        ("yosys", ["/usr/local/bin/yosys", "--version"]),
        ("klayout", ["klayout", "-v"]),
        ("magic", None),
        ("nix-shell", ["nix-shell", "--version"]),
    ]
    for name, cmd in tools:
        found, info = check_tool(name, cmd)
        status = "OK" if found else "WARN"
        if not found:
            warnings.append(f"Tool {name} not found")
        print(f"  {name:15s} [{status}] {info}")

    # 2. LibreLane
    print("\n--- LibreLane ---")
    found, info = check_librelane()
    status = "OK" if found else "ERROR"
    if not found:
        errors.append("LibreLane not found")
    print(f"  librelane       [{status}] {info}")

    # 3. PDK
    print("\n--- PDK ---")
    found, info = check_pdk()
    status = "OK" if found else "WARN"
    if not found:
        warnings.append("GF180MCU PDK not found")
    print(f"  GF180MCU        [{status}] {info}")

    # 4. Designs
    print(f"\n--- Designs (in {DESIGNS_DIR}) ---")
    designs_to_check = (
        REQUIRED_DESIGNS if args.design == "all"
        else {args.design: REQUIRED_DESIGNS[args.design]}
    )
    for name, repo in designs_to_check.items():
        found, info = check_design(name, repo)
        status = "OK" if found else "WARN"
        if not found:
            warnings.append(f"Design {name} not cloned")
        print(f"  {name:15s} [{status}] {info}")

    # 5. Precheck
    print("\n--- Precheck ---")
    precheck_dir = DESIGNS_DIR / PRECHECK_REPO
    if precheck_dir.is_dir():
        print(f"  precheck        [OK] {precheck_dir}")
    else:
        print(f"  precheck        [WARN] not found at {precheck_dir}")
        warnings.append("Precheck repo not cloned")

    # 6. Claude Code CLI (optional)
    print("\n--- Claude Code CLI (optional) ---")
    claude_found, claude_info = check_tool("claude", ["claude", "--version"])
    if not claude_found:
        # Try npm global path
        npm_claude = Path.home() / ".npm-global" / "bin" / "claude"
        if npm_claude.is_file():
            claude_found = True
            claude_info = f"{npm_claude}"
    status = "OK" if claude_found else "SKIP"
    print(f"  claude          [{status}] {claude_info}")

    # Summary
    print("\n" + "=" * 60)
    if errors:
        print(f"ERRORS: {len(errors)}")
        for e in errors:
            print(f"  - {e}")
    if warnings:
        print(f"WARNINGS: {len(warnings)}")
        for w in warnings:
            print(f"  - {w}")
    if not errors and not warnings:
        print("All checks passed.")
    elif not errors:
        print("No critical errors. Some optional tools missing.")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
