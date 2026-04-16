"""Circuit topology implementations.

Exposes both a Python import path per topology class and a
string-keyed resolver (``get_topology_by_name``) used by clients
that name topologies without importing them — LLM-facing tools
(S10c skill injection) and the MCP server (S10d).
"""

from __future__ import annotations

from typing import Callable

from eda_agents.core.system_topology import SystemTopology
from eda_agents.core.topology import CircuitTopology
from eda_agents.topologies.comparator_strongarm import StrongARMComparatorTopology
from eda_agents.topologies.ota_analogacademy import AnalogAcademyOTATopology
from eda_agents.topologies.ota_gf180 import GF180OTATopology
from eda_agents.topologies.ota_miller import MillerOTATopology
from eda_agents.topologies.sar_adc_7bit import SAR7BitTopology
from eda_agents.topologies.sar_adc_7bit_behavioral import SAR7BitBehavioralTopology
from eda_agents.topologies.sar_adc_11bit import SARADC11BitTopology

__all__ = [
    "MillerOTATopology",
    "AnalogAcademyOTATopology",
    "StrongARMComparatorTopology",
    "GF180OTATopology",
    "SAR7BitTopology",
    "SAR7BitBehavioralTopology",
    "SARADC11BitTopology",
    "get_topology_by_name",
    "list_topology_names",
]


# Maps canonical topology_name() strings to zero-arg factory callables.
# Deprecation shims (sar_adc_8bit*) are intentionally omitted — callers
# should go through the canonical names.
_TOPOLOGY_REGISTRY: dict[str, Callable[[], CircuitTopology | SystemTopology]] = {
    "miller_ota": MillerOTATopology,
    "aa_ota": AnalogAcademyOTATopology,
    "strongarm_comp": StrongARMComparatorTopology,
    "gf180_ota": GF180OTATopology,
    "sar_adc_7bit": SAR7BitTopology,
    "sar_adc_7bit_behavioral": SAR7BitBehavioralTopology,
    "sar_adc_11bit": SARADC11BitTopology,
}


def get_topology_by_name(name: str) -> CircuitTopology | SystemTopology:
    """Instantiate a topology by its canonical ``topology_name()``.

    Parameters
    ----------
    name : str
        Identifier returned by the topology's ``topology_name()``
        method (e.g. ``"miller_ota"``).

    Returns
    -------
    CircuitTopology | SystemTopology
        A freshly constructed topology with default parameters.

    Raises
    ------
    KeyError
        If ``name`` does not match any registered topology.
    """
    try:
        factory = _TOPOLOGY_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown topology {name!r}. Known: {sorted(_TOPOLOGY_REGISTRY)}"
        ) from exc
    return factory()


def list_topology_names() -> list[str]:
    """Return the sorted list of registered topology names."""
    return sorted(_TOPOLOGY_REGISTRY)
