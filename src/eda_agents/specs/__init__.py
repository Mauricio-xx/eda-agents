"""Spec YAML loader for analog block specifications.

Re-exports the Pydantic v2 models used by the analog-role harness so
callers can do ``from eda_agents.specs import BlockSpec, load_spec``.
"""

from eda_agents.specs.spec_yaml import (
    BlockSpec,
    SpecTarget,
    Supply,
    load_spec,
    load_spec_from_string,
)

__all__ = [
    "BlockSpec",
    "SpecTarget",
    "Supply",
    "load_spec",
    "load_spec_from_string",
]
