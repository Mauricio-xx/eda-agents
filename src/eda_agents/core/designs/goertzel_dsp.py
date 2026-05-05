"""Goertzel DSP filter digital design with throughput-aware FoM.

Validation case for the domain-agnostic :class:`DigitalDesign` API.
The earlier Goertzel FP32 demo on GF180MCU showed both ``cc_cli`` and
``opencode`` LLM backends converging to a ~5000 ns clock (the centroid
of the design space tuple) and "winning" a PPA-style FoM despite
producing ~2 kSPS, far below any audio-rate sample target. The LLM
optimised exactly what we measured. The fix is to encode the Nyquist /
throughput floor inside the design's spec, FoM, and validity gates so
the harness has nothing domain-specific to know.

This module ships the design class. It does NOT ship the RTL or the
cocotb testbench — those live next to a real Goertzel project; tests
exercise the design via fixture-driven sidecar JSON files. The
runner-side cocotb sidecar artefact convention (a testbench writes
``meas.json`` next to the cocotb work dir; ``RtlSimRunner`` surfaces
it via ``StageResult.artifacts["meas.json"]``) is the runtime path
this design assumes when an autoresearch loop drives it for real.

Spec encoded here:

* 8-point Goertzel filter for 1 kHz tone detection.
* Inputs are FP32 samples streamed at ``fs_target`` Hz via the
  ``valid_in / data_in`` handshake.
* The design must keep up with the input rate at all times.

FoM = PPA term + ``dsp_w * log10(throughput_sps / fs_target)``. The
throughput term rewards margin over the sample rate and saturates
gracefully on a logarithmic scale. The bare frequency term in the PPA
helper is dropped (``perf_w=0.0``) because raw clock speed is no
longer the goal — throughput is.

Validity: rejects evals with ``throughput_sps < fs_target`` (Nyquist
floor for the signal of interest, conservative in that real anti-alias
filters need >> Nyquist). The hard floor means any clock-relaxation
attack produces ``FoM=0``. The PPA gates from
:class:`FlowMetrics.validity_check` (timing closed, DRC clean, LVS
match) still apply on top.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from eda_agents.core.digital_design import (
    DEFAULT_PPA_COLUMNS,
    DigitalDesign,
)
from eda_agents.core.flow_metrics import FlowMetrics
from eda_agents.core.stage_results import StageResults

logger = logging.getLogger(__name__)


_DSP_COLUMNS = ("cycles_per_sample", "throughput_sps")


class GoertzelDspDesign(DigitalDesign):
    """8-point Goertzel filter with throughput-aware FoM.

    Parameters
    ----------
    project_dir
        Project root containing the Goertzel RTL, cocotb testbench,
        and LibreLane config. For unit tests a tmp directory works
        because the design class never touches the filesystem until
        the runner invokes it.
    fs_target
        Target input sample rate in Hz. Default 8 kHz (DTMF / audio).
    dsp_w
        Weight for the throughput-margin term in the FoM. Default
        2.0 — a 10x margin contributes ``+2.0`` to FoM, a 100x margin
        contributes ``+4.0``.
    pdk_root
        Optional PDK root path. Required for live LibreLane runs;
        unit tests leave this ``None``.
    """

    def __init__(
        self,
        project_dir: Path | str,
        *,
        fs_target: float = 8000.0,
        dsp_w: float = 2.0,
        pdk_root: Path | str | None = None,
    ):
        if fs_target <= 0:
            raise ValueError(
                f"fs_target must be positive, got {fs_target!r}"
            )
        if dsp_w < 0:
            raise ValueError(f"dsp_w must be non-negative, got {dsp_w!r}")
        self._project_dir = Path(project_dir).resolve()
        self._fs_target = float(fs_target)
        self._dsp_w = float(dsp_w)
        self._pdk_root = Path(pdk_root) if pdk_root else None

    # ------------------------------------------------------------------
    # Identity / metadata
    # ------------------------------------------------------------------

    def project_name(self) -> str:
        return "goertzel-dsp"

    def specification(self) -> str:
        return (
            f"8-point Goertzel filter for 1 kHz tone detection. "
            f"Inputs are FP32 samples streamed at {self._fs_target:.0f} Hz "
            f"via valid_in / data_in handshake. Output is one magnitude "
            f"per 8-sample window. The design MUST keep up with the input "
            f"rate at all times: throughput_sps >= {self._fs_target:.0f} "
            f"is a hard correctness requirement (the cocotb testbench "
            f"measures it post-flow and rejects evals that fall behind)."
        )

    def design_space(self) -> dict[str, list | tuple]:
        # Same two knobs the LibreLane flow accepts. Bounds match
        # GenericDesign so the autoresearch loop can explore freely;
        # the FoM/validity gate is what enforces the DSP-correct
        # operating region, not the design space.
        return {
            "PL_TARGET_DENSITY_PCT": (1.0, 99.0),
            "CLOCK_PERIOD": (0.1, 10000.0),
        }

    def flow_config_overrides(self) -> dict[str, object]:
        return {}

    def project_dir(self) -> Path:
        return self._project_dir

    def librelane_config(self) -> Path:
        return self._project_dir / "config.yaml"

    def pdk_root(self) -> Path | None:
        return self._pdk_root

    # ------------------------------------------------------------------
    # Domain-specific measurement & FoM
    # ------------------------------------------------------------------

    def measurement_columns(self) -> list[str]:
        # PPA columns first (so the TSV stays a strict superset of
        # the GenericDesign schema) then the two DSP columns the
        # cocotb sidecar produces.
        return list(DEFAULT_PPA_COLUMNS) + list(_DSP_COLUMNS)

    def extract_measurements(
        self, stage_results: StageResults
    ) -> dict[str, float | int | None]:
        # Start from the default PPA extraction so subclassed
        # behaviour is additive, not parallel.
        measurements = super().extract_measurements(stage_results)

        cycles = self._read_cycles_per_sample(stage_results)
        period_ns = self._resolve_period_ns(stage_results)

        if cycles is not None and period_ns is not None and period_ns > 0:
            throughput = 1.0e9 / (period_ns * cycles)
        else:
            throughput = None

        measurements["cycles_per_sample"] = cycles
        measurements["throughput_sps"] = throughput
        return measurements

    def check_validity(
        self, measurements: dict[str, float | int | None]
    ) -> tuple[bool, list[str]]:
        # PPA gates first.
        valid_ppa, violations = (
            FlowMetrics.from_measurements(measurements).validity_check()
        )

        # DSP gate: hard floor at fs_target. Missing measurements are
        # treated as a measurement failure rather than a benefit-of-the-doubt
        # pass; an unmeasured DSP design is by construction not known
        # to keep up with the sample rate.
        throughput = measurements.get("throughput_sps")
        if throughput is None:
            violations.append(
                "DSP measurement missing: cocotb sidecar did not "
                "report cycles_per_sample (cannot verify throughput "
                "floor)."
            )
        elif throughput < self._fs_target:
            violations.append(
                f"Throughput {throughput:.0f} sps below floor "
                f"{self._fs_target:.0f} sps"
            )

        return (len(violations) == 0, violations)

    def compute_fom(
        self, measurements: dict[str, float | int | None]
    ) -> float:
        valid, _ = self.check_validity(measurements)
        if not valid:
            return 0.0

        metrics = FlowMetrics.from_measurements(measurements)
        # PPA term without the bare frequency component: throughput
        # carries the performance signal below, so rewarding
        # ``1000/period_ns`` would double-count and re-introduce the
        # clock-relaxation attack.
        ppa = metrics.weighted_fom(
            timing_w=0.5, perf_w=0.0, area_w=1.0, power_w=1.0,
        )

        throughput = measurements.get("throughput_sps")
        if throughput is None or throughput <= 0:
            # Already gated by check_validity; defensive.
            return 0.0
        dsp = self._dsp_w * math.log10(throughput / self._fs_target)
        return ppa + dsp

    # ------------------------------------------------------------------
    # Prompt metadata
    # ------------------------------------------------------------------

    def prompt_description(self) -> str:
        return (
            "Goertzel DSP: 8-point single-bin DFT for tone detection at "
            f"1 kHz with input sample rate {self._fs_target:.0f} Hz on "
            "GF180MCU. The autoresearch loop must keep throughput above "
            "the sample rate floor; clock-relaxation alone produces "
            "FoM=0 because the FoM rewards throughput margin, not raw "
            "clock speed."
        )

    def design_vars_description(self) -> str:
        return (
            "- PL_TARGET_DENSITY_PCT: (1.0, 99.0). Placement density "
            "for LibreLane; ranges outside crash floorplan but neither "
            "endpoint hangs the tool.\n"
            "- CLOCK_PERIOD: (0.1, 10000.0) ns. Tool-level fence; the "
            "spec gate (Nyquist floor) rejects clocks too slow to keep "
            "up with the input sample rate, so feel free to explore.\n"
            "\nDerived (not a knob): throughput_sps = "
            "1e9 / (CLOCK_PERIOD_ns * cycles_per_sample). "
            "cycles_per_sample is MEASURED post-flow by cocotb; the "
            "LLM cannot bypass it by editing the RTL because the "
            "testbench reads the live valid_in/valid_out cadence."
        )

    def specs_description(self) -> str:
        return (
            f"WNS >= 0 at all corners, DRC clean, LVS match, AND "
            f"throughput_sps >= {self._fs_target:.0f} (Nyquist floor "
            f"for the {self._fs_target/1000:.1f} kHz input). All four "
            f"must hold simultaneously."
        )

    def fom_description(self) -> str:
        return (
            f"FoM = PPA_term + {self._dsp_w} * log10(throughput_sps / "
            f"{self._fs_target:.0f}). PPA_term = "
            f"0.5 * timing_met + 1.0 * (1e6 / die_area_um2) + "
            f"1.0 * (1 / (power_W * clock_period_ns)). The bare "
            f"frequency term is dropped because throughput already "
            f"carries the performance signal; rewarding raw clock "
            f"speed on top would re-enable the clock-relaxation "
            f"attack. Higher is better. Returns 0.0 for designs that "
            f"fail timing/DRC/LVS or fall below the sample-rate floor."
        )

    def reference_description(self) -> str:
        return (
            "No reference run established yet. The first valid design "
            "(throughput >= fs_target, timing closed, DRC clean, LVS "
            "match) becomes the baseline for the autoresearch loop."
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_cycles_per_sample(
        self, stage_results: StageResults
    ) -> int | None:
        """Pull ``cycles_per_sample`` from the cocotb sidecar.

        Looks first at ``stage_results.rtl_sim.artifacts["meas.json"]``
        (the canonical surfacing path produced by ``RtlSimRunner``);
        falls back to ``stage_results.run_dir / "rtl_sim" / "meas.json"``
        for the rare case where the rtl_sim StageResult is missing
        but the file landed inside the LibreLane run dir anyway.
        """
        sidecar: Path | None = None
        if stage_results.rtl_sim is not None:
            sidecar = stage_results.rtl_sim.artifacts.get("meas.json")
        if sidecar is None and stage_results.run_dir is not None:
            candidate = stage_results.run_dir / "rtl_sim" / "meas.json"
            if candidate.is_file():
                sidecar = candidate
        if sidecar is None or not sidecar.is_file():
            return None
        try:
            data = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "GoertzelDspDesign: cannot read sidecar %s: %s",
                sidecar, exc,
            )
            return None
        value = data.get("cycles_per_sample")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning(
                "GoertzelDspDesign: invalid cycles_per_sample=%r in %s",
                value, sidecar,
            )
            return None

    def _resolve_period_ns(
        self, stage_results: StageResults
    ) -> float | None:
        """Return the achievable clock period in ns for this eval.

        Prefers the LibreLane-resolved value (``flow_metrics.clock_period_ns``,
        sourced from ``resolved.json`` by ``FlowMetrics.from_librelane_run_dir``);
        falls back to the eval's input parameter so designs that
        never reached LibreLane (mock path, early failure) can still
        compute a throughput estimate when ``cycles_per_sample`` is
        otherwise known.
        """
        if (
            stage_results.flow_metrics is not None
            and stage_results.flow_metrics.clock_period_ns is not None
        ):
            return float(stage_results.flow_metrics.clock_period_ns)
        params = stage_results.params or {}
        if "CLOCK_PERIOD" in params:
            try:
                return float(params["CLOCK_PERIOD"])
            except (TypeError, ValueError):
                return None
        return None
