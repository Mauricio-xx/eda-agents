# SAR ADC naming convention — **RESOLVED (S9-gap-closure, gap #3)**

**Status**: Closed. The canonical names
:class:`SAR7BitTopology` and :class:`SAR7BitBehavioralTopology` live
in ``src/eda_agents/topologies/sar_adc_7bit.py`` and
``sar_adc_7bit_behavioral.py``. The legacy
``sar_adc_8bit`` / ``sar_adc_8bit_behavioral`` modules remain as
thin deprecation shims that re-export the canonical classes as
``SARADCTopology`` / ``SARADC8BitBehavioralTopology`` and emit a
``DeprecationWarning`` on instantiation (not on import, so the shim
does not break bulk-imports).

## What changed

| Before                                                | After                                                        |
|-------------------------------------------------------|--------------------------------------------------------------|
| `topologies/sar_adc_8bit.py::SARADCTopology`          | `topologies/sar_adc_7bit.py::SAR7BitTopology` (canonical)    |
| `topologies/sar_adc_8bit_behavioral.py::SARADC8BitBehavioralTopology` | `topologies/sar_adc_7bit_behavioral.py::SAR7BitBehavioralTopology` (canonical) |
| `sar_adc_8bit.cir` emitted by the netlist generator   | `sar_adc_7bit.cir`                                           |
| `topology_name()` returned `"sar_adc_8bit(_behavioral)"` | `"sar_adc_7bit(_behavioral)"`                                |

The legacy symbols still import cleanly; instantiating the shim class
(`SARADCTopology()` / `SARADC8BitBehavioralTopology()`) emits a
`DeprecationWarning` pointing at the canonical names.

## Why 7, not 8

- The upstream SAR FSM (`sar_logic.v`) iterates 7 times (`counter < 7`),
  so D[7] always stays 0.
- The upstream CDAC reuses the LSB switch for the dummy cap (8 caps,
  7 distinct binary-weighted controls).
- Net effective resolution = 7 bits, despite the 8-wire D bus.

`SARADC11BitTopology` is unaffected — it was designed from scratch
with 11 true binary weights and keeps its name.

## Coverage

- `tests/test_sar_adc_7bit_behavioral.py` — canonical API coverage
  (the same scenarios the old 8-bit test exercised).
- `tests/test_sar_adc_8bit_behavioral.py` — now shim-specific: verifies
  import is silent, re-exports align, and instantiation emits
  `DeprecationWarning`.
- `src/eda_agents/agents/system_handler.py` isinstance check covers
  both the canonical class and the legacy alias so pre-rename callers
  keep working with zero code change.

## Follow-up (deferred, low-priority)

- Drop the shim modules in a future session once external callers
  have migrated (search outside this repo).
- Upstream `sar_logic.v` is third-party and stays on the AA naming.
