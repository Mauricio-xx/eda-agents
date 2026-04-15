"""Behavioural variant of the 8-bit SAR ADC comparator path.

**Scope for Session 5 (incremental):** this module provides the
building blocks needed to swap the StrongARM transistor-level
comparator in :mod:`eda_agents.topologies.sar_adc_netlist` for the
XSPICE ``ea_comparator_ideal`` primitive compiled from
``src/eda_agents/veriloga/voltage_domain/comparator_ideal/``. The
full end-to-end behavioural ``SARADCTopology`` subclass that
substitutes the comparator inside the existing 8-D SAR netlist is
deferred to Session 7 (SAR 11-bit architecture template) where it
lands alongside the 11-bit design reference.

What this module offers today:

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

The classes in this file deliberately mirror the naming of the
forthcoming behavioural SARADCTopology so that Session 7 can absorb
them as internal helpers without a breaking rename.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from eda_agents.core.stages.xspice_compile import (
    CodeModelSource,
    XSpiceCompiler,
    load_codemodel_line,
)
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


__all__ = [
    "BehavioralComparatorKit",
    "behavioral_comparator_cards",
    "build_behavioral_comparator_kit",
    "generate_behavioral_comparator_deck",
]


# Convenience re-exports used by docs / tests that don't want to
# rummage through ``veriloga.voltage_domain`` for the primitive paths.
COMPARATOR_MODEL_NAME = _MODEL_NAME


def _default_tempdir() -> Path:
    """Return a cached temp dir for throwaway builds. Not used by the
    public API — kept so reviewers find the intent quickly: tests call
    :func:`build_behavioral_comparator_kit` with an explicit tmp_path
    fixture rather than relying on hidden global state."""
    return Path(tempfile.gettempdir()) / "eda_agents_behavioral_comparator"
