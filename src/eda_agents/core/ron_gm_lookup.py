"""Ron/gm lookup for inverter-based dynamic amplifiers (IBAs).

Implements the methodology from Wrøngm (Code-a-Chip VLSI26 #18, Apache-2.0,
Nithin P et al., 2025) on top of the same PSP103 (.npz) LUT that
``eda_agents.core.gmid_lookup.GmIdLookup`` already consumes for IHP SG13G2
and GF180MCU. No new external LUT data is required: Ron is derived
analytically from ``Vds / Id`` at the user-specified on-state operating
point, then divided by the small-signal ``gm`` at the bias operating
point read from the same LUT slice.

Two operating points coexist:

  * **On-state** (``Vgs_on``, ``Vds_on``): the transistor is fully driven
    (gate at VDD in Wrøngm's reference testbench) and conducts the
    large-signal RC settling current. ``Ron = Vds_on / Id_on``.
  * **Bias point** (``Vgs_bias``, ``Vds_bias``): the device is biased
    by the replica mirror to the small-signal operating point that
    sets gm. ``gm_bias`` is the small-signal transconductance there.

The headline metric is

    Ron/gm = (Vds_on / Id_on) / gm_bias

which scales as ``1/W^2`` (Ron ∝ 1/W, gm ∝ W). The deadzone bias
``V_DZN`` / ``V_DZP`` is the ``Vbias`` at which Ron/gm crosses a
designer-chosen threshold from the high side -- a transistor biased
below that Vbias has not yet entered an operating region where the
two-phase settling model holds.

This module is the data backbone for the ``analog.ron_gm_sizing`` skill
and the ``InverterBasedAmplifier`` topology
(``eda_agents.topologies.iba_ihp``).

Faithful upstream
=================

The chennakeshavadasa/gmid_IHP130 companion repo
(commit ``c31c01edbed41c06078b8272c32997c03db0000e``) ships per-corner
CSV LUTs (75 files, ~5 MB) that capture Ron/gm at exactly the operating
points Wrøngm's notebook uses for its design helper. The CSV path is
intentionally NOT consumed here; the analytical path is simpler, keeps
the existing PSP103 LUT as a single source of truth, and works
identically for IHP and GF180. If a future divergence forces us to
follow the CSV LUT verbatim, add a ``RonGmCsvLookup`` sibling rather
than mixing the two.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from eda_agents.core.gmid_lookup import GmIdLookup
from eda_agents.core.pdk import PdkConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RonGmPoint:
    """Sized device at a Ron/gm operating point.

    Mirrors ``GmIdLookup.size()`` return dict so downstream code can
    consume both interchangeably, with the Ron-specific fields added
    on top.
    """

    W_um: float
    L_um: float
    Id_uA: float
    gm_uS: float
    gds_uS: float
    ft_Hz: float | None
    vgs_V: float
    vds_V: float
    vbs_V: float
    gmid: float
    gmro: float
    vth_V: float
    mos_type: str
    # Ron/gm specific
    Ron_ohm: float          # at (Vgs_on, Vds_on), scaled to W_um
    Ron_gm: float           # Ron / gm at the bias point, dimensionless
    Ipeak_uA: float         # peak large-signal current at on-state
    Vds_on_V: float
    Vgs_on_V: float
    deadzone_bias_V: float  # informational; threshold defined by user
    deadzone_threshold: float

    def as_sizing_dict(self) -> dict:
        """Return the dict shape GmIdLookup.size() returns, extended."""
        return {
            "W_um": self.W_um,
            "L_um": self.L_um,
            "Id_uA": self.Id_uA,
            "gm_uS": self.gm_uS,
            "gds_uS": self.gds_uS,
            "ft_Hz": self.ft_Hz,
            "vgs_V": self.vgs_V,
            "vds_V": self.vds_V,
            "vbs_V": self.vbs_V,
            "gmid": self.gmid,
            "gmro": self.gmro,
            "vth_V": self.vth_V,
            "mos_type": self.mos_type,
            "Ron_ohm": self.Ron_ohm,
            "Ron_gm": self.Ron_gm,
            "Ipeak_uA": self.Ipeak_uA,
            "Vds_on_V": self.Vds_on_V,
            "Vgs_on_V": self.Vgs_on_V,
            "deadzone_bias_V": self.deadzone_bias_V,
            "deadzone_threshold": self.deadzone_threshold,
        }


class RonGmLookup:
    """Ron/gm-based sizing built on top of GmIdLookup.

    Composition, not inheritance: ``GmIdLookup`` already understands
    PDK selection, LUT directory resolution, length interpolation, and
    the per-PDK env-var conventions. We reuse all of that and add the
    Ron-centric views Wrøngm's methodology needs.

    Parameters
    ----------
    gmid : GmIdLookup, optional
        Existing lookup instance to reuse. If omitted, a new one is
        constructed from ``pdk`` and ``lut_dir``.
    pdk : PdkConfig or str, optional
        PDK selector forwarded to ``GmIdLookup``. Ignored if ``gmid``
        is provided.
    lut_dir : Path, optional
        LUT directory override forwarded to ``GmIdLookup``. Ignored if
        ``gmid`` is provided.
    """

    def __init__(
        self,
        gmid: GmIdLookup | None = None,
        *,
        pdk: PdkConfig | str | None = None,
        lut_dir: Path | None = None,
    ) -> None:
        self.gmid = gmid or GmIdLookup(pdk=pdk, lut_dir=lut_dir)
        self.pdk = self.gmid.pdk

    # ------------------------------------------------------------------
    # Per-unit-width point lookups.
    #
    # The LUT was generated at ``w_ref_m`` (10 µm for ihp-gmid-kit;
    # 1 µm in Wrøngm's own CSVs). Per-width values (A/m, S/m) let the
    # caller multiply by their target W in metres to get the absolute
    # operating point.
    # ------------------------------------------------------------------

    def _lut_sign(self, mos_type: str, data: dict) -> int:
        """Sign multiplier mapping user-facing positive magnitudes to LUT
        axis values.

        The IHP and GF180 LUTs store NMOS sweeps in ``[0, +Vmax]`` and
        PMOS sweeps in ``[0, -Vmax]``. Callers of ``ron_gm`` /
        ``size_from_ron_gm`` pass positive magnitudes (``VSG`` /
        ``VSD`` for PMOS, ``Vgs`` / ``Vds`` for NMOS) so the public
        API is symmetric; this helper picks ``-1`` for PMOS so we can
        index the LUT with the correct sign.
        """
        vgs = data["vgs"]
        # Axis can be ascending NMOS or descending PMOS (starts at 0,
        # ends at the negative max). Pick by the tail sign.
        return -1 if vgs[-1] < 0 else 1

    def _point(
        self,
        mos_type: str,
        L_um: float,
        Vgs: float,
        Vds: float,
        Vbs: float = 0.0,
    ) -> dict:
        """Interpolate Id, gm, gds, Cgg (if present) at the chosen point.

        Callers pass positive magnitudes (``Vgs`` for NMOS = VGS;
        ``Vgs`` for PMOS = VSG). The LUT sign convention is applied
        internally so user-facing math stays symmetric across device
        polarity.

        Returns per-unit-width values plus the per-LUT-row references.
        All values are returned positive.
        """
        data = self.gmid._load(mos_type)
        L = L_um * 1e-6
        w_ref = float(data.get("w_ref_m", 10e-6))

        sign = self._lut_sign(mos_type, data)
        Vgs_signed = sign * Vgs
        Vds_signed = sign * Vds
        Vbs_signed = sign * Vbs

        vbs_idx = self.gmid._find_nearest_idx(data["vbs"], Vbs_signed)
        vds_idx = self.gmid._find_nearest_idx(data["vds"], Vds_signed)
        vgs_idx = self.gmid._find_nearest_idx(data["vgs"], Vgs_signed)

        id_3d = self.gmid._interp_length(data["id"], data["length"], L)
        gm_3d = self.gmid._interp_length(data["gm"], data["length"], L)
        gds_3d = self.gmid._interp_length(data["gds"], data["length"], L)
        vth_3d = self.gmid._interp_length(data["vth"], data["length"], L)

        id_val = float(id_3d[vbs_idx, vgs_idx, vds_idx])
        gm_val = float(gm_3d[vbs_idx, vgs_idx, vds_idx])
        gds_val = float(gds_3d[vbs_idx, vgs_idx, vds_idx])
        vth_val = float(vth_3d[vbs_idx, vgs_idx, vds_idx])

        # Sign convention: report positive magnitudes regardless of
        # device polarity. PSP103 returns negative Id/gm for PMOS in
        # some sweeps; abs() collapses both.
        id_abs = abs(id_val)
        gm_abs = abs(gm_val)
        gds_abs = abs(gds_val)

        out = {
            "id_per_w_Apm": id_abs / w_ref,
            "gm_per_w_Spm": gm_abs / w_ref,
            "gds_per_w_Spm": gds_abs / w_ref,
            "vth_V": abs(vth_val) if vth_val else 0.0,
            # Report user-facing positive magnitudes regardless of
            # polarity so downstream code does not have to track sign.
            "vgs_V": abs(float(data["vgs"][vgs_idx])),
            "vds_V": abs(float(data["vds"][vds_idx])),
            "vbs_V": abs(float(data["vbs"][vbs_idx])),
            "w_ref_m": w_ref,
        }

        if "cgg" in data:
            cgg_3d = self.gmid._interp_length(data["cgg"], data["length"], L)
            cgg_val = float(cgg_3d[vbs_idx, vgs_idx, vds_idx])
            out["cgg_per_w_Fpm"] = abs(cgg_val) / w_ref
        else:
            out["cgg_per_w_Fpm"] = None
        return out

    def _clip_vgs_to_lut(self, mos_type: str, Vgs: float) -> float:
        """Cap ``|Vgs|`` to the LUT's available range, return the
        positive magnitude.

        Wrøngm drives the on-state device at ``VG = VDD = 1.65 V``; our
        IHP PSP103 LUT typically stops at ``|Vgs| = 1.5 V``. Clipping is
        the only way to read the on-state point without re-running the
        LUT generator -- log a warning when it happens so users see the
        compromise.
        """
        data = self.gmid._load(mos_type)
        vgs_max_abs = abs(float(data["vgs"][-1]))
        if abs(Vgs) > vgs_max_abs + 1e-6:
            logger.warning(
                "Vgs=%.3fV clipped to LUT max %.3fV for %s (consider "
                "regenerating the LUT with vgs_max=VDD if Ron values "
                "look off)",
                Vgs, vgs_max_abs, mos_type,
            )
            return vgs_max_abs
        return abs(Vgs)

    # ------------------------------------------------------------------
    # Ron and Ron/gm queries.
    # ------------------------------------------------------------------

    def ron_per_w(
        self,
        mos_type: str,
        L_um: float,
        Vgs_on: float,
        Vds_on: float,
        Vbs: float = 0.0,
    ) -> float:
        """Per-unit-width ``Ron = Vds_on / Id`` at the on-state point.

        Returns ``Ron · W`` in Ω · m; multiply by ``W^-1`` (1/m) to get
        the absolute Ron of a device with width W. Equivalently, the
        absolute Ron of a W-wide device is::

            Ron_ohm = ron_per_w / W_m
        """
        Vgs_eff = self._clip_vgs_to_lut(mos_type, Vgs_on)
        p = self._point(mos_type, L_um, Vgs_eff, Vds_on, Vbs)
        id_per_w = p["id_per_w_Apm"]
        if id_per_w <= 0:
            raise ValueError(
                f"Id/W is non-positive at {mos_type} (L={L_um}um, "
                f"Vgs={Vgs_on}V, Vds={Vds_on}V) -- device is below "
                "threshold; Ron is undefined."
            )
        # Ron · W = Vds / (Id / W). Units: V / (A/m) = V·m/A = Ω·m.
        return Vds_on / id_per_w

    def gm_per_w(
        self,
        mos_type: str,
        L_um: float,
        Vgs_bias: float,
        Vds_bias: float,
        Vbs: float = 0.0,
    ) -> float:
        """Per-unit-width gm [S/m] at the bias operating point."""
        return self._point(mos_type, L_um, Vgs_bias, Vds_bias, Vbs)["gm_per_w_Spm"]

    def ron_gm(
        self,
        mos_type: str,
        L_um: float,
        W_um: float,
        Vgs_bias: float,
        Vds_on: float,
        Vds_bias: float,
        Vgs_on: float | None = None,
        Vbs: float = 0.0,
    ) -> dict:
        """Ron/gm at user-chosen on-state and bias operating points.

        Computes ``Ron(Vgs_on, Vds_on)`` divided by ``gm(Vgs_bias, Vds_bias)``
        for a device of width ``W_um``. Both terms scale linearly with W
        (Ron ∝ 1/W, gm ∝ W), so the resulting ``Ron · gm`` is
        W-independent and ``Ron/gm`` scales as ``1/W^2``.

        Parameters
        ----------
        Vgs_on : float, optional
            Gate-source voltage at the on-state point. Defaults to the
            PDK's VDD so the on-state device is fully driven, matching
            Wrøngm's reference testbench (``VG = 1.65 V`` on IHP SG13G2
            LV).
        """
        if Vgs_on is None:
            Vgs_on = self.pdk.VDD
        W_m = W_um * 1e-6

        ron_per_w = self.ron_per_w(mos_type, L_um, Vgs_on, Vds_on, Vbs)
        Ron_ohm = ron_per_w / W_m

        gm_per_w = self.gm_per_w(mos_type, L_um, Vgs_bias, Vds_bias, Vbs)
        gm_S = gm_per_w * W_m
        if gm_S <= 0:
            raise ValueError(
                f"gm is non-positive at {mos_type} bias point "
                f"(L={L_um}um, Vgs={Vgs_bias}V, Vds={Vds_bias}V) -- "
                "the bias point is below threshold; choose a higher Vbias."
            )

        ron_gm_val = Ron_ohm / gm_S

        # Ipeak: peak large-signal current at the on-state point for a
        # W_um device. Same Id/W as the Ron read, multiplied by W.
        on_point = self._point(mos_type, L_um, self._clip_vgs_to_lut(mos_type, Vgs_on), Vds_on, Vbs)
        Ipeak_A = on_point["id_per_w_Apm"] * W_m

        bias = self._point(mos_type, L_um, Vgs_bias, Vds_bias, Vbs)
        Id_bias_A = bias["id_per_w_Apm"] * W_m
        gds_bias_S = bias["gds_per_w_Spm"] * W_m
        gmro = gm_S / gds_bias_S if gds_bias_S > 0 else float("inf")
        gmid_actual = gm_S / Id_bias_A if Id_bias_A > 0 else float("inf")
        ft_Hz = None
        if bias["cgg_per_w_Fpm"] is not None and bias["cgg_per_w_Fpm"] > 0:
            ft_Hz = gm_per_w / (2 * np.pi * bias["cgg_per_w_Fpm"])

        return {
            "Ron_ohm": Ron_ohm,
            "gm_S": gm_S,
            "Ron_gm": ron_gm_val,
            "Ipeak_A": Ipeak_A,
            "Id_bias_A": Id_bias_A,
            "gds_S": gds_bias_S,
            "gmro": gmro,
            "gmid": gmid_actual,
            "ft_Hz": ft_Hz,
            "vth_V": bias["vth_V"],
            "Vgs_on_V": self._clip_vgs_to_lut(mos_type, Vgs_on),
            "Vds_on_V": Vds_on,
            "Vgs_bias_V": Vgs_bias,
            "Vds_bias_V": Vds_bias,
            "Vbs_V": Vbs,
        }

    # ------------------------------------------------------------------
    # Deadzone (Wrøngm cell 4.1).
    #
    # Plot 4.1 sweeps Vbias across the operating range and reads off the
    # Vbias at which log(Ron/gm) crosses a designer-chosen threshold
    # from above. That Vbias is the boundary of the two-phase settling
    # window -- below it, the device hasn't entered the operating
    # region where the methodology holds.
    # ------------------------------------------------------------------

    def deadzone_bias(
        self,
        mos_type: str,
        L_um: float,
        W_um: float,
        ron_gm_threshold: float,
        Vds_on: float,
        Vds_bias: float,
        Vgs_on: float | None = None,
        Vbs: float = 0.0,
        Vbias_min: float = 0.0,
        Vbias_max: float | None = None,
    ) -> dict:
        """Find the Vbias at which Ron/gm equals ``ron_gm_threshold``.

        Sweeps the LUT's Vgs axis between ``Vbias_min`` and ``Vbias_max``,
        builds ``Ron/gm`` as a function of Vbias for the given W, and
        interpolates the crossing.

        Returns ``{Vbias_V, Ron_gm, achievable}``. ``achievable`` is
        ``True`` when the threshold lies inside the sweep range;
        ``False`` when Ron/gm never drops to the requested level (the
        device cannot operate that fast at this L/W). In the False
        case ``Vbias_V`` is the bound that minimised Ron/gm.
        """
        if Vgs_on is None:
            Vgs_on = self.pdk.VDD
        if Vbias_max is None:
            Vbias_max = self.pdk.VDD

        data = self.gmid._load(mos_type)
        vgs_axis_mag = np.abs(np.asarray(data["vgs"]))
        mask = (vgs_axis_mag >= Vbias_min) & (vgs_axis_mag <= Vbias_max)
        vgs_sweep = vgs_axis_mag[mask]
        if vgs_sweep.size == 0:
            raise ValueError(
                f"Vbias range [{Vbias_min}, {Vbias_max}] yields no LUT "
                f"points for {mos_type}; widen the range."
            )

        ron_gm_curve: list[float] = []
        for v in vgs_sweep:
            try:
                point = self.ron_gm(
                    mos_type, L_um, W_um,
                    Vgs_bias=float(v),
                    Vds_on=Vds_on,
                    Vds_bias=Vds_bias,
                    Vgs_on=Vgs_on,
                    Vbs=Vbs,
                )
                ron_gm_curve.append(point["Ron_gm"])
            except ValueError:
                ron_gm_curve.append(float("inf"))
        ron_gm_arr = np.asarray(ron_gm_curve)

        finite = np.isfinite(ron_gm_arr)
        if not finite.any():
            raise ValueError(
                "Ron/gm is infinite/non-finite across the whole sweep; "
                "the device never turns on at the specified bias point."
            )

        # Ron/gm decreases monotonically as Vbias passes Vth and the
        # device enters strong inversion. Read the crossing from the
        # decreasing branch.
        ron_log = np.log10(np.where(finite, ron_gm_arr, np.nan))
        log_target = np.log10(float(ron_gm_threshold))

        # Find the first (smallest) Vbias whose log(Ron/gm) drops at or
        # below the target.
        idx_below = np.where(ron_log <= log_target)[0]
        if idx_below.size == 0:
            best_idx = int(np.nanargmin(ron_log))
            return {
                "Vbias_V": float(vgs_sweep[best_idx]),
                "Ron_gm": float(ron_gm_arr[best_idx]),
                "achievable": False,
            }

        i = int(idx_below[0])
        if i == 0:
            return {
                "Vbias_V": float(vgs_sweep[i]),
                "Ron_gm": float(ron_gm_arr[i]),
                "achievable": True,
            }

        # Linear interp in log(Ron/gm) between the two bracketing samples.
        x0, x1 = float(vgs_sweep[i - 1]), float(vgs_sweep[i])
        y0, y1 = float(ron_log[i - 1]), float(ron_log[i])
        if y1 == y0:
            v_cross = x1
        else:
            v_cross = x0 + (log_target - y0) * (x1 - x0) / (y1 - y0)
        return {
            "Vbias_V": float(v_cross),
            "Ron_gm": float(ron_gm_threshold),
            "achievable": True,
        }

    # ------------------------------------------------------------------
    # Sizing entry point.
    # ------------------------------------------------------------------

    def size_from_ron_gm(
        self,
        ron_gm_target: float,
        mos_type: str,
        L_um: float,
        Ibias_uA: float,
        *,
        Vds_on: float | None = None,
        Vds_bias: float | None = None,
        Vgs_on: float | None = None,
        Vbs: float = 0.0,
        Vbias_min: float = 0.0,
        Vbias_max: float | None = None,
        gmid_max: float = 20.0,
    ) -> RonGmPoint:
        """Size a single device by the Ron/gm methodology.

        Algorithm (analytical port of Wrøngm's nearest-neighbour helper,
        cells 109-115):

          1. Sweep the LUT's Vgs axis, building ``W(Vbias) = Ibias /
             (Id/W)`` and ``Ron/gm @ W(Vbias)`` at each candidate.
          2. Discard candidates with ``gm/ID > gmid_max`` so the search
             does not slide into deep subthreshold where ``gm/I_D`` is
             artificially high and the sized device balloons. Wrøngm's
             helper avoids this implicitly by anchoring the search to
             a moderate-inversion characterisation current; we expose
             ``gmid_max`` (default 20 S/A, "moderate inversion or
             stronger") as the equivalent guardrail.
          3. Pick the candidate that meets ``Ron/gm ≤ ron_gm_target``
             with the highest ``Vbias`` (smallest device, fastest
             large-signal). If no candidate meets the target, return
             the closest miss and emit a warning.

        ``Vds_on`` defaults to ``0.05 V`` (the linear-region edge of
        the LUT sweep) so ``Ron = Vds/Id`` reflects the switch-on
        resistance the device exhibits during the RC settling phase,
        matching Wrøngm's cell-29 testbench. ``Vds_bias`` defaults to
        ``VDD / 2`` (mid-rail). Override either if your circuit drives
        a different settling step or asymmetric rails.

        Raises ``ValueError`` if no operating point in the LUT range
        can meet the target.
        """
        if Vgs_on is None:
            Vgs_on = self.pdk.VDD
        if Vds_on is None:
            Vds_on = 0.05
        if Vds_bias is None:
            Vds_bias = self.pdk.VDD / 2
        if Vbias_max is None:
            Vbias_max = self.pdk.VDD

        data = self.gmid._load(mos_type)
        # User passes positive Vbias magnitudes; LUT axis is negative
        # for PMOS. Convert axis to magnitudes before masking so the
        # interval bounds work the same for both polarities.
        vgs_axis_mag = np.abs(np.asarray(data["vgs"]))
        mask = (vgs_axis_mag >= Vbias_min) & (vgs_axis_mag <= Vbias_max)
        vgs_sweep = vgs_axis_mag[mask]
        if vgs_sweep.size == 0:
            raise ValueError(
                f"Vbias range [{Vbias_min}, {Vbias_max}] yields no LUT "
                f"points for {mos_type}."
            )

        Ibias_A = Ibias_uA * 1e-6
        Vgs_on_eff = self._clip_vgs_to_lut(mos_type, Vgs_on)

        # Cache the on-state read: Id/W at (Vgs_on, Vds_on) only
        # depends on L (and Vbs); independent of Vbias.
        on_point = self._point(mos_type, L_um, Vgs_on_eff, Vds_on, Vbs)
        id_per_w_on = on_point["id_per_w_Apm"]
        if id_per_w_on <= 0:
            raise ValueError(
                f"Id/W is non-positive at the on-state for {mos_type} "
                f"(L={L_um}um); device cannot conduct -- choose a "
                "shorter L or check the LUT."
            )
        # Ron · W = Vds / (Id/W) -- W-independent.
        ron_w = Vds_on / id_per_w_on

        # For each Vbias, compute W such that Id_bias = Ibias_A, then
        # the achievable Ron/gm at that W. Keep the smallest |Ron/gm -
        # target|; require achievable Ron/gm to be <= target (i.e. the
        # device is fast enough).
        candidates: list[tuple[float, float, float, float]] = []
        for v in vgs_sweep:
            bias_point = self._point(mos_type, L_um, float(v), Vds_bias, Vbs)
            id_per_w_bias = bias_point["id_per_w_Apm"]
            if id_per_w_bias <= 0:
                continue
            W_m = Ibias_A / id_per_w_bias
            if W_m <= 0:
                continue
            gm_per_w_bias = bias_point["gm_per_w_Spm"]
            if gm_per_w_bias <= 0:
                continue
            # gm/ID is W-independent (both scale with W). Compute once
            # per Vbias and filter out the subthreshold tail.
            gmid_here = gm_per_w_bias / id_per_w_bias
            if gmid_here > gmid_max:
                continue
            gm_S = gm_per_w_bias * W_m
            Ron_ohm = ron_w / W_m
            achieved = Ron_ohm / gm_S
            candidates.append((float(v), W_m, achieved, gmid_here))

        if not candidates:
            raise ValueError(
                f"No valid Vbias in [{Vbias_min}, {Vbias_max}] V "
                f"reaches Ibias={Ibias_uA} uA at the chosen bias "
                f"point for {mos_type} L={L_um}um with gm/ID <= "
                f"{gmid_max}. Either raise Ibias, increase gmid_max, "
                "or pick a different L."
            )

        # Among candidates meeting ron_gm_target, pick the one with the
        # highest Vbias (smallest device, lowest Ron, fastest large-
        # signal settling). If nothing meets, pick the closest miss.
        meeting = [c for c in candidates if c[2] <= ron_gm_target]
        if meeting:
            meeting.sort(key=lambda t: -t[0])
            Vbias_pick, W_m_pick, achieved_pick, _ = meeting[0]
        else:
            candidates.sort(key=lambda t: t[2])
            Vbias_pick, W_m_pick, achieved_pick, _ = candidates[0]
            logger.warning(
                "Ron/gm target=%.3g not achievable for %s L=%.3g um "
                "Ibias=%.3g uA (gmid_max=%.1f); best=%.3g at "
                "Vbias=%.3f V",
                ron_gm_target, mos_type, L_um, Ibias_uA, gmid_max,
                achieved_pick, Vbias_pick,
            )

        W_um = W_m_pick * 1e6
        full = self.ron_gm(
            mos_type, L_um, W_um,
            Vgs_bias=Vbias_pick,
            Vds_on=Vds_on,
            Vds_bias=Vds_bias,
            Vgs_on=Vgs_on_eff,
            Vbs=Vbs,
        )
        # Deadzone informational only; record the same threshold we
        # solved against so reports stay coherent.
        try:
            dz = self.deadzone_bias(
                mos_type, L_um, W_um,
                ron_gm_threshold=ron_gm_target,
                Vds_on=Vds_on,
                Vds_bias=Vds_bias,
                Vgs_on=Vgs_on_eff,
                Vbs=Vbs,
                Vbias_min=Vbias_min,
                Vbias_max=Vbias_max,
            )
            dz_bias = dz["Vbias_V"]
        except ValueError:
            dz_bias = float("nan")

        return RonGmPoint(
            W_um=W_um,
            L_um=L_um,
            Id_uA=full["Id_bias_A"] * 1e6,
            gm_uS=full["gm_S"] * 1e6,
            gds_uS=full["gds_S"] * 1e6,
            ft_Hz=full["ft_Hz"],
            vgs_V=Vbias_pick,
            vds_V=Vds_bias,
            vbs_V=Vbs,
            gmid=full["gmid"],
            gmro=full["gmro"],
            vth_V=full["vth_V"],
            mos_type=mos_type,
            Ron_ohm=full["Ron_ohm"],
            Ron_gm=full["Ron_gm"],
            Ipeak_uA=full["Ipeak_A"] * 1e6,
            Vds_on_V=Vds_on,
            Vgs_on_V=Vgs_on_eff,
            deadzone_bias_V=dz_bias,
            deadzone_threshold=ron_gm_target,
        )

    # ------------------------------------------------------------------
    # Diagnostics.
    # ------------------------------------------------------------------

    def operating_range(self, mos_type: str = "nmos") -> dict:
        """Summarise the achievable Ron/gm envelope.

        Returns the min/max Ron/gm achievable for a 1 µm wide device at
        each LUT length, plus the on/bias slice info, so designers can
        scope what is physically realisable before fixing a target.
        """
        data = self.gmid._load(mos_type)
        lengths_um = (np.asarray(data["length"]) * 1e6).tolist()

        # Probe Ron/gm at the LUT's bias-rail midpoint for each L.
        vds_bias = float(data["vds"][-1]) / 2.0
        vds_on = vds_bias
        Vgs_on = min(self.pdk.VDD, float(data["vgs"][-1]))

        ron_gm_min: list[float] = []
        ron_gm_max: list[float] = []
        for L_um in lengths_um:
            sweep = []
            for v in data["vgs"]:
                try:
                    p = self.ron_gm(
                        mos_type, L_um, 1.0,
                        Vgs_bias=float(v),
                        Vds_on=vds_on,
                        Vds_bias=vds_bias,
                        Vgs_on=Vgs_on,
                    )
                    if np.isfinite(p["Ron_gm"]) and p["Ron_gm"] > 0:
                        sweep.append(p["Ron_gm"])
                except ValueError:
                    continue
            if not sweep:
                ron_gm_min.append(float("nan"))
                ron_gm_max.append(float("nan"))
            else:
                ron_gm_min.append(min(sweep))
                ron_gm_max.append(max(sweep))

        return {
            "L_um": lengths_um,
            "Ron_gm_min_perW1um": ron_gm_min,
            "Ron_gm_max_perW1um": ron_gm_max,
            "Vds_on_V": vds_on,
            "Vds_bias_V": vds_bias,
            "Vgs_on_V": Vgs_on,
            "Vgs_max_V": float(data["vgs"][-1]),
            "VDD_V": self.pdk.VDD,
            "w_ref_m": float(data.get("w_ref_m", 10e-6)),
        }
