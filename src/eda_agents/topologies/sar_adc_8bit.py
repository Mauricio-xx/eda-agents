"""Deprecation shim for the legacy 8-bit SAR module name.

The AnalogAcademy SAR referenced here is effectively 7 bits (see
canonical module :mod:`eda_agents.topologies.sar_adc_7bit`). This
module keeps the old import path + class name alive to avoid
breaking external callers but emits a :class:`DeprecationWarning`
on instantiation.

New code should use:

    from eda_agents.topologies.sar_adc_7bit import SAR7BitTopology

Closed in session S9-gap-closure (gap #3).
"""

from __future__ import annotations

import warnings

from eda_agents.topologies.sar_adc_7bit import SAR7BitTopology

__all__ = ["SARADCTopology", "SAR7BitTopology"]


_DEPRECATION_MSG = (
    "eda_agents.topologies.sar_adc_8bit.SARADCTopology is deprecated: "
    "the AnalogAcademy SAR is 7-bit-effective, not 8-bit. Import "
    "SAR7BitTopology from eda_agents.topologies.sar_adc_7bit instead. "
    "The shim will be removed in a future session."
)


class SARADCTopology(SAR7BitTopology):
    """Legacy alias for :class:`SAR7BitTopology`.

    Behaviourally identical; emits a ``DeprecationWarning`` on
    instantiation so downstream callers migrate at their own pace.
    """

    def __init__(self, *args, **kwargs):
        warnings.warn(
            _DEPRECATION_MSG,
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args, **kwargs)
