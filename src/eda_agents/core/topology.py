"""Abstract base class for circuit topology wrappers.

Defines the interface for topology-specific operations: design space
definition, parameter-to-sizing conversion, netlist generation,
FoM computation, and validity checking. Concrete implementations
wrap specific circuits (e.g., MillerOTA, AnalogAcademyOTA, comparators).

This is the extension point for supporting new circuit types in the
autoresearch exploration loop (``AutoresearchRunner``) and the ADK
agent pipeline. Implementing a new CircuitTopology subclass is all
that's needed -- zero changes to runner, harness, or prompt code.

Also provides prompt metadata methods so agent harnesses can build
topology-agnostic prompts and tool specs without hardcoding circuit
details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from eda_agents.core.spice_runner import SpiceResult


class CircuitTopology(ABC):
    """Abstract interface for circuit topologies.

    Each topology defines its own design space, sizing methodology,
    and netlist format, but all share a common evaluation pipeline:

        params -> sizing -> netlist -> SpiceRunner -> FoM

    Subclasses also provide prompt metadata (descriptions, specs,
    reference designs) used by experiment harnesses to generate
    topology-agnostic agent prompts and tool specifications.
    """

    @abstractmethod
    def topology_name(self) -> str:
        """Short identifier (e.g., 'miller_ota', 'aa_ota')."""
        ...

    @abstractmethod
    def design_space(self) -> dict[str, tuple[float, float]]:
        """Parameter ranges as {name: (min, max)}.

        These are the knobs an agent can turn. Values are in the
        natural units for each parameter (S/A, um, pF, uA, etc.).
        """
        ...

    @abstractmethod
    def params_to_sizing(self, params: dict[str, float]) -> dict[str, dict]:
        """Convert design parameters to transistor sizing.

        Parameters
        ----------
        params : dict
            Design space parameters (keys matching design_space()).

        Returns
        -------
        dict
            Mapping of device name -> {W, L, ng, ...} in SI units,
            or {"error": str} if the parameters are invalid.
        """
        ...

    @abstractmethod
    def generate_netlist(
        self, sizing: dict[str, dict], work_dir: Path
    ) -> Path:
        """Write SPICE netlist files and return path to the control file.

        Parameters
        ----------
        sizing : dict
            Output from params_to_sizing().
        work_dir : Path
            Directory to write netlist files into.

        Returns
        -------
        Path
            Path to the .cir control file (ready for SpiceRunner).
        """
        ...

    @abstractmethod
    def compute_fom(
        self, spice_result: SpiceResult, sizing: dict[str, dict]
    ) -> float:
        """Compute figure of merit from SPICE results.

        Should return 0.0 for invalid/failed simulations.
        """
        ...

    @abstractmethod
    def check_validity(
        self, spice_result: SpiceResult, sizing: dict | None = None
    ) -> tuple[bool, list[str]]:
        """Check if SPICE results meet design specifications.

        Parameters
        ----------
        spice_result : SpiceResult
            Parsed simulation results.
        sizing : dict, optional
            Transistor sizing from params_to_sizing(). Some topologies
            need sizing for analytical checks (e.g., Pelgrom offset).

        Returns
        -------
        tuple[bool, list[str]]
            (is_valid, list_of_violations)
        """
        ...

    def default_params(self) -> dict[str, float]:
        """Return a reasonable default design point, if known.

        Default implementation returns midpoint of each range.
        """
        space = self.design_space()
        return {
            name: (lo + hi) / 2.0
            for name, (lo, hi) in space.items()
        }

    def exploration_hints(self) -> dict[str, int | float]:
        """Hints for orchestrated exploration scheduling.

        Override to tune the explore-analyze loop for this topology's
        complexity. Defaults work for 4-6 dimensional continuous spaces.

        Returns
        -------
        dict with optional keys:
            evals_per_round : int
                SPICE evaluations per explorer per round.
            min_rounds : int
                Minimum rounds before considering convergence.
            convergence_threshold : float
                Fractional FoM improvement below which to stop.
            partition_dim : str
                Preferred dimension for partitioning across agents.
                Default: first key from design_space().
        """
        return {}

    def relevant_skills(self) -> list[str | tuple[str, dict]]:
        """Names of skills an LLM runner should inject into the system prompt.

        Each entry is either a bare skill name (``"analog.gmid_sizing"``)
        or a ``(name, kwargs)`` tuple when the skill's ``prompt_fn``
        needs extra render arguments beyond the topology itself.

        Runners that honor this hook (``AutoresearchRunner`` in S10c)
        look up each name in ``eda_agents.skills.registry`` and render
        the skill with ``self`` as context. Default is empty so existing
        topologies remain silent unless they opt in.
        """
        return []

    def auxiliary_tools_description(self) -> str:
        """Description of auxiliary (non-SPICE) tools available.

        Override to tell agents about free lookup tools specific to
        this topology's PDK. Default assumes gmid_lookup is available.
        Return empty string if no auxiliary tools available.
        """
        return (
            "gmid_lookup is available (FREE, no budget cost). Use it to check "
            "intrinsic gain (gm/gds), current density (ID/W), and transit "
            "frequency (fT) at different L values and inversion levels before "
            "committing SPICE budget."
        )

    # ------------------------------------------------------------------
    # Prompt metadata: subclasses provide these so harnesses can build
    # topology-agnostic prompts without hardcoding circuit details.
    # ------------------------------------------------------------------

    @abstractmethod
    def prompt_description(self) -> str:
        """One-paragraph description of the circuit topology for agent prompts.

        Should include: topology type, input pair type, load type,
        compensation scheme, and PDK/process.
        """
        ...

    @abstractmethod
    def design_vars_description(self) -> str:
        """Multi-line description of each design variable for agent prompts.

        Format: one line per variable with name, range, units, and
        physical meaning. Uses '- ' prefix per line.
        """
        ...

    @abstractmethod
    def specs_description(self) -> str:
        """Target specifications as a compact string (e.g., 'Adc >= 50 dB, ...')."""
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

        Subclasses may override to add richer parameter descriptions.
        Default builds a spec from design_space() keys and ranges.
        """
        space = self.design_space()
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
                "name": "simulate_circuit",
                "description": (
                    f"Run SPICE simulation for {self.topology_name()}. "
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
