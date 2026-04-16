"""Golden-sine regression for the ADCToolbox wrapper.

``tools.adc_metrics.compute_adc_metrics`` is the canonical entry point
for ADC dynamic/static analysis. This suite hits it with a synthetic
coherent sine (ideal quantisation, additive Gaussian noise at a known
dBFS) and checks that the reported metrics hit the analytical target
within a tolerance that leaves room for window / bin-leakage effects.

Skips cleanly when ``adctoolbox`` is not installed - the extra
``[adc]`` is optional for the eda-agents core.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("adctoolbox")

from eda_agents.tools.adc_metrics import (  # noqa: E402
    calculate_walden_fom,
    compute_adc_metrics,
)


# Coherent sampling with a prime number of cycles avoids bin leakage
# without any window. N=4096 is power of two, 127 is the largest prime
# below N/32; this gives ~31 samples per cycle, far from DC.
_N = 4096
_CYCLES = 127
_FS = 1e6


def _quantized_sine(num_bits: int, snr_target_db: float | None = None):
    """Return (codes, fin) for an ideal quantised sine.

    When ``snr_target_db`` is ``None``, only the quantisation noise is
    present (ENOB should land within ~0.1 of ``num_bits``).
    """
    full_scale_lsb = 2 ** (num_bits - 1)
    fin = _CYCLES * _FS / _N
    t = np.arange(_N) / _FS
    # 99% of full scale so the quantiser never saturates.
    amp = 0.99 * full_scale_lsb
    sine = amp * np.sin(2 * math.pi * fin * t)
    if snr_target_db is not None:
        sig_pwr = amp**2 / 2
        noise_pwr = sig_pwr * 10 ** (-snr_target_db / 10.0)
        rng = np.random.default_rng(42)
        sine = sine + rng.normal(0, math.sqrt(noise_pwr), _N)
    codes = np.round(sine).astype(float)
    return codes, fin


class TestComputeAdcMetrics:
    def test_ideal_10bit_sine(self):
        num_bits = 10
        codes, fin = _quantized_sine(num_bits)

        metrics = compute_adc_metrics(
            codes,
            fs=_FS,
            num_bits=num_bits,
            include_inl=False,
            fin_target_hz=fin,
        )

        # Ideal quantiser: ENOB ~= num_bits, SNDR close to
        # 6.02 * num_bits + 1.76 dB.
        expected_sndr = 6.02 * num_bits + 1.76
        assert metrics["enob"] == pytest.approx(num_bits, abs=0.2)
        assert metrics["sndr_dbc"] == pytest.approx(expected_sndr, abs=1.0)
        assert metrics["sfdr_dbc"] is not None
        assert metrics["sfdr_dbc"] > expected_sndr  # SFDR >= SNDR always
        assert metrics["snr_dbc"] == pytest.approx(expected_sndr, abs=1.5)
        assert metrics["coherent_freq_hz"] is not None

    def test_noisy_12bit_drops_enob(self):
        num_bits = 12
        codes, fin = _quantized_sine(num_bits, snr_target_db=50.0)

        metrics = compute_adc_metrics(
            codes,
            fs=_FS,
            num_bits=num_bits,
            include_inl=False,
            fin_target_hz=fin,
        )

        # 50 dB SNR bounds ENOB to ~ (50-1.76)/6.02 ~= 8 bits regardless of
        # the quantiser resolution.
        expected_enob = (50.0 - 1.76) / 6.02
        assert metrics["enob"] == pytest.approx(expected_enob, abs=0.4)
        assert metrics["sndr_dbc"] == pytest.approx(50.0, abs=1.5)

    def test_inl_dnl_shape(self):
        num_bits = 10
        codes, _ = _quantized_sine(num_bits)

        metrics = compute_adc_metrics(
            codes,
            fs=_FS,
            num_bits=num_bits,
            include_inl=True,
        )

        assert metrics["inl"] is not None
        assert metrics["dnl"] is not None
        assert metrics["inl"].ndim == 1
        assert metrics["dnl"].ndim == 1
        # Ideal quantiser: |INL| and |DNL| should stay well below 1 LSB
        # aside from edge clipping artefacts.
        assert np.abs(metrics["inl"]).max() < 1.0
        assert np.abs(metrics["dnl"]).max() < 1.0

    def test_walden_fom_present_when_power_given(self):
        codes, fin = _quantized_sine(10)
        metrics = compute_adc_metrics(
            codes,
            fs=_FS,
            num_bits=10,
            include_inl=False,
            power_w=100e-6,
            fin_target_hz=fin,
        )
        assert metrics["walden_fom_fj"] is not None
        assert metrics["walden_fom_fj"] > 0

    def test_walden_fom_fj_formula(self):
        # Directly check the fJ-unit wrapper against the textbook
        # definition: Power / (2^ENOB * Fs) in Joules, scaled to fJ.
        power_w = 1e-4
        fs = 1e6
        enob = 10.0
        fj = calculate_walden_fom(power_w=power_w, fs=fs, enob=enob)
        expected_fj = power_w / ((2**enob) * fs) * 1e15
        assert fj == pytest.approx(expected_fj, rel=1e-9)

    def test_too_few_samples_raises(self):
        with pytest.raises(ValueError, match=">=4 samples"):
            compute_adc_metrics([0.0, 1.0, 2.0], fs=_FS)

    def test_output_schema_keys(self):
        codes, _ = _quantized_sine(10)
        metrics = compute_adc_metrics(codes, fs=_FS, num_bits=10,
                                      include_inl=False)
        for key in (
            "enob",
            "sndr_dbc",
            "sfdr_dbc",
            "snr_dbc",
            "thd_dbc",
            "inl",
            "dnl",
            "walden_fom_fj",
            "coherent_freq_hz",
        ):
            assert key in metrics, f"missing canonical key {key}"
