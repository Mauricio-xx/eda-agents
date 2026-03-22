"""Miller OTA analytical designer using sEKV methodology.

Wraps the EKV normalized functions and IHP SG13G2 process parameters
for fast analytical design of two-stage Miller-compensated OTAs.

Design space dimensions:
    gmid_input: gm/ID of input differential pair (5-25 S/A)
    gmid_load:  gm/ID of first-stage active load (5-20 S/A)
    L_input:    channel length of input pair (0.13-2.0 um)
    L_load:     channel length of load transistors (0.13-2.0 um)
    Cc:         Miller compensation capacitor (0.1-5.0 pF)

Based on:
    - sEKV-Design-in-IHP-SG13G2 by C. Enz
    - IHP SG13G2 130nm BiCMOS extracted parameters
"""

from __future__ import annotations

import math
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eda_agents.core.pdk import PdkConfig, resolve_pdk

# ---------------------------------------------------------------------------
# EKV normalized functions (inlined from ekv_functions.py)
# ---------------------------------------------------------------------------

_LN = math.log
_SQRT = math.sqrt
_EXP = math.exp
_PI = math.pi


def _linspace(start: float, stop: float, n: int) -> list[float]:
    """Pure-Python linspace (no numpy dependency)."""
    if n <= 1:
        return [start]
    step = (stop - start) / (n - 1)
    return [start + i * step for i in range(n)]


def _ic_q(q: float) -> float:
    """Normalized drain current from charge."""
    return q * (q + 1)


def _q_ic(ic: float) -> float:
    """Normalized charge from inversion coefficient."""
    return (_SQRT(4 * ic + 1) - 1) / 2


def _gms_ic(ic: float) -> float:
    """Normalized source transconductance (long-channel)."""
    return (_SQRT(4 * ic + 1) - 1) / 2


def _gmsid_ic(ic: float) -> float:
    """Normalized Gm/ID (long-channel)."""
    return _gms_ic(ic) / ic if ic > 0 else 2.0


def _ic_gmsid(gmsid: float) -> float:
    """IC from normalized Gm/ID (long-channel)."""
    if gmsid >= 2.0:
        return 1e-6  # deep weak inversion limit
    return (1 - gmsid) / gmsid**2


def _vps_ic(ic: float) -> float:
    """Normalized saturation voltage from IC."""
    return _LN(_SQRT(4 * ic + 1) - 1) + _SQRT(4 * ic + 1) - 1 - _LN(2)


def _vdssat_ic(ic: float) -> float:
    """Drain-source saturation voltage (normalized) from IC."""
    return 2 * _SQRT(ic + 4)


def _cgsi_ic(ic: float) -> float:
    """Intrinsic gate-source capacitance ratio in saturation."""
    qs = _q_ic(ic)
    num = 2 * qs + 3
    den = (qs + 1) ** 2
    return qs / 3 * num / den


def _gamman_ic(ic: float, n: float) -> float:
    """Thermal noise excess factor (long-channel)."""
    qs = _q_ic(ic)
    deltan = 2 / 3 * (qs + 3 / 4) / (qs + 1)
    return n * deltan


# ---------------------------------------------------------------------------
# IHP SG13G2 process parameters (from ihp130g2_sekv.py)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProcessParams:
    """IHP SG13G2 130nm BiCMOS sEKV process parameters."""

    # Physical constants
    kB: float = 1.38064852e-23
    q_e: float = 1.60217662e-19
    T: float = 300.15  # 27C
    epsilon0: float = 8.854e-12
    epsilonox: float = 3.9

    # Process
    tox: float = 2.2404e-9
    VDD: float = 1.2
    Lmin: float = 130e-9
    Wmin: float = 150e-9
    z1: float = 340e-9  # junction perimeter constant

    # nMOS
    DLn: float = 58.846e-9
    DWn: float = -20.0e-9
    n0n: float = 1.22
    Ispecsqn: float = 708.3e-9  # A / (W/L)
    VT0n: float = 0.246
    lambdan: float = 0.8e6  # 1/m
    KFn: float = 2.208e-24
    AVTn: float = 5.0e-9
    Abetan: float = 0.01e-6

    # Overlap/fringing caps (nMOS)
    CGSOn: float = 4.535e-10
    CGDOn: float = 4.535e-10
    CGSFn: float = 2.0e-10
    CGDFn: float = 2.0e-10

    # Junction caps (nMOS)
    CJn: float = 9.764e-4
    CJSWSTIn: float = 2.528e-11
    CJSWGATn: float = 3.0e-11

    # pMOS
    DLp: float = 50.508e-9
    DWp: float = 30.0e-9
    n0p: float = 1.23
    Ispecsqp: float = 244.6e-9
    VT0p: float = 0.365
    lambdap: float = 6.078e6
    KFp: float = 12.0e-24
    AVTp: float = 5.0e-9
    Abetap: float = 0.01e-6

    # Overlap/fringing caps (pMOS)
    CGSOp: float = 4.426e-10
    CGDOp: float = 4.426e-10
    CGSFp: float = 1.0e-10
    CGDFp: float = 1.0e-10

    # Junction caps (pMOS)
    CJp: float = 8.631e-4
    CJSWSTIp: float = 3.192e-11
    CJSWGATp: float = 2.747e-11

    @property
    def UT(self) -> float:
        return self.kB * self.T / self.q_e

    @property
    def Cox(self) -> float:
        return self.epsilonox * self.epsilon0 / self.tox


