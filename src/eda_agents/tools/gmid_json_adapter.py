"""Adapter between the Arcadia-1 ``gmoverid-skill`` JSON cache layout
and the 4D ``.npz`` format eda-agents consumes via ``GmIdLookup``.

gmoverid-skill stores one JSON per ``(model, W, L, Vds)`` slice, with
arrays ``vgs``, ``id``, ``gm``, ``gds``, ``gmid``, ``ft``, ``id_w``,
``vov``, and the capacitances ``cgg``, ``cgs``, ``cgd``. Our LUT is a
single ``.npz`` keyed by ``lookup_table → <model_key> → {id, gm, gds,
vth, cgg, cgs, cgd, vgs, vds, vbs, length, ...}`` with the per-device
arrays shaped ``(L, Vbs, Vgs, Vds)``.

This module provides a minimal round-trip utility:

  - :func:`npz_slice_to_json_dict` — pull a single ``(L, Vds, Vbs)``
    slice out of a 4D ``.npz``-backed ``GmIdLookup`` and emit a dict
    that matches the gmoverid-skill JSON schema.
  - :func:`save_json_slice` / :func:`load_json_slice` — on-disk I/O
    round-trip for the slice dicts.
  - :func:`assemble_npz_from_json_slices` — reconstruct a 4D ``.npz``
    compatible with ``GmIdLookup`` from a bag of JSON slices covering
    a (L × Vds) grid at one Vbs. Stitches the grids automatically.

The adapter is not a drop-in migration tool — gmoverid-skill caches
PTM data that targets 180/45/22 nm with hard-coded bias ranges, so
users only need it when they arrive with third-party caches and want
to reuse our sizing API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np

# Keys we always persist in the JSON slice (arrays-of-length-N, indexed
# by Vgs). Parity with gmoverid-skill's ``_ARRAY_KEYS`` minus the ones
# we either don't cache (``ro``) or can't reconstruct losslessly from a
# single 4D slice (``cgb``).
_SLICE_ARRAY_KEYS = (
    "vgs", "id", "gm", "gds", "cgg", "cgs", "cgd",
    "gmid", "ft", "id_w", "vov",
)


def _as_array(value: Any) -> np.ndarray:
    """Coerce nested list/ndarray payloads to a 1D float ndarray."""
    return np.asarray(value, dtype=float).reshape(-1)


def npz_slice_to_json_dict(
    lut,
    mos_type: str = "nmos",
    L_um: float = 1.0,
    Vds: float = 0.6,
    Vbs: float = 0.0,
) -> dict[str, Any]:
    """Emit a gmoverid-schema JSON dict from a single ``(L_um, Vds,
    Vbs)`` slice of ``lut`` (a :class:`GmIdLookup`).

    The slice is taken at the LUT's nearest Vbs/Vds grid points and
    linearly interpolated along the length axis; arrays returned are
    indexed by the LUT's full Vgs grid.
    """
    # Reuse the lookup's private helpers without subclassing — we only
    # need to produce 1D arrays along Vgs at the requested corner.
    data = lut._load(mos_type)  # noqa: SLF001 - deliberate adapter coupling
    L = L_um * 1e-6

    vbs_idx = lut._find_nearest_idx(data["vbs"], Vbs)  # noqa: SLF001
    vds_idx = lut._find_nearest_idx(data["vds"], Vds)  # noqa: SLF001

    def _slice_at(arr_key: str) -> np.ndarray:
        arr_3d = lut._interp_length(  # noqa: SLF001
            data[arr_key], data["length"], L
        )
        return np.asarray(arr_3d[vbs_idx, :, vds_idx], dtype=float)

    id_1d = _slice_at("id")
    gm_1d = _slice_at("gm")
    gds_1d = _slice_at("gds")
    vgs = np.asarray(data["vgs"], dtype=float)

    eps = 1e-30
    with np.errstate(divide="ignore", invalid="ignore"):
        gmid = np.where(np.abs(id_1d) > eps, gm_1d / np.abs(id_1d), 0.0)

    cgg_1d = _slice_at("cgg") if "cgg" in data else None
    cgs_1d = _slice_at("cgs") if "cgs" in data else None
    cgd_1d = _slice_at("cgd") if "cgd" in data else None

    if cgg_1d is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            ft = np.where(
                np.abs(cgg_1d) > eps, gm_1d / (2 * np.pi * cgg_1d), 0.0
            )
    else:
        ft = np.zeros_like(gm_1d)

    w_ref = float(data.get("w_ref_m", 10e-6))
    id_w = id_1d / w_ref
    # Vov = Vgs - Vth_median (per-slice); falls back to Vgs when Vth==0.
    vth_1d = _slice_at("vth")
    vth_valid = vth_1d[vth_1d != 0]
    vth_med = float(np.median(vth_valid)) if vth_valid.size else 0.0
    vov = vgs - vth_med

    slice_dict: dict[str, Any] = {
        "schema": "gmoverid.v1",
        "model": data.get("model_name") or lut.pdk.nmos_symbol
        if mos_type == "nmos"
        else lut.pdk.pmos_symbol,
        "W_um": w_ref * 1e6,
        "L_um": float(L_um),
        "Vds_V": float(data["vds"][vds_idx]),
        "Vbs_V": float(data["vbs"][vbs_idx]),
        "vgs": vgs.tolist(),
        "id": id_1d.tolist(),
        "gm": gm_1d.tolist(),
        "gds": gds_1d.tolist(),
        "gmid": gmid.tolist(),
        "ft": ft.tolist(),
        "id_w": id_w.tolist(),
        "vov": vov.tolist(),
    }
    if cgg_1d is not None:
        slice_dict["cgg"] = cgg_1d.tolist()
    if cgs_1d is not None:
        slice_dict["cgs"] = cgs_1d.tolist()
    if cgd_1d is not None:
        slice_dict["cgd"] = cgd_1d.tolist()
    return slice_dict


def save_json_slice(slice_dict: dict[str, Any], path: Path) -> Path:
    """Write a slice dict to ``path`` as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(slice_dict, indent=2))
    return path


