"""XSPICE voltage-domain primitives built in-house for eda-agents.

These are plain C code-model sources (``.c`` / ``.mod`` + ``.ifs``)
consumed by ``eda_agents.core.stages.xspice_compile.XSpiceCompiler``.
The primitives fill the gap where pure Verilog-A (current-domain)
cannot express level-sensitive or event-driven behaviour for ngspice —
for that, ngspice needs an XSPICE shared object.

Each primitive directory contains:

  - ``cfunc.mod`` — the C body, preprocessed by ``cmpp -mod``.
  - ``ifspec.ifs`` — the interface spec, preprocessed by ``cmpp -ifs``.

All sources in this package are authored from scratch for this project;
they do not incorporate code from the Arcadia-1 ``veriloga-skills`` or
EVAS repositories.
"""

from __future__ import annotations

from pathlib import Path

PRIMITIVES_DIR: Path = Path(__file__).resolve().parent


def primitive_paths(name: str) -> tuple[Path, Path]:
    """Return the ``(cfunc.mod, ifspec.ifs)`` pair for ``name``.

    Raises ``FileNotFoundError`` if either file is missing.
    """
    mod = PRIMITIVES_DIR / name / "cfunc.mod"
    ifs = PRIMITIVES_DIR / name / "ifspec.ifs"
    for p in (mod, ifs):
        if not p.is_file():
            raise FileNotFoundError(p)
    return mod, ifs


__all__ = ["PRIMITIVES_DIR", "primitive_paths"]