# ---------------------------------------------------------------------------
# Design specifications
# ---------------------------------------------------------------------------

@dataclass
class MillerOTASpecs:
    """Miller OTA target specifications.

    Defaults are realistic for a simple (non-cascode) Miller OTA on
    IHP SG13G2 130nm BiCMOS:
    - 50 dB gain: achievable with L >= 1um, requires careful sizing
    - 1 MHz GBW: modest, agents need Ibias > ~0.5 uA with Cc=0.5pF
    - 60 deg PM: standard stability target
    - 10 mV Vos: 3-sigma, needs adequate W*L for mismatch
    """

    Adc_dB: float = 50.0       # DC gain [dB]
    GBW: float = 1e6           # Gain-bandwidth product [Hz]
    CL: float = 1e-12          # Load capacitance [F]
    PM_deg: float = 60.0       # Phase margin [degrees]
    Vos_max: float = 10e-3     # Max input offset voltage [V]
    VDD: float = 1.2           # Supply voltage [V]

    @property
    def Adc(self) -> float:
        return 10 ** (self.Adc_dB / 20)


# ---------------------------------------------------------------------------
# Transistor sizing result
# ---------------------------------------------------------------------------

@dataclass
class TransistorParams:
    """Parameters for a single transistor."""

    name: str
    mos_type: str  # "nmos" or "pmos"
    W: float = 0.0   # [m]
    L: float = 0.0   # [m]
    ID: float = 0.0   # [A]
    IC: float = 0.0   # inversion coefficient
    Gm: float = 0.0   # [S]
    Gds: float = 0.0  # [S]
    VDSsat: float = 0.0  # [V]

    @property
    def area(self) -> float:
        """Active area [m^2]."""
        return self.W * self.L

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.mos_type,
            "W_um": self.W * 1e6,
            "L_um": self.L * 1e6,
            "ID_uA": self.ID * 1e6,
            "IC": self.IC,
            "Gm_uS": self.Gm * 1e6,
            "Gds_nS": self.Gds * 1e9,
            "VDSsat_mV": self.VDSsat * 1e3,
        }


# ---------------------------------------------------------------------------
# Design result
# ---------------------------------------------------------------------------

