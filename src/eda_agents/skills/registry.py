"""Module-level registry mapping skill name to ``Skill`` instance."""

from __future__ import annotations

import logging
from typing import Any

from eda_agents.skills.base import Skill

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Skill] = {}

# Separator between rendered skills in ``render_relevant_skills``.
# Chosen to be visible in prompts but cheap tokens-wise.
_SKILL_SEPARATOR = "\n\n---\n\n"

# Rough tokens/char ratio used for the soft budget warning. ~4 chars per
# token is the long-standing OpenAI approximation; exact counting would
# require model-specific tokenizers, which this helper intentionally
# avoids so it can stay dependency-free.
_CHARS_PER_TOKEN = 4


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


def render_relevant_skills(
    entries: list[str | tuple[str, dict[str, Any]]],
    context: Any,
    *,
    max_tokens: int = 12_000,
) -> str:
    """Render a list of skill declarations into a single prompt block.

    Entries accept both the bare ``"namespace.skill"`` string form and
    the ``("namespace.skill", {"kwarg": value})`` tuple form for skills
    whose ``prompt_fn`` needs extra arguments beyond the topology or
    design ``context``.

    Each skill is rendered via ``get_skill(name).render(context, **kwargs)``
    and concatenated with ``_SKILL_SEPARATOR`` between them. An empty
    ``entries`` list returns an empty string so callers can safely
    prepend the result unconditionally.

    ``max_tokens`` is a soft cap: once the approximate token count
    (4 chars/token) exceeds it, a warning is logged but the full text is
    still returned. The runner decides whether to truncate; this helper
    deliberately does not, because silently dropping a skill would make
    the prompt dependent on declaration order in a way callers cannot
    see.
    """
    if not entries:
        return ""

    parts: list[str] = []
    for entry in entries:
        if isinstance(entry, tuple):
            name, kwargs = entry
        else:
            name, kwargs = entry, {}
        skill = get_skill(name)
        parts.append(skill.render(context, **kwargs))

    rendered = _SKILL_SEPARATOR.join(parts)

    approx_tokens = len(rendered) // _CHARS_PER_TOKEN
    if approx_tokens > max_tokens:
        logger.warning(
            "render_relevant_skills: rendered prompt ~%d tokens exceeds "
            "soft cap of %d (%d skills, %d chars). Skill authors should "
            "trim content or split into narrower skills.",
            approx_tokens, max_tokens, len(parts), len(rendered),
        )

    return rendered
