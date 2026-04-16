"""Per-PDK sEKV process parameters for the analytical Miller OTA designer.

Closes gap #1. Session 9 diagnosed that ``MillerOTADesigner`` was
hardcoded to IHP SG13G2 parameters
(``miller_ota.py:104``), so passing ``pdk=GF180MCU_D`` only swapped
symbol names while the analytical math kept computing IHP sizings. The
result was ``W ~170 nm`` transistors that the GF180 BSIM4 binner
rejected (``Wmin = 220 nm``), surfacing as the
``spec_miller_ota_gf180_easy`` ``FAIL_SIM`` in the bench baseline.

This module centralises the :class:`ProcessParams` table per PDK. The
IHP SG13G2 numbers were extracted and validated in the original
``ihp130g2_sekv.py`` pipeline from the external ihp-gmid-kit; they are
reproduced verbatim here so the analytical path stays bit-identical
for IHP regressions.

The GF180MCU numbers are **approximations** — enough for the
analytical sizing path to produce transistors inside the BSIM4
binner's ``W >= 220 nm`` / ``L >= 280 nm`` envelope, but not a
silicon-traceable extraction. Anchors used:

* ``Lmin`` / ``Wmin`` / ``VDD`` come straight from the PDK spec
  (nfet_03v3 / pfet_03v3 subcircuit binners).
* ``tox`` is the BSIM4 ``TOXE`` from the GF180MCU model card
  (``sm141064.ngspice``).
* ``n0``, ``mu*Cox``, ``Ispecsq``, ``VT0``, ``lambda`` are standard
  literature values for 180 nm 3.3 V CMOS; they are refined against
  the existing ``data/gmid_luts/gf180_{nfet,pfet}_03v3.npz`` LUTs,
  which were extracted from real ngspice sweeps of the PDK.
* ``AVT`` Pelgrom constants are 180 nm literature values (8 nV·m NMOS,
  12 nV·m PMOS).

When the GF180 sEKV port gets a full extraction (post-gap-closure), the
numbers below get overwritten with higher-fidelity values; the
registry + lookup contract stays the same.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessParams:
    """sEKV process parameters for one PDK.

    All lengths/areas in meters, capacitances in F/m or F/m², currents
    in A. ``UT``, ``Cox`` are derived properties.
    """

    # Physical constants
    kB: float = 1.38064852e-23
    q_e: float = 1.60217662e-19
    T: float = 300.15  # 27 degC
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
# IHP SG13G2 130nm BiCMOS — silicon-traceable sEKV extraction
# (matches the values that lived in miller_ota.py through Session 9)
# ---------------------------------------------------------------------------

IHP_SG13G2_PARAMS = ProcessParams()


# ---------------------------------------------------------------------------
# GF180MCU-D 180nm 3.3V CMOS — literature-anchored approximation
# (see module docstring; refinement from a full sEKV extraction is a
# post-gap-closure task)
# ---------------------------------------------------------------------------

GF180MCU_PARAMS = ProcessParams(
    # Process
    tox=7.95e-9,           # BSIM4 TOXE, sm141064.ngspice model card
    VDD=3.3,
    Lmin=280e-9,           # nfet_03v3.0-7 binner floor
    Wmin=220e-9,           # nfet_03v3.0-7 binner floor
    z1=400e-9,             # rough 180nm junction perimeter constant
    # nMOS
    DLn=50e-9,
    DWn=0.0,
    n0n=1.30,              # typical 180nm subthreshold slope factor
    # Ispecsqn ~ 2*n*mu*Cox*UT^2 with mu_n*Cox ~ 195 uA/V^2:
    #   2 * 1.30 * 195e-6 * (0.02585)^2 = 339 nA/(W/L)
    Ispecsqn=339.0e-9,
    VT0n=0.45,
    lambdan=2.0e6,         # 180nm typical channel-length modulation
    KFn=2.2e-24,
    AVTn=8.0e-9,           # Pelgrom nMOS 180nm
    Abetan=0.01e-6,
    CGSOn=3.5e-10, CGDOn=3.5e-10, CGSFn=1.5e-10, CGDFn=1.5e-10,
    CJn=9.0e-4, CJSWSTIn=2.0e-11, CJSWGATn=2.5e-11,
    # pMOS (~4.6x lower mobility than nMOS in 180nm)
    DLp=50e-9,
    DWp=0.0,
    n0p=1.35,
    # Ispecsqp ~ 2*1.35*42e-6*(0.02585)^2 = 75.7 nA/(W/L)
    Ispecsqp=75.7e-9,
    VT0p=0.55,
    lambdap=3.0e6,
    KFp=12.0e-24,
    AVTp=12.0e-9,          # Pelgrom pMOS 180nm
    Abetap=0.01e-6,
    CGSOp=3.5e-10, CGDOp=3.5e-10, CGSFp=1.5e-10, CGDFp=1.5e-10,
    CJp=8.0e-4, CJSWSTIp=2.5e-11, CJSWGATp=2.2e-11,
)


# ---------------------------------------------------------------------------
# Registry + resolver
# ---------------------------------------------------------------------------

PDK_TO_PROCESS_PARAMS: dict[str, ProcessParams] = {
    "ihp_sg13g2": IHP_SG13G2_PARAMS,
    "gf180mcu": GF180MCU_PARAMS,
}


def resolve_process_params(pdk_name: str | None) -> ProcessParams:
    """Look up ``ProcessParams`` for a PDK registry name.

    Falls back to :data:`IHP_SG13G2_PARAMS` when ``pdk_name`` is
    ``None`` or unknown, so the designer behaves identically to the
    pre-gap-closure code when no PDK is passed.
    """
    if pdk_name is None:
        return IHP_SG13G2_PARAMS
    return PDK_TO_PROCESS_PARAMS.get(pdk_name, IHP_SG13G2_PARAMS)


__all__ = [
    "GF180MCU_PARAMS",
    "IHP_SG13G2_PARAMS",
    "PDK_TO_PROCESS_PARAMS",
    "ProcessParams",
    "resolve_process_params",
]
