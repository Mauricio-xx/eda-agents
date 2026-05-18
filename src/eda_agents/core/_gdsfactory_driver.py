#!/usr/bin/env python3
"""gdsfactory driver script -- runs inside .venv-gdsfactory.

Reads a JSON spec from stdin, resolves a Python callable, calls it,
and writes a JSON result to stdout. Captures stdout/stderr from the
factory itself to a log file so the driver's own JSON line stays
parseable.

Input JSON format::

    {
        "component_factory": "module.path:function",
        "params": {"frequency_hz": 60e9, ...},
        "output_dir": "/abs/path/out",
        "output_gds_name": "sparx60_top.gds"   # optional
    }

The factory callable is resolved by ``importlib.import_module`` then
attribute lookup. Allowed return types:

  - ``gdsfactory.Component`` (any object with a ``write_gds`` method
    and a ``name`` attribute): driver calls ``write_gds`` against
    ``output_dir / output_gds_name`` and uses ``name`` as the top cell.
  - ``str`` or ``pathlib.Path``: treated as the absolute path of an
    already-written GDS. Top cell name is sniffed via ``gdstk``.
  - ``None``: driver scans ``output_dir`` for the newest ``*.gds``
    file written during the factory call; top cell sniffed via
    ``gdstk`` as well.

Output JSON format (single line on stdout)::

    {"success": true, "gds_path": "...", "log_path": "...",
     "top_cell": "...", "lyp_path": null, "run_time_s": 1.23}
    or
    {"success": false, "error": "...", "log_path": "..."}

Invoked by :class:`GdsfactoryRunner` as::

    .venv-gdsfactory/bin/python _gdsfactory_driver.py < spec.json
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
import time
import traceback
from pathlib import Path


def _resolve_callable(component_factory: str):
    if ":" not in component_factory:
        raise ValueError(
            f"component_factory must be 'module:callable', got {component_factory!r}"
        )
    module_path, attr = component_factory.split(":", 1)
    mod = importlib.import_module(module_path)
    fn = getattr(mod, attr, None)
    if fn is None or not callable(fn):
        raise AttributeError(
            f"{component_factory!r}: {attr!r} not found or not callable on {module_path!r}"
        )
    return fn


def _top_cell_of(gds_path: Path) -> str:
    try:
        import gdstk  # type: ignore
    except ImportError:
        return ""
    try:
        lib = gdstk.read_gds(str(gds_path))
    except Exception:
        return ""
    tops = lib.top_level()
    return tops[0].name if tops else ""


def _looks_like_component(obj) -> bool:
    return hasattr(obj, "write_gds") and hasattr(obj, "name")


def _newest_gds_in(directory: Path, since: float) -> Path | None:
    if not directory.is_dir():
        return None
    fresh = [
        p for p in directory.glob("*.gds")
        if p.stat().st_mtime >= since - 0.5
    ]
    if not fresh:
        return None
    return max(fresh, key=lambda p: p.stat().st_mtime)


def main() -> int:
    spec_raw = sys.stdin.read()
    try:
        spec = json.loads(spec_raw)
    except json.JSONDecodeError as exc:
        sys.stdout.write(json.dumps({
            "success": False,
            "error": f"invalid spec JSON: {exc}",
        }) + "\n")
        return 2

    component_factory = spec.get("component_factory")
    params = spec.get("params") or {}
    output_dir_raw = spec.get("output_dir")
    output_gds_name = spec.get("output_gds_name")

    if not component_factory or not output_dir_raw:
        sys.stdout.write(json.dumps({
            "success": False,
            "error": "spec must contain component_factory and output_dir",
        }) + "\n")
        return 2

    output_dir = Path(output_dir_raw).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_dir / (
        (output_gds_name or "gdsfactory_run") + ".log"
    )

    started = time.monotonic()
    t_wall = time.time()

    try:
        fn = _resolve_callable(component_factory)
    except Exception as exc:
        log_path.write_text(traceback.format_exc())
        sys.stdout.write(json.dumps({
            "success": False,
            "error": f"resolve_callable: {exc}",
            "log_path": str(log_path),
        }) + "\n")
        return 1

    log_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(log_buf), contextlib.redirect_stderr(log_buf):
            result = fn(**params)
    except Exception as exc:
        log_buf.write("\n--- traceback ---\n")
        log_buf.write(traceback.format_exc())
        log_path.write_text(log_buf.getvalue())
        sys.stdout.write(json.dumps({
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "log_path": str(log_path),
        }) + "\n")
        return 1

    log_path.write_text(log_buf.getvalue())

    gds_path: Path | None = None
    top_cell = ""

    if _looks_like_component(result):
        if not output_gds_name:
            output_gds_name = f"{getattr(result, 'name', 'top') or 'top'}.gds"
        gds_path = output_dir / output_gds_name
        try:
            result.write_gds(str(gds_path))
        except Exception as exc:
            sys.stdout.write(json.dumps({
                "success": False,
                "error": f"write_gds: {type(exc).__name__}: {exc}",
                "log_path": str(log_path),
            }) + "\n")
            return 1
        top_cell = getattr(result, "name", "") or _top_cell_of(gds_path)
    elif isinstance(result, (str, Path)):
        gds_path = Path(str(result)).expanduser().resolve()
        if not gds_path.is_file():
            sys.stdout.write(json.dumps({
                "success": False,
                "error": f"factory returned path {gds_path}, but file is missing",
                "log_path": str(log_path),
            }) + "\n")
            return 1
        top_cell = _top_cell_of(gds_path)
    elif result is None:
        candidate = _newest_gds_in(output_dir, t_wall)
        if candidate is None:
            sys.stdout.write(json.dumps({
                "success": False,
                "error": (
                    f"factory returned None and no fresh .gds was written "
                    f"to {output_dir}"
                ),
                "log_path": str(log_path),
            }) + "\n")
            return 1
        gds_path = candidate
        top_cell = _top_cell_of(gds_path)
    else:
        sys.stdout.write(json.dumps({
            "success": False,
            "error": (
                f"factory returned {type(result).__name__}; expected "
                "gdsfactory.Component, str/Path, or None"
            ),
            "log_path": str(log_path),
        }) + "\n")
        return 1

    elapsed = time.monotonic() - started

    sys.stdout.write(json.dumps({
        "success": True,
        "gds_path": str(gds_path),
        "log_path": str(log_path),
        "lyp_path": None,
        "top_cell": top_cell,
        "run_time_s": elapsed,
    }) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
