# Docker images for eda-agents

## `xspice` — XSPICE toolchain container (ngspice-45 + OpenVAF 23.5.0)

### What it is

A pinned ngspice-45 source build with ``cmpp`` + in-tree headers,
plus OpenVAF 23.5.0, inside a minimal ``ubuntu:24.04`` base. Exists
because building XSPICE ``.cm`` code models needs an ngspice source
tree with ``cmpp`` compiled — a dependency we do not assume is
present on the host.

The image does **not** ship any PDK (IHP SG13G2 or GF180MCU). XSPICE
primitives are orthogonal to the PDK. When running tests that need a
PDK, bind-mount ``$PDK_ROOT`` at runtime.

### Build

Single command, takes 5-10 min on a cold run:

```bash
docker build -f docker/xspice.Dockerfile -t eda-agents-xspice:ng45 .
```

The wrapper below builds lazily on first use, so you don't need to
run this by hand.

### Run

```bash
# Interactive shell inside the container
scripts/xspice_docker.sh

# Run the xspice test suite
scripts/xspice_docker.sh pytest -m xspice tests/test_xspice_primitives.py

# Run the veriloga suite (OpenVAF current-domain primitives)
scripts/xspice_docker.sh pytest -m veriloga tests/test_veriloga_current_primitives.py

# Ad-hoc ngspice invocation
scripts/xspice_docker.sh ngspice --version
```

The wrapper bind-mounts the repo root at ``/work`` and sets
``PYTHONPATH=/work/src`` so ``import eda_agents`` resolves to the
bind-mounted sources. ``eda-agents`` is installed editable on first
container start.

### Environment variables

- ``XSPICE_IMAGE`` — override the image tag (default
  ``eda-agents-xspice:ng45``).
- ``XSPICE_REBUILD=1`` — force ``docker build`` even if the image
  already exists. Use after editing ``xspice.Dockerfile``.

### Why pinned versions

- **ngspice-45**: matches what the host usually carries (see
  ``ngspice --version``). ``XSpiceCompiler`` verifies that the
  toolchain probe resolves to a consistent in-tree layout; version
  skew between host and container produces hard-to-diagnose mismatches.
- **OpenVAF 23.5.0**: the ``.osdi`` files produced by this version
  are ABI-compatible with the ngspice-45 OSDI loader. Later OpenVAF
  versions may require matching ngspice updates; raise that in a
  follow-up PR rather than silently rolling forward.

### Typical session

```bash
# First use — builds the image (5-10 min), then runs the tests.
scripts/xspice_docker.sh pytest -m xspice

# Subsequent runs reuse the cached image, ~3 s startup overhead.
scripts/xspice_docker.sh pytest -m "xspice or veriloga"
```

### CI usage

Drop this into a GitHub Actions job:

```yaml
- uses: docker/build-push-action@v5
  with:
    file: docker/xspice.Dockerfile
    tags: eda-agents-xspice:ng45
    load: true
- run: scripts/xspice_docker.sh pytest -m xspice
```

The image layer cache carries across runs, so only ngspice source
downloads or Dockerfile edits trigger a full rebuild.

### Troubleshooting

- ``Cannot find compiler`` when building the image: the base has a
  full ``build-essential`` toolchain; usually this means the
  Dockerfile's apt step failed — inspect with ``docker build
  --progress=plain`` for the specific package.
- ``_discover_toolchain returned None`` inside the container: the
  entrypoint sets ``NGSPICE_SRC_DIR=/opt/ngspice/ngspice-45``. If it
  isn't set, check that the image tag you're running really was built
  from ``xspice.Dockerfile`` (``docker inspect`` → ``Env``).
- Tests marked ``spice`` or ``klayout`` are not expected to run
  inside this image — they need PDKs and tools we deliberately left
  out. Bind-mount ``$PDK_ROOT`` and install the extra tool chain
  separately if you need those.
