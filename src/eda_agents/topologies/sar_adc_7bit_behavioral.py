"""Behavioural variant of the 7-bit SAR ADC (canonical name).

This module provides:

  - :class:`BehavioralComparatorKit` — convenience bundle that
    compiles the XSPICE ``.cm`` once and returns the paths / netlist
    snippet needed to wire ``ea_comparator_ideal`` into any netlist.

  - :func:`behavioral_comparator_cards` — function form of the same
    helper for callers that want to cache / inject the ``.cm`` via
    their own build system.

  - :func:`generate_behavioral_comparator_deck` — standalone testbench
    generator that exercises the primitive end-to-end through
    ``SpiceRunner(extra_codemodel=...)`` without any SAR logic; used
    by ``tests/test_xspice_primitives.py`` and by anyone benchmarking
    the comparator itself.

  - :class:`SAR7BitBehavioralTopology` — full SystemTopology that
    reuses the transistor-level C-DAC and Verilator-compiled SAR logic
    from :class:`~eda_agents.topologies.sar_adc_7bit.SAR7BitTopology`
    but replaces the StrongARM comparator with ``ea_comparator_ideal``
    so the dynamic metrics can be measured without the transistor-level
    regen latch dominating SPICE wall time.

The behavioural topology is S7 (Arcadia integration, 2026) completing
what S5 deferred. It exposes a 4-D design space (comparator hysteresis
/ output swing + CDAC unit cap) so autoresearch can still meaningfully
explore it without the 6-D StrongARM parameters.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from eda_agents.core.pdk import PdkConfig, resolve_pdk
from eda_agents.core.spice_runner import SpiceResult
from eda_agents.core.stages.xspice_compile import (
    CodeModelSource,
    XSpiceCompiler,
    load_codemodel_line,
)
from eda_agents.core.system_topology import SystemTopology
from eda_agents.core.topology import CircuitTopology  # noqa: F401 (public re-export)
from eda_agents.tools import adc_metrics as _adc_metrics
from eda_agents.topologies.sar_adc_7bit import SAR7BitTopology
from eda_agents.topologies.sar_adc_netlist import generate_sar_adc_netlist
from eda_agents.veriloga.voltage_domain import primitive_paths


_MODEL_NAME = "ea_comparator_ideal"


@dataclass
class BehavioralComparatorKit:
    """Paths and netlist snippets for a behavioural SAR comparator.

    Attributes
    ----------
    cm_path : Path
        Absolute path to the compiled XSPICE ``.cm`` shared object.
        Feed this into ``SpiceRunner(extra_codemodel=[cm_path])``.
    model_card : str
        The ``.model`` line for the comparator, including chosen
        ``vout_high`` / ``vout_low`` / ``hysteresis_v`` parameters.
    instance_line : str
        The XSPICE ``A`` device instance line. Pins are
        ``(inp, inn, out)``; override ``name`` / node names with
        :func:`behavioral_comparator_cards` if the defaults do not fit.
    """

    cm_path: Path
    model_card: str
    instance_line: str
    model_ref: str

    def spiceinit_line(self) -> str:
        """ngspice directive that pre-loads the ``.cm``. Emitted into
        the cwd ``.spiceinit`` shim by
        :class:`~eda_agents.core.spice_runner.SpiceRunner` when the
        runner is constructed with ``extra_codemodel=[kit.cm_path]``.
        """
        return load_codemodel_line(self.cm_path)

    def netlist_snippet(self) -> str:
        """Two-line block that can be inserted into any netlist."""
        return f"{self.instance_line}\n{self.model_card}"


def behavioral_comparator_cards(
    *,
    instance_name: str = "ACMP",
    node_inp: str = "cmp_p",
    node_inn: str = "cmp_n",
    node_out: str = "cmp_out",
    model_ref: str = "ea_cmp",
    vout_high: float = 1.2,
    vout_low: float = 0.0,
    hysteresis_v: float = 0.001,
) -> tuple[str, str]:
    """Build ``(instance_line, model_card)`` for ``ea_comparator_ideal``.

    Split from :class:`BehavioralComparatorKit` so callers can
    customise naming without needing the compiled ``.cm`` to exist
    yet (handy for unit tests that only assert on strings).
    """
    instance_line = f"{instance_name} {node_inp} {node_inn} {node_out} {model_ref}"
    model_card = (
        f".model {model_ref} {_MODEL_NAME}("
        f"vout_high={vout_high} vout_low={vout_low} "
        f"hysteresis_v={hysteresis_v})"
    )
    return instance_line, model_card


def build_behavioral_comparator_kit(
    out_dir: str | Path,
    *,
    model_ref: str = "ea_cmp",
    vout_high: float = 1.2,
    vout_low: float = 0.0,
    hysteresis_v: float = 0.001,
    node_inp: str = "cmp_p",
    node_inn: str = "cmp_n",
    node_out: str = "cmp_out",
    instance_name: str = "ACMP",
    compiler: XSpiceCompiler | None = None,
) -> BehavioralComparatorKit | None:
    """Compile the comparator ``.cm`` and return a kit.

    Returns ``None`` when the XSPICE toolchain is unavailable so
    callers can degrade gracefully (tests skip, production paths
    propagate the ``None`` so the caller can pick a fallback).
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    compiler = compiler or XSpiceCompiler()
    if not compiler.available():
        return None
    mod, ifs = primitive_paths("comparator_ideal")
    sources = [
        CodeModelSource(name=_MODEL_NAME, cfunc_mod=mod, ifspec_ifs=ifs),
    ]
    cm_path = out_dir / "behavioral_comparator.cm"
    res = compiler.compile(sources, cm_path, work_dir=out_dir / "_xspice_build")
    if not res.success:
        return None
    instance_line, model_card = behavioral_comparator_cards(
        instance_name=instance_name,
        node_inp=node_inp,
        node_inn=node_inn,
        node_out=node_out,
        model_ref=model_ref,
        vout_high=vout_high,
        vout_low=vout_low,
        hysteresis_v=hysteresis_v,
    )
    return BehavioralComparatorKit(
        cm_path=res.artifacts["cm"],
        model_card=model_card,
        instance_line=instance_line,
        model_ref=model_ref,
    )