@dataclass
class DesignResult:
    """Complete Miller OTA analytical design result."""

    # Design variables
    gmid_input: float = 0.0   # [S/A]
    gmid_load: float = 0.0    # [S/A]
    L_input: float = 0.0      # [m]
    L_load: float = 0.0       # [m]
    Cc: float = 0.0           # [F]
    Ibias: float = 0.0        # first-stage bias current per branch [A]

    # Spec thresholds for penalty (set by designer from MillerOTASpecs)
    _spec_Adc_dB: float = 70.0
    _spec_GBW: float = 1e6
    _spec_PM_deg: float = 60.0
    _spec_Vos_max: float = 10e-3

    # Transistors
    transistors: dict[str, TransistorParams] = field(default_factory=dict)

    # Performance
    Adc1: float = 0.0       # first-stage gain [V/V]
    Adc2: float = 0.0       # second-stage gain [V/V]
    Adc: float = 0.0        # total DC gain [V/V]
    Adc_dB: float = 0.0     # total DC gain [dB]
    GBW: float = 0.0        # gain-bandwidth product [Hz]
    fp1: float = 0.0        # dominant pole [Hz]
    fp2: float = 0.0        # non-dominant pole [Hz]
    fz: float = 0.0         # RHP zero [Hz]
    PM: float = 0.0         # phase margin [degrees]
    power_uW: float = 0.0   # total power [uW]
    area_um2: float = 0.0   # total transistor area [um^2]
    Ib1: float = 0.0        # first-stage bias current [A]
    Ib2: float = 0.0        # second-stage bias current [A]
    Vos_sigma: float = 0.0  # input offset voltage 1-sigma [V]

    # Validity
    valid: bool = False
    violations: list[str] = field(default_factory=list)

    @property
    def raw_FoM(self) -> float:
        """Unpenalized figure of merit: Adc * GBW / (Power_W * Area_m2)."""
        power_W = self.power_uW * 1e-6
        area_m2 = self.area_um2 * 1e-12
        if power_W <= 0 or area_m2 <= 0:
            return 0.0
        return self.Adc * self.GBW / (power_W * area_m2)

    @property
    def spec_penalty(self) -> float:
        """Penalty factor in [0, 1] based on how far specs are violated.

        Each violated spec contributes (actual/target)^2, clamped to [0, 1].
        Product of all factors. Returns 1.0 if all specs are met.
        Uses thresholds from the designer's MillerOTASpecs.
        """
        penalty = 1.0

        # Gain
        if self.Adc_dB < self._spec_Adc_dB:
            penalty *= max(0.0, self.Adc_dB / self._spec_Adc_dB) ** 2

        # GBW
        if self.GBW < self._spec_GBW:
            penalty *= max(0.0, self.GBW / self._spec_GBW) ** 2

        # Phase margin
        if self.PM < self._spec_PM_deg:
            penalty *= max(0.0, self.PM / self._spec_PM_deg) ** 2

        # Offset voltage: 3*sigma <= Vos_max
        vos_3sigma = 3 * self.Vos_sigma
        if vos_3sigma > self._spec_Vos_max:
            penalty *= min(1.0, self._spec_Vos_max / max(vos_3sigma, 1e-9)) ** 2

        return penalty

    @property
    def FoM(self) -> float:
        """Spec-penalized figure of merit: raw_FoM * spec_penalty.

        Higher is better. Penalizes designs that violate specs (gain, GBW,
        PM, offset voltage) with a quadratic penalty per violated spec.
        A design meeting all specs has penalty=1.0 (no change).
        """
        return self.raw_FoM * self.spec_penalty

    def summary(self) -> str:
        """One-line summary for knowledge sharing."""
        return (
            f"Av={self.Adc_dB:.1f}dB GBW={self.GBW/1e6:.2f}MHz "
            f"PM={self.PM:.1f}deg P={self.power_uW:.1f}uW "
            f"A={self.area_um2:.1f}um2 FoM={self.FoM:.2e} "
            f"valid={self.valid}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "gmid_input": self.gmid_input,
            "gmid_load": self.gmid_load,
            "L_input_um": self.L_input * 1e6,
            "L_load_um": self.L_load * 1e6,
            "Cc_pF": self.Cc * 1e12,
            "Ibias_uA": self.Ibias * 1e6,
            "Adc_dB": self.Adc_dB,
            "GBW_MHz": self.GBW / 1e6,
            "PM_deg": self.PM,
            "power_uW": self.power_uW,
            "area_um2": self.area_um2,
            "raw_FoM": self.raw_FoM,
            "spec_penalty": self.spec_penalty,
            "FoM": self.FoM,
            "valid": self.valid,
            "violations": self.violations,
            "Ib1_nA": self.Ib1 * 1e9,
            "Ib2_uA": self.Ib2 * 1e6,
            "transistors": {
                k: v.as_dict() for k, v in self.transistors.items()
            },
        }


# ---------------------------------------------------------------------------
# Miller OTA Designer
# ---------------------------------------------------------------------------

