#!/usr/bin/env python3
"""Idempotent patcher for iic-jku/IHP (branch IHP-TO) hardcoded paths.

The upstream gdsfactory PCell wrapper was authored against the
IIC-OSIC-TOOLS container, where ``/foss/pdks/ihp-sg13g2/...`` is a
real path. Three spots in the package read that literal without going
through ``PDK_ROOT``:

  * ``ihp/__init__.py``: submodule imports run before ``cells/utils``
    has had a chance to fix ``sys.path``, so the
    ``sg13g2_pycell_lib`` paths must be inserted at the top of the
    main ``__init__``.
  * ``ihp/tech.py``: ``techFilePath`` is built against the hardcoded
    literal even though ``pdk_root`` is already in scope.
  * ``ihp/cells/utils.py``: ``sys.path.append`` lines use the
    literal directly.

Usage::

    python3 scripts/patch_iic_jku_ihp.py /path/to/iic-jku-IHP

This script is idempotent: running it twice on the same clone is a
no-op. It does not touch any other file. ``docs/sparx_rf_pdk_variants.md``
walks through the full native bring-up; this patcher exists so the
patches survive a fresh ``git clone`` plus
``pip install -e ./iic-jku-IHP``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


_INIT_INJECTION = (
    "# Inject sg13g2_pycell_lib paths early so submodule imports below can resolve\n"
    "# `from sg13g2_pycell_lib...` at module load. iic-jku/IHP only adds these\n"
    "# paths inside `ihp.cells.utils`, which loads after the first failing import.\n"
    "import os as _os\n"
    "import sys as _sys\n"
    "\n"
    "_pdk_root = _os.environ.get(\"PDK_ROOT\", \"/foss/pdks\")\n"
    "for _p in (\n"
    "    _os.path.join(_pdk_root, \"ihp-sg13g2/libs.tech/klayout/python\"),\n"
    "    _os.path.join(_pdk_root, \"ihp-sg13g2/libs.tech/klayout/python/pycell4klayout-api/source/python/\"),\n"
    "):\n"
    "    if _p not in _sys.path:\n"
    "        _sys.path.append(_p)\n"
    "\n"
)


def _patch_init(path: Path) -> bool:
    src = path.read_text()
    if "Inject sg13g2_pycell_lib paths early" in src:
        return False
    lines = src.splitlines(keepends=True)
    # Insert directly after the module docstring.
    insert_at = 0
    in_doc = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i == 0 and stripped.startswith('"""'):
            if stripped.count('"""') == 2 and len(stripped) > 3:
                insert_at = 1
                break
            in_doc = True
            continue
        if in_doc and stripped.endswith('"""'):
            insert_at = i + 1
            break
    if insert_at == 0 and not in_doc:
        insert_at = 0
    new = "".join(lines[:insert_at]) + "\n" + _INIT_INJECTION + "".join(lines[insert_at:])
    path.write_text(new)
    return True


def _patch_tech(path: Path) -> bool:
    src = path.read_text()
    old = (
        'techFilePath: str = os.path.join('
        '"/foss/pdks/ihp-sg13g2/libs.tech/klayout/python/sg13g2_pycell_lib/", '
        'jsonTechFile) #TODO hardcoded path, böse'
    )
    new = (
        'techFilePath: str = os.path.join(pdk_root, '
        '"ihp-sg13g2/libs.tech/klayout/python/sg13g2_pycell_lib/", '
        'jsonTechFile)  # patched: was hardcoded /foss/pdks'
    )
    if new in src:
        return False
    if old not in src:
        print(
            f"warning: expected literal not found in {path}; "
            "schema may have drifted",
            file=sys.stderr,
        )
        return False
    path.write_text(src.replace(old, new))
    return True


def _patch_utils(path: Path) -> bool:
    src = path.read_text()
    old = (
        'import sys\n'
        'sys.path.append("/foss/pdks/ihp-sg13g2/libs.tech/klayout/python")\n'
        'sys.path.append("/foss/pdks/ihp-sg13g2/libs.tech/klayout/python/pycell4klayout-api/source/python/")\n'
    )
    new = (
        'import os\n'
        'import sys\n'
        '_pdk_root = os.environ.get("PDK_ROOT", "/foss/pdks")\n'
        'sys.path.append(os.path.join(_pdk_root, "ihp-sg13g2/libs.tech/klayout/python"))\n'
        'sys.path.append(os.path.join(_pdk_root, "ihp-sg13g2/libs.tech/klayout/python/pycell4klayout-api/source/python/"))\n'
    )
    if new in src:
        return False
    if old not in src:
        print(
            f"warning: expected literal not found in {path}; "
            "schema may have drifted",
            file=sys.stderr,
        )
        return False
    path.write_text(src.replace(old, new))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "iic_jku_ihp_root",
        type=Path,
        help="Path to a clone of iic-jku/IHP (branch IHP-TO)",
    )
    args = parser.parse_args()

    root = args.iic_jku_ihp_root.expanduser().resolve()
    if not (root / "ihp" / "__init__.py").is_file():
        print(f"error: {root} does not look like an iic-jku/IHP clone", file=sys.stderr)
        return 2

    targets = [
        ("ihp/__init__.py", _patch_init),
        ("ihp/tech.py", _patch_tech),
        ("ihp/cells/utils.py", _patch_utils),
    ]
    summary = []
    for rel, fn in targets:
        path = root / rel
        if not path.is_file():
            print(f"error: missing {path}", file=sys.stderr)
            return 2
        changed = fn(path)
        summary.append((rel, "patched" if changed else "already up to date"))

    width = max(len(r) for r, _ in summary)
    for rel, status in summary:
        print(f"  {rel:<{width}}  {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
