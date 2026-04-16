"""Verilog-A current-domain primitives for OpenVAF / ngspice.

Each ``.va`` here compiles with ``openvaf`` into an ``.osdi`` shared
object that ngspice loads via ``pre_osdi`` / the transient spiceinit
written by ``SpiceRunner``. Sources are authored from scratch for this
project and are not derived from Arcadia-1 ``veriloga-skills``.

The conventions followed are the eight OpenVAF rules recorded in the
``Verilog-A -> OSDI -> ngspice`` pipeline documentation (see
``CLAUDE.md``): ANSI port declarations with ``electrical``, supplies
parametrised and passed via ``inout``, module-level declarations,
``@(initial_step)`` for init, ``@(cross())`` (where applicable) for
edge detection, outputs driven with ``V(...) <+ ...`` under a
``transition()`` envelope, and ``` `default_transition ``` set where
needed.
"""

from __future__ import annotations

from pathlib import Path

PRIMITIVES_DIR: Path = Path(__file__).resolve().parent


def primitive_path(name: str) -> Path:
    """Return the ``.va`` path for ``name``; raises ``FileNotFoundError``
    if missing."""
    p = PRIMITIVES_DIR / f"{name}.va"
    if not p.is_file():
        raise FileNotFoundError(p)
    return p


__all__ = ["PRIMITIVES_DIR", "primitive_path"]
