"""Module-level registry mapping skill name to ``Skill`` instance."""

from __future__ import annotations

from eda_agents.skills.base import Skill

_REGISTRY: dict[str, Skill] = {}


def register_skill(skill: Skill, *, overwrite: bool = False) -> Skill:
    """Add a skill to the registry.

    Args:
        skill: The ``Skill`` instance to register.
        overwrite: If ``True``, replace an existing skill with the same
            name. Otherwise, raise ``ValueError`` on conflict.

    Returns:
        The registered skill (for call-site chaining).
    """
    if not overwrite and skill.name in _REGISTRY:
        raise ValueError(
            f"Skill {skill.name!r} already registered; pass "
            "overwrite=True to replace."
        )
    _REGISTRY[skill.name] = skill
    return skill


def get_skill(name: str) -> Skill:
    """Look up a skill by dotted name."""
    try:
        return _REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(
            f"Skill {name!r} not found. Available: {available}"
        ) from None


def list_skills(prefix: str | None = None) -> list[Skill]:
    """List registered skills, optionally filtered by name prefix.

    Results are sorted by name for stable output.
    """
    items = _REGISTRY.values()
    if prefix:
        items = (s for s in items if s.name.startswith(prefix))
    return sorted(items, key=lambda s: s.name)


def clear_registry() -> None:
    """Remove every skill. Intended for tests."""
    _REGISTRY.clear()
