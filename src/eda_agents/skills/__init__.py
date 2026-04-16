"""Skill abstraction for eda-agents.

A ``Skill`` bundles a named, reusable unit of agent capability:
- an optional prompt template (callable returning str),
- an optional OpenAI/ADK function-calling tool spec (dict),
- an optional validator callable,
- a tuple of reference file paths (docs / cheatsheets / examples).

Skills live in a module-level registry so callers can look them up by
name (``get_skill("analog.explorer")``) instead of importing individual
functions. This enables external skill packs and mechanical refactors
of the prompt surface without chasing import sites.

The registry is populated by importing modules under
``eda_agents.skills.*`` at import time. New skills register themselves
with ``register_skill(...)`` at module load.
"""

from __future__ import annotations

from eda_agents.skills.base import Skill
from eda_agents.skills.registry import (
    clear_registry,
    get_skill,
    list_skills,
    register_skill,
)

# Import side-effect modules to populate the registry.
# Grouped by domain for clarity; each module's import registers skills.
from eda_agents.skills import analog as _analog  # noqa: F401
from eda_agents.skills import analog_roles as _analog_roles  # noqa: F401
from eda_agents.skills import digital as _digital  # noqa: F401
from eda_agents.skills import flow as _flow  # noqa: F401
from eda_agents.skills import tools as _tools  # noqa: F401

__all__ = [
    "Skill",
    "get_skill",
    "list_skills",
    "register_skill",
    "clear_registry",
]
