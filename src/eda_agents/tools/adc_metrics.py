"""ADC performance metrics via ADCToolbox (MIT, PyPI).

Thin, stable wrapper around ``adctoolbox`` primitives so the rest of
eda-agents does not need to know its exact API or pin to a specific
release. ``adctoolbox`` is an optional dependency: import it lazily and
raise a helpful ``ImportError`` only when the caller actually invokes
a function that needs it.

The canonical entry point is :func:`compute_adc_metrics`, which
returns a uniform dict regardless of how many analyses are requested.
Keys are always present; the value is ``None`` when the underlying
analysis was not computed for this call.

Install with::

    pip install "eda-agents[adc]"
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np

_INSTALL_HINT = (
    "adctoolbox is required for ADC metric analysis. "
    'Install with: pip install "eda-agents[adc]"'
)


def _import_adctoolbox():
    """Import adctoolbox lazily. Raise a clear ImportError otherwise."""
    try:
        import adctoolbox  # noqa: WPS433 (runtime import by design)
    except ImportError as exc:  # pragma: no cover - exercised via test skip
        raise ImportError(_INSTALL_HINT) from exc
    return adctoolbox


# Canonical output schema. Every key is always present in the returned
# dict; unknown/unavailable metrics are ``None``. Array metrics (INL,
# DNL) are returned as ``numpy.ndarray`` when computed, else ``None``.
_OUTPUT_KEYS = (
    "enob",
    "sndr_dbc",
    "sfdr_dbc",
    "snr_dbc",
    "thd_dbc",
    "inl",
    "dnl",
    "walden_fom_fj",
    "coherent_freq_hz",
)


def _empty_result() -> dict[str, Any]:
    return {key: None for key in _OUTPUT_KEYS}


def compute_adc_metrics(
    samples: Iterable[float],
    fs: float,
    num_bits: int | None = None,
    *,
    win_type: str = "hann",
    osr: int = 1,
    max_harmonic: int = 10,
    full_scale: float | tuple[float, float] | None = None,
    power_w: float | None = None,
    fin_target_hz: float | None = None,
    include_inl: bool = True,
) -> dict[str, Any]:
    """Compute the standard set of ADC performance metrics.

    Parameters
    ----------
    samples : array-like
        Output sample sequence (ADC codes or normalised voltages). Must
        contain at least a handful of cycles of the test tone for the
        spectrum analysis to be meaningful.
    fs : float
        Sampling frequency in Hz.
    num_bits : int, optional
        Nominal ADC resolution. Required for INL/DNL when ``samples``
        are normalised voltages; optional otherwise.
    win_type : str, default ``"hann"``
        Window passed to ``adctoolbox.analyze_spectrum`` (``"hann"``,
        ``"hamming"``, ``"boxcar"``).
    osr : int, default 1
        Oversampling ratio forwarded to ``analyze_spectrum``.
    max_harmonic : int, default 10
        Number of harmonics included in THD.
    full_scale : float or (min, max), optional
        Full-scale amplitude or range. Passed to ``analyze_spectrum`` as
        ``max_scale_range`` and to ``analyze_inl_from_sine`` as
        ``full_scale``.
    power_w : float, optional
        Core power consumption in watts. If provided together with the
        computed ENOB, the Walden FoM is added as
        ``walden_fom_fj`` (in fJ/conv-step).
    fin_target_hz : float, optional
        Target input tone frequency in Hz. If provided, the function
        reports the coherent tone frequency ADCToolbox would use.
    include_inl : bool, default True
        When ``False``, skip INL/DNL (useful when you only need
        dynamic metrics and want the call to stay cheap).

    Returns
    -------
    dict
        Keys: ``enob``, ``sndr_dbc``, ``sfdr_dbc``, ``snr_dbc``,
        ``thd_dbc``, ``inl``, ``dnl``, ``walden_fom_fj``,
        ``coherent_freq_hz``. Values are ``None`` when not computed.
    """
    adctoolbox = _import_adctoolbox()

    data = np.asarray(list(samples), dtype=float)
    if data.size < 4:
        raise ValueError(
            f"compute_adc_metrics requires >=4 samples, got {data.size}"
        )

    result = _empty_result()

    spectrum = adctoolbox.analyze_spectrum(
        data,
        fs=fs,
        osr=osr,
        win_type=win_type,
        max_harmonic=max_harmonic,
        max_scale_range=full_scale,
        create_plot=False,
    )
    for src_key, dst_key in (
        ("enob", "enob"),
        ("sndr_dbc", "sndr_dbc"),
        ("sfdr_dbc", "sfdr_dbc"),
        ("snr_dbc", "snr_dbc"),
        ("thd_dbc", "thd_dbc"),
    ):
        if src_key in spectrum:
            result[dst_key] = float(spectrum[src_key])

    if include_inl:
        try:
            inl_dnl = adctoolbox.analyze_inl_from_sine(
                data,
                num_bits=num_bits,
                full_scale=full_scale if isinstance(full_scale, (int, float)) else None,
                create_plot=False,
            )
        except Exception:  # pragma: no cover - defensive; depends on data
            inl_dnl = {}
        if "inl" in inl_dnl:
            result["inl"] = np.asarray(inl_dnl["inl"], dtype=float)
        if "dnl" in inl_dnl:
            result["dnl"] = np.asarray(inl_dnl["dnl"], dtype=float)

    if power_w is not None and result["enob"] is not None:
        result["walden_fom_fj"] = calculate_walden_fom(
            power_w=power_w, fs=fs, enob=result["enob"]
        )

    if fin_target_hz is not None:
        n_fft = int(data.size)
        try:
            fin_actual, _ = adctoolbox.find_coherent_frequency(
                fs=fs, fin_target=fin_target_hz, n_fft=n_fft
            )
            result["coherent_freq_hz"] = float(fin_actual)
        except Exception:  # pragma: no cover - search failure is informational
            result["coherent_freq_hz"] = None

    return result


def calculate_walden_fom(power_w: float, fs: float, enob: float) -> float:
    """Walden FoM in fJ/conv-step (lower is better).

    Thin wrapper around ``adctoolbox.calculate_walden_fom`` converting
    the J/conv-step output into fJ/conv-step since that is the unit
    analog designers report.
    """
    adctoolbox = _import_adctoolbox()
    fom_j = adctoolbox.calculate_walden_fom(power=power_w, fs=fs, enob=enob)
    return float(fom_j) * 1e15


__all__ = ["compute_adc_metrics", "calculate_walden_fom"]
