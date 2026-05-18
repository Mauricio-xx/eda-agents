"""SPARX Six-Port Receiver layout wrapper for eda-agents.

Drives the upstream `iic-jku/SG13G2_SPARX <https://github.com/iic-jku/SG13G2_SPARX>`_
generator from inside ``.venv-gdsfactory`` without reimplementing the
geometry. The wrapper invokes ``scripts/six_port_gen.py`` via
``runpy.run_path`` with a synthesised ``sys.argv`` that mirrors the
upstream Makefile's ``build-layout`` target.

Dependency surface (all must resolve at call time, none at import
time):

* gdsfactory 9.18.1, kfactory 1.x, scikit-rf, jax, ... pulled by the
  iic-jku/IHP package install.
* The iic-jku/IHP gdsfactory wrapper (branch ``IHP-TO``). Currently
  needs three local patches that strip hardcoded ``/foss/pdks`` paths
  so it works outside the IIC-OSIC-TOOLS container.
* The iic-jku/IHP-Open-PDK fork (not the IHP-GmbH mainline), with
  submodules ``pycell4klayout-api`` and ``pypreprocessor`` initialised.
  ``PDK_ROOT`` (or the runner's ``env_extra``) must point at it.

The wrapper deliberately does not import ``gdsfactory`` or ``ihp`` at
module top-level: it must stay importable from the main eda-agents
venv (e.g. for unit tests that mock the runner) where those packages
are not installed. All heavy imports live inside
:func:`build_sparx_six_port`.

Typical use::

    from eda_agents.core.gdsfactory_runner import GdsfactoryRunner
    runner = GdsfactoryRunner()
    result = runner.generate_component(
        component_factory=(
            "eda_agents.topologies.rf.sparx_six_port:build_sparx_six_port"
        ),
        params={"frequency_hz": 60e9, "no_fill": True},
        output_dir="/tmp/sparx_run",
        env_extra={"PDK_ROOT": "/path/to/iic-jku-IHP-Open-PDK"},
    )
    print(result.gds_path, result.top_cell)
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

_SPARX_REPO_ENV = "EDA_AGENTS_SPARX_REPO"
_DEFAULT_SPARX_REPO_CANDIDATES = (
    "/home/montanares/personal_exp/cac2026-reviews/sparx/SG13G2_SPARX",
)


def _resolve_sparx_repo(sparx_repo: str | os.PathLike | None) -> Path:
    """Return the SPARX repository root, raising on missing.

    Priority: explicit argument, ``EDA_AGENTS_SPARX_REPO`` env var, then
    the small list of well-known clone locations. The repository must
    contain ``scripts/six_port_gen.py`` and ``layout/`` to be accepted.
    """
    candidates: list[Path] = []
    if sparx_repo:
        candidates.append(Path(sparx_repo))
    env = os.environ.get(_SPARX_REPO_ENV)
    if env:
        candidates.append(Path(env))
    for path in _DEFAULT_SPARX_REPO_CANDIDATES:
        candidates.append(Path(path))
    for path in candidates:
        if path.is_dir() and (path / "scripts" / "six_port_gen.py").is_file():
            return path.resolve()
    raise FileNotFoundError(
        "SPARX repo not found. Set "
        f"{_SPARX_REPO_ENV} or pass sparx_repo=... pointing at a clone "
        "of https://github.com/iic-jku/SG13G2_SPARX containing "
        "scripts/six_port_gen.py."
    )


def build_sparx_six_port(
    *,
    frequency_hz: float = 60e9,
    output_dir: str | os.PathLike | None = None,
    top_gds_name: str | None = None,
    powdet_gds_name: str = "sparx_powdet_sbd.gds",
    no_fill: bool = True,
    no_fill_m5: bool = True,
    sparx_repo: str | os.PathLike | None = None,
) -> str:
    """Generate one SPARX top-level six-port GDS at ``frequency_hz``.

    Returns the absolute path of the top-level GDS the upstream
    generator wrote. The generator also writes a power-detector
    sub-cell to ``output_dir / powdet_gds_name``; that path is not
    returned but is left in place for downstream consumers.

    Parameters
    ----------
    frequency_hz : float
        Design frequency in Hz. Upstream defaults to ``160e9``; this
        wrapper defaults to ``60e9`` because the lower-frequency
        layouts have larger features and finish faster.
    output_dir : path or None
        Where the GDS files land. When None, falls back to
        ``<sparx_repo>/layout``. The directory is created if missing.
    top_gds_name : str or None
        Output filename for the top-level GDS. Defaults to
        ``sparx<freq_ghz>_top.gds`` matching the upstream convention.
    powdet_gds_name : str
        Output filename for the power-detector sub-cell. Keeps the
        upstream naming so downstream LVS / PEX scripts find it.
    no_fill : bool
        Pass ``--no-fill`` to the upstream generator (skip metal
        fill, much faster for a verification-gate reproduction).
        Default ``True``.
    no_fill_m5 : bool
        Pass ``--no-fill-m5`` to the upstream generator (skip Metal5
        ground fill specifically). Default ``True``.
    sparx_repo : path or None
        Override SPARX clone location; otherwise falls back to
        ``EDA_AGENTS_SPARX_REPO`` then the built-in candidates.
    """
    repo = _resolve_sparx_repo(sparx_repo)
    scripts_dir = repo / "scripts"
    script = scripts_dir / "six_port_gen.py"

    if output_dir is None:
        out_dir = (repo / "layout").resolve()
    else:
        out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if top_gds_name is None:
        top_gds_name = f"sparx{int(round(frequency_hz / 1e9))}_top.gds"

    top_gds = out_dir / top_gds_name
    powdet_gds = out_dir / powdet_gds_name

    # `scripts/six_port_gen.py` imports a sibling `make_gds` module
    # by bare name. runpy.run_path does not auto-add the script's
    # parent to sys.path, so we do it explicitly.
    saved_path = list(sys.path)
    saved_argv = list(sys.argv)
    sys.path.insert(0, str(scripts_dir))
    sys.argv = [
        str(script),
        str(top_gds),
        str(powdet_gds),
        "--frequency",
        str(frequency_hz),
    ]
    if no_fill:
        sys.argv.append("--no-fill")
    if no_fill_m5:
        sys.argv.append("--no-fill-m5")

    try:
        runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = saved_argv
        sys.path[:] = saved_path

    if not top_gds.is_file():
        raise RuntimeError(
            f"SPARX generator ran but produced no GDS at {top_gds}. "
            "Check the upstream argparse contract has not changed."
        )

    return str(top_gds)
