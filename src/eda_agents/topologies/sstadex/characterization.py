"""LUT-driven small-signal characterization for SSTADEX primitives.

Reads the PSP103 (.npz) LUTs that ``GmIdLookup`` already consumes for
IHP SG13G2 and GF180MCU. Given a bias current per branch and a sweep
list of ``(length, vgs, vds)`` operating points, it returns a pandas
DataFrame mirroring the upstream SSTADEx primitive ``build.py`` output:

::

    length  width   gm     gds    gdsid   Ro     cgg    cgs    cgd

This module is intentionally narrow. The upstream SSTADEx project
loads mosplot's ``Transistor`` class to do the same job, but mosplot
needs its own LUT format and tooling. Our PSP103 LUT was already
generated and shipped via ``ihp-gmid-kit``, so we read it directly
through ``GmIdLookup`` and skip the dependency. See ``docs/
sstadex_port.md`` for the deviation rationale.

PMOS sign convention follows the same rule as ``RonGmLookup``:
callers pass positive ``vgs`` / ``vds`` magnitudes (e.g. ``vgs=0.5``,
``vds=0.6`` for a PMOS biased at VSG=0.5 V, VSD=0.6 V); the LUT axis
sign is resolved internally from the stored ``vgs[0..-1]`` direction.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.core.pdk import PdkConfig

logger = logging.getLogger(__name__)


def _resolve_lut_axis_sign(axis: np.ndarray) -> int:
    """Return +1 if axis is non-negative (NMOS), -1 if non-positive (PMOS)."""
    a = np.asarray(axis)
    if a.size == 0:
        return +1
    if (a >= 0).all():
        return +1
    if (a <= 0).all():
        return -1
    # Mixed sign (rare); fall back to sign of largest magnitude.
    return int(np.sign(a[np.argmax(np.abs(a))]))


def _lut_axis_signs(data: dict) -> tuple[int, int]:
    """Read the vgs/vds polarity from a loaded LUT slice. Returns
    ``(sign_vgs, sign_vds)`` where +1 indicates an NMOS-style ascending
    positive axis and -1 indicates a PMOS-style descending negative
    axis."""
    return _resolve_lut_axis_sign(data["vgs"]), _resolve_lut_axis_sign(data["vds"])


def _interp_along_length(
    arr: np.ndarray, lengths: np.ndarray, L: float
) -> np.ndarray:
    """Linear interpolation along the leading L axis. Returns a 3-D
    slice ``(Vbs, Vgs, Vds)`` at ``L``."""
    if L <= lengths[0]:
        return arr[0]
    if L >= lengths[-1]:
        return arr[-1]
    idx = int(np.searchsorted(lengths, L) - 1)
    idx = max(0, min(idx, len(lengths) - 2))
    L0, L1 = lengths[idx], lengths[idx + 1]
    frac = (L - L0) / (L1 - L0)
    return arr[idx] * (1 - frac) + arr[idx + 1] * frac


def _query_per_w(
    lut: GmIdLookup,
    mos_type: str,
    L_um: float,
    vgs_mag: float,
    vds_mag: float,
    vbs: float = 0.0,
) -> dict[str, float]:
    """Read per-unit-width small-signal params at a single operating
    point. PMOS magnitudes converted to the LUT's negative axis as
    needed. Returns a dict with keys ``id_per_w_Apm``, ``gm_per_w_Spm``,
    ``gds_per_w_Spm``, ``cgg_per_w_Fpm`` (or ``None`` if Cgg absent),
    ``cgs_per_w_Fpm``, ``cgd_per_w_Fpm``, ``vgs_V``, ``vds_V``,
    ``vbs_V``.

    Linear interpolation is used along all four LUT axes (length, vgs,
    vds, vbs). Out-of-range queries are clipped to the LUT edge.
    """
    data = lut._load(mos_type)  # raise FileNotFoundError if LUT missing
    sign_vgs, sign_vds = _lut_axis_signs(data)

    vgs_lut = sign_vgs * vgs_mag
    vds_lut = sign_vds * vds_mag

    lengths = np.asarray(data["length"])
    vbs_axis = np.asarray(data["vbs"])
    vgs_axis = np.asarray(data["vgs"])
    vds_axis = np.asarray(data["vds"])

    L = L_um * 1e-6
    L = float(np.clip(L, lengths[0], lengths[-1]))

    id_3d = _interp_along_length(data["id"], lengths, L)
    gm_3d = _interp_along_length(data["gm"], lengths, L)
    gds_3d = _interp_along_length(data["gds"], lengths, L)
    cgg_3d = (
        _interp_along_length(data["cgg"], lengths, L)
        if "cgg" in data
        else None
    )
    cgs_3d = (
        _interp_along_length(data["cgs"], lengths, L)
        if "cgs" in data
        else None
    )
    cgd_3d = (
        _interp_along_length(data["cgd"], lengths, L)
        if "cgd" in data
        else None
    )

    def _interp3d(arr3d: np.ndarray) -> float:
        """Trilinear interp on (vbs, vgs, vds)."""
        if arr3d is None:
            return float("nan")
        # 1-D interp on Vbs first (rare; usually single-point at 0).
        vbs_clipped = float(np.clip(vbs, vbs_axis.min(), vbs_axis.max()))
        if len(vbs_axis) == 1:
            arr2d = arr3d[0]
        else:
            j = int(np.searchsorted(np.sort(vbs_axis), vbs_clipped))
            j = max(0, min(j, len(vbs_axis) - 1))
            # Assume Vbs axis ordered ascending OR descending; use idx-based.
            order = np.argsort(vbs_axis)
            vbs_sorted = vbs_axis[order]
            j2 = int(np.searchsorted(vbs_sorted, vbs_clipped))
            j2 = max(0, min(j2, len(vbs_sorted) - 2))
            v0, v1 = vbs_sorted[j2], vbs_sorted[j2 + 1]
            f = (vbs_clipped - v0) / (v1 - v0) if v1 != v0 else 0.0
            arr2d = arr3d[order[j2]] * (1 - f) + arr3d[order[j2 + 1]] * f
        # 2-D interp on (vgs, vds).
        vgs_clipped = float(np.clip(vgs_lut, vgs_axis.min(), vgs_axis.max()))
        vds_clipped = float(np.clip(vds_lut, vds_axis.min(), vds_axis.max()))
        # Ascending order:
        vgs_order = np.argsort(vgs_axis)
        vds_order = np.argsort(vds_axis)
        vgs_sorted = vgs_axis[vgs_order]
        vds_sorted = vds_axis[vds_order]
        arr2d_sorted = arr2d[vgs_order, :][:, vds_order]

        ig = int(np.searchsorted(vgs_sorted, vgs_clipped) - 1)
        ig = max(0, min(ig, len(vgs_sorted) - 2))
        id_ = int(np.searchsorted(vds_sorted, vds_clipped) - 1)
        id_ = max(0, min(id_, len(vds_sorted) - 2))

        g0, g1 = vgs_sorted[ig], vgs_sorted[ig + 1]
        d0, d1 = vds_sorted[id_], vds_sorted[id_ + 1]
        fg = (vgs_clipped - g0) / (g1 - g0) if g1 != g0 else 0.0
        fd = (vds_clipped - d0) / (d1 - d0) if d1 != d0 else 0.0

        c00 = arr2d_sorted[ig, id_]
        c01 = arr2d_sorted[ig, id_ + 1]
        c10 = arr2d_sorted[ig + 1, id_]
        c11 = arr2d_sorted[ig + 1, id_ + 1]
        return float(
            c00 * (1 - fg) * (1 - fd)
            + c01 * (1 - fg) * fd
            + c10 * fg * (1 - fd)
            + c11 * fg * fd
        )

    w_ref = float(data.get("w_ref_m", 10e-6))
    id_at = _interp3d(id_3d)
    gm_at = _interp3d(gm_3d)
    gds_at = _interp3d(gds_3d)
    cgg_at = _interp3d(cgg_3d) if cgg_3d is not None else None
    cgs_at = _interp3d(cgs_3d) if cgs_3d is not None else None
    cgd_at = _interp3d(cgd_3d) if cgd_3d is not None else None

    out: dict[str, float] = {
        "id_per_w_Apm": id_at / w_ref,
        "gm_per_w_Spm": gm_at / w_ref,
        "gds_per_w_Spm": gds_at / w_ref,
        "vgs_V": vgs_mag,  # user-facing magnitude
        "vds_V": vds_mag,
        "vbs_V": float(vbs),
        "w_ref_m": w_ref,
    }
    out["cgg_per_w_Fpm"] = cgg_at / w_ref if cgg_at is not None else None
    out["cgs_per_w_Fpm"] = cgs_at / w_ref if cgs_at is not None else None
    out["cgd_per_w_Fpm"] = cgd_at / w_ref if cgd_at is not None else None
    return out


def characterize_primitive(
    lut: GmIdLookup,
    mos_type: str,
    *,
    lengths_um: list[float],
    vgs_sweep: list[float] | np.ndarray,
    vds_sweep: list[float] | np.ndarray,
    id_target_per_branch: float,
    vbs: float = 0.0,
) -> pd.DataFrame:
    """Sweep ``(length, vgs, vds)`` and return SSTADEx-style DataFrame.

    Parameters
    ----------
    lut
        ``GmIdLookup`` instance whose LUT directory must contain the
        per-PDK .npz files for ``mos_type``.
    mos_type
        ``"nmos"`` or ``"pmos"``. User-facing magnitudes; PMOS sign is
        handled internally against the LUT axis polarity.
    lengths_um
        Channel lengths in micrometers. One row per (L, sweep_point).
    vgs_sweep, vds_sweep
        Operating-point sweeps as magnitudes. Must have the same
        length (zipped, not Cartesian). For a Cartesian sweep, the
        caller should pre-build the cross-product with ``np.meshgrid``.
    id_target_per_branch
        Drain current per branch in amperes. The primitive ``W`` at
        each operating point is computed as
        ``W = id_target / id_per_w``.
    vbs
        Body-source voltage (magnitude). Default 0.

    Returns
    -------
    pd.DataFrame
        Columns: ``length`` (m), ``width`` (m), ``gm`` (S), ``gds``
        (S), ``gdsid`` (1/V), ``Ro`` (Ohm), ``cgg``, ``cgs``, ``cgd``
        (F), ``vgs`` (V), ``vds`` (V). One row per (L, sweep_point).

    The output schema exactly matches the upstream SSTADEx
    ``simplediffpair`` build for drop-in interoperability.
    """
    vgs = np.atleast_1d(np.asarray(vgs_sweep, dtype=float))
    vds = np.atleast_1d(np.asarray(vds_sweep, dtype=float))
    if vgs.size != vds.size:
        raise ValueError(
            f"vgs_sweep ({vgs.size}) and vds_sweep ({vds.size}) must "
            "be the same length. For a Cartesian sweep, pre-broadcast "
            "via np.meshgrid."
        )

    rows = []
    for L in lengths_um:
        for vgs_pt, vds_pt in zip(vgs, vds):
            per_w = _query_per_w(
                lut, mos_type,
                L_um=L,
                vgs_mag=float(vgs_pt),
                vds_mag=float(vds_pt),
                vbs=vbs,
            )
            id_per_w = per_w["id_per_w_Apm"]
            if id_per_w <= 0:
                # Subthreshold or off — W blows up; skip this point.
                continue
            W = id_target_per_branch / id_per_w
            gm = per_w["gm_per_w_Spm"] * W
            gds = per_w["gds_per_w_Spm"] * W
            Ro = 1.0 / gds if gds > 0 else float("inf")
            gdsid = gds / id_target_per_branch
            cgg = (
                per_w["cgg_per_w_Fpm"] * W
                if per_w["cgg_per_w_Fpm"] is not None
                else float("nan")
            )
            cgs = (
                per_w["cgs_per_w_Fpm"] * W
                if per_w["cgs_per_w_Fpm"] is not None
                else float("nan")
            )
            cgd = (
                per_w["cgd_per_w_Fpm"] * W
                if per_w["cgd_per_w_Fpm"] is not None
                else float("nan")
            )
            rows.append(
                {
                    "length": L * 1e-6,
                    "width": W,
                    "gm": gm,
                    "gds": gds,
                    "gdsid": gdsid,
                    "Ro": Ro,
                    "cgg": cgg,
                    "cgs": cgs,
                    "cgd": cgd,
                    "vgs": float(vgs_pt),
                    "vds": float(vds_pt),
                }
            )
    return pd.DataFrame(rows)
