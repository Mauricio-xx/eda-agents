"""Abstract base class for multi-block circuit system topologies.

Extends the single-block CircuitTopology pattern to systems where multiple
interacting circuit blocks must be sized concurrently. The canonical example
is an 8-bit SAR ADC: comparator + C-DAC + switches + bias + timing, with
genuine inter-block constraints (comparator offset affects ENOB, C-DAC size
affects comparator input load, etc.).

Key difference from CircuitTopology: SystemTopology composes block-level
CircuitTopology instances and adds system-level evaluation (e.g., ENOB, SNDR,
Walden FoM) computed from a full-system simulation rather than per-block sims.

Agent assignment strategies:
  - per_block: Each agent owns one block's parameters, receives system-level
    FoM. Coordination via CT shares interface constraints across blocks.
  - co_tuning: All agents see the full system design space. CT coordinates
    which regions each explores. Current single-block approach extended.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from eda_agents.core.spice_runner import SpiceResult
from eda_agents.core.topology import CircuitTopology

if TYPE_CHECKING:
    pass


class SystemTopology(ABC):
    """Abstract interface for multi-block circuit systems.

    Composes CircuitTopology instances for individual blocks and adds
    system-level netlist generation, simulation, and evaluation.

    Evaluation pipeline:
        system_params -> per-block params -> per-block sizing
            -> system netlist -> SpiceRunner -> system FoM

    Subclasses provide system-specific netlisting (how blocks connect)
    and system-level FoM (e.g., Walden FoM for SAR ADC).
    """

    @abstractmethod
    def topology_name(self) -> str:
        """Short identifier (e.g., 'sar_adc_8bit')."""
        ...

    @abstractmethod
    def block_names(self) -> list[str]:
        """Ordered list of designable block names."""
        ...

    @abstractmethod
    def block_topology(self, name: str) -> CircuitTopology | None:
        """Return the CircuitTopology for a named block, or None if no
        standalone topology exists (e.g., passive C-DAC)."""
        ...

    @abstractmethod
    def system_design_space(self) -> dict[str, tuple[float, float]]:
        """Full system parameter ranges as {name: (min, max)}.

        Includes all block parameters and any system-level params
        (e.g., sampling frequency, bias current). Names should be
        prefixed with block name for clarity: 'comp_W_input_um',
        'cdac_C_unit_fF', 'bias_Ibias_uA'.
        """
        ...

    @abstractmethod
    def block_design_space(self, block_name: str) -> dict[str, tuple[float, float]]:
        """Parameter ranges for a single block (subset of system_design_space).

        Used in per_block agent mode to restrict an agent's view.
        """
        ...

    @abstractmethod
    def params_to_block_params(
        self, system_params: dict[str, float]
    ) -> dict[str, dict[str, float]]:
        """Decompose system params into per-block param dicts.

        Returns {block_name: {param_name: value}} mapping.
        Block param names may differ from system param names
        (e.g., system 'comp_W_input_um' -> block 'W_input_um').
        """
        ...

    @abstractmethod
    def generate_system_netlist(
        self,
        system_params: dict[str, float],
        work_dir: Path,
    ) -> Path:
        """Generate full system SPICE netlist for simulation.

        This creates the top-level .cir with all blocks wired together,
        including any mixed-signal bridges (d_cosim for digital logic).

        Parameters
        ----------
        system_params : dict
            Full system parameters (keys from system_design_space()).
        work_dir : Path
            Directory to write netlist and support files.

        Returns
        -------
        Path
            Path to the top-level .cir control file.
        """
        ...

    @abstractmethod
    def compute_system_fom(
        self,
        spice_result: SpiceResult,
        system_params: dict[str, float],
    ) -> float:
        """Compute system-level figure of merit.

        For SAR ADC: Walden FoM = 2^ENOB * f_s / P_total.
        Returns 0.0 for failed or invalid simulations.
        """
        ...

    @abstractmethod
    def check_system_validity(
        self,
        spice_result: SpiceResult,
        system_params: dict[str, float],
    ) -> tuple[bool, list[str]]:
        """Check if system meets performance specifications.

        Returns (is_valid, violations) where violations lists
        human-readable spec failures.
        """
        ...

    # ------------------------------------------------------------------
    # Defaults with sensible implementations
    # ------------------------------------------------------------------

    def default_params(self) -> dict[str, float]:
        """Return a reasonable default system design point.

        Default: midpoint of each parameter range.
        Override for known-good reference designs.
        """
        space = self.system_design_space()
        return {
            name: (lo + hi) / 2.0
            for name, (lo, hi) in space.items()
        }

    # ------------------------------------------------------------------
    # CircuitTopology compatibility shims
    # ------------------------------------------------------------------
    # These allow SystemTopology to be used in harnesses that expect
    # CircuitTopology (design_space, params_to_sizing, generate_netlist,
    # compute_fom, check_validity). The system-level methods are used
    # under the hood.

    def design_space(self) -> dict[str, tuple[float, float]]:
        """Alias for system_design_space() -- CircuitTopology compat."""
        return self.system_design_space()

    def params_to_sizing(self, params: dict[str, float]) -> dict[str, dict]:
        """Minimal sizing dict for compatibility. Returns block decomposition."""
        block_params = self.params_to_block_params(params)
        sizing = {"_system_params": params}
        for bname, bparams in block_params.items():
            topo = self.block_topology(bname)
            if topo is not None:
                sizing[bname] = topo.params_to_sizing(bparams)
            else:
                sizing[bname] = bparams
        return sizing

    def generate_netlist(
        self, sizing: dict[str, dict], work_dir: Path
    ) -> Path:
        """Delegate to generate_system_netlist -- CircuitTopology compat."""
        params = sizing.get("_system_params", self.default_params())
        return self.generate_system_netlist(params, work_dir)

    def compute_fom(
        self, spice_result, sizing: dict[str, dict]
    ) -> float:
        """Delegate to compute_system_fom -- CircuitTopology compat."""
        params = sizing.get("_system_params", self.default_params())
        return self.compute_system_fom(spice_result, params)

    def check_validity(
        self, spice_result, sizing: dict | None = None
    ) -> tuple[bool, list[str]]:
        """Delegate to check_system_validity -- CircuitTopology compat."""
        params = (sizing or {}).get("_system_params", self.default_params())
        return self.check_system_validity(spice_result, params)

    def agent_assignment_strategy(self) -> str:
        """Preferred agent assignment: 'per_block' or 'co_tuning'.

        per_block: agents specialize on individual blocks, coordinate
                   via CT for interface constraints.
        co_tuning: all agents see full space, CT coordinates regions.

        Default is per_block -- the whole point of SystemTopology is
        that blocks have distinct owners who must coordinate.
        """
        return "per_block"

    def inter_block_constraints(self) -> list[str]:
        """Human-readable descriptions of inter-block coupling.

        These are included in agent prompts so they understand why
        coordination matters. Example:
          "Comparator input capacitance loads the C-DAC top plate,
           affecting settling time and ENOB."
        """
        return []

    # ------------------------------------------------------------------
    # Prompt metadata (parallel to CircuitTopology)
    # ------------------------------------------------------------------

    @abstractmethod
    def prompt_description(self) -> str:
        """One-paragraph system description for agent prompts."""
        ...

    @abstractmethod
    def design_vars_description(self) -> str:
        """Multi-line description of system design variables."""
        ...

    @abstractmethod
    def specs_description(self) -> str:
        """Target system specifications as a compact string."""
        ...

    @abstractmethod
    def fom_description(self) -> str:
        """System FoM formula and explanation."""
        ...

    @abstractmethod
    def reference_description(self) -> str:
        """Reference system design point and measured performance."""
        ...

    def block_prompt_description(self, block_name: str) -> str:
        """Per-block description for per_block agent mode.

        Includes the block's role in the system and interface constraints
        with neighboring blocks. Default delegates to block topology.
        """
        topo = self.block_topology(block_name)
        if topo is not None:
            return topo.prompt_description()
        return f"Block '{block_name}' (no standalone topology)"

    def auxiliary_tools_description(self) -> str:
        """Auxiliary tools available for system-level exploration."""
        return ""

    def exploration_hints(self) -> dict[str, int | float]:
        """Hints for system-level exploration scheduling.

        System sims are typically slower than single-block, so
        default to fewer evals per round.
        """
        return {
            "evals_per_round": 3,
            "min_rounds": 4,
            "convergence_threshold": 0.02,
        }

    def tool_spec(self) -> dict:
        """Generate OpenAI function-calling tool spec from system_design_space()."""
        space = self.system_design_space()
        properties = {}
        required = []
        for name, (lo, hi) in space.items():
            properties[name] = {
                "type": "number",
                "description": f"{name} [{lo}-{hi}]",
            }
            required.append(name)

        return {
            "type": "function",
            "function": {
                "name": "simulate_system",
                "description": (
                    f"Run full-system SPICE simulation for {self.topology_name()} "
                    f"on IHP SG13G2. {self.specs_description()}. "
                    f"{self.fom_description()}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def block_tool_spec(self, block_name: str) -> dict:
        """Generate tool spec for a single block (per_block mode).

        Only exposes parameters for the given block, but the simulation
        still runs the full system (other blocks at their current values).
        """
        space = self.block_design_space(block_name)
        properties = {}
        required = []
        for name, (lo, hi) in space.items():
            properties[name] = {
                "type": "number",
                "description": f"{name} [{lo}-{hi}]",
            }
            required.append(name)

        constraints = self.inter_block_constraints()
        constraint_text = " ".join(constraints) if constraints else ""

        return {
            "type": "function",
            "function": {
                "name": f"simulate_{block_name}",
                "description": (
                    f"Run full-system SPICE simulation varying {block_name} block "
                    f"parameters. Other blocks held at current best values. "
                    f"System FoM: {self.fom_description()} "
                    f"{constraint_text}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
