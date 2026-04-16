# XSPICE-capable ngspice toolchain image for eda-agents.
#
# The host typically lacks the ngspice source tree required by
# XSpiceCompiler (needs cmpp + in-tree headers). This image bundles
# a pinned ngspice-45 build alongside openvaf 23.5.0, so
# ``scripts/xspice_docker.sh pytest -m xspice`` is self-contained on
# any machine with Docker.
#
# The image deliberately does NOT include IHP SG13G2 or GF180MCU —
# XSPICE primitives are orthogonal to the PDK. Tests that need a PDK
# must bind-mount ``$PDK_ROOT`` at runtime.
#
# Build:
#   docker build -f docker/xspice.Dockerfile -t eda-agents-xspice:ng45 .
#
# Run:
#   scripts/xspice_docker.sh pytest -m xspice tests/test_xspice_primitives.py

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    NGSPICE_VERSION=45 \
    OPENVAF_VERSION=23.5.0 \
    NGSPICE_SRC_DIR=/opt/ngspice/ngspice-45

# System toolchain + ngspice build prerequisites.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        autoconf \
        automake \
        libtool \
        bison \
        flex \
        pkg-config \
        libreadline-dev \
        libncurses-dev \
        libx11-dev \
        libxaw7-dev \
        libxmu-dev \
        libxext-dev \
        libxft-dev \
        libfontconfig1-dev \
        libtool-bin \
        libfftw3-dev \
        libxrender-dev \
        libxinerama-dev \
        ca-certificates \
        curl \
        git \
        unzip \
        xz-utils \
        python3 \
        python3-dev \
        python3-venv \
        python3-pip \
        && rm -rf /var/lib/apt/lists/*

# ngspice-45 source build. We build in-tree so the relative layout that
# ``_discover_toolchain`` probes (``src/xspice/cmpp/cmpp``,
# ``src/include/ngspice``, ``src/misc/dstring.c``,
# ``src/xspice/icm/dlmain.c``) is intact. The install step places
# binaries under /usr/local/bin so ``ngspice`` is the pinned version.
WORKDIR /opt/ngspice
RUN curl -fsSL -o ngspice-45.tar.gz \
        "https://downloads.sourceforge.net/ngspice/ngspice-45.tar.gz" \
    && tar xzf ngspice-45.tar.gz \
    && rm ngspice-45.tar.gz

WORKDIR /opt/ngspice/ngspice-45
RUN ./configure --enable-xspice --enable-osdi --disable-debug --with-readline=yes \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig

# openvaf 23.5.0 — static Linux x86_64 release binary. Matches the
# version currently used on the host (so .osdi files are ABI-compatible
# when developers want to move them between environments).
RUN curl -fsSL -o /tmp/openvaf.tar.gz \
        "https://openva.fra1.cdn.digitaloceanspaces.com/openvaf_23_5_0_linux_amd64.tar.gz" \
    && tar xzf /tmp/openvaf.tar.gz -C /usr/local/bin/ \
    && rm /tmp/openvaf.tar.gz \
    && chmod +x /usr/local/bin/openvaf \
    && openvaf --version

# Python venv isolated from system site-packages. eda-agents is
# installed editable at runtime via bind-mount, so we only ship the
# core wheels here.
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir \
        "numpy>=1.24" \
        "scipy>=1.10" \
        "matplotlib>=3.7" \
        "pytest>=8" \
        "pytest-asyncio>=0.23" \
        "anyio>=4.0" \
        "pydantic>=2" \
        "ruff" \
        "adctoolbox>=0.6.4" \
        "pyyaml"

# Default work dir is /work — the xspice_docker.sh wrapper binds the
# repo root here so ``pip install -e .`` (run at container start) can
# find pyproject.toml.
WORKDIR /work

# The entrypoint script installs eda-agents editable on first run
# (if not already installed in the mounted venv site-packages) and
# then execs the user command.
COPY docker/xspice_entrypoint.sh /usr/local/bin/xspice_entrypoint
RUN chmod +x /usr/local/bin/xspice_entrypoint

ENTRYPOINT ["/usr/local/bin/xspice_entrypoint"]
CMD ["bash"]
