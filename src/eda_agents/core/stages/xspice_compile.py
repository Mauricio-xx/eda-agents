"""XSPICE code model (``.cm``) compilation stage runner.

XSPICE code models are C-coded primitives that ngspice loads as shared
objects at runtime via the ``codemodel`` command. This runner wraps the
three-step build:

  1. ``cmpp -lst`` to generate the descriptor headers (``cmextrn.h``,
     ``cminfo.h``, ``cminfo2.h``, ``udnextrn.h``, ``udninfo.h``,
     ``udninfo2.h``, ``objects.inc``) from ``modpath.lst`` +
     ``udnpath.lst``.
  2. Per-model ``cmpp -ifs`` / ``cmpp -mod`` to turn ``ifspec.ifs`` /
     ``cfunc.mod`` into ``ifspec.c`` / ``cfunc.c``.
  3. ``cc -c`` on ``dlmain.c``, ``dstring.c``, and each model's
     ``cfunc.c`` / ``ifspec.c``; then ``cc -shared`` to link the ``.cm``.

We do **not** bundle the ngspice source tree — too large, wrong license
to vendor. Instead the compiler requires a pointer at an installed or
built ngspice checkout (``ngspice_src_dir``) that provides ``cmpp``,
``include/ngspice/*.h``, ``src/misc/dstring.c``, and ``src/xspice/icm/
dlmain.c``. When ``None`` we look up a few known locations and skip
with ``error`` set if none found (so tests guard cleanly on missing
toolchain).

ngspice autoloads its bundled ``.cm`` set at startup. Do **not** emit a
``codemodel`` line for those bundled libraries — it segfaults on
duplicate registration. Emit ``codemodel`` only for user-built ``.cm``
paths returned by this runner.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from eda_agents.core.flow_stage import FlowStage, StageResult

logger = logging.getLogger(__name__)


# Paths we probe for an ngspice source checkout that has cmpp built and
# the headers available in-tree. Users can override via the
# ``NGSPICE_SRC_DIR`` environment variable or the constructor argument.
_DEFAULT_SRC_CANDIDATES = (
    "/home/montanares/personal_exp/ai-ihp-demo/ngspice/ngspice-ngspice",
    "/usr/local/src/ngspice",
    "/usr/src/ngspice",
)


@dataclass(frozen=True)
class XSpiceToolchain:
    """Resolved paths for building XSPICE code models."""

    cmpp: Path
    include_dirs: tuple[Path, ...]
    dlmain_c: Path
    dstring_c: Path

    @property
    def ok(self) -> bool:
        return (
            self.cmpp.is_file()
            and self.dlmain_c.is_file()
            and self.dstring_c.is_file()
            and all(d.is_dir() for d in self.include_dirs)
        )


def _discover_toolchain(src_dir: Path | None) -> XSpiceToolchain | None:
    """Locate ``cmpp``, headers, and support sources inside an ngspice
    source tree. Returns ``None`` if nothing viable is found."""
    candidates: list[Path] = []
    env = os.environ.get("NGSPICE_SRC_DIR")
    if env:
        candidates.append(Path(env))
    if src_dir is not None:
        candidates.append(Path(src_dir))
    for p in _DEFAULT_SRC_CANDIDATES:
        candidates.append(Path(p))

    for base in candidates:
        if not base.is_dir():
            continue
        cmpp_release = base / "release/src/xspice/cmpp/cmpp"
        cmpp_src = base / "src/xspice/cmpp/cmpp"
        cmpp = cmpp_release if cmpp_release.is_file() else (
            cmpp_src if cmpp_src.is_file() else None
        )
        if cmpp is None:
            continue
        include_dirs: list[Path] = []
        for sub in ("src/include", "release/src/include"):
            d = base / sub
            if d.is_dir():
                include_dirs.append(d)
        if not include_dirs:
            continue
        dlmain = base / "src/xspice/icm/dlmain.c"
        dstring = base / "src/misc/dstring.c"
        if not dlmain.is_file() or not dstring.is_file():
            continue
        return XSpiceToolchain(
            cmpp=cmpp,
            include_dirs=tuple(include_dirs),
            dlmain_c=dlmain,
            dstring_c=dstring,
        )
    return None


@dataclass
class CodeModelSource:
    """One XSPICE primitive expressed as a ``cfunc.mod`` + ``ifspec.ifs``
    pair. ``name`` becomes the subdirectory in the build tree and should
    match the primitive's ``Spice_Model_Name`` in the ifs file."""

    name: str
    cfunc_mod: Path
    ifspec_ifs: Path
    extra_sources: tuple[Path, ...] = field(default_factory=tuple)


