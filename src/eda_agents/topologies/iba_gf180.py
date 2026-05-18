"""Inverter-Based Dynamic Amplifier (IBA) topology for GF180MCU.

GF180 sibling of ``iba_ihp.InverterBasedAmplifier``. The deck-emission
code is inherited verbatim and works on GF180 because:

* ``netlist_lib_lines`` already resolves the GF180 BSIM4 corner lib.
* ``netlist_osdi_lines`` returns ``[]`` on GF180 (no OSDI).
* ``pdk.instance_prefix`` stays ``"X"`` (subcircuit-based primitives).
* ``pdk.finger_param`` is ``"nf"`` on GF180 vs ``"ng"`` on IHP; the
  parent's ``_devline`` reads it through the PDK config so the same
  source emits the right param name.

This subclass narrows the design space + spec floors to the GF180
3.3 V rail. The methodology is unchanged: a single CMOS inverter
biased at its trip point drives a CL = 667 fF load; Ron governs the
large-signal RC settling and gm governs the small-signal phase.
The exact numerical targets (Wrøngm Table II at Iq = 2.5 uA, UGB =
9.55 MHz) do not transfer to GF180 because the higher rail and the
slower nfet_03v3 / pfet_03v3 devices change the gm budget; the spec
floors below were tuned against ngspice at the default sizing.
"""

from __future__ import annotations

from eda_agents.core.pdk import PdkConfig
from eda_agents.topologies.iba_ihp import InverterBasedAmplifier


class InverterBasedAmplifierGF180(InverterBasedAmplifier):
    """Inverter-Based Dynamic Amplifier on GF180MCU.

    Parameters
    ----------
    pdk : PdkConfig or str, optional
        Defaults to ``gf180mcu``. Passing a non-GF180 PDK is allowed
        for parameter-space experiments but the spec floors below
        assume the 3.3 V rail.
    """

    # GF180-tuned floor. The methodology still targets a single-stage
    # inverter at the trip point; the spec values reflect what the
    # default sizing reaches in ngspice on a 3.3 V rail and what the
    # Ron/gm-derived inverter sizes can credibly hit.
    SPEC_ADC_DB = 15.0       # Default sizing reaches ~19 dB; the floor
                              # leaves a few dB of margin for the
                              # autoresearch sampler.
    SPEC_GBW_HZ = 5.0e6      # Default sizing reaches ~8.75 MHz; the
                              # floor matches the IHP-comparable range.
    SPEC_PM_DEG = 60.0
    SPEC_IQ_UA = 20.0        # 4x the IHP 5 uA budget. The 3.3 V rail
                              # plus the slower devices double current
                              # at the trip point; RonGmLookup-sized
                              # designs with W_p ~ 5x W_n typically land
                              # around 15-18 uA at trip in ngspice.
    CL_F = 667e-15           # Wrøngm reference load, unchanged.

    def __init__(self, pdk: PdkConfig | str | None = None):
        super().__init__(pdk=pdk if pdk is not None else "gf180mcu")

    def topology_name(self) -> str:
        return "iba_gf180"

    def design_space(self) -> dict[str, tuple[float, float]]:
        """GF180 design space.

        Wmin = 0.22 um and Lmin = 0.28 um. Ranges open up the upper
        bounds so the autoresearch sampler can reach Ron/gm-comfortable
        sizing (wider NMOS to sink the inverter switching current).
        """
        return {
            "W_n_um": (0.22, 10.0),
            "L_n_um": (0.28, 8.0),
            "m_n": (1.0, 8.0),
            "W_p_um": (0.22, 20.0),
            "L_p_um": (0.28, 8.0),
            "m_p": (1.0, 8.0),
            "Vbias_V": (1.0, 2.5),
        }

    def default_params(self) -> dict[str, float]:
        """Default Ron/gm-comparable seed sized against ngspice at
        VDD=3.3 V.

        At W_n=0.3 um L_n=3.0 um m_n=2, W_p=1.5 um L_p=3.0 um m_p=2,
        Vbias=1.65 V the inverter sits near its trip point and the
        ngspice run produces Adc=19 dB, GBW=8.75 MHz, Iq=10 uA --
        comparable to Wrøngm's IHP reference (~25 dB, 9.55 MHz,
        2.5 uA) up to the rail-and-process delta.
        """
        return {
            "W_n_um": 0.3,
            "L_n_um": 3.0,
            "m_n": 2.0,
            "W_p_um": 1.5,
            "L_p_um": 3.0,
            "m_p": 2.0,
            "Vbias_V": 1.65,
        }

    def reference_description(self) -> str:
        return (
            "GF180MCU IBA seed: NMOS W=0.3 um L=3.0 um m=2; PMOS "
            "W=1.5 um L=3.0 um m=2; Vbias=1.65 V (mid-rail trip "
            "point). The methodology is identical to Wrøngm's IHP "
            "reference; the absolute numbers shift because GF180 is "
            "a 180 nm 3.3 V process. Expected at the default "
            "sizing: Adc around 19 dB, GBW around 8.75 MHz, Iq "
            "around 10 uA at CL=667 fF."
        )
