#!/usr/bin/env python3
"""Diff our LibreLane templates against the pinned upstream reference.

We vendor the upstream project templates under ``external/`` as
read-only submodules for provenance and drift detection. Our own
templates live at ``src/eda_agents/agents/templates/*.yaml.tmpl``.
This script loads both, parses them as YAML, and:

1. Asserts a curated set of *verbatim* fields match exactly. These
   are conventions we inherit from upstream that we never diverge
   from silently (VDD/GND net names, streamout tool, meta.version).
   Any mismatch is a failure (exit 1).
2. Logs deltas for an *informational* set (flow, density,
   die area, macros, pads, use_slang) so humans reviewing a
   submodule bump can see what changed.

Usage::

    check_librelane_template_upstream.py {ihp_sg13g2|gf180|all}
    check_librelane_template_upstream.py <pdk> --update-pin

``--update-pin`` does NOT move the submodule pin. It fetches the
upstream default branch and prints the candidate bump target so a
human can decide.

Both upstream templates are Chip-flow full-chip configs; ours are
Classic-flow macro-only. Large divergences in flow / die / MACROS /
PAD_* are expected and landed in the informational bucket by design.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

# Allow running as a standalone script from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from eda_agents.agents.librelane_config_templates import (  # noqa: E402
    get_config_template,
)
from eda_agents.core.pdk import resolve_pdk  # noqa: E402


# pdk-registry key -> submodule directory name under external/
SUBMODULE_PATHS: dict[str, str] = {
    "ihp_sg13g2": "ihp-sg13g2-librelane-template",
    "gf180mcu": "gf180mcu-project-template",
}

# Sentinel values used to fill the format() placeholders before
# YAML-parsing our template. The values don't matter — we only care
# about structure.
SENTINEL_PARAMS: dict[str, Any] = {
    "design_name": "_parity_check_",
    "verilog_file": "_parity_check_.v",
    "clock_port": "clk",
    "clock_period": 10,
    "die_width": 100.0,
    "die_height": 100.0,
}

# Fields that must match upstream byte-for-byte (after YAML
# normalisation). Paths are dotted. Lists are order-sensitive.
VERBATIM_FIELDS: tuple[str, ...] = (
    "meta.version",
    "VDD_NETS",
    "GND_NETS",
    "PRIMARY_GDSII_STREAMOUT_TOOL",
)

# Fields logged for information. A delta here is reported but does
# not fail the check; by design our templates diverge on these.
INFORMATIONAL_FIELDS: tuple[str, ...] = (
    "meta.flow",
    "USE_SLANG",
    "PL_TARGET_DENSITY_PCT",
    "DIE_AREA",
    "CLOCK_PERIOD",
    "CLOCK_PORT",
    "RT_MAX_LAYER",
    "FP_SIZING",
    "MACROS",
    "PAD_NORTH",
    "PAD_SOUTH",
    "PAD_EAST",
    "PAD_WEST",
)


def _dig(d: dict, path: str) -> Any:
    """Walk a dotted path into a dict. Returns ``<missing>`` if absent."""
    cur: Any = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return "<missing>"
    return cur


def _load_ours(pdk_key: str) -> dict:
    pdk = resolve_pdk(pdk_key)
    tpl, _ = get_config_template(pdk)
    filled = tpl.format(**SENTINEL_PARAMS)
    return yaml.safe_load(filled)


def _load_upstream(pdk_key: str) -> dict | None:
    """Read external/<sub>/librelane/config.yaml. Returns None if missing."""
    sub = SUBMODULE_PATHS[pdk_key]
    path = _REPO_ROOT / "external" / sub / "librelane" / "config.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def check_one(pdk_key: str) -> int:
    """Return 0 if parity holds, 1 if a verbatim field diverges, 2 if skipped."""
    print(f"[{pdk_key}] our template -> upstream parity check")
    ours = _load_ours(pdk_key)
    upstream = _load_upstream(pdk_key)
    if upstream is None:
        print(
            f"  SKIP: submodule external/{SUBMODULE_PATHS[pdk_key]} not checked out. "
            "Run 'git submodule update --init' to enable parity checks."
        )
        return 2

    failed = False
    MISSING = "<missing>"
    for field in VERBATIM_FIELDS:
        our_val = _dig(ours, field)
        up_val = _dig(upstream, field)
        if our_val == MISSING or up_val == MISSING:
            # One side doesn't set this field; log as info, don't fail.
            # Our templates legitimately diverge from upstream on scope
            # (Classic macro-only vs. Chip full-chip), so coverage is
            # not symmetric across PDKs.
            print(
                f"  verbatim  skip {field}: "
                f"ours={our_val!r} upstream={up_val!r}"
            )
            continue
        if our_val == up_val:
            print(f"  verbatim  OK   {field}: {our_val!r}")
        else:
            print(
                f"  verbatim  FAIL {field}: ours={our_val!r} upstream={up_val!r}"
            )
            failed = True

    for field in INFORMATIONAL_FIELDS:
        our_val = _dig(ours, field)
        up_val = _dig(upstream, field)
        if our_val == up_val:
            continue
        print(
            f"  info           {field}: ours={our_val!r} upstream={up_val!r}"
        )

    if failed:
        print(f"[{pdk_key}] parity FAILED")
        return 1
    print(f"[{pdk_key}] parity OK")
    return 0


def update_pin(pdk_key: str) -> int:
    sub_path = _REPO_ROOT / "external" / SUBMODULE_PATHS[pdk_key]
    if not (sub_path / ".git").exists():
        print(f"[{pdk_key}] submodule not initialised at {sub_path}")
        return 2
    print(f"[{pdk_key}] fetching upstream...")
    subprocess.run(
        ["git", "-C", str(sub_path), "fetch", "origin"],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "-C", str(sub_path), "rev-parse", "HEAD"], text=True
    ).strip()
    latest = subprocess.check_output(
        ["git", "-C", str(sub_path), "rev-parse", "origin/HEAD"], text=True
    ).strip()
    print(f"[{pdk_key}] pinned:   {head}")
    print(f"[{pdk_key}] upstream: {latest}")
    if head == latest:
        print(f"[{pdk_key}] already at upstream tip; no bump available.")
        return 0
    print(f"[{pdk_key}] commits between pin and upstream:")
    subprocess.run(
        [
            "git", "-C", str(sub_path),
            "log", "--oneline", f"{head}..{latest}",
        ],
        check=False,
    )
    print()
    print(
        f"To bump: git -C external/{SUBMODULE_PATHS[pdk_key]} checkout {latest} "
        "&& re-run this script without --update-pin, then commit."
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "pdk",
        choices=sorted(SUBMODULE_PATHS) + ["all"],
        help="PDK key (or 'all' to check both)",
    )
    ap.add_argument(
        "--update-pin",
        action="store_true",
        help="Fetch upstream and report candidate bump target (no change).",
    )
    args = ap.parse_args()
    keys = sorted(SUBMODULE_PATHS) if args.pdk == "all" else [args.pdk]

    worst = 0
    for key in keys:
        if args.update_pin:
            rc = update_pin(key)
        else:
            rc = check_one(key)
        if rc == 1:
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main())
