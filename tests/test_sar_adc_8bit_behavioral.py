"""Deprecation-shim coverage for the legacy ``sar_adc_8bit_behavioral``
module name.

Canonical behavioural-SAR tests live in
:mod:`tests.test_sar_adc_7bit_behavioral`. This file only verifies that
the shim at ``eda_agents.topologies.sar_adc_8bit_behavioral``:

* still imports cleanly (no hidden syntax/runtime errors),
* re-exports the same public API as the canonical module,
* does NOT warn on import,
* DOES emit a :class:`DeprecationWarning` on instantiation, and
* keeps behavioural equality with :class:`SAR7BitBehavioralTopology`.

Closed in S9-gap-closure (gap #3). Remove together with the shim once
downstream callers have migrated.
"""

from __future__ import annotations

import warnings

from eda_agents.topologies.sar_adc_7bit_behavioral import (
    SAR7BitBehavioralTopology,
)


def test_shim_module_import_is_silent():
    """Merely importing the shim must not raise or warn."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        import eda_agents.topologies.sar_adc_8bit_behavioral  # noqa: F401


def test_shim_reexports_public_names():
    """The shim re-exports the canonical API under its __all__."""
    from eda_agents.topologies import sar_adc_8bit_behavioral as shim

    for name in (
        "BehavioralComparatorKit",
        "SAR7BitBehavioralTopology",
        "SARADC8BitBehavioralTopology",
        "behavioral_comparator_cards",
        "behavioral_comparator_section",
        "generate_behavioral_comparator_deck",
    ):
        assert hasattr(shim, name), name
        assert name in shim.__all__, name


def test_legacy_class_emits_deprecation_warning_on_instantiation():
    from eda_agents.topologies.sar_adc_8bit_behavioral import (
        SARADC8BitBehavioralTopology,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        SARADC8BitBehavioralTopology()

    deprecation = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecation, "expected DeprecationWarning on instantiation"
    msg = str(deprecation[0].message)
    assert "SARADC8BitBehavioralTopology" in msg
    assert "SAR7BitBehavioralTopology" in msg
    assert "sar_adc_7bit_behavioral" in msg


def test_legacy_instance_is_subclass_of_canonical():
    """The alias must still behave as a canonical instance."""
    from eda_agents.topologies.sar_adc_8bit_behavioral import (
        SARADC8BitBehavioralTopology,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        inst = SARADC8BitBehavioralTopology()
    assert isinstance(inst, SAR7BitBehavioralTopology)
    # Topology name stays on the canonical value; the shim does not
    # rebrand behaviour, only the Python symbol used to construct it.
    assert inst.topology_name() == "sar_adc_7bit_behavioral"
