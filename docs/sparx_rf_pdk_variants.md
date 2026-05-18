# SPARX RF: PDK variants and native bring-up

SPARX is the parametric Six-Port Receiver generator from JKU (CAC2026
#10, `iic-jku/SG13G2_SPARX`). It opens the first RF / mm-wave vertical
inside eda-agents: a gdsfactory backend, an IHP-only PDK story, and a
dependency chain that is currently tied to the IIC-OSIC-TOOLS Docker
container. This document captures the variants we depend on, the
patches required for a native (non-container) bring-up, and why GF180
does not get a port.

## Upstream dependency graph

SPARX itself is just generator scripts plus a Makefile. The substance
sits in three repos that have to align:

1. `iic-jku/SG13G2_SPARX` (branch `main`, SHA `28ca9cc1` pinned for
   Session 1): the generator. Pure Python (`scripts/six_port_gen.py`),
   licensed Solderpad-2.1 with Apache-2.0 fallback. Reads design
   constants at module top-level and writes its GDS via
   `gf.Component.write_gds()`.
2. `iic-jku/IHP` on branch `IHP-TO`: the gdsfactory PCell wrapper that
   exposes IHP cells as `gf.Component` factories (`ihp.PDK.activate()`).
   This is the package SPARX imports as `import ihp`. It pip-installs
   from a clone and pins gdsfactory to 9.18.x.
3. `iic-jku/IHP-Open-PDK` (a JKU fork of `IHP-GmbH/IHP-Open-PDK`): the
   PDK proper. Ships the same `ihp-sg13g2/libs.tech/klayout/python/`
   layout as upstream, but adds modules the iic-jku PCell wrapper
   needs and that mainline does not yet provide.

The README of SPARX states both 2 and 3 as requirements. The README
also points at `IIC-OSIC-TOOLS` tag `2026.05+`, which bundles all of
the above plus the rest of the open-source EDA toolchain.

## Why iic-jku/IHP-Open-PDK and not the mainline

The `ihp.cells.passives` module imports
`sg13g2_pycell_lib.ihp.guard_ring_code`. That module is present in
`iic-jku/IHP-Open-PDK` but not in `IHP-GmbH/IHP-Open-PDK` at our
working tip of the `dev` branch. The same imbalance applies to a
handful of RF primitives the JKU team has authored ahead of the
mainline. Until those land upstream, the gdsfactory PCell wrapper
cannot resolve its imports against mainline.

For Session 5 we point `PDK_ROOT` at a shallow clone of
`iic-jku/IHP-Open-PDK` and initialise its `klayout/python/` submodules
(`pycell4klayout-api`, `pypreprocessor`). The JKU fork is layered on
top of the mainline, so once the upstreams converge we can drop the
fork dependency and run against the mainline plus our existing
`PDK_ROOT`.

## Hardcoded paths in iic-jku/IHP

The IHP-TO branch of `iic-jku/IHP` was authored against the
IIC-OSIC-TOOLS Docker container, where `/foss/pdks/ihp-sg13g2/...`
is a real path. Native installs trip over three spots that read this
path without going through `PDK_ROOT`:

* `ihp/tech.py` line ~315 builds `techFilePath` against the hardcoded
  `/foss/pdks/...` prefix (the author flags it with `#TODO hardcoded
  path, böse`).
* `ihp/cells/utils.py` adds `/foss/pdks/...` entries to `sys.path`
  unconditionally.
* `ihp/__init__.py` imports `cells` before `cells/utils` has had a
  chance to fix `sys.path`, so even if `utils.py` is patched the
  import order is still wrong for native use.

Our local clone at `cac2026-reviews/sparx/iic-jku-IHP/` is
pip-installed editable into `.venv-gdsfactory`, with three local
patches applied:

1. `ihp/__init__.py`: prepend a small block that reads `PDK_ROOT`
   from the environment and inserts the `sg13g2_pycell_lib` paths into
   `sys.path` before any submodule import.
2. `ihp/tech.py`: replace the `/foss/pdks` literal in `techFilePath`
   with the `pdk_root` variable already defined earlier in the file.
3. `ihp/cells/utils.py`: read `PDK_ROOT` once and append the same two
   paths via `os.path.join`.

The patches are mechanical and good candidates for an upstream PR; we
keep them local for the duration of Session 5 and track upstreaming
as a follow-up. The patches sit on the local editable clone, so an
unmodified `pip install ./iic-jku-IHP` against a freshly cloned
fork would have to re-apply them.

## Native bring-up cheat sheet

```bash
# 1) gdsfactory venv next to the worktree
python3 -m venv .venv-gdsfactory
.venv-gdsfactory/bin/pip install --upgrade pip

# 2) iic-jku/IHP gdsfactory adapter on branch IHP-TO
git clone -b IHP-TO https://github.com/iic-jku/IHP.git iic-jku-IHP
# apply the three patches from this document
.venv-gdsfactory/bin/pip install -e ./iic-jku-IHP

# 3) iic-jku/IHP-Open-PDK fork with its klayout/python submodules
git clone https://github.com/iic-jku/IHP-Open-PDK.git IHP-Open-PDK-iic-jku
cd IHP-Open-PDK-iic-jku
git submodule update --init --recursive \
    ihp-sg13g2/libs.tech/klayout/python/
cd -

# 4) SPARX clone, pinned in our Session 1 review log
git clone https://github.com/iic-jku/SG13G2_SPARX.git \
    cac2026-reviews/sparx/SG13G2_SPARX
git -C cac2026-reviews/sparx/SG13G2_SPARX checkout 28ca9cc1

# 5) Drive everything from PDK_ROOT
export PDK_ROOT=$(pwd)/IHP-Open-PDK-iic-jku
export EDA_AGENTS_SPARX_REPO=$(pwd)/cac2026-reviews/sparx/SG13G2_SPARX
```

After that, `eda_agents.core.gdsfactory_runner.GdsfactoryRunner` plus
`eda_agents.topologies.rf.sparx_six_port:build_sparx_six_port` will
generate a SPARX layout at any frequency the upstream supports.

## Why GF180 is not in scope

GF180MCU does not ship the high-fT primitives SPARX needs at the
target frequencies (60 GHz floor, 160 GHz default, up to 300 GHz).
There is no Schottky barrier diode in GF180, no TopMetal2 routing
analogue for the RF traces, and the transit frequency of the
3.3 V devices is far below SPARX's design points. We acknowledge this
gap rather than working around it: the RF vertical inside eda-agents
will stay IHP-only until either GF180 grows the missing primitives or
a different RF-friendly PDK is integrated. SPARX, the generator
itself, is process-agnostic only in its software; the physics is not.

## Upstream contribution targets

Captured here so they survive the session boundary. None of these are
blocking, but they collapse the fork dependency and make native use
the default story:

* Send the three `iic-jku/IHP` patches upstream (one PR, mechanical).
* Track the mainline `IHP-GmbH/IHP-Open-PDK` `dev` branch for the
  `guard_ring_code` and RF-primitive additions; when those land the
  `iic-jku/IHP-Open-PDK` fork can fall away.
* Once both fall away, drop `EDA_AGENTS_IHP_RF_PDK_ROOT` from the
  Session 5 prompt and key `PDK_ROOT` directly to the mainline clone.
