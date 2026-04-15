"""Upstream-parity test for LibreLane templates.

Invokes the curated parity check from
``scripts/check_librelane_template_upstream.py`` for both PDKs. The
check enforces that *verbatim* conventions (VDD/VSS net names,
``meta.version``, ``PRIMARY_GDSII_STREAMOUT_TOOL``) stay in sync with
the vendored upstream project templates under ``external/``.

Skipped gracefully when the submodules are not initialised so that
contributors who ran ``git clone`` without ``--recurse-submodules``
do not get a spurious failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import check_librelane_template_upstream as parity  # noqa: E402


@pytest.mark.parametrize("pdk_key", sorted(parity.SUBMODULE_PATHS))
def test_template_upstream_parity(pdk_key: str) -> None:
    rc = parity.check_one(pdk_key)
    if rc == 2:
        pytest.skip(
            f"submodule external/{parity.SUBMODULE_PATHS[pdk_key]} not "
            f"initialised; run 'git submodule update --init'"
        )
    assert rc == 0, (
        f"verbatim parity failed for {pdk_key}; see stdout above and "
        f"docs/librelane_templates.md for the bump workflow"
    )
