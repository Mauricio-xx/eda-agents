"""Invariant guard for the SAR 11-bit architectural ceiling.

Written in S9-residual-closure (gap #6b). Reads the
`bench/results/sar11_ceiling_characterization/sweep.tsv` produced by
`scripts/characterize_sar11_ceiling.py` and asserts that the measured
ceiling still sits above the active `_SPEC_ENOB_MIN` / `_SPEC_SNDR_MIN`
thresholds — i.e. the topology is reachable under parameter tuning.

If a future commit either

  * lowers the measured ceiling (e.g. topology regression that breaks
    the d_cosim coupling, moves the bias rail out of the operating
    range, or inverts a netlist weight), or
  * raises the thresholds past the measured ceiling,

this test will fail loudly and point the reader at the TSV.

The test does NOT re-run the sweep — that takes 15+ min of ngspice
simulation per invocation and is the business of
`scripts/characterize_sar11_ceiling.py`. It parses the committed TSV
as a static fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agents.topologies.sar_adc_11bit import (
    _SPEC_ENOB_MIN,
    _SPEC_SNDR_MIN,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SWEEP_TSV = (
    _REPO_ROOT
    / "bench"
    / "results"
    / "sar11_ceiling_characterization"
    / "sweep.tsv"
)


def _parse_sweep_tsv(path: Path) -> list[dict[str, str]]:
    """Tiny TSV parser. Returns a list of {column: value} dicts."""
    lines = path.read_text().strip().splitlines()
    header = lines[0].split("\t")
    rows: list[dict[str, str]] = []
    for raw in lines[1:]:
        cells = raw.split("\t")
        # Pad trailing cells for rows without an error string.
        cells += [""] * (len(header) - len(cells))
        rows.append(dict(zip(header, cells)))
    return rows


@pytest.fixture(scope="module")
def sweep_rows() -> list[dict[str, str]]:
    if not _SWEEP_TSV.is_file():
        pytest.skip(
            f"{_SWEEP_TSV.relative_to(_REPO_ROOT)} not committed; run "
            "scripts/characterize_sar11_ceiling.py to regenerate."
        )
    rows = _parse_sweep_tsv(_SWEEP_TSV)
    assert rows, f"sweep.tsv has no data rows: {_SWEEP_TSV}"
    return rows


def _valid_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in rows:
        if r.get("status", "").strip().upper() != "OK":
            continue
        try:
            enob = float(r.get("ENOB", "0") or 0)
        except ValueError:
            continue
        if enob <= 0.0:
            # Degenerate runs (bias_V=0.9 starves the tail) show ENOB=0;
            # keep them out of the ceiling calculation.
            continue
        out.append(r)
    return out


def test_sweep_has_expected_number_of_rows(sweep_rows):
    """12-point L9 + 3 corners = 12 configurations."""
    assert len(sweep_rows) == 12, (
        f"expected 12 rows in sweep.tsv, got {len(sweep_rows)}. Re-run "
        "scripts/characterize_sar11_ceiling.py if the design matrix "
        "changed shape."
    )


def test_ceiling_is_above_spec_enob_min(sweep_rows):
    """max(ENOB) across valid runs must exceed `_SPEC_ENOB_MIN`."""
    valid = _valid_rows(sweep_rows)
    assert valid, (
        "no valid runs in sweep.tsv (all degenerate). The topology "
        "cannot satisfy its own thresholds under any sampled params — "
        "that is a topology regression, not a test bug."
    )
    enobs = [float(r["ENOB"]) for r in valid]
    max_enob = max(enobs)
    assert max_enob >= _SPEC_ENOB_MIN, (
        f"Measured ENOB ceiling ({max_enob:.3f} bit) has fallen below "
        f"the topology's _SPEC_ENOB_MIN ({_SPEC_ENOB_MIN:.2f} bit). "
        f"Either recalibrate the spec against the current sweep or "
        f"revert the change that dropped the ceiling. TSV: "
        f"{_SWEEP_TSV.relative_to(_REPO_ROOT)}"
    )


def test_ceiling_is_above_spec_sndr_min(sweep_rows):
    """max(SNDR) across valid runs must exceed `_SPEC_SNDR_MIN`."""
    valid = _valid_rows(sweep_rows)
    sndrs = [float(r["SNDR_dBc"]) for r in valid]
    max_sndr = max(sndrs)
    assert max_sndr >= _SPEC_SNDR_MIN, (
        f"Measured SNDR ceiling ({max_sndr:.2f} dB) has fallen below "
        f"the topology's _SPEC_SNDR_MIN ({_SPEC_SNDR_MIN:.2f} dB). "
        f"Same remediation as ENOB invariant above. TSV: "
        f"{_SWEEP_TSV.relative_to(_REPO_ROOT)}"
    )


def test_ceiling_is_reproducible(sweep_rows):
    """run_01 and run_10 have identical params; their ENOB must match.

    The design matrix replays (W=8, L=0.15, Cu=20, Vb=0.5) twice
    specifically to detect run-to-run jitter. If the delta exceeds a
    small numerical-noise budget, something non-deterministic landed
    in the sim pipeline and gap #6a needs re-opening.
    """
    by_id = {r["run_id"]: r for r in sweep_rows}
    a = by_id.get("run_01")
    b = by_id.get("run_10")
    if a is None or b is None:
        pytest.skip("run_01 / run_10 replica pair missing from TSV")
    enob_a = float(a["ENOB"])
    enob_b = float(b["ENOB"])
    assert abs(enob_a - enob_b) <= 0.01, (
        f"replica pair run_01 vs run_10 diverged: "
        f"ENOB {enob_a:.3f} vs {enob_b:.3f}. Bit-exact expected; "
        f"a drift means gap #6a flakiness may be back."
    )
