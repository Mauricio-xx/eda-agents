"""Stage runners for discrete RTL-to-GDS flow steps.

Each runner wraps a specific EDA tool or LibreLane sub-flow and returns
a typed ``StageResult``.  Runners accept either a ``ToolEnvironment``
(for standalone tools like verilator/iverilog) or a ``LibreLaneRunner``
(for flow-backed stages like synthesis, placement, STA).

Designed to be tool-agnostic at the interface level: adding a new
design requires a ``DigitalDesign`` subclass, not changes to runners.
"""

from eda_agents.core.stages.rtl_lint_runner import RtlLintRunner
from eda_agents.core.stages.rtl_sim_runner import CocotbDriver, IVerilogDriver, RtlSimRunner
from eda_agents.core.stages.synth_runner import SynthRunner
from eda_agents.core.stages.physical_slice_runner import STAGE_TO_LIBRELANE, PhysicalSliceRunner
from eda_agents.core.stages.sta_runner import StaRunner
from eda_agents.core.stages.precheck_runner import PrecheckRunner

__all__ = [
    "CocotbDriver",
    "IVerilogDriver",
    "PhysicalSliceRunner",
    "PrecheckRunner",
    "RtlLintRunner",
    "RtlSimRunner",
    "STAGE_TO_LIBRELANE",
    "StaRunner",
    "SynthRunner",
]