def generate_behavioral_comparator_deck(
    work_dir: str | Path,
    *,
    vin_low: float = 0.3,
    vin_high: float = 0.9,
    vref: float = 0.6,
    ramp_duration_s: float = 1e-6,
    hold_duration_s: float = 0.5e-6,
) -> tuple[Path, BehavioralComparatorKit]:
    """Emit a standalone ``.cir`` that sweeps an input ramp against a
    fixed reference using the behavioural comparator, plus its kit.

    The generated deck has no SAR logic; it exists so tests and demos
    can exercise :class:`XSpiceCompiler` + :class:`SpiceRunner` with
    ``extra_codemodel`` wiring without the ~400-line SAR netlist.

    Returns ``(cir_path, kit)``. ``kit`` is ``None``-on-failure — see
    :func:`build_behavioral_comparator_kit`. When ``kit`` is ``None``
    the caller should skip the simulation (tests do so automatically).
    """
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    kit = build_behavioral_comparator_kit(work_dir)
    if kit is None:
        raise RuntimeError(
            "XSPICE toolchain unavailable; cannot build behavioural "
            "comparator deck. Set NGSPICE_SRC_DIR or install an "
            "ngspice source tree with built cmpp."
        )

    cir = work_dir / "behavioral_comparator.cir"
    ramp_end = ramp_duration_s
    hold_end = ramp_end + hold_duration_s
    ramp_down_end = hold_end + ramp_duration_s
    # PWL voltage ramp: low -> high -> hold -> low
    pwl = (
        f"pwl(0 {vin_low} "
        f"{ramp_end} {vin_high} "
        f"{hold_end} {vin_high} "
        f"{ramp_down_end} {vin_low})"
    )
    meas_decision_high_t = ramp_end + 0.5 * hold_duration_s
    meas_decision_low_t = ramp_down_end + hold_duration_s

    deck = f"""* behavioural SAR comparator demo
* input ramp against fixed reference; measures comparator output
* at a known-high and a known-low phase.
vref  cmp_n 0 dc {vref}
vin   cmp_p 0 {pwl}
{kit.instance_line}
{kit.model_card}
rout  cmp_out 0 1meg
.tran 5n {ramp_down_end + hold_duration_s:.3e}
.control
run
meas tran vcmp_hi find v(cmp_out) at={meas_decision_high_t:.3e}
meas tran vcmp_lo find v(cmp_out) at={meas_decision_low_t:.3e}
quit
.endc
.end
"""
    cir.write_text(deck)
    return cir, kit


