"""Download-on-first-use for GF180 gm/ID lookup tables.

Resolves the local path of a GF180 ``.npz`` LUT without shipping the
73 MB of data inside the wheel. Resolution order:

  1. ``EDA_AGENTS_GMID_LUT_DIR`` env var. If set and the requested
     file exists there, return it. If set and the file is missing,
     raise a clear error (do not silently fall through).
  2. XDG cache directory (``$XDG_CACHE_HOME/eda-agents/gmid_luts/`` or
     ``~/.cache/eda-agents/gmid_luts/``). If the file exists and its
     SHA256 matches the known-good hash, return it.
  3. Download from the project's GitHub Release ``luts-v1`` tag,
     verify SHA256, store in cache, return.

``EDA_AGENTS_OFFLINE=1`` disables step 3. On miss in offline mode the
fetcher emits an actionable error telling the user to set
``EDA_AGENTS_GMID_LUT_DIR`` or disable ``EDA_AGENTS_OFFLINE``.

The IHP PDK uses a separate LUT kit (ihp-gmid-kit) that is not
published here; those LUTs are resolved via ``EDA_AGENTS_IHP_LUT_DIR``
only. This module targets GF180MCU only.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# GitHub Release tag and asset URL template. Bumping LUTs requires:
#   1. Updating this tag, the CHECKSUMS dict, and the asset filenames.
#   2. Publishing a new Release on GitHub with the .npz files attached.
# See ``docs/release_luts.md`` for the full procedure.
_RELEASE_TAG = "luts-v1"
_RELEASE_URL_BASE = (
    "https://github.com/Mauricio-xx/eda-agents/releases/download"
    f"/{_RELEASE_TAG}"
)

# Known-good SHA256 hashes. Keep in sync with the assets attached to
# the Release tagged above. Used both as integrity check after
# download and to detect local cache corruption.
_CHECKSUMS: dict[str, str] = {
    "gf180_nfet_03v3.npz": (
        "d180ca7b8e9d752b6b60f593df58115f30b63c4e878b3161fe15b0568460be3f"
    ),
    "gf180_pfet_03v3.npz": (
        "254f411bf9f209f5bee29595513b3558854dc9636861c29e49aa931fde62d4e2"
    ),
}


def _cache_root() -> Path:
    """Resolve the XDG-aware cache directory for LUT downloads."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "eda-agents" / "gmid_luts"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(path: Path, fname: str) -> bool:
    expected = _CHECKSUMS.get(fname)
    if not expected:
        # Unknown file — trust it (user may be pointing at a custom LUT
        # via EDA_AGENTS_GMID_LUT_DIR). Only strict on known filenames.
        return True
    return _sha256(path) == expected


def _download(fname: str, dest: Path) -> None:
    """Fetch ``fname`` from the GitHub Release into ``dest``."""
    try:
        import httpx
    except ImportError as e:
        raise RuntimeError(
            "Downloading GF180 LUTs requires 'httpx'. Install with "
            "'pip install eda-agents[mcp]' or set EDA_AGENTS_GMID_LUT_DIR "
            "to a directory that already contains the .npz files."
        ) from e

    url = f"{_RELEASE_URL_BASE}/{fname}"
    logger.info("Fetching %s -> %s", url, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes(chunk_size=1 << 20):
                fh.write(chunk)
    tmp.replace(dest)


def resolve_gmid_lut(fname: str) -> Path:
    """Return a local path to ``fname`` (a GF180 LUT), fetching if needed.

    ``fname`` must be a bare filename (e.g. ``gf180_nfet_03v3.npz``),
    not a path. See module docstring for resolution order.
    """
    # 1) Explicit override via env var — absolute authority.
    override = os.environ.get("EDA_AGENTS_GMID_LUT_DIR")
    if override:
        candidate = Path(override) / fname
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(
            f"EDA_AGENTS_GMID_LUT_DIR is set to '{override}' but "
            f"'{fname}' is not present there. Either place the file or "
            "unset EDA_AGENTS_GMID_LUT_DIR to use the download cache."
        )

    # 2) XDG cache with checksum verification.
    cache = _cache_root() / fname
    if cache.is_file():
        if _verify(cache, fname):
            return cache
        logger.warning(
            "Cached LUT %s has wrong SHA256; re-downloading.", cache
        )
        cache.unlink()

    # 3) Download (unless offline).
    if os.environ.get("EDA_AGENTS_OFFLINE") == "1":
        raise RuntimeError(
            f"LUT '{fname}' not found in cache and EDA_AGENTS_OFFLINE=1. "
            "Set EDA_AGENTS_GMID_LUT_DIR to a directory containing the "
            "file, or unset EDA_AGENTS_OFFLINE."
        )

    _download(fname, cache)

    if not _verify(cache, fname):
        cache.unlink(missing_ok=True)
        raise RuntimeError(
            f"Downloaded LUT '{fname}' failed SHA256 verification. "
            "The GitHub Release may be corrupted; retry later or set "
            "EDA_AGENTS_GMID_LUT_DIR to a trusted local copy."
        )
    return cache


def ensure_gf180_cache(nmos_fname: str, pmos_fname: str) -> Path:
    """Ensure both GF180 LUTs are available and return their parent dir.

    Convenience for ``GmIdLookup`` which expects a directory. Calls
    ``resolve_gmid_lut`` for each file and returns the cache root. Both
    files will live in the same directory by construction.
    """
    n_path = resolve_gmid_lut(nmos_fname)
    p_path = resolve_gmid_lut(pmos_fname)
    assert n_path.parent == p_path.parent, (
        "nmos/pmos LUTs resolved to different parent directories; this "
        "should not happen."
    )
    return n_path.parent
