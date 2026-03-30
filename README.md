# eda-agents

AI/LLM-assisted analog circuit design with SPICE-in-the-loop validation for IHP SG13G2 130nm BiCMOS.

## Overview

`eda-agents` provides infrastructure for automated analog circuit design exploration using LLM agents with real SPICE simulation feedback. It includes:

- **Circuit topology abstractions** (`CircuitTopology`, `SystemTopology`) with a clean evaluation pipeline: params -> sizing -> netlist -> SPICE -> FoM
- **IHP SG13G2 topologies**: Miller OTA, AnalogAcademy PMOS-input OTA, StrongARM comparator, 8-bit SAR ADC
- **SPICE execution** via ngspice with PDK validation, async support, and measurement parsing
- **gm/ID lookup** tables for informed design decisions before committing SPICE budget
- **Agent harnesses** for round-based LLM exploration (OpenAI API) and Google ADK orchestration
- **Budget management** with analytical pre-filtering and MD5 caching

## Installation

```bash
pip install eda-agents

# With LLM agent support
pip install eda-agents[agents]

# With Google ADK support
pip install eda-agents[adk]

# For development
pip install -e ".[dev]"
```

### Requirements

- Python >= 3.11
- ngspice (for SPICE simulations)
- IHP SG13G2 PDK (set `PDK_ROOT` environment variable)

## Quick Start

```python
from eda_agents.topologies.miller_ota import MillerOTADesigner

designer = MillerOTADesigner()
result = designer.analytical_design(
    gmid_input=12.0,    # gm/ID of input pair [S/A]
    gmid_load=10.0,     # gm/ID of load [S/A]
    L_input=0.5e-6,     # input pair channel length [m]
    L_load=0.5e-6,      # load channel length [m]
    Cc=0.5e-12,         # compensation cap [F]
    Ibias=10e-6,        # bias current per branch [A]
)
print(result.summary())
# Av=42.3dB GBW=3.82MHz PM=72.1deg P=28.8uW A=1.23um2 FoM=1.23e+19 valid=False
```

### SPICE-in-the-loop evaluation

```python
from eda_agents.core import SpiceRunner
from eda_agents.topologies.ota_miller import MillerOTATopology

topology = MillerOTATopology()
runner = SpiceRunner()  # uses PDK_ROOT env var

params = topology.default_params()
sizing = topology.params_to_sizing(params)
cir_path = topology.generate_netlist(sizing, work_dir=Path("/tmp/sim"))
result = runner.run(cir_path)

print(f"Adc={result.Adc_dB:.1f}dB, GBW={result.GBW_MHz:.3f}MHz, PM={result.PM_deg:.1f}deg")
```

## Architecture

```
eda_agents/
  core/           # CircuitTopology ABC, SystemTopology ABC, SpiceRunner, gm/ID LUT,
                  # GLayoutRunner, MagicPexRunner, KLayoutDrcRunner, KLayoutLvsRunner
  topologies/     # Miller OTA, AnalogAcademy OTA, GF180 OTA, StrongARM comparator, SAR ADC
  agents/         # SpiceEvaluationHandler, reactive LLM harness, ADK harness,
                  # PostLayoutValidator (full layout -> DRC -> LVS -> PEX -> sim pipeline)
  tools/          # Agent-callable wrappers (DRC, LVS, PEX, layout, post-layout validation)
  parsers/        # DRC, LVS, Liberty, LibreLane, ORFS parsers
  utils/          # Verilog compilation (vlnggen), EDA project detection
```

### Full analog design closure (GF180MCU)

The post-layout validation pipeline closes the full design loop from sizing
through physical verification:

```
specs -> sizing (autoresearch) -> layout (gLayout opamp_twostage)
   -> DRC (KLayout) -> LVS (KLayout) -> PEX (Magic)
   -> post-layout SPICE (ngspice) -> pre/post comparison
```

```bash
# Check all prerequisites
python examples/08_postlayout_validation.py --dry-run

# Validate default OTA design through the full pipeline
python examples/08_postlayout_validation.py

# Validate top-N from autoresearch results
python examples/08_postlayout_validation.py \
    --from-autoresearch /tmp/autoresearch_results/ --top-n 3
```

**Requirements**: gLayout (.venv-glayout), Magic, KLayout, ngspice, GF180MCU PDK.

### Adding a new topology

Implement `CircuitTopology` (or `SystemTopology` for multi-block systems):

```python
from eda_agents.core.topology import CircuitTopology

class MyAmplifier(CircuitTopology):
    def topology_name(self) -> str: return "my_amp"
    def design_space(self) -> dict[str, tuple[float, float]]: ...
    def params_to_sizing(self, params): ...
    def generate_netlist(self, sizing, work_dir): ...
    def compute_fom(self, spice_result, sizing): ...
    def check_validity(self, spice_result, sizing): ...
    # + prompt metadata methods for agent harnesses
```

The harness infrastructure automatically generates topology-agnostic prompts and tool specs from your implementation.

## Coordination (optional)

When installed alongside [Context Teleport](https://github.com/Mauricio-xx/context-teleport) (`pip install eda-agents[coordination]`), agents can use multi-agent coordination strategies (intents, reservations) via MCP. Without CT, all strategies degrade gracefully to independent exploration.

## License

Apache-2.0