def behavioral_comparator_section(
    *,
    vout_high: float,
    vout_low: float,
    hysteresis_v: float,
    model_ref: str = "ea_cmp",
) -> list[str]:
    """Return SAR-compatible comparator block that replaces the StrongARM.

    Two ``ea_comparator_ideal`` instances are wired with swapped polarity
    so that ``comp_outp`` / ``comp_outn`` follow the same differential
    convention as the transistor-level comparator (``outp`` HIGH when
    ``cdac_top_p`` > ``cdac_top_n``).  A weak resistor keeps ``vbias``
    referenced since the transistor tail is gone.

    The returned list is intended to be passed through
    :func:`eda_agents.topologies.sar_adc_netlist.generate_sar_adc_netlist`
    as ``comparator_section``.
    """
    return [
        "* XSPICE ideal comparator pair (replaces StrongARM latch)",
        f"ACMP_p cdac_top_p cdac_top_n comp_outp {model_ref}",
        f"ACMP_n cdac_top_n cdac_top_p comp_outn {model_ref}",
        f".model {model_ref} ea_comparator_ideal("
        f"vout_high={vout_high} vout_low={vout_low} "
        f"hysteresis_v={hysteresis_v})",
        "* Keep vbias anchored (unused in behavioural path)",
        "Rvbias_term vbias 0 1meg",
    ]


__all__ = [
    "BehavioralComparatorKit",
    "behavioral_comparator_cards",
    "behavioral_comparator_section",
    "build_behavioral_comparator_kit",
    "generate_behavioral_comparator_deck",
    "SAR7BitBehavioralTopology",
]


# Convenience re-exports used by docs / tests that don't want to
# rummage through ``veriloga.voltage_domain`` for the primitive paths.
COMPARATOR_MODEL_NAME = _MODEL_NAME


# -----------------------------------------------------------------------
# Full SystemTopology (S7)
# -----------------------------------------------------------------------

# Default ``comp_params`` passed through ``generate_sar_adc_netlist``.
# The behavioural comparator section ignores these values, but the
# generator still expects the keys. They are also what the NAND tail
# sizing falls back to on the transistor-level default path.
_PLACEHOLDER_COMP_PARAMS: dict[str, float] = {
    "W_input_um": 32.0,
    "L_input_um": 0.2,
    "W_tail_um": 18.0,
    "L_tail_um": 0.3,
    "W_latch_p_um": 8.0,
    "W_latch_n_um": 4.0,
}

# Specs mirror the transistor-level SAR so autoresearch reporting
# uses the same goalposts; the behavioural variant should meet them
# comfortably, making it a useful reference baseline.
_SPEC_ENOB_MIN = 5.0
_SPEC_SNDR_MIN = 32.0
_SPEC_POWER_MAX_UW = 200.0


