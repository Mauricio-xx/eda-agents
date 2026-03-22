"""8-bit SAR ADC SystemTopology.

Wraps the AnalogAcademy-derived SAR ADC as a SystemTopology with:
  - StrongARM comparator (6D parametric)
  - Charge redistribution C-DAC (1D: unit cap)
  - Bias voltage (1D)
  = 8D total system design space

Uses ngspice mixed-signal simulation: analog blocks (comparator, C-DAC)
via SPICE models + Verilator-compiled SAR logic via d_cosim.

Supports any PDK via PdkConfig (defaults to IHP SG13G2).
System FoM: Walden FoM = 2^ENOB * f_s / P_total

Reference: IHP-AnalogAcademy Module 3 - 8-bit SAR ADC
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from eda_agents.core.pdk import PdkConfig, resolve_pdk
from eda_agents.topologies.comparator_strongarm import StrongARMComparatorTopology
from eda_agents.topologies.sar_adc_netlist import generate_sar_adc_netlist
from eda_agents.core.spice_runner import SpiceResult
from eda_agents.core.system_topology import SystemTopology
from eda_agents.core.topology import CircuitTopology
from eda_agents.utils.vlnggen import compile_verilog

logger = logging.getLogger(__name__)

# Default SAR logic Verilog source
_DEFAULT_VERILOG = Path(
    "/home/montanares/git/eda_sandbox/IHP-AnalogAcademy/"
    "modules/module_3_8_bit_SAR_ADC/part_2_digital_comps/"
    "algorithm/verilog/sar_logic.v"
)

# Specs
_SPEC_ENOB_MIN = 4.0       # minimum ENOB (target > 6)
_SPEC_SNDR_MIN = 26.0      # dB
_SPEC_FS_MIN = 500e3       # Hz (500 kHz minimum sampling rate)
_SPEC_POWER_MAX = 200.0    # uW maximum

# Sine test parameters for ENOB
_N_FFT_SAMPLES = 64        # power of 2
_SINE_CYCLES = 7           # prime, coherent sampling
_SINE_AMP = 0.25           # V (per side)


class SARADCTopology(SystemTopology):
    """8-bit SAR ADC.

    System design space (8D):
      - comp_W_input_um, comp_L_input_um: comparator input pair (2D)
      - comp_W_tail_um, comp_L_tail_um: comparator tail/bias (2D)
      - comp_W_latch_p_um, comp_W_latch_n_um: comparator latch (2D)
      - cdac_C_unit_fF: unit capacitance for C-DAC (1D)
      - bias_V: comparator bias voltage (1D)

    Timing (T_period) is fixed at 1us (1MHz sampling rate).

    Parameters
    ----------
    pdk : PdkConfig or str, optional
        PDK configuration. Defaults to resolve_pdk().
    """

    def __init__(
        self,
        verilog_src: Path | None = None,
        so_cache_dir: Path | None = None,
        pdk: PdkConfig | str | None = None,
    ):
        self.pdk = resolve_pdk(pdk)
        self._verilog_src = Path(verilog_src) if verilog_src else _DEFAULT_VERILOG
        self._so_cache_dir = Path(so_cache_dir) if so_cache_dir else None
        self._so_path: Path | None = None
        self._comp_topo = StrongARMComparatorTopology(pdk=self.pdk)

    def _ensure_so(self, work_dir: Path) -> Path:
        """Compile SAR logic if not already cached."""
        if self._so_path is not None and self._so_path.is_file():
            return self._so_path

        cache_dir = self._so_cache_dir or work_dir
        so_candidate = cache_dir / "sar_logic.so"
        if so_candidate.is_file():
            self._so_path = so_candidate
            return self._so_path

        self._so_path = compile_verilog(self._verilog_src, cache_dir)
        return self._so_path

    # ------------------------------------------------------------------
    # SystemTopology ABC implementation
    # ------------------------------------------------------------------

    def topology_name(self) -> str:
        return "sar_adc_8bit"

    def block_names(self) -> list[str]:
        return ["comparator", "cdac", "bias"]

    def block_topology(self, name: str) -> CircuitTopology | None:
        if name == "comparator":
            return self._comp_topo
        return None  # C-DAC and bias have no standalone topology

    def system_design_space(self) -> dict[str, tuple[float, float]]:
        return {
            # Comparator (6D)
            "comp_W_input_um": (4.0, 64.0),
            "comp_L_input_um": (0.13, 2.0),
            "comp_W_tail_um": (4.0, 40.0),
            "comp_L_tail_um": (0.13, 2.0),
            "comp_W_latch_p_um": (1.0, 16.0),
            "comp_W_latch_n_um": (1.0, 16.0),
            # C-DAC (1D)
            "cdac_C_unit_fF": (50.0, 500.0),
            # Bias (1D)
            "bias_V": (0.4, 0.8),
        }

    def block_design_space(self, block_name: str) -> dict[str, tuple[float, float]]:
        full = self.system_design_space()
        if block_name == "comparator":
            return {k: v for k, v in full.items() if k.startswith("comp_")}
        elif block_name == "cdac":
            return {"cdac_C_unit_fF": full["cdac_C_unit_fF"]}
        elif block_name == "bias":
            return {"bias_V": full["bias_V"]}
        raise ValueError(f"Unknown block: {block_name}")

    def params_to_block_params(
        self, system_params: dict[str, float]
    ) -> dict[str, dict[str, float]]:
        return {
            "comparator": {
                "W_input_um": system_params["comp_W_input_um"],
                "L_input_um": system_params["comp_L_input_um"],
                "W_tail_um": system_params["comp_W_tail_um"],
                "L_tail_um": system_params["comp_L_tail_um"],
                "W_latch_p_um": system_params["comp_W_latch_p_um"],
                "W_latch_n_um": system_params["comp_W_latch_n_um"],
            },
            "cdac": {
                "C_unit_fF": system_params["cdac_C_unit_fF"],
            },
            "bias": {
                "V": system_params["bias_V"],
            },
        }

    def default_params(self) -> dict[str, float]:
        """Reference design from AnalogAcademy."""
        return {
            "comp_W_input_um": 32.0,
            "comp_L_input_um": 0.2,
            "comp_W_tail_um": 18.0,
            "comp_L_tail_um": 0.3,
            "comp_W_latch_p_um": 8.0,
            "comp_W_latch_n_um": 4.0,
            "cdac_C_unit_fF": 200.0,
            "bias_V": 0.6,
        }

    def generate_system_netlist(
        self,
        system_params: dict[str, float],
        work_dir: Path,
    ) -> Path:
        """Generate SAR ADC netlist for sine ENOB test."""
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        so_path = self._ensure_so(work_dir)

        block_params = self.params_to_block_params(system_params)
        T_period_us = 1.0  # Fixed at 1MHz

        f_s = 1e6 / T_period_us
        f_in = _SINE_CYCLES * f_s / _N_FFT_SAMPLES

        return generate_sar_adc_netlist(
            comp_params=block_params["comparator"],
            cdac_C_unit_fF=block_params["cdac"]["C_unit_fF"],
            bias_V=block_params["bias"]["V"],
            T_period_us=T_period_us,
            work_dir=work_dir,
            so_path=so_path,
            n_samples=_N_FFT_SAMPLES,
            input_mode="sine",
            vin_sine_amp=_SINE_AMP,
            vin_sine_freq_hz=f_in,
            pdk=self.pdk,
        )

    def compute_system_fom(
        self,
        spice_result: SpiceResult,
        system_params: dict[str, float],
    ) -> float:
        """Compute Walden FoM = 2^ENOB * f_s / P_total.

        Requires ENOB from _extract_enob() stored in measurements.
        Returns 0.0 for failed/invalid results.
        """
        m = spice_result.measurements
        enob = m.get("enob")
        if enob is None or enob <= 0:
            return 0.0

        f_s = 1e6  # 1 MHz
        avg_idd = m.get("avg_idd")
        if avg_idd is None:
            return 0.0

        power_w = 1.2 * abs(avg_idd)
        if power_w <= 0:
            return 0.0

        # Walden FoM: lower is better (energy per conversion step)
        # But for optimization we want HIGHER = better, so invert:
        # FoM = 2^ENOB * f_s / P_total (conversions per joule * resolution)
        fom = (2**enob) * f_s / power_w

        # Spec penalty
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
            power_uw = 1.2 * abs(avg_idd) * 1e6
            if power_uw > _SPEC_POWER_MAX:
                violations.append(f"Power={power_uw:.1f}uW > {_SPEC_POWER_MAX}uW")

        return (len(violations) == 0, violations)

    # ------------------------------------------------------------------
    # ENOB extraction from simulation output
    # ------------------------------------------------------------------

    @staticmethod
    def extract_enob(work_dir: Path) -> dict[str, float]:
        """Extract ENOB from bit_data.txt produced by SAR ADC simulation.

        Uses rectangular window (no windowing) since sampling is coherent
        (M=7 cycles in N=64 samples). Fixes startup artifact (first sample
        may be zero due to SAR reset timing).

        Returns dict with keys: enob, sndr_dB, n_samples, code_min, code_max,
        unique_codes, code_span.
        """
        bit_file = work_dir / "bit_data.txt"
        if not bit_file.exists():
            return {"enob": 0.0, "sndr_dB": 0.0, "error": "no bit_data.txt"}

        data = np.loadtxt(str(bit_file), skiprows=1)
        if data.ndim < 2 or data.shape[1] < 11:
            return {"enob": 0.0, "sndr_dB": 0.0, "error": "insufficient columns"}

        D_raw = data[:, 1:9]  # D0-D7
        dac_clk = data[:, 10]

        # Sample at dac_clk rising edges
        threshold = 0.6
        edges = []
        for i in range(1, len(dac_clk)):
            if dac_clk[i - 1] < threshold <= dac_clk[i]:
                edges.append(i)

        if len(edges) < _N_FFT_SAMPLES:
            return {
                "enob": 0.0,
                "sndr_dB": 0.0,
                "error": f"only {len(edges)} samples (need {_N_FFT_SAMPLES})",
            }

        # Convert to codes: D[0]=MSB (weight 64), D[6]=LSB (weight 1)
        codes = []
        for idx in edges[:_N_FFT_SAMPLES]:
            d_bits = [1 if D_raw[idx, i] > 0.6 else 0 for i in range(8)]
            code = sum(d_bits[i] * (64 >> i) for i in range(7))
            codes.append(code)

        codes = np.array(codes, dtype=float)

        # Fix startup artifact: first sample often reads 0 due to
        # SAR reset timing (clk_samp HIGH during first period)
        if len(codes) > 1 and abs(codes[0] - codes[1]) > 30:
            codes[0] = codes[1]

        N = len(codes)
        unique_codes = len(np.unique(codes))
        code_span = int(codes.max() - codes.min())

        # FFT with rectangular window (coherent sampling: M cycles in N samples)
        # No windowing needed because signal lands exactly on bin M
        spectrum = np.abs(np.fft.fft(codes - codes.mean()))[: N // 2]

        sig_bin = _SINE_CYCLES
        signal_power = spectrum[sig_bin] ** 2
        noise_power = sum(
            spectrum[i] ** 2 for i in range(1, N // 2) if i != sig_bin
        )

        if noise_power <= 0 or signal_power <= 0:
            return {
                "enob": 0.0,
                "sndr_dB": 0.0,
                "n_samples": N,
                "code_min": int(codes.min()),
                "code_max": int(codes.max()),
                "unique_codes": unique_codes,
                "code_span": code_span,
            }

        sndr_dB = 10 * math.log10(signal_power / noise_power)
        enob = (sndr_dB - 1.76) / 6.02

        return {
            "enob": max(0.0, enob),
            "sndr_dB": round(sndr_dB, 2),
            "n_samples": N,
            "code_min": int(codes.min()),
            "code_max": int(codes.max()),
            "unique_codes": unique_codes,
            "code_span": code_span,
        }

    # ------------------------------------------------------------------
    # Prompt metadata
    # ------------------------------------------------------------------

    def prompt_description(self) -> str:
        return (
            f"8-bit SAR ADC on {self.pdk.display_name}. "
            "StrongARM dynamic comparator (PMOS input pair, 12 transistors) "
            "driving a charge redistribution C-DAC (binary-weighted CMIM caps) "
            "with Verilator-compiled SAR logic via d_cosim mixed-signal bridge. "
            "System-level optimization: comparator sizing + C-DAC capacitance + "
            "bias voltage affect ENOB, speed, and power simultaneously. "
            "Key tradeoffs: larger comparator = lower offset but more kickback to C-DAC; "
            "larger C-DAC = less kickback sensitivity but more power and slower settling."
        )

    def design_vars_description(self) -> str:
        return (
            "- comp_W_input_um: comparator input pair PMOS width [4-64 um]. "
            "KEY variable: affects offset (sigma_Vos ~ 1/sqrt(W*L)) and kickback.\n"
            "- comp_L_input_um: input pair length [0.13-2.0 um]. Matching vs speed.\n"
            "- comp_W_tail_um: tail/bias PMOS width [4-40 um]. Current and speed.\n"
            "- comp_L_tail_um: tail/bias length [0.13-2.0 um]. Current matching.\n"
            "- comp_W_latch_p_um: PMOS latch width [1-16 um]. Regeneration speed.\n"
            "- comp_W_latch_n_um: NMOS latch width [1-16 um]. Regen and reset speed.\n"
            "- cdac_C_unit_fF: unit cap for C-DAC [50-500 fF]. "
            "CRITICAL for ENOB: larger = less kickback corruption = higher ENOB, "
            "but more power (larger caps to charge) and slower settling.\n"
            "- bias_V: comparator bias voltage [0.4-0.8 V]. "
            "Controls tail current (higher = more current = faster but more power)."
        )

    def specs_description(self) -> str:
        return (
            f"ENOB >= {_SPEC_ENOB_MIN:.0f} bits, "
            f"SNDR >= {_SPEC_SNDR_MIN:.0f} dB, "
            f"Power <= {_SPEC_POWER_MAX:.0f} uW, "
            f"f_s = 1 MHz"
        )

    def fom_description(self) -> str:
        return (
            "Walden FoM = 2^ENOB * f_s / P_total [conversions/(J*step)]. "
            "ENOB from 64-point FFT of sine response. "
            "Higher FoM is better. Key: maximize ENOB while minimizing power."
        )

    def reference_description(self) -> str:
        return (
            "Reference: comp W_input=32um L_input=0.2um W_tail=18um L_tail=0.3um "
            "W_latch_p=8um W_latch_n=4um, C_unit=200fF, bias=0.6V. "
            "This gives ENOB~2.8 bits, SNDR~18.6dB, Power~833uW. "
            "Only 21 unique codes out of 128 (comparator kickback limits resolution). "
            "Challenge: improve ENOB to >= 4 bits by tuning comp+C-DAC+bias. "
            "Hint: larger C-DAC absorbs kickback better; smaller comparator has less kickback "
            "but worse offset. Key tradeoff."
        )

    def inter_block_constraints(self) -> list[str]:
        return [
            "Comparator input capacitance causes kickback to C-DAC top plate, "
            "corrupting the sampled voltage. Larger C-DAC absorbs this better.",
            "Comparator offset must be less than 0.5 LSB for good ENOB. "
            "Offset depends on input pair W*L (Pelgrom model).",
            "Comparator decision delay must be less than the SAR bit period "
            "(~31ns at T=1us) for all 7 bits to complete within one conversion.",
            "Bias voltage controls comparator tail current: higher bias = more "
            "current = faster decision but more power consumption.",
        ]

    def exploration_hints(self) -> dict[str, int | float]:
        return {
            "evals_per_round": 2,
            "min_rounds": 5,
            "convergence_threshold": 0.05,
            "partition_dim": "cdac_C_unit_fF",
        }
