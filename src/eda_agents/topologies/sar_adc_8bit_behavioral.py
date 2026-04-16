"""Deprecation shim for the legacy 8-bit behavioural SAR module name.

New code should use:

    from eda_agents.topologies.sar_adc_7bit_behavioral import (
        SAR7BitBehavioralTopology,
        BehavioralComparatorKit,
        behavioral_comparator_cards,
        generate_behavioral_comparator_deck,
    )

Closed in session S9-gap-closure (gap #3).
"""

from __future__ import annotations

import warnings

from eda_agents.topologies.sar_adc_7bit_behavioral import (
    BehavioralComparatorKit,
    SAR7BitBehavioralTopology,
    behavioral_comparator_cards,
    behavioral_comparator_section,
    build_behavioral_comparator_kit,
    generate_behavioral_comparator_deck,
)

__all__ = [
    "BehavioralComparatorKit",
    "SAR7BitBehavioralTopology",
    "SARADC8BitBehavioralTopology",
    "behavioral_comparator_cards",
    "behavioral_comparator_section",
    "build_behavioral_comparator_kit",
    "generate_behavioral_comparator_deck",
]


_DEPRECATION_MSG = (
    "eda_agents.topologies.sar_adc_8bit_behavioral."
    "SARADC8BitBehavioralTopology is deprecated: the AnalogAcademy "
    "SAR is 7-bit-effective. Import SAR7BitBehavioralTopology from "
    "eda_agents.topologies.sar_adc_7bit_behavioral instead. The "
    "shim will be removed in a future session."
)


class SARADC8BitBehavioralTopology(SAR7BitBehavioralTopology):
    """Legacy alias for :class:`SAR7BitBehavioralTopology`.

    Behaviourally identical; emits a ``DeprecationWarning`` on
    instantiation.
    """

    def __init__(self, *args, **kwargs):
        warnings.warn(
            _DEPRECATION_MSG,
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args, **kwargs)