class SAR7BitBehavioralTopology(SystemTopology):
    """SAR ADC behavioural variant — canonical 7-bit name.

    Swaps the StrongARM comparator for the XSPICE
    ``ea_comparator_ideal``; everything else (CDAC, SAR FSM, sampling
    switches) is reused verbatim from
    :class:`~eda_agents.topologies.sar_adc_7bit.SAR7BitTopology`, so
    the resolution ceiling is identical. For a true 11-bit converter
    see :mod:`eda_agents.topologies.sar_adc_11bit`.

    Blocks:
      - comparator: ``ea_comparator_ideal`` XSPICE primitive.
        Design knobs: ``vout_high``, ``vout_low``, ``hysteresis_v``.
      - cdac: transistor-level binary-weighted CMIM array (identical
        to :class:`SAR7BitTopology`).
        Design knobs: ``cdac_C_unit_fF``.

    The transistor-level NAND stays because SAR logic consumes digital
    rails only — the NAND is not on the ENOB-critical path. The
    Verilator-compiled SAR logic and sampling switches are reused
    verbatim from :class:`SAR7BitTopology`.

    The behavioural comparator is compiled once per instance via
    :class:`XSpiceCompiler` and cached; the ``.cm`` path is returned
    so the caller can wire it through
    ``SpiceRunner(extra_codemodel=[cm_path])``. When the XSPICE
    toolchain is unavailable :meth:`generate_system_netlist` raises
    ``RuntimeError`` so callers can degrade gracefully (tests skip).
    """

    def __init__(
        self,
        verilog_src: Path | None = None,
        so_cache_dir: Path | None = None,
        pdk: PdkConfig | str | None = None,
    ):
        self.pdk = resolve_pdk(pdk)
        # Reuse the parent topology so we inherit the Verilator SAR
        # source resolution, the .so cache, and ``extract_enob``.
        self._parent = SAR7BitTopology(
            verilog_src=verilog_src,
            so_cache_dir=so_cache_dir,
            pdk=self.pdk,
        )
        self._kit_cache: BehavioralComparatorKit | None = None
        self._last_codemodel_path: Path | None = None

    # -- SystemTopology API --------------------------------------------

    def topology_name(self) -> str:
        return "sar_adc_7bit_behavioral"

    def relevant_skills(self) -> list[str | tuple[str, dict]]:
        return ["analog.sar_adc_design"]

    def block_names(self) -> list[str]:
        return ["comparator", "cdac"]

    def block_topology(self, name: str) -> CircuitTopology | None:
        # Behavioural comparator is not a CircuitTopology; the CDAC
        # remains a passive block without a standalone topology.
        return None

    def system_design_space(self) -> dict[str, tuple[float, float]]:
        # Ranges kept conservative so the deck stays in the regime where
        # the ideal comparator is representative.
        VDD = self.pdk.VDD
        return {
            "cmp_vout_high": (0.8 * VDD, VDD),
            "cmp_vout_low": (0.0, 0.2 * VDD),
            "cmp_hysteresis_v": (1e-5, 1e-2),
            "cdac_C_unit_fF": (50.0, 500.0),
        }

    def block_design_space(
        self, block_name: str
    ) -> dict[str, tuple[float, float]]:
        full = self.system_design_space()
        if block_name == "comparator":
            return {k: v for k, v in full.items() if k.startswith("cmp_")}
        if block_name == "cdac":
            return {"cdac_C_unit_fF": full["cdac_C_unit_fF"]}
        raise ValueError(f"Unknown block: {block_name}")

    def params_to_block_params(
        self, system_params: dict[str, float]
    ) -> dict[str, dict[str, float]]:
        return {
            "comparator": {
                "vout_high": system_params["cmp_vout_high"],
                "vout_low": system_params["cmp_vout_low"],
                "hysteresis_v": system_params["cmp_hysteresis_v"],
            },
            "cdac": {
                "C_unit_fF": system_params["cdac_C_unit_fF"],
            },
        }

    def default_params(self) -> dict[str, float]:
        VDD = self.pdk.VDD
        return {
            "cmp_vout_high": VDD,
            "cmp_vout_low": 0.0,
            "cmp_hysteresis_v": 1e-3,
            "cdac_C_unit_fF": 200.0,
        }

    # -- netlist generation --------------------------------------------

    def _ensure_kit(self, work_dir: Path) -> BehavioralComparatorKit:
        if self._kit_cache is not None and self._kit_cache.cm_path.is_file():
            return self._kit_cache
        kit = build_behavioral_comparator_kit(work_dir)
        if kit is None:
            raise RuntimeError(
                "XSPICE toolchain unavailable; set NGSPICE_SRC_DIR or "
                "install an ngspice source tree with built cmpp to run "
                "the behavioural SAR 8-bit flow."
            )
        self._kit_cache = kit
        self._last_codemodel_path = kit.cm_path
        return kit

    @property
    def last_codemodel_path(self) -> Path | None:
        """Path to the compiled ``.cm`` after the last netlist build.

        Consumers wire it into ``SpiceRunner(extra_codemodel=[...])``
        so ngspice pre-loads the XSPICE primitive before running the
        deck. ``None`` until :meth:`generate_system_netlist` succeeds
        at least once.
        """
        return self._last_codemodel_path

    def generate_system_netlist(
        self,
        system_params: dict[str, float],
        work_dir: Path,
    ) -> Path:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        kit = self._ensure_kit(work_dir)
        so_path = self._parent._ensure_so(work_dir)

        blocks = self.params_to_block_params(system_params)
        cmp_section = behavioral_comparator_section(
            vout_high=blocks["comparator"]["vout_high"],
            vout_low=blocks["comparator"]["vout_low"],
            hysteresis_v=blocks["comparator"]["hysteresis_v"],
            model_ref=kit.model_ref,
        )
        T_period_us = 1.0
        f_s = 1e6 / T_period_us
        n_fft = 64
        cycles = 7
        f_in = cycles * f_s / n_fft

        return generate_sar_adc_netlist(
            comp_params=_PLACEHOLDER_COMP_PARAMS,
            cdac_C_unit_fF=blocks["cdac"]["C_unit_fF"],
            bias_V=0.6,
            T_period_us=T_period_us,
            work_dir=work_dir,
            so_path=so_path,
            n_samples=n_fft,
            input_mode="sine",
            vin_sine_amp=0.25,
            vin_sine_freq_hz=f_in,
            pdk=self.pdk,
            comparator_section=cmp_section,
        )

    # -- FoM / validity -------------------------------------------------

    def compute_system_fom(
        self,
        spice_result: SpiceResult,
        system_params: dict[str, float],
    ) -> float:
        m = spice_result.measurements
        enob = m.get("enob")
        avg_idd = m.get("avg_idd")
        if not enob or enob <= 0 or avg_idd is None:
            return 0.0
        f_s = 1e6
        power_w = self.pdk.VDD * abs(avg_idd)
        if power_w <= 0:
            return 0.0
        try:
            walden_fj = _adc_metrics.calculate_walden_fom(
                power_w=power_w, fs=f_s, enob=enob
            )
            fom = 1e15 / walden_fj if walden_fj > 0 else 0.0
        except ImportError:
            fom = (2**enob) * f_s / power_w
        valid, violations = self.check_system_validity(spice_result, system_params)
        penalty = 1.0 if valid else max(0.01, 1.0 - 0.15 * len(violations))
        return fom * penalty

    def check_system_validity(
        self,
        spice_result: SpiceResult,
        system_params: dict[str, float],
    ) -> tuple[bool, list[str]]:
        violations: list[str] = []
        if not spice_result.success:
            return (False, ["simulation failed"])
        m = spice_result.measurements
        enob = m.get("enob")
        sndr = m.get("sndr_dB")
        if enob is not None and enob < _SPEC_ENOB_MIN:
            violations.append(f"ENOB={enob:.2f} < {_SPEC_ENOB_MIN}")
        if sndr is not None and sndr < _SPEC_SNDR_MIN:
            violations.append(f"SNDR={sndr:.1f}dB < {_SPEC_SNDR_MIN}dB")
        avg_idd = m.get("avg_idd")
        if avg_idd is not None:
            power_uw = self.pdk.VDD * abs(avg_idd) * 1e6
            if power_uw > _SPEC_POWER_MAX_UW:
                violations.append(
                    f"Power={power_uw:.1f}uW > {_SPEC_POWER_MAX_UW:.1f}uW"
                )
        return (len(violations) == 0, violations)

    def extract_enob(self, work_dir: Path) -> dict[str, float]:
        """Delegate to :meth:`SAR7BitTopology.extract_enob` — same format."""
        return self._parent.extract_enob(work_dir)

    # -- prompt metadata -----------------------------------------------

    def prompt_description(self) -> str:
        return (
            f"8-bit SAR ADC (behavioural comparator) on "
            f"{self.pdk.display_name}. Transistor-level C-DAC + ideal "
            "XSPICE comparator + Verilator SAR logic. Useful as an upper "
            "bound on ENOB/SNDR: any gap vs. the transistor-level topology "
            "measures what the StrongARM contributes to the non-idealities."
        )

    def design_vars_description(self) -> str:
        return (
            "- cmp_vout_high: ideal comparator output HIGH level [V]. "
            "Sets digital swing seen by the SAR latches.\n"
            "- cmp_vout_low: ideal comparator output LOW level [V]. "
            "Should track GND closely; raising it models a weak pulldown.\n"
            "- cmp_hysteresis_v: differential hysteresis band [V]. "
            "Larger = immune to CDAC settling ripple but wastes LSBs.\n"
            "- cdac_C_unit_fF: unit capacitance of the binary array [fF]. "
            "Larger = more noise immunity + slower settling."
        )

    def specs_description(self) -> str:
        return (
            f"ENOB >= {_SPEC_ENOB_MIN:.0f} bits, "
            f"SNDR >= {_SPEC_SNDR_MIN:.0f} dB, "
            f"Power <= {_SPEC_POWER_MAX_UW:.0f} uW, "
            f"f_s = 1 MHz"
        )

    def fom_description(self) -> str:
        return (
            "Walden FoM = 2^ENOB * f_s / P_total. ENOB from a 64-point "
            "coherent FFT of the reconstructed output. ADCToolbox is used "
            "when available."
        )

    def reference_description(self) -> str:
        return (
            f"Reference: vout_high={self.pdk.VDD}, vout_low=0, "
            "hysteresis=1 mV, C_unit=200 fF. Sets an ENOB ceiling for "
            "the StrongARM path on the same CDAC."
        )

    def inter_block_constraints(self) -> list[str]:
        return [
            "Comparator hysteresis_v gates how small a residue can be "
            "resolved: if it exceeds half an LSB on the CDAC top plate, "
            "the converter loses bits regardless of C_unit.",
            "The ideal comparator has zero offset and zero kickback, so "
            "larger CDAC caps only help by reducing kT/C noise, not by "
            "absorbing transistor kickback as in the StrongARM path.",
        ]

    def exploration_hints(self) -> dict[str, int | float]:
        return {
            "evals_per_round": 2,
            "min_rounds": 3,
            "convergence_threshold": 0.05,
            "partition_dim": "cdac_C_unit_fF",
        }


def _default_tempdir() -> Path:
    """Return a cached temp dir for throwaway builds. Not used by the
    public API — kept so reviewers find the intent quickly: tests call
    :func:`build_behavioral_comparator_kit` with an explicit tmp_path
    fixture rather than relying on hidden global state."""
    return Path(tempfile.gettempdir()) / "eda_agents_behavioral_comparator"