def load_json_slice(path: Path) -> dict[str, Any]:
    """Load a gmoverid-schema JSON slice from ``path`` and re-cast the
    array-valued entries to lists of floats (JSON safe)."""
    path = Path(path)
    data = json.loads(path.read_text())
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in _SLICE_ARRAY_KEYS and isinstance(value, list):
            out[key] = [float(v) for v in value]
        else:
            out[key] = value
    return out


def assemble_npz_from_json_slices(
    slice_paths: Iterable[Path],
    out_path: Path,
    *,
    model_key: str,
    Vbs_V: float = 0.0,
) -> Path:
    """Reconstruct a 4D ``.npz`` LUT from a bag of JSON slices.

    All slices must share the same ``vgs`` grid and the same ``W_um``.
    The set of ``L_um`` and ``Vds_V`` values is determined by the
    slices themselves; the resulting array axes are sorted ascending.
    The single ``Vbs_V`` given applies to the whole bundle (the source
    slices have only one Vbs each).

    Raises ``ValueError`` when slices are inconsistent.
    """
    slices = [load_json_slice(p) for p in slice_paths]
    if not slices:
        raise ValueError("No JSON slices provided.")

    ref_vgs = _as_array(slices[0]["vgs"])
    ref_w_um = float(slices[0]["W_um"])
    for s in slices[1:]:
        if not np.allclose(_as_array(s["vgs"]), ref_vgs):
            raise ValueError("Vgs grid mismatch across slices.")
        if abs(float(s["W_um"]) - ref_w_um) > 1e-9:
            raise ValueError("W mismatch across slices.")

    lengths_um = sorted({float(s["L_um"]) for s in slices})
    vds_list = sorted({float(s["Vds_V"]) for s in slices})

    if len(lengths_um) * len(vds_list) != len(slices):
        raise ValueError(
            "Slice set is not a full rectangular grid over (L, Vds); "
            f"got {len(slices)} slices but expected "
            f"{len(lengths_um)} * {len(vds_list)} = "
            f"{len(lengths_um) * len(vds_list)}."
        )

    nL = len(lengths_um)
    nVgs = ref_vgs.size
    nVds = len(vds_list)
    nVbs = 1

    def _empty() -> np.ndarray:
        return np.zeros((nL, nVbs, nVgs, nVds), dtype=np.float32)

    arrays = {k: _empty() for k in ("id", "gm", "gds", "cgg", "cgs", "cgd")}
    vth_arr = _empty()

    for s in slices:
        l_idx = lengths_um.index(float(s["L_um"]))
        v_idx = vds_list.index(float(s["Vds_V"]))
        for key in ("id", "gm", "gds"):
            arrays[key][l_idx, 0, :, v_idx] = _as_array(s[key])
        for key in ("cgg", "cgs", "cgd"):
            if key in s:
                arrays[key][l_idx, 0, :, v_idx] = _as_array(s[key])
        # Vth isn't tracked in gmoverid JSON; back-compute a coarse
        # estimate from Vgs-Vov. If vov is missing, leave zero.
        if "vov" in s:
            vov = _as_array(s["vov"])
            vth = ref_vgs - vov
            vth_arr[l_idx, 0, :, v_idx] = vth.astype(np.float32)

    model_payload = {
        "id": arrays["id"],
        "gm": arrays["gm"],
        "gds": arrays["gds"],
        "vth": vth_arr,
        "cgg": arrays["cgg"],
        "cgs": arrays["cgs"],
        "cgd": arrays["cgd"],
        "vgs": ref_vgs.astype(np.float64),
        "vds": np.asarray(vds_list, dtype=np.float64),
        "vbs": np.asarray([float(Vbs_V)], dtype=np.float64),
        "length": np.asarray(
            [length * 1e-6 for length in lengths_um], dtype=np.float64
        ),
        "model_name": model_key,
        "parameter_names": ["id", "gm", "gds", "vth", "cgg", "cgs", "cgd"],
        "device_parameters": {"w": ref_w_um * 1e-6, "ng": 1, "m": 1},
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        str(out_path),
        lookup_table={model_key: model_payload},
    )
    return out_path


__all__ = [
    "npz_slice_to_json_dict",
    "save_json_slice",
    "load_json_slice",
    "assemble_npz_from_json_slices",
]
