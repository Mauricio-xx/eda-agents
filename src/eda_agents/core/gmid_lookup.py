"""gm/ID lookup table interface for MOSFET characterization.

Provides fast lookup of transistor performance metrics from pre-generated
ngspice sweep data. Agents use this for informed design decisions
BEFORE running expensive SPICE simulations.

Key metrics available:
    - gm/gds (intrinsic gain) at given L, gm/ID
    - ID/W (current density) at given gm/ID
    - fT (transit frequency) at given L, gm/ID
    - Vth (threshold voltage) at given L
    - Vdsat at given operating point

Supports any PDK via PdkConfig (defaults to IHP SG13G2).
LUT data source: .npz files from ngspice DC sweeps (e.g., ihp-gmid-kit).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np

from eda_agents.core.pdk import PdkConfig, resolve_pdk

logger = logging.getLogger(__name__)


# Per-PDK env var that can override cfg.lut_dir_default. Keeps IHP LUTs
# (ihp-gmid-kit, external repo) separate from GF180 LUTs (shipped via
# GitHub Release + on-demand download).
_LUT_DIR_ENV_VARS: dict[str, str] = {
    "ihp_sg13g2": "EDA_AGENTS_IHP_LUT_DIR",
    "gf180mcu": "EDA_AGENTS_GMID_LUT_DIR",
}


class GmIdLookup:
    """gm/ID lookup table for MOSFETs.

    Loads .npz LUT data and provides interpolated lookups for
    transistor performance at arbitrary operating points.
    PDK-specific file names and model keys are derived from PdkConfig.

    Parameters
    ----------
    pdk : PdkConfig or str, optional
        PDK configuration. Defaults to resolve_pdk().
    lut_dir : Path, optional
        Directory containing .npz LUT files. Overrides pdk.lut_dir_default.
    """

    def __init__(
        self,
        pdk: PdkConfig | str | None = None,
        lut_dir: Path | None = None,
    ):
        self.pdk = resolve_pdk(pdk)
        self.lut_dir = self._resolve_lut_dir(lut_dir)
        self._nmos: dict | None = None
        self._pmos: dict | None = None

    def _resolve_lut_dir(self, lut_dir: Path | None) -> Path:
        """Pick the LUT directory following the PDK-aware fallback chain.

        Order:
          1. Explicit ``lut_dir=...`` argument.
          2. ``cfg.lut_dir_default`` if set (legacy in-repo override).
          3. Per-PDK env var (``EDA_AGENTS_IHP_LUT_DIR`` for IHP,
             ``EDA_AGENTS_GMID_LUT_DIR`` for GF180).
          4. GF180 only: on-demand download via ``lut_fetcher`` into
             the XDG cache. IHP does not auto-download; the kit lives
             in the external ``ihp-gmid-kit`` repo.
          5. Raise ``ValueError`` with an actionable hint.
        """
        if lut_dir:
            return Path(lut_dir)
        if self.pdk.lut_dir_default:
            return Path(self.pdk.lut_dir_default)

        env_var = _LUT_DIR_ENV_VARS.get(self.pdk.name)
        if env_var:
            env_val = os.environ.get(env_var)
            if env_val:
                return Path(env_val)

        if self.pdk.name == "gf180mcu":
            from eda_agents.core import lut_fetcher

            return lut_fetcher.ensure_gf180_cache(
                self.pdk.lut_nmos_file,
                self.pdk.lut_pmos_file,
            )

        raise ValueError(
            f"No LUT directory for PDK '{self.pdk.name}'. Pass "
            f"lut_dir explicitly"
            + (f" or set {env_var}." if env_var else ".")
        )

    def _load(self, mos_type: str) -> dict:
        """Load and cache LUT data for given type."""
        if mos_type == "nmos":
            if self._nmos is not None:
                return self._nmos
            fname = self.pdk.lut_nmos_file
            model_key = self.pdk.lut_model_key_nmos
        else:
            if self._pmos is not None:
                return self._pmos
            fname = self.pdk.lut_pmos_file
            model_key = self.pdk.lut_model_key_pmos

        path = self.lut_dir / fname
        if not path.exists():
            raise FileNotFoundError(f"LUT file not found: {path}")

        data = np.load(str(path), allow_pickle=True)

        # Handle both flat and nested formats
        if "lookup_table" in data:
            lt = data["lookup_table"].item()
            model = lt[model_key]
        elif model_key in data:
            model = data[model_key].item()
        else:
            raise KeyError(f"Model key '{model_key}' not found in {path}")

        result = {
            "id": model["id"],       # (L, Vbs, Vgs, Vds)
            "gm": model["gm"],
            "gds": model["gds"],
            "vth": model["vth"],
            "length": model["length"],
            "vgs": model["vgs"],
            "vds": model["vds"],
            "vbs": model["vbs"],
        }

        # Optional parameters
        for key in ("cgg", "cgs", "cgd", "vdsat"):
            if key in model:
                result[key] = model[key]

        # Reference width the sweep was simulated at (ihp-gmid-kit uses
        # 10 um). Size-by-Id/W/gm needs it to scale per-unit-width values
        # to the user's target. Fall back to 10 um for legacy LUTs that
        # don't record device_parameters.
        w_ref = 10e-6
        if "device_parameters" in model:
            try:
                w_ref = float(model["device_parameters"]["w"])
            except (KeyError, TypeError, ValueError):
                pass
        result["w_ref_m"] = w_ref

        if mos_type == "nmos":
            self._nmos = result
        else:
            self._pmos = result

        logger.info(
            "Loaded %s LUT: %d L values [%.3f-%.3f um], %d Vgs, %d Vds, %d Vbs",
            mos_type,
            len(result["length"]),
            result["length"][0] * 1e6,
            result["length"][-1] * 1e6,
            len(result["vgs"]),
            len(result["vds"]),
            len(result["vbs"]),
        )
        return result

    def available_lengths(self, mos_type: str = "nmos") -> list[float]:
        """Return available L values in meters."""
        data = self._load(mos_type)
        return list(data["length"])

    def _find_nearest_idx(self, arr: np.ndarray, value: float) -> int:
        """Find index of nearest value in array (ascending or descending)."""
        return int(np.argmin(np.abs(arr - value)))

    def _interp_length(
        self, arr: np.ndarray, lengths: np.ndarray, L: float
    ) -> np.ndarray:
        """Interpolate 4D array along length axis at given L.

        Returns 3D array (Vbs, Vgs, Vds).
        """
        if L <= lengths[0]:
            return arr[0]
        if L >= lengths[-1]:
            return arr[-1]

        idx = np.searchsorted(lengths, L) - 1
        idx = max(0, min(idx, len(lengths) - 2))
        L0, L1 = lengths[idx], lengths[idx + 1]
        frac = (L - L0) / (L1 - L0)
        return arr[idx] * (1 - frac) + arr[idx + 1] * frac

    def lookup(
        self,
        mos_type: str = "nmos",
        L_um: float = 1.0,
        Vds: float = 0.6,
        Vbs: float = 0.0,
    ) -> dict:
        """Lookup transistor characteristics at given operating point.

        Returns a dict with arrays indexed by Vgs:
            gm_id: gm/ID [S/A] vs Vgs
            gm_gds: intrinsic gain (gm/gds) vs Vgs
            id_w: current density ID/W [A/m] vs Vgs
            fT: transit frequency [Hz] vs Vgs (if cgg available)
            vth: threshold voltage [V]

        Parameters
        ----------
        mos_type : str
            "nmos" or "pmos"
        L_um : float
            Channel length in micrometers
        Vds : float
            Drain-source voltage (positive for NMOS, negative for PMOS)
        Vbs : float
            Body-source voltage
        """
        data = self._load(mos_type)
        L = L_um * 1e-6

        # Find nearest Vbs and Vds indices
        vbs_idx = self._find_nearest_idx(data["vbs"], Vbs)
        vds_idx = self._find_nearest_idx(data["vds"], Vds)

        # Interpolate along length axis
        id_3d = self._interp_length(data["id"], data["length"], L)
        gm_3d = self._interp_length(data["gm"], data["length"], L)
        gds_3d = self._interp_length(data["gds"], data["length"], L)
        vth_3d = self._interp_length(data["vth"], data["length"], L)

        # Extract 1D arrays at (Vbs, Vds) point
        id_1d = id_3d[vbs_idx, :, vds_idx]
        gm_1d = gm_3d[vbs_idx, :, vds_idx]
        gds_1d = gds_3d[vbs_idx, :, vds_idx]
        vth_1d = vth_3d[vbs_idx, :, vds_idx]

        # Compute derived quantities
        # Avoid division by zero
        eps = 1e-30
        gm_id = np.where(np.abs(id_1d) > eps, gm_1d / np.abs(id_1d), 0.0)
        gm_gds = np.where(np.abs(gds_1d) > eps, np.abs(gm_1d) / np.abs(gds_1d), 0.0)
        id_w = id_1d / 10e-6  # Reference width is 10um

        result = {
            "vgs": data["vgs"].tolist(),
            "gm_id": gm_id.tolist(),
            "gm_gds": gm_gds.tolist(),
            "id_w": id_w.tolist(),
            "vth": float(np.median(vth_1d[vth_1d != 0])) if np.any(vth_1d != 0) else 0.0,
            "L_um": L_um,
            "Vds": float(data["vds"][vds_idx]),
            "Vbs": float(data["vbs"][vbs_idx]),
        }

        # Transit frequency if capacitance data available
        if "cgg" in data:
            cgg_3d = self._interp_length(data["cgg"], data["length"], L)
            cgg_1d = cgg_3d[vbs_idx, :, vds_idx]
            fT = np.where(
                np.abs(cgg_1d) > eps,
                np.abs(gm_1d) / (2 * np.pi * np.abs(cgg_1d)),
                0.0,
            )
            result["fT"] = fT.tolist()

        return result

    def query_at_gmid(
        self,
        target_gmid: float,
        mos_type: str = "nmos",
        L_um: float = 1.0,
        Vds: float = 0.6,
        Vbs: float = 0.0,
    ) -> dict | None:
        """Query transistor performance at a specific gm/ID target.

        Returns the interpolated intrinsic gain, current density, fT,
        and Vgs at the target gm/ID operating point.

        Parameters
        ----------
        target_gmid : float
            Target gm/ID in S/A (e.g., 12 for moderate inversion)
        mos_type : str
            "nmos" or "pmos"
        L_um : float
            Channel length in micrometers
        Vds : float
            Drain-source voltage
        Vbs : float
            Body-source voltage

        Returns
        -------
        dict or None
            Keys: gm_gds, id_w_A_m, fT_Hz, Vgs, gm_id_actual, L_um
            Returns None if target_gmid is outside the data range.
        """
        data_dict = self.lookup(mos_type, L_um, Vds, Vbs)
        vgs = np.array(data_dict["vgs"])
        gm_id = np.array(data_dict["gm_id"])
        gm_gds = np.array(data_dict["gm_gds"])
        id_w = np.array(data_dict["id_w"])

        # Use the monotonically decreasing portion of gm/ID vs |Vgs|.
        # gm/ID peaks near subthreshold and decreases into strong inversion.
        # Find the peak and use only the decreasing portion (above threshold).
        peak_idx = int(np.argmax(gm_id))

        # Use data from peak onwards (increasing |Vgs|, decreasing gm/ID)
        gm_id_mono = gm_id[peak_idx:]
        vgs_mono = vgs[peak_idx:]
        gm_gds_mono = gm_gds[peak_idx:]
        id_w_mono = id_w[peak_idx:]

        # Filter out invalid points (gm/ID <= 0)
        valid = gm_id_mono > 0.5
        if np.sum(valid) < 2:
            return None

        gm_id_v = gm_id_mono[valid]
        vgs_v = vgs_mono[valid]
        gm_gds_v = gm_gds_mono[valid]
        id_w_v = id_w_mono[valid]

        # Ensure gm/ID is monotonically decreasing (for np.interp we need increasing)
        # Reverse arrays so gm/ID is ascending
        gm_id_v = gm_id_v[::-1]
        vgs_v = vgs_v[::-1]
        gm_gds_v = gm_gds_v[::-1]
        id_w_v = id_w_v[::-1]

        # Check if target is in range
        gmid_min, gmid_max = float(gm_id_v[0]), float(gm_id_v[-1])
        if target_gmid < gmid_min or target_gmid > gmid_max:
            return None

        # Interpolate
        vgs_at_target = float(np.interp(target_gmid, gm_id_v, vgs_v))
        gain_at_target = float(np.interp(target_gmid, gm_id_v, gm_gds_v))
        idw_at_target = float(np.interp(target_gmid, gm_id_v, id_w_v))

        result = {
            "gm_gds": round(gain_at_target, 2),
            "gm_gds_dB": round(20 * np.log10(max(gain_at_target, 1e-10)), 1),
            "id_w_A_m": idw_at_target,
            "Vgs": round(vgs_at_target, 4),
            "gm_id_actual": round(target_gmid, 2),
            "L_um": L_um,
            "mos_type": mos_type,
        }

        # fT interpolation if available
        if "fT" in data_dict:
            fT = np.array(data_dict["fT"])
            fT_mono = fT[peak_idx:]
            fT_v = fT_mono[valid][::-1]
            if len(fT_v) == len(gm_id_v):
                fT_at_target = float(np.interp(target_gmid, gm_id_v, fT_v))
                result["fT_Hz"] = fT_at_target
                result["fT_GHz"] = round(fT_at_target / 1e9, 3)

        return result

    def gain_at_length(
        self,
        L_um: float,
        mos_type: str = "nmos",
        gmid_target: float = 12.0,
        Vds: float = 0.6,
        Vbs: float = 0.0,
    ) -> dict | None:
        """Convenience: get intrinsic gain at given L and gm/ID.

        This is the most commonly needed lookup for OTA design:
        "What gain can I get from this transistor at this operating point?"
        """
        return self.query_at_gmid(gmid_target, mos_type, L_um, Vds, Vbs)

    def sweep_lengths(
        self,
        gmid_target: float = 12.0,
        mos_type: str = "nmos",
        Vds: float = 0.6,
        Vbs: float = 0.0,
        L_range_um: tuple[float, float] | None = None,
    ) -> list[dict]:
        """Sweep across all available L values at fixed gm/ID.

        Returns list of dicts with gain, fT, id_w at each L.
        Useful for understanding the gain-speed tradeoff.

        Parameters
        ----------
        gmid_target : float
            Target gm/ID operating point
        mos_type : str
            "nmos" or "pmos"
        Vds : float
            Drain-source voltage
        Vbs : float
            Body-source voltage
        L_range_um : tuple, optional
            (min, max) L range in um. If None, uses all available.
        """
        data = self._load(mos_type)
        lengths = data["length"]

        results = []
        for L in lengths:
            L_um = float(L * 1e6)
            if L_range_um:
                if L_um < L_range_um[0] or L_um > L_range_um[1]:
                    continue

            point = self.query_at_gmid(gmid_target, mos_type, L_um, Vds, Vbs)
            if point:
                results.append(point)

        return results

    def design_summary(
        self,
        L_um: float,
        mos_type: str = "nmos",
        Vds: float = 0.6,
        Vbs: float = 0.0,
    ) -> str:
        """Human-readable summary of transistor at given L.

        Returns a formatted string with key metrics at three
        inversion levels (weak, moderate, strong).
        """
        lines = [
            f"{self.pdk.display_name} {mos_type.upper()} at L={L_um}um, Vds={Vds}V, Vbs={Vbs}V",
            f"{'gm/ID':>8} {'gm/gds':>8} {'gain_dB':>8} {'ID/W':>12} {'fT':>10} {'Region':>12}",
            "-" * 70,
        ]

        for gmid, region in [(25, "weak inv"), (15, "moderate"), (10, "moderate-strong"), (5, "strong inv")]:
            point = self.query_at_gmid(gmid, mos_type, L_um, Vds, Vbs)
            if point:
                fT_str = f"{point.get('fT_GHz', 0):.2f} GHz" if "fT_GHz" in point else "N/A"
                lines.append(
                    f"{gmid:>8.0f} {point['gm_gds']:>8.1f} {point['gm_gds_dB']:>8.1f} "
                    f"{point['id_w_A_m']:>12.4e} {fT_str:>10} {region:>12}"
                )
            else:
                lines.append(f"{gmid:>8.0f}     {'(out of range)':>40} {region:>12}")

        return "\n".join(lines)

    def to_json_summary(
        self,
        L_um: float,
        mos_type: str = "nmos",
        Vds: float = 0.6,
        Vbs: float = 0.0,
    ) -> str:
        """JSON summary for agent consumption."""
        points = {}
        for gmid in [25, 20, 15, 12, 10, 8, 5]:
            point = self.query_at_gmid(gmid, mos_type, L_um, Vds, Vbs)
            if point:
                points[f"gmid_{gmid}"] = {
                    "gm_gds": point["gm_gds"],
                    "gain_dB": point["gm_gds_dB"],
                    "id_w_A_m": point["id_w_A_m"],
                }
                if "fT_GHz" in point:
                    points[f"gmid_{gmid}"]["fT_GHz"] = point["fT_GHz"]

        return json.dumps({
            "mos_type": mos_type,
            "L_um": L_um,
            "Vds": Vds,
            "Vbs": Vbs,
            "available_lengths_um": [
                round(length * 1e6, 3)
                for length in self.available_lengths(mos_type)
            ],
            "operating_points": points,
        }, indent=2)

    # ---------------------------------------------------------------- #
    # gm/ID sizing API (S4 — Arcadia-1 reconciliation)
    #
    # Methods below mirror the public surface of the gmoverid-skill
    # ``GmIdTable`` class (``size``, ``size_from_ft``, ``size_from_gmro``,
    # ``operating_range``) but are reimplemented from scratch against our
    # 4D ``(L, Vbs, Vgs, Vds)`` LUT layout, so they work natively on
    # IHP-SG13G2 + GF180MCU without any PTM detour. The output dict is
    # the canonical ``{W_um, L_um, Id_uA, gm_uS, gds_uS, ft_Hz, vgs_V,
    # vds_V}`` schema declared for eda-agents S4.
    # ---------------------------------------------------------------- #

    _SIZE_DICT_KEYS = (
        "W_um", "L_um", "Id_uA", "gm_uS", "gds_uS",
        "ft_Hz", "vgs_V", "vds_V", "vbs_V",
        "gmid", "gmro",
    )

    def _per_width_at_gmid(
        self,
        target_gmid: float,
        mos_type: str,
        L_um: float,
        Vds: float,
        Vbs: float,
    ) -> dict | None:
        """Interpolate per-unit-width quantities at a target gm/ID.

        Returns ``None`` when the LUT slice has no valid monotone
        gm/ID branch or ``target_gmid`` is out of the achievable range.
        All per-width values use Amperes/Siemens per metre so the
        caller can multiply by the user-requested W (in metres) to
        get the absolute operating point.

        Returned dict:
            vgs_V, vds_V, vbs_V,
            id_per_w_Apm, gm_per_w_Spm, gds_per_w_Spm,
            gm_gds, fT_Hz, vth_V, gmid_actual
        """
        data = self._load(mos_type)
        L = L_um * 1e-6

        vbs_idx = self._find_nearest_idx(data["vbs"], Vbs)
        vds_idx = self._find_nearest_idx(data["vds"], Vds)

        id_3d = self._interp_length(data["id"], data["length"], L)
        gm_3d = self._interp_length(data["gm"], data["length"], L)
        gds_3d = self._interp_length(data["gds"], data["length"], L)
        vth_3d = self._interp_length(data["vth"], data["length"], L)

        id_1d = id_3d[vbs_idx, :, vds_idx]
        gm_1d = gm_3d[vbs_idx, :, vds_idx]
        gds_1d = gds_3d[vbs_idx, :, vds_idx]
        vth_1d = vth_3d[vbs_idx, :, vds_idx]

        eps = 1e-30
        gm_id_full = np.where(np.abs(id_1d) > eps, gm_1d / np.abs(id_1d), 0.0)

        # Work on the decreasing branch (above-threshold side), as
        # query_at_gmid does. gm/ID climbs to a peak in sub-threshold
        # and falls monotonically into strong inversion; only the
        # falling portion is physically meaningful for sizing.
        peak_idx = int(np.argmax(gm_id_full))
        gm_id = gm_id_full[peak_idx:]
        vgs = data["vgs"][peak_idx:]
        gm = gm_1d[peak_idx:]
        gds = gds_1d[peak_idx:]
        id_ = id_1d[peak_idx:]
        vth = vth_1d[peak_idx:]

        valid = gm_id > 0.5
        if np.sum(valid) < 2:
            return None

        gm_id = gm_id[valid]
        vgs = vgs[valid]
        gm = gm[valid]
        gds = gds[valid]
        id_ = id_[valid]
        vth = vth[valid]

        # np.interp needs ascending x. gm/ID is descending with Vgs, so
        # reverse everything first.
        gm_id = gm_id[::-1]
        vgs = vgs[::-1]
        gm = gm[::-1]
        gds = gds[::-1]
        id_ = id_[::-1]
        vth = vth[::-1]

        if target_gmid < gm_id[0] or target_gmid > gm_id[-1]:
            return None

        w_ref = float(data.get("w_ref_m", 10e-6))

        def _interp(y):
            return float(np.interp(target_gmid, gm_id, y))

        gm_at = _interp(gm)
        gds_at = _interp(gds)
        id_at = _interp(id_)
        vgs_at = _interp(vgs)
        vth_at_candidates = vth[vth != 0]
        vth_at = (
            float(np.median(vth_at_candidates))
            if vth_at_candidates.size
            else 0.0
        )

        out: dict = {
            "vgs_V": vgs_at,
            "vds_V": float(data["vds"][vds_idx]),
            "vbs_V": float(data["vbs"][vbs_idx]),
            "id_per_w_Apm": id_at / w_ref,
            "gm_per_w_Spm": gm_at / w_ref,
            "gds_per_w_Spm": gds_at / w_ref,
            "gm_gds": gm_at / gds_at if abs(gds_at) > eps else float("inf"),
            "vth_V": vth_at,
            "gmid_actual": target_gmid,
            "w_ref_m": w_ref,
        }

        if "cgg" in data:
            cgg_3d = self._interp_length(data["cgg"], data["length"], L)
            cgg_1d = cgg_3d[vbs_idx, :, vds_idx]
            cgg_trim = cgg_1d[peak_idx:][valid][::-1]
            if len(cgg_trim) == len(gm_id):
                cgg_at = _interp(cgg_trim)
                if abs(cgg_at) > eps:
                    out["fT_Hz"] = gm_at / (2 * np.pi * cgg_at)
                    out["cgg_per_w_Fpm"] = cgg_at / w_ref
                else:
                    out["fT_Hz"] = None
            else:
                out["fT_Hz"] = None
        else:
            out["fT_Hz"] = None

        return out

    def size(
        self,
        gmid: float,
        mos_type: str = "nmos",
        L_um: float = 1.0,
        Vds: float = 0.6,
        Vbs: float = 0.0,
        *,
        Id: float | None = None,
        W: float | None = None,
        gm: float | None = None,
    ) -> dict:
        """Size a transistor at a target gm/ID.

        Exactly one of ``Id`` [A], ``W`` [µm], or ``gm`` [S] must be
        provided. The remaining two are solved from the LUT slice at
        the requested ``(L_um, Vds, Vbs)``.

        Returns the canonical sizing dict::

            {
                "W_um": ..., "L_um": ..., "Id_uA": ...,
                "gm_uS": ..., "gds_uS": ...,
                "ft_Hz": ..., "vgs_V": ..., "vds_V": ...,
                "vbs_V": ..., "gmid": ..., "gmro": ...,
            }

        Raises ``ValueError`` if the constraint set is ambiguous or if
        the ``gmid`` is out of range for the LUT slice.
        """
        n_given = sum(x is not None for x in (Id, W, gm))
        if n_given != 1:
            raise ValueError(
                "Provide exactly one of Id [A], W [um], or gm [S]."
            )

        per_w = self._per_width_at_gmid(
            float(gmid), mos_type, L_um, Vds, Vbs
        )
        if per_w is None:
            raise ValueError(
                f"gm/ID={gmid} is out of range for {mos_type} "
                f"at L={L_um} um, Vds={Vds} V, Vbs={Vbs} V."
            )

        id_per_w = per_w["id_per_w_Apm"]
        gm_per_w = per_w["gm_per_w_Spm"]
        gds_per_w = per_w["gds_per_w_Spm"]

        if Id is not None:
            Id_A = float(Id)
            if id_per_w <= 0:
                raise ValueError(
                    "Non-positive current density at this operating "
                    "point; cannot invert to W."
                )
            W_m = Id_A / id_per_w
        elif W is not None:
            W_m = float(W) * 1e-6
            Id_A = id_per_w * W_m
        else:
            gm_A = float(gm)
            if gm_per_w <= 0:
                raise ValueError(
                    "Non-positive gm/W at this operating point; "
                    "cannot invert to W."
                )
            W_m = gm_A / gm_per_w
            Id_A = id_per_w * W_m

        gm_S = gm_per_w * W_m
        gds_S = gds_per_w * W_m
        gmro = gm_S / gds_S if gds_S > 0 else float("inf")

        return {
            "W_um": W_m * 1e6,
            "L_um": float(L_um),
            "Id_uA": Id_A * 1e6,
            "gm_uS": gm_S * 1e6,
            "gds_uS": gds_S * 1e6,
            "ft_Hz": per_w["fT_Hz"],
            "vgs_V": per_w["vgs_V"],
            "vds_V": per_w["vds_V"],
            "vbs_V": per_w["vbs_V"],
            "gmid": float(gmid),
            "gmro": gmro,
            "vth_V": per_w["vth_V"],
            "mos_type": mos_type,
        }

    def _gmid_grid_at_slice(
        self,
        mos_type: str,
        L_um: float,
        Vds: float,
        Vbs: float,
        n_points: int = 64,
    ) -> tuple[np.ndarray, list[dict]]:
        """Dense per-width sampling of the LUT slice vs gm/ID.

        Returns ``(gmid_array, per_w_list)`` where ``gmid_array`` is an
        ascending vector and ``per_w_list[i]`` is the dict returned by
        ``_per_width_at_gmid`` at ``gmid_array[i]``. Used internally by
        ``size_from_ft`` / ``size_from_gmro`` to search across the
        achievable gm/ID range.
        """
        data = self._load(mos_type)
        L = L_um * 1e-6
        vbs_idx = self._find_nearest_idx(data["vbs"], Vbs)
        vds_idx = self._find_nearest_idx(data["vds"], Vds)

        id_3d = self._interp_length(data["id"], data["length"], L)
        gm_3d = self._interp_length(data["gm"], data["length"], L)

        id_1d = id_3d[vbs_idx, :, vds_idx]
        gm_1d = gm_3d[vbs_idx, :, vds_idx]
        eps = 1e-30
        gm_id_full = np.where(np.abs(id_1d) > eps, gm_1d / np.abs(id_1d), 0.0)

        peak_idx = int(np.argmax(gm_id_full))
        gm_id = gm_id_full[peak_idx:]
        valid = gm_id > 0.5
        if np.sum(valid) < 2:
            return np.array([]), []

        branch = gm_id[valid]
        gmid_min = float(np.min(branch))
        gmid_max = float(np.max(branch))

        grid = np.linspace(gmid_min, gmid_max, int(n_points))
        per_w_list: list[dict] = []
        for g in grid:
            p = self._per_width_at_gmid(float(g), mos_type, L_um, Vds, Vbs)
            if p is not None:
                per_w_list.append(p)
            else:
                per_w_list.append({})

        return grid, per_w_list

    def size_from_ft(
        self,
        ft_target_hz: float,
        mos_type: str = "nmos",
        L_um: float = 1.0,
        Vds: float = 0.6,
        Vbs: float = 0.0,
        *,
        Id: float | None = None,
        W: float | None = None,
    ) -> dict:
        """Pick the most power-efficient gm/ID (highest) that still
        hits ``ft_target_hz`` at ``(L_um, Vds, Vbs)``, then size to the
        user's Id or W.

        Raises ``ValueError`` if no operating point on the LUT slice
        meets the fT target.
        """
        if (Id is None) == (W is None):
            raise ValueError("Provide exactly one of Id [A] or W [um].")

        grid, per_list = self._gmid_grid_at_slice(
            mos_type, L_um, Vds, Vbs
        )
        if grid.size == 0:
            raise ValueError(
                f"No valid gm/ID branch for {mos_type} at L={L_um} um, "
                f"Vds={Vds} V, Vbs={Vbs} V."
            )

        ft_vals = np.array([
            (p["fT_Hz"] if p and p.get("fT_Hz") is not None else -1.0)
            for p in per_list
        ])
        mask = ft_vals >= float(ft_target_hz)
        if not np.any(mask):
            ft_max = float(np.nanmax(ft_vals))
            raise ValueError(
                f"ft_target={ft_target_hz/1e9:.2f} GHz exceeds max "
                f"achievable fT={ft_max/1e9:.2f} GHz for {mos_type} "
                f"at L={L_um} um."
            )

        # grid is ascending in gm/ID → highest gm/ID satisfying the
        # fT constraint is the largest masked index.
        best_idx = int(np.where(mask)[0].max())
        best_gmid = float(grid[best_idx])
        return self.size(
            best_gmid,
            mos_type=mos_type, L_um=L_um, Vds=Vds, Vbs=Vbs,
            Id=Id, W=W,
        )

    def size_from_gmro(
        self,
        gmro_target: float,
        mos_type: str = "nmos",
        L_um: float = 1.0,
        Vds: float = 0.6,
        Vbs: float = 0.0,
        *,
        Id: float | None = None,
        W: float | None = None,
    ) -> dict:
        """Pick the most power-efficient gm/ID (highest) that still
        hits a minimum intrinsic-gain target (``gm * ro``)."""
        if (Id is None) == (W is None):
            raise ValueError("Provide exactly one of Id [A] or W [um].")

        grid, per_list = self._gmid_grid_at_slice(
            mos_type, L_um, Vds, Vbs
        )
        if grid.size == 0:
            raise ValueError(
                f"No valid gm/ID branch for {mos_type} at L={L_um} um, "
                f"Vds={Vds} V, Vbs={Vbs} V."
            )

        gmro_vals = np.array([
            (p["gm_gds"] if p and p.get("gm_gds") is not None else -1.0)
            for p in per_list
        ])
        mask = gmro_vals >= float(gmro_target)
        if not np.any(mask):
            gmro_max = float(np.nanmax(gmro_vals))
            raise ValueError(
                f"gmro_target={gmro_target:.1f} exceeds max achievable "
                f"gm*ro={gmro_max:.1f} for {mos_type} at L={L_um} um."
            )

        best_idx = int(np.where(mask)[0].max())
        best_gmid = float(grid[best_idx])
        return self.size(
            best_gmid,
            mos_type=mos_type, L_um=L_um, Vds=Vds, Vbs=Vbs,
            Id=Id, W=W,
        )

    def operating_range(self, mos_type: str = "nmos") -> dict:
        """Summarise the achievable operating envelope of the LUT.

        Returns the min/max of gm/ID across the full sweep and the
        raw Vgs / Vds / Vbs / L axes so callers can bound their
        search. The exact ``8 keys`` contract (from the Arcadia-1
        S4 spec) is: ``gmid_min``, ``gmid_max``, ``id_density_min``,
        ``id_density_max``, ``L_min_um``, ``L_max_um``, ``vgs_range``,
        ``vds_range``.
        """
        data = self._load(mos_type)
        eps = 1e-30
        id_arr = data["id"]
        gm_arr = data["gm"]
        with np.errstate(divide="ignore", invalid="ignore"):
            gm_id = np.where(
                np.abs(id_arr) > eps, gm_arr / np.abs(id_arr), 0.0
            )
        valid = gm_id > 0.5
        if np.any(valid):
            gmid_min = float(gm_id[valid].min())
            gmid_max = float(gm_id[valid].max())
        else:
            gmid_min = 0.0
            gmid_max = 0.0

        w_ref = float(data.get("w_ref_m", 10e-6))
        id_density = np.abs(id_arr) / w_ref
        id_density_valid = id_density[np.abs(id_arr) > eps]
        if id_density_valid.size:
            id_density_min = float(id_density_valid.min())
            id_density_max = float(id_density_valid.max())
        else:
            id_density_min = 0.0
            id_density_max = 0.0

        return {
            "gmid_min": gmid_min,
            "gmid_max": gmid_max,
            "id_density_min": id_density_min,  # A/m
            "id_density_max": id_density_max,  # A/m
            "L_min_um": float(data["length"][0]) * 1e6,
            "L_max_um": float(data["length"][-1]) * 1e6,
            "vgs_range": (
                float(data["vgs"][0]),
                float(data["vgs"][-1]),
            ),
            "vds_range": (
                float(data["vds"][0]),
                float(data["vds"][-1]),
            ),
        }
