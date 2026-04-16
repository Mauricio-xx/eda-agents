"""Skill dataclass: a named bundle of prompt + tool spec + validator.

Skills are deliberately thin: they wrap existing callables and dicts
with a discoverable, string-keyed identity. Implementations live in the
callables themselves — the dataclass is a descriptor, not a framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class Skill:
    """A reusable unit of agent capability.

    Attributes:
        name: Dotted namespace identifier, e.g. ``"analog.explorer"``.
        description: One-line summary used in listings.
        prompt_fn: Callable returning a prompt string. Signature is
            skill-specific (most accept a topology or design object).
            ``None`` if this skill is pure tool_spec.
        tool_spec: Static OpenAI/ADK function-calling spec dict, or
            ``None``. For dynamic specs (topology-driven) use the
            topology's own ``tool_spec()`` method.
        validator: Optional callable to post-validate agent output.
            Contract is skill-specific.
        references: Tuple of paths to supporting docs or example files.
            Consumers may attach these to prompts or surface them in
            UIs. Paths are stored as-is; resolution is caller's job.
    """

    name: str
    description: str
    prompt_fn: Callable[..., str] | None = None
    tool_spec: dict[str, Any] | None = None
    validator: Callable[..., Any] | None = None
    references: tuple[Path, ...] = field(default_factory=tuple)

    def render(self, *args: Any, **kwargs: Any) -> str:
        """Invoke the prompt callable with the skill's args."""
        if self.prompt_fn is None:
            raise RuntimeError(
                f"Skill {self.name!r} has no prompt_fn; use .spec() for "
                "tool-spec-only skills."
            )
        return self.prompt_fn(*args, **kwargs)

    def spec(self) -> dict[str, Any]:
        """Return the static tool spec dict."""
        if self.tool_spec is None:
            raise RuntimeError(
                f"Skill {self.name!r} has no tool_spec; use .render() for "
                "prompt-only skills."
            )
        return self.tool_spec

    def validate(self, *args: Any, **kwargs: Any) -> Any:
        """Invoke the validator callable."""
        if self.validator is None:
            raise RuntimeError(
                f"Skill {self.name!r} has no validator."
            )
        return self.validator(*args, **kwargs)