class MillerOTADesigner:
    """Analytical Miller OTA designer using sEKV methodology.

    Sizes a two-stage Miller-compensated OTA for IHP SG13G2 130nm:
        Stage 1: nMOS diff pair (M1a/M1b) + pMOS mirror load (M4a/M4b)
        Stage 2: pMOS CS amplifier (M2) + nMOS current source (M5b)
        Bias: nMOS mirror (M3a/M3b)
        Compensation: Miller capacitor Cc
    """

    # Second stage Gm ratio: Gm2 = GM2_RATIO * Gm1
    # This places the RHP zero at Gm2/Cc >> GBW for stability
    GM2_RATIO: float = 8.0

    def __init__(
        self,
        specs: MillerOTASpecs | None = None,
        process: ProcessParams | None = None,
        pdk: PdkConfig | str | None = None,
    ):
        self.specs = specs or MillerOTASpecs()
        self.proc = process or ProcessParams()
        self.pdk = resolve_pdk(pdk)

    def analytical_design(
        self,
        gmid_input: float,
        gmid_load: float,
        L_input: float,
        L_load: float,
        Cc: float,
        Ibias: float | None = None,
    ) -> DesignResult:
        """Run analytical design for given design variables.

        Args:
            gmid_input: gm/ID of input diff pair [S/A], range 5-25
            gmid_load: gm/ID of first-stage active load [S/A], range 5-20
            L_input: channel length of input pair [m], range 0.13e-6 to 2e-6
            L_load: channel length of load transistors [m], range 0.13e-6 to 2e-6
            Cc: Miller compensation capacitor [F], range 0.1e-12 to 5e-12
            Ibias: first-stage bias current per branch [A], range 0.5e-6 to 50e-6.
                If None, current is sized from GBW spec (legacy behavior).

        Returns:
            DesignResult with full sizing, performance, and validity.
        """
        p = self.proc
        s = self.specs
        UT = p.UT
        result = DesignResult(
            gmid_input=gmid_input,
            gmid_load=gmid_load,
            L_input=L_input,
            L_load=L_load,
            Cc=Cc,
            Ibias=Ibias if Ibias is not None else 0.0,
            _spec_Adc_dB=s.Adc_dB,
            _spec_GBW=s.GBW,
            _spec_PM_deg=s.PM_deg,
            _spec_Vos_max=s.Vos_max,
        )
        violations = []

        # --- Convert gm/ID [S/A] to inversion coefficient ---
        # gm/ID = gmsid_ic(IC) / (n * UT)
        gmsid_norm_1 = gmid_input * p.n0n * UT
        gmsid_norm_4 = gmid_load * p.n0p * UT

        if gmsid_norm_1 >= 2.0:
            gmsid_norm_1 = 1.999  # clamp at weak-inversion limit
        if gmsid_norm_4 >= 2.0:
            gmsid_norm_4 = 1.999

        IC1 = _ic_gmsid(gmsid_norm_1)
        IC4 = _ic_gmsid(gmsid_norm_4)

        if IC1 <= 0:
            violations.append("IC1 <= 0: gmid_input too high for nMOS")
            result.violations = violations
            return result

        if IC4 <= 0:
            violations.append("IC4 <= 0: gmid_load too high for pMOS")
            result.violations = violations
            return result

        # --- Stage 1: Differential pair M1a/M1b + mirror load M4a/M4b ---

        gmsid_1 = _gmsid_ic(IC1)

        if Ibias is not None:
            # Current-driven sizing: agent chooses Ib1, Gm1 is a result
            Ib1 = Ibias
            Gm1 = Ib1 * gmsid_1 / (p.n0n * UT)
        else:
            # Legacy GBW-spec sizing: Gm1 forced from spec, Ib1 derived
            wu = 2 * _PI * s.GBW
            Gm1 = wu * Cc
            Ib1 = Gm1 * p.n0n * UT / gmsid_1

        # W/L for M1
        Ispec1 = Ib1 / IC1
        WoverL1 = Ispec1 / p.Ispecsqn
        W1 = WoverL1 * L_input

        # Enforce minimum width
        if W1 < p.Wmin:
            W1 = p.Wmin
            WoverL1 = W1 / L_input
            Ispec1 = WoverL1 * p.Ispecsqn
            IC1 = Ib1 / Ispec1
            violations.append(f"M1 width clamped to Wmin={p.Wmin*1e9:.0f}nm")

        # Output conductance for M1
        Gds1 = Ib1 / (p.lambdan * L_input)
        VDSsat1 = UT * _vdssat_ic(IC1)

        m1 = TransistorParams(
            name="M1a", mos_type="nmos",
            W=W1, L=L_input, ID=Ib1, IC=IC1,
            Gm=Gm1, Gds=Gds1, VDSsat=VDSsat1,
        )

        # M4a/M4b: pMOS mirror load, same current Ib1
        Ispec4 = Ib1 / IC4
        WoverL4 = Ispec4 / p.Ispecsqp
        W4 = WoverL4 * L_load

        if W4 < p.Wmin:
            W4 = p.Wmin
            WoverL4 = W4 / L_load
            Ispec4 = WoverL4 * p.Ispecsqp
            IC4 = Ib1 / Ispec4
            violations.append(f"M4 width clamped to Wmin={p.Wmin*1e9:.0f}nm")

        Gm4 = Ib1 * _gmsid_ic(IC4) / (p.n0p * UT) if IC4 > 0 else 0
        Gds4 = Ib1 / (p.lambdap * L_load)
        VDSsat4 = UT * _vdssat_ic(IC4)

        m4 = TransistorParams(
            name="M4a", mos_type="pmos",
            W=W4, L=L_load, ID=Ib1, IC=IC4,
            Gm=Gm4, Gds=Gds4, VDSsat=VDSsat4,
        )

        # First-stage gain
        G1 = Gds1 + Gds4
        Adc1 = Gm1 / G1 if G1 > 0 else 0

        # --- Stage 2: pMOS CS amplifier M2 + nMOS load M5b ---

        # Gm2 = GM2_RATIO * Gm1 (for stability: pushes RHP zero high)
        Gm2 = self.GM2_RATIO * Gm1

        # Second-stage IC: moderate inversion for area efficiency
        IC2 = 3.0
        gmsid_2 = _gmsid_ic(IC2)
        Ib2 = Gm2 * p.n0p * UT / gmsid_2

        Ispec2 = Ib2 / IC2
        WoverL2 = Ispec2 / p.Ispecsqp

        # L2 = L_load: pMOS transistors share channel length (M2, M4)
        # Gain of second stage is a RESULT, not a target.
        L2 = L_load

        W2 = WoverL2 * L2
        if W2 < p.Wmin:
            W2 = p.Wmin
            violations.append(f"M2 width clamped to Wmin={p.Wmin*1e9:.0f}nm")

        Gds2 = Ib2 / (p.lambdap * L2)
        VDSsat2 = UT * _vdssat_ic(IC2)

        m2 = TransistorParams(
            name="M2", mos_type="pmos",
            W=W2, L=L2, ID=Ib2, IC=IC2,
            Gm=Gm2, Gds=Gds2, VDSsat=VDSsat2,
        )

        # M5b: nMOS current source, same current Ib2
        # L5 = L_input: nMOS transistors share channel length (M1, M5)
        L5 = L_input

        IC5 = 5.0  # moderate inversion for headroom
        Ispec5 = Ib2 / IC5
        WoverL5 = Ispec5 / p.Ispecsqn
        W5 = WoverL5 * L5
        if W5 < p.Wmin:
            W5 = p.Wmin
            violations.append(f"M5 width clamped to Wmin={p.Wmin*1e9:.0f}nm")

        Gds5 = Ib2 / (p.lambdan * L5)
        VDSsat5 = UT * _vdssat_ic(IC5)

        m5 = TransistorParams(
            name="M5b", mos_type="nmos",
            W=W5, L=L5, ID=Ib2, IC=IC5,
            Gm=0, Gds=Gds5, VDSsat=VDSsat5,
        )

        # Second-stage gain
        G2 = Gds2 + Gds5
        Adc2 = Gm2 / G2 if G2 > 0 else 0

        # --- Bias: nMOS mirror M3a/M3b (provides 2*Ib1 to diff pair) ---
        IC3 = 10.0  # strong inversion for compact area
        Ib3 = 2 * Ib1
        Ispec3 = Ib3 / IC3
        WoverL3 = Ispec3 / p.Ispecsqn
        # Use Wmin, solve for L3
        W3 = p.Wmin
        L3 = W3 / WoverL3 if WoverL3 > 0 else p.Lmin
        L3 = max(L3, p.Lmin)
        L3 = min(L3, 20e-6)

        Gds3 = Ib3 / (p.lambdan * L3)
        VDSsat3 = UT * _vdssat_ic(IC3)

        m3 = TransistorParams(
            name="M3a", mos_type="nmos",
            W=W3, L=L3, ID=Ib3, IC=IC3,
            Gm=0, Gds=Gds3, VDSsat=VDSsat3,
        )

        # --- Store transistors ---
        result.transistors = {
            "M1a": m1,
            "M1b": TransistorParams(
                name="M1b", mos_type="nmos",
                W=W1, L=L_input, ID=Ib1, IC=IC1,
                Gm=Gm1, Gds=Gds1, VDSsat=VDSsat1,
            ),
            "M2": m2,
            "M3a": m3,
            "M3b": TransistorParams(
                name="M3b", mos_type="nmos",
                W=W3, L=L3, ID=Ib3, IC=IC3,
                Gm=0, Gds=Gds3, VDSsat=VDSsat3,
            ),
            "M4a": m4,
            "M4b": TransistorParams(
                name="M4b", mos_type="pmos",
                W=W4, L=L_load, ID=Ib1, IC=IC4,
                Gm=Gm4, Gds=Gds4, VDSsat=VDSsat4,
            ),
            "M5a": TransistorParams(
                name="M5a", mos_type="nmos",
                W=W5, L=L5, ID=Ib2, IC=IC5,
                Gm=0, Gds=Gds5, VDSsat=VDSsat5,
            ),
            "M5b": m5,
        }

        # --- Performance calculations ---

        # DC gain
        result.Adc1 = Adc1
        result.Adc2 = Adc2
        result.Adc = Adc1 * Adc2
        result.Adc_dB = 20 * math.log10(result.Adc) if result.Adc > 0 else -999

        # GBW (actual, accounting for Cc loading)
        result.GBW = Gm1 / (2 * _PI * Cc)

        # Poles and zero
        # Dominant pole: fp1 ~ G1 / (2*pi * Adc2 * Cc)  (Miller effect)
        result.fp1 = G1 / (2 * _PI * result.Adc2 * Cc) if result.Adc2 > 0 else 0

        # Non-dominant pole: fp2 ~ Gm2 / (2*pi * CL)
        result.fp2 = Gm2 / (2 * _PI * s.CL)

        # RHP zero: fz = Gm2 / (2*pi * Cc)
        result.fz = Gm2 / (2 * _PI * Cc)

        # Phase margin (simplified two-pole one-zero model)
        GBW = result.GBW
        if result.fp2 > 0 and result.fz > 0 and GBW > 0:
            result.PM = (
                180
                - 90  # from dominant pole (always at -90 at GBW)
                - math.degrees(math.atan(GBW / result.fp2))
                - math.degrees(math.atan(GBW / result.fz))
            )
        else:
            result.PM = 0

        # Power
        Itotal = 2 * Ib1 + Ib2  # diff pair + second stage
        result.Ib1 = Ib1
        result.Ibias = Ib1  # store actual first-stage current (may differ from input in legacy mode)
        result.Ib2 = Ib2
        result.power_uW = s.VDD * Itotal * 1e6

        # Area (sum of all transistor active areas)
        total_area = sum(t.area for t in result.transistors.values())
        result.area_um2 = total_area * 1e12  # convert m^2 to um^2

        # Input offset voltage (1-sigma mismatch)
        if W1 > 0 and L_input > 0:
            sigma2_vt1 = p.AVTn**2 / (W1 * L_input)
            # Mirror contribution
            if W4 > 0 and L_load > 0:
                xi_vt = (Gm4 / Gm1) ** 2 * (p.AVTp / p.AVTn) ** 2 * (W1 * L_input) / (W4 * L_load) if Gm1 > 0 else 0
            else:
                xi_vt = 0
            result.Vos_sigma = _SQRT(sigma2_vt1 * (1 + xi_vt))
        else:
            result.Vos_sigma = float("inf")

        # --- Validity checks ---
        if result.Adc_dB < s.Adc_dB:
            violations.append(f"Gain {result.Adc_dB:.1f}dB < {s.Adc_dB}dB target")
        if result.GBW < s.GBW:
            violations.append(f"GBW {result.GBW/1e6:.3f}MHz < {s.GBW/1e6:.1f}MHz target")
        if result.PM < s.PM_deg:
            violations.append(f"PM {result.PM:.1f}deg < {s.PM_deg}deg target")
        if 3 * result.Vos_sigma > s.Vos_max:
            violations.append(f"3*Vos_sigma {3*result.Vos_sigma*1e3:.2f}mV > {s.Vos_max*1e3:.1f}mV")

        # Headroom check: VDSsat1 + VDSsat3 < VGS1 (M1 source above ground)
        Vicm = s.VDD / 2  # assume mid-rail common mode
        VGS1 = p.VT0n + p.n0n * UT * _vps_ic(IC1)
        headroom_bot = Vicm - VGS1
        if headroom_bot < VDSsat3 + 0.05:
            violations.append(f"Bottom headroom insufficient: {headroom_bot*1e3:.0f}mV")

        headroom_top = s.VDD - Vicm + VGS1 - VDSsat1
        if headroom_top < VDSsat4 + 0.05:
            violations.append(f"Top headroom insufficient: {headroom_top*1e3:.0f}mV")

        result.valid = len(violations) == 0
        result.violations = violations

        return result

    def evaluate_fom(self, result: DesignResult) -> float:
        """Evaluate figure of merit for a design result."""
        return result.FoM

    def generate_netlist(self, result: DesignResult, path: Path) -> Path:
        """Generate ngspice netlist for a design result.

        Creates three files at the given path:
            - miller_ota.net (circuit netlist)
            - miller_ota.par (sizing and bias parameters)
            - miller_ota.ac.cir (AC analysis control file)

        Returns path to the .ac.cir file.
        """
        path.mkdir(parents=True, exist_ok=True)
        p = self.proc
        s = self.specs

        # Junction area/perimeter calculations
        def _junc(W: float) -> tuple[float, float, float, float]:
            z = p.z1
            AS = W * z
            PS = 2 * (W + z)
            return AS, PS, AS, PS  # symmetric S/D

        # --- Parameter file ---
        par_lines = [
            f".param VDD={s.VDD} Vic={s.VDD/2} Vos=0.0 Ib={result.Ib1}",
            f".param CL={s.CL} Cc={result.Cc}",
        ]

        for name, t in result.transistors.items():
            if name.endswith("b") and name != "M5b" and name != "M3b":
                continue  # skip duplicates
            idx = name.replace("a", "").replace("b", "").replace("M", "")
            AS, PS, AD, PD = _junc(t.W)
            par_lines.append(
                f".param W{idx}={t.W:.4e} L{idx}={t.L:.4e} "
                f"AS{idx}={AS:.3e} PS{idx}={PS:.3e} AD{idx}={AD:.3e} PD{idx}={PD:.3e}"
            )

        par_file = path / "miller_ota.par"
        par_file.write_text("\n".join(par_lines) + "\n")

        # --- Netlist ---
        # Miller OTA: 2-stage with compensation capacitor
        # Stage 1: M1a/M1b (nMOS DP) + M4a/M4b (pMOS mirror load)
        # Stage 2: M2 (pMOS CS) + M5b (nMOS current source)
        # Bias: M3a/M3b (nMOS mirror)
        # Node names: n1=stage1 output, out=stage2 output, nb=bias, ns=DP source
        nmos = self.pdk.nmos_symbol
        pmos = self.pdk.pmos_symbol
        px = self.pdk.instance_prefix

        net_lines = [
            f"* Miller OTA - {self.pdk.display_name}",
            "* Stage 1: nMOS diff pair + pMOS mirror load",
            f"{px}1a n1  inp ns ns  {nmos} W={{W1}} L={{L1}} AS={{AS1}} PS={{PS1}} AD={{AD1}} PD={{PD1}}",
            f"{px}1b out inn ns ns  {nmos} W={{W1}} L={{L1}} AS={{AS1}} PS={{PS1}} AD={{AD1}} PD={{PD1}}",
            f"{px}4a n1  n1  VDD VDD {pmos} W={{W4}} L={{L4}} AS={{AS4}} PS={{PS4}} AD={{AD4}} PD={{PD4}}",
            f"{px}4b out n1  VDD VDD {pmos} W={{W4}} L={{L4}} AS={{AS4}} PS={{PS4}} AD={{AD4}} PD={{PD4}}",
            "* Stage 2: pMOS CS + nMOS current source",
            f"{px}2  vout out VDD VDD {pmos} W={{W2}} L={{L2}} AS={{AS2}} PS={{PS2}} AD={{AD2}} PD={{PD2}}",
            f"{px}5b vout nb2 0   0   {nmos} W={{W5}} L={{L5}} AS={{AS5}} PS={{PS5}} AD={{AD5}} PD={{PD5}}",
            "* Bias mirror",
            f"{px}3a nb  nb  0 0 {nmos} W={{W3}} L={{L3}} AS={{AS3}} PS={{PS3}} AD={{AD3}} PD={{PD3}}",
            f"{px}3b ns  nb  0 0 {nmos} W={{W3}} L={{L3}} AS={{AS3}} PS={{PS3}} AD={{AD3}} PD={{PD3}}",
            "* Second stage mirror diode for M5",
            f"{px}5a nb2 nb2 0 0 {nmos} W={{W5}} L={{L5}} AS={{AS5}} PS={{PS5}} AD={{AD5}} PD={{PD5}}",
            "Ibias2 VDD nb2 {Ib2}",
            f".param Ib2={result.Ib2}",
            "* Compensation and load",
            "Cc n1 vout {Cc}",
            "CL vout 0 {CL}",
            "* Supply and input",
            "Ib VDD nb {2*Ib}",
            "VVDD VDD 0 {VDD}",
            "Vic ic 0 {Vic}",
            "Vid id 0 DC={Vos} AC=1",
            "Einp inp ic id 0 0.5",
            "Einn inn ic id 0 -0.5",
        ]

        net_file = path / "miller_ota.net"
        net_file.write_text("\n".join(net_lines) + "\n")

        # --- AC analysis control file ---
        model_lib = f"$PDK_ROOT/{self.pdk.model_lib_rel}"

        lib_lines = []
        if self.pdk.model_corner:
            lib_lines.append(f".lib {model_lib} {self.pdk.model_corner}")
        else:
            lib_lines.append(f".include {model_lib}")

        osdi_lines = []
        if self.pdk.has_osdi():
            osdi_base = f"$PDK_ROOT/{self.pdk.osdi_dir_rel}"
            for osdi_file in self.pdk.osdi_files:
                osdi_lines.append(f"  osdi '{osdi_base}/{osdi_file}'")

        ac_lines = [
            f"Miller OTA AC analysis - {self.pdk.display_name}",
            f"",
            *lib_lines,
            f".include {par_file.name}",
            f".include {net_file.name}",
            f"",
            f".control",
            f"  set ngbehavior=hsa",
            *osdi_lines,
            f"  op",
            f"  save v(vout)",
            f"  ac dec 41 10 100MEG",
            f"  let AmagdB=vdb(vout)",
            f"  let Aphdeg=180/PI*vp(vout)",
            f"  meas ac Adc find AmagdB at=10",
            f"  meas ac Adc_peak max AmagdB",
            f"  meas ac GBW when AmagdB=0",
            f"  meas ac PGBW find Aphdeg at=GBW",
            f"  set wr_singlescale",
            f"  set wr_vecnames",
            f"  wrdata miller_ota.ac.dat AmagdB Aphdeg",
            f".endc",
            f".end",
        ]

        ac_file = path / "miller_ota.ac.cir"
        ac_file.write_text("\n".join(ac_lines) + "\n")

        return ac_file

    def run_simulation(
        self, result: DesignResult, work_dir: Path | None = None
    ) -> dict[str, Any]:
        """Generate netlist and run ngspice AC simulation.

        Returns dict with simulated performance metrics, or error info.
        Requires ngspice to be installed and PDK_ROOT set.
        """
        if work_dir is None:
            work_dir = Path(tempfile.mkdtemp(prefix="miller-ota-sim-"))

        cir_file = self.generate_netlist(result, work_dir)

        try:
            proc = subprocess.run(
                ["ngspice", "-b", str(cir_file)],
                capture_output=True, text=True, timeout=60,
                cwd=work_dir,
            )
        except FileNotFoundError:
            return {"error": "ngspice not found", "success": False}
        except subprocess.TimeoutExpired:
            return {"error": "ngspice timeout (60s)", "success": False}

        sim_result: dict[str, Any] = {
            "success": proc.returncode == 0,
            "stdout": proc.stdout[-2000:] if proc.stdout else "",
            "stderr": proc.stderr[-2000:] if proc.stderr else "",
        }

        if proc.returncode != 0:
            sim_result["error"] = "ngspice non-zero exit"
            return sim_result

        # Parse measurement results from stdout
        for line in proc.stdout.splitlines():
            line = line.strip().lower()
            if line.startswith("adc_peak") and "=" in line:
                parts = line.split("=")
                if len(parts) >= 2:
                    try:
                        sim_result["Adc_peak_dB"] = float(parts[-1].strip())
                    except ValueError:
                        pass
            elif line.startswith("adc") and "=" in line and not line.startswith("adc_"):
                parts = line.split("=")
                if len(parts) >= 2:
                    try:
                        sim_result["Adc_dB"] = float(parts[-1].strip())
                    except ValueError:
                        pass
            elif line.startswith("gbw"):
                parts = line.split("=")
                if len(parts) >= 2:
                    try:
                        sim_result["GBW_Hz"] = float(parts[-1].strip())
                    except ValueError:
                        pass
            elif line.startswith("pgbw") and not line.startswith("pgbw_"):
                parts = line.split("=")
                if len(parts) >= 2:
                    try:
                        phase = float(parts[-1].strip())
                        # For inverting OTA: PM = PGBW (wrapped phase
                        # directly gives PM, since DC phase is -180 deg
                        # and instability is at -360 deg = 0 deg wrapped)
                        sim_result["PM_deg"] = phase
                    except ValueError:
                        pass

        return sim_result

    def sweep_design_space(
        self,
        gmid_input_range: tuple[float, float, int] = (5.0, 25.0, 5),
        gmid_load_range: tuple[float, float, int] = (5.0, 20.0, 4),
        L_input_range: tuple[float, float, int] = (0.13e-6, 2.0e-6, 5),
        L_load_range: tuple[float, float, int] = (0.13e-6, 2.0e-6, 5),
        Cc_range: tuple[float, float, int] = (0.1e-12, 5.0e-12, 5),
    ) -> list[DesignResult]:
        """Sweep the full design space and return all results.

        Each range is (min, max, n_points). Total evaluations = product of all n_points.
        """
        gmid_inputs = _linspace(*gmid_input_range)
        gmid_loads = _linspace(*gmid_load_range)
        L_inputs = _linspace(*L_input_range)
        L_loads = _linspace(*L_load_range)
        Ccs = _linspace(*Cc_range)

        results = []
        for gi in gmid_inputs:
            for gl in gmid_loads:
                for li in L_inputs:
                    for ll in L_loads:
                        for cc in Ccs:
                            r = self.analytical_design(gi, gl, li, ll, cc)
                            results.append(r)
        return results


IHP_SG13G2 = ProcessParams()
MILLER_OTA_SPECS = MillerOTASpecs()
