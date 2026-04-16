"""Abstract base class for digital design wrappers.

Defines the interface for digital RTL-to-GDS designs, parallel to
``CircuitTopology`` for analog circuits.  Concrete implementations
wrap specific projects (e.g., fazyrv-hachure, Systolic_MAC) and
expose their design space, flow configuration, FoM computation, and
prompt metadata.

This is the extension point for supporting new digital designs in the
DigitalAutoresearchRunner (Phase 3) and the ADK digital sub-agents
(Phase 4).  Implementing a new DigitalDesign subclass is all that's
needed — zero changes to runners, harnesses, or prompt code.

Evaluation pipeline:
    config overrides -> LibreLane flow -> FlowMetrics -> FoM
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from eda_agents.core.flow_metrics import FlowMetrics

if TYPE_CHECKING:
    from eda_agents.core.pdk import PdkConfig


@dataclass
class TestbenchSpec:
    """Describes how to run RTL simulation for a design.

    Parameters
    ----------
    driver : {"cocotb", "iverilog"}
        Simulation driver type.
    target : str
        Make target or script path (e.g. "sim", "make sim").
    env_overrides : dict
        Extra environment variables (e.g. {"GL": "1"} for gate-level).
    work_dir_relative : str
        Working directory relative to project_dir (default: ".").
    """

    driver: Literal["cocotb", "iverilog"]
    target: str
    env_overrides: dict[str, str] = field(default_factory=dict)
    work_dir_relative: str = "."


class DigitalDesign(ABC):
    """Abstract interface for digital RTL-to-GDS designs.

    Each design defines its own design space, flow configuration, and
    FoM formula, but all share a common evaluation pipeline:

        config overrides -> LibreLane flow -> FlowMetrics -> FoM

    Subclasses also provide prompt metadata (descriptions, specs,
    reference designs) used by agent harnesses to generate
    design-agnostic prompts and tool specifications.
    """

    @abstractmethod
    def project_name(self) -> str:
        """Short identifier (e.g., 'fazyrv-hachure')."""
        ...

    @abstractmethod
    def specification(self) -> str:
        """Human-readable design specification (multi-line).

        Describes what the design does, its interfaces, and any
        constraints that the flow must respect.
        """
        ...

    @abstractmethod
    def design_space(self) -> dict[str, list | tuple]:
        """Tunable parameters as {name: values_or_range}.

        Values may be:
        - ``list``: discrete choices (e.g., ``[45, 55, 65, 75, 85]``).
          Use for knobs with non-monotonic response (Phase 0 finding).
        - ``tuple[float, float]``: continuous range ``(min, max)``.

        Only knobs whose effect was observed in Phase 0 should be
        exposed.  Additional knobs are reachable via
        ``flow_config_overrides()`` with ``force=True``.
        """
        ...

    @abstractmethod
    def flow_config_overrides(self) -> dict[str, object]:
        """Default config overrides applied before every flow run.

        These are design-specific values that must be set regardless
        of the exploration parameters (e.g., macro placements, PDN
        config, design name).

        Keys should match LibreLane v3 config key names.
        """
        ...

    @abstractmethod
    def project_dir(self) -> Path:
        """Root directory of the design project.

        Typically ``$EDA_AGENTS_DIGITAL_DESIGNS_DIR/<repo_name>``.
        """
        ...

    @abstractmethod
    def librelane_config(self) -> Path:
        """Path to the primary LibreLane config file (YAML or JSON)."""
        ...

    @abstractmethod
    def compute_fom(self, metrics: FlowMetrics) -> float:
        """Design-specific figure of merit from flow metrics.

        Higher is better.  Return 0.0 for invalid/failed runs.
        """
        ...

    @abstractmethod
    def check_validity(self, metrics: FlowMetrics) -> tuple[bool, list[str]]:
        """Check whether flow metrics meet design constraints.

        Returns (is_valid, list_of_violations).
        """
        ...

    # ------------------------------------------------------------------
    # Optional overrides with sensible defaults
    # ------------------------------------------------------------------

    def rtl_sources(self) -> list[Path]:
        """Paths to RTL source files.

        Default returns an empty list (design may use LibreLane's
        VERILOG_FILES glob instead of explicit paths).
        """
        return []

    def rtl_params(self) -> dict[str, tuple]:
        """Typed RTL parameters that can be set before synthesis.

        E.g., Verilog defines or top-level generics.
        Default: empty (no RTL-level parameters exposed).
        """
        return {}

    def rtl_total_lines(self) -> int:
        """Total line count across all RTL sources.

        Used to decide between litellm (small) and CC CLI (large)
        backends for RTL-aware autoresearch strategies.
        """
        total = 0
        for src in self.rtl_sources():
            if src.is_file():
                total += len(src.read_text().splitlines())
        return total

    def testbench(self) -> TestbenchSpec | None:
        """How to run RTL simulation, or None if not available."""
        return None

    def flow_type(self) -> Literal["Classic", "Chip"]:
        """LibreLane flow type: 'Classic' (macro-only) or 'Chip' (with padring)."""
        return "Classic"

    def pdk_root(self) -> Path | None:
        """Explicit PDK root for this design, or None to use env/default.

        Designs that clone their own PDK (e.g., via ``make clone-pdk``)
        should return the per-project PDK path here.
        """
        return None

    def pdk_config(self) -> PdkConfig | None:
        """PDK config bound to this design, or None to resolve from env.

        When a design knows which PDK it targets (e.g. GenericDesign
        constructed with ``pdk_config="ihp_sg13g2"``), override this to
        expose that config. Callers fall back to ``resolve_pdk()`` when
        this returns None.
        """
        return None

    def gl_sim_cells_glob(self) -> str | None:
        """Glob (pdk_root-relative) for stdcell Verilog models, or None.

        Override to point the GlSimRunner at non-default cell libraries
        (e.g. a high-density variant). Default ``None`` falls back to
        ``PdkConfig.stdcell_verilog_models_glob``.
        """
        return None

    def gl_sim_dut_instance_path(self) -> str:
        """Hierarchical path to the DUT instance inside the testbench.

        Used by GlSimRunner to point ``$sdf_annotate`` at the right
        module. The default assumes the agent follows the prompt
        convention (``module tb; <design> dut (...);``), i.e.
        ``"tb.dut"``.
        """
        return "tb.dut"

    def shell_wrapper(self) -> str | None:
        """Shell command prefix for environments like nix-shell.

        When set, LibreLaneRunner wraps flow commands as:
        ``<shell_wrapper> '<python> -m librelane ...'``

        Example: ``"nix-shell /path/to/project --run"`` for projects
        whose LibreLane and tools live inside a Nix devshell.
        """
        return None

    def default_config(self) -> dict[str, object]:
        """Return a reasonable default design point.

        For discrete design spaces, returns the first value in each list.
        For continuous ranges, returns the midpoint.
        """
        space = self.design_space()
        result: dict[str, object] = {}
        for name, values in space.items():
            if isinstance(values, list):
                # Discrete: pick the middle element
                result[name] = values[len(values) // 2]
            elif isinstance(values, tuple) and len(values) == 2:
                # Continuous range: midpoint
                result[name] = (values[0] + values[1]) / 2.0
            else:
                result[name] = values
        return result

    def exploration_hints(self) -> dict[str, int | float]:
        """Hints for the autoresearch exploration loop.

        Override to tune budget, convergence, etc. for this design.
        Defaults are conservative for digital flows (~5 min per eval).
        """
        return {}

    def relevant_skills(self) -> list[str | tuple[str, dict]]:
        """Names of skills an LLM runner should inject into the system prompt.

        Each entry is either a bare skill name (``"digital.synthesis"``)
        or a ``(name, kwargs)`` tuple when the skill's ``prompt_fn``
        needs extra render arguments beyond the design itself.

        Runners that honor this hook (``DigitalAutoresearchRunner`` in
        S10c) look up each name in ``eda_agents.skills.registry`` and
        render the skill with ``self`` as context. Default is empty so
        existing designs remain silent unless they opt in.
        """
        return []

    # ------------------------------------------------------------------
    # Prompt metadata: agent harnesses consume these to build prompts
    # ------------------------------------------------------------------

    @abstractmethod
    def prompt_description(self) -> str:
        """One-paragraph description of the design for agent prompts.

        Should include: design type, key features, target PDK/process.
        """
        ...

    @abstractmethod
    def design_vars_description(self) -> str:
        """Multi-line description of each tunable variable.

        Format: one line per variable with name, range/values, units,
        and physical meaning.
        """
        ...

    @abstractmethod
    def specs_description(self) -> str:
        """Target specs as a compact string (e.g., 'WNS >= 0, DRC clean')."""
        ...

    @abstractmethod
    def fom_description(self) -> str:
        """FoM formula and explanation for agent prompts."""
        ...

    @abstractmethod
    def reference_description(self) -> str:
        """Reference design point and its measured performance."""
        ...

    def tool_spec(self) -> dict:
        """Generate OpenAI function-calling tool spec from design_space().

        Produces a spec where each tunable knob is a parameter.
        Discrete choices use ``enum``; continuous ranges use min/max.
        """
        space = self.design_space()
        properties: dict[str, dict] = {}
        required: list[str] = []

        for name, values in space.items():
            if isinstance(values, list):
                properties[name] = {
                    "type": "number",
                    "enum": values,
                    "description": f"{name} (one of {values})",
                }
            elif isinstance(values, tuple) and len(values) == 2:
                lo, hi = values
                properties[name] = {
                    "type": "number",
                    "description": f"{name} [{lo}-{hi}]",
                }
            required.append(name)

        return {
            "type": "function",
            "function": {
                "name": f"run_flow_{self.project_name().replace('-', '_')}",
                "description": (
                    f"Run LibreLane flow for {self.project_name()}. "
                    f"{self.specs_description()}. "
                    f"{self.fom_description()}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