class XSpiceCompiler:
    """Compile a bundle of XSPICE primitives into a single ``.cm``.

    Parameters
    ----------
    ngspice_src_dir : str or Path, optional
        Explicit ngspice source tree root. Falls back to
        ``$NGSPICE_SRC_DIR`` and then to
        ``_DEFAULT_SRC_CANDIDATES``.
    cc : str
        C compiler binary. Default ``gcc``.
    timeout_s : int
        Wall-clock cap on the whole build. Default 180.
    """

    def __init__(
        self,
        ngspice_src_dir: str | Path | None = None,
        cc: str = "gcc",
        timeout_s: int = 180,
    ):
        self._explicit_src = Path(ngspice_src_dir) if ngspice_src_dir else None
        self.cc = cc
        self.timeout_s = timeout_s

    def toolchain(self) -> XSpiceToolchain | None:
        """Resolve the toolchain. ``None`` if no viable checkout found."""
        return _discover_toolchain(self._explicit_src)

    def available(self) -> bool:
        tc = self.toolchain()
        if tc is None or not tc.ok:
            return False
        if not shutil.which(self.cc):
            return False
        return True

    def compile(
        self,
        sources: list[CodeModelSource],
        out_path: str | Path,
        work_dir: str | Path | None = None,
    ) -> StageResult:
        """Build a single ``.cm`` from ``sources``.

        ``out_path`` is where the final shared object is written.
        ``work_dir`` is an intermediate scratch area (created if
        missing); default is ``out_path.parent / "_xspice_build"``.
        """
        out_path = Path(out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if work_dir is None:
            build = out_path.parent / "_xspice_build"
        else:
            build = Path(work_dir).resolve()
        build.mkdir(parents=True, exist_ok=True)

        if not sources:
            return StageResult(
                stage=FlowStage.XSPICE_COMPILE,
                success=False,
                error="No XSPICE sources provided.",
            )

        tc = self.toolchain()
        if tc is None or not tc.ok:
            return StageResult(
                stage=FlowStage.XSPICE_COMPILE,
                success=False,
                error=(
                    "XSPICE toolchain unavailable (need ngspice source "
                    "tree with built cmpp and in-tree headers; set "
                    "NGSPICE_SRC_DIR or pass ngspice_src_dir=...)."
                ),
            )
        if not shutil.which(self.cc):
            return StageResult(
                stage=FlowStage.XSPICE_COMPILE,
                success=False,
                error=f"C compiler {self.cc!r} not found on PATH.",
            )

        t0 = time.monotonic()
        try:
            cm_path = self._run_build(tc, sources, build, out_path)
        except _BuildError as err:
            return StageResult(
                stage=FlowStage.XSPICE_COMPILE,
                success=False,
                error=str(err),
                log_tail=err.log_tail,
                run_time_s=time.monotonic() - t0,
            )
        except subprocess.TimeoutExpired:
            return StageResult(
                stage=FlowStage.XSPICE_COMPILE,
                success=False,
                error=f"XSPICE build timed out ({self.timeout_s}s)",
                run_time_s=time.monotonic() - t0,
            )

        elapsed = time.monotonic() - t0
        if not cm_path.is_file():
            return StageResult(
                stage=FlowStage.XSPICE_COMPILE,
                success=False,
                error=f"XSPICE build reported success but {cm_path} missing.",
                run_time_s=elapsed,
            )
        return StageResult(
            stage=FlowStage.XSPICE_COMPILE,
            success=True,
            artifacts={"cm": cm_path, "build_dir": build},
            run_time_s=elapsed,
        )

    def _run_build(
        self,
        tc: XSpiceToolchain,
        sources: list[CodeModelSource],
        build: Path,
        out_path: Path,
    ) -> Path:
        # Stage 1 — lay out the build directory.
        for src in sources:
            if not src.cfunc_mod.is_file():
                raise _BuildError(f"cfunc.mod missing: {src.cfunc_mod}")
            if not src.ifspec_ifs.is_file():
                raise _BuildError(f"ifspec.ifs missing: {src.ifspec_ifs}")
            dst = build / src.name
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "cfunc.mod").write_bytes(src.cfunc_mod.read_bytes())
            (dst / "ifspec.ifs").write_bytes(src.ifspec_ifs.read_bytes())

        modpath = build / "modpath.lst"
        modpath.write_text("\n".join(s.name for s in sources) + "\n")
        udnpath = build / "udnpath.lst"
        udnpath.write_text("")

        env = os.environ.copy()
        env["CMPP_IDIR"] = "."
        env["CMPP_ODIR"] = "."

        # Stage 2 — descriptor headers (cminfo.h, cmextrn.h, ...).
        self._exec([str(tc.cmpp), "-lst"], cwd=build, env=env)

        # Stage 3 — per-model ifs + mod expansion.
        for src in sources:
            mod_cwd = build / src.name
            mod_env = env.copy()
            self._exec([str(tc.cmpp), "-ifs"], cwd=mod_cwd, env=mod_env)
            self._exec([str(tc.cmpp), "-mod"], cwd=mod_cwd, env=mod_env)

        # Stage 4 — compile.
        includes: list[str] = ["-I."]
        for d in tc.include_dirs:
            includes.extend(["-I", str(d)])
        cflags = [
            "-O2",
            "-fPIC",
            "-fvisibility=hidden",
            "-std=gnu11",
            "-Wall",
        ]
        objs: list[Path] = []

        def _compile_one(src_c: Path, obj: Path) -> None:
            self._exec(
                [self.cc, *cflags, *includes, "-c", str(src_c), "-o", str(obj)],
                cwd=build,
            )
            objs.append(obj)

        # Copy shared sources locally so header include paths stay simple.
        dlmain_local = build / "dlmain.c"
        dstring_local = build / "dstring.c"
        dlmain_local.write_bytes(tc.dlmain_c.read_bytes())
        dstring_local.write_bytes(tc.dstring_c.read_bytes())
        _compile_one(dstring_local, build / "dstring.o")
        _compile_one(dlmain_local, build / "dlmain.o")

        for src in sources:
            mod_cwd = build / src.name
            _compile_one(mod_cwd / "cfunc.c", mod_cwd / "cfunc.o")
            _compile_one(mod_cwd / "ifspec.c", mod_cwd / "ifspec.o")
            for extra in src.extra_sources:
                if not extra.is_file():
                    raise _BuildError(f"extra source missing: {extra}")
                extra_local = mod_cwd / extra.name
                extra_local.write_bytes(extra.read_bytes())
                _compile_one(extra_local, extra_local.with_suffix(".o"))

        # Stage 5 — link.
        link_cmd = [
            self.cc,
            "-shared",
            "-fvisibility=hidden",
            *[str(o) for o in objs],
            "-lm",
            "-o",
            str(out_path),
        ]
        self._exec(link_cmd, cwd=build)
        return out_path

    def _exec(
        self,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> None:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env if env is not None else os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or "") + (proc.stdout or "")
            raise _BuildError(
                f"command failed (exit {proc.returncode}): {' '.join(cmd)}",
                log_tail=tail[-2000:],
            )


class _BuildError(Exception):
    def __init__(self, msg: str, log_tail: str = "") -> None:
        super().__init__(msg)
        self.log_tail = log_tail


def load_codemodel_line(cm_path: str | Path) -> str:
    """Return the ngspice ``.spiceinit`` line that pre-loads ``cm_path``.

    ngspice's ``codemodel`` command takes a single absolute path and
    must fire before the netlist is parsed so A-devices can resolve
    their model types. Place this line into the cwd ``.spiceinit``
    (the ``SpiceRunner`` does this automatically when
    ``extra_codemodel`` is set).
    """
    return f"codemodel {Path(cm_path).resolve()}"


__all__ = [
    "CodeModelSource",
    "XSpiceCompiler",
    "XSpiceToolchain",
    "load_codemodel_line",
]
