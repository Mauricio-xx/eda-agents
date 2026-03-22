"""gm/ID lookup table interface for IHP SG13G2 MOSFET characterization.

Provides fast lookup of transistor performance metrics from pre-generated
ngspice PSP103 sweep data. Agents use this for informed design decisions
BEFORE running expensive SPICE simulations.

Key metrics available:
    - gm/gds (intrinsic gain) at given L, gm/ID
    - ID/W (current density) at given gm/ID
    - fT (transit frequency) at given L, gm/ID
    - Vth (threshold voltage) at given L
    - Vdsat at given operating point

LUT data source: ihp-gmid-kit (.npz files from ngspice PSP103 DC sweeps)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Default LUT location
_DEFAULT_LUT_DIR = Path("/home/montanares/personal_exp/ihp-gmid-kit/data")

# LUT file names
_NMOS_FILE = "sg13_lv_nmos.npz"
_PMOS_FILE = "sg13_lv_pmos.npz"


class GmIdLookup:
    """gm/ID lookup table for IHP SG13G2 MOSFETs.

    Loads .npz LUT data and provides interpolated lookups for
    transistor performance at arbitrary operating points.

    Parameters
    ----------
    lut_dir : Path, optional
        Directory containing .npz LUT files.
    """

    def __init__(self, lut_dir: Path | None = None):
        self.lut_dir = Path(lut_dir) if lut_dir else _DEFAULT_LUT_DIR
        self._nmos: dict | None = None
        self._pmos: dict | None = None

    def _load(self, mos_type: str) -> dict:
        """Load and cache LUT data for given type."""
        if mos_type == "nmos":
            if self._nmos is not None:
                return self._nmos
            fname = _NMOS_FILE
            model_key = "sg13_lv_nmos"
        else:
            if self._pmos is not None:
                return self._pmos
            fname = _PMOS_FILE
            model_key = "sg13_lv_pmos"

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
        """Find index of nearest value in sorted array."""
        idx = np.searchsorted(arr, value)
        if idx == 0:
            return 0
        if idx >= len(arr):
            return len(arr) - 1
        if abs(arr[idx] - value) < abs(arr[idx - 1] - value):
            return int(idx)
        return int(idx - 1)

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
            f"IHP SG13G2 {mos_type.upper()} at L={L_um}um, Vds={Vds}V, Vbs={Vbs}V",
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
            "available_lengths_um": [round(l * 1e6, 3) for l in self.available_lengths(mos_type)],
            "operating_points": points,
        }, indent=2)
