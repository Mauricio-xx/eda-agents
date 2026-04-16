"""Role enum + executor protocol for the analog DAG."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class Role(str, Enum):
    """The four analog DAG roles."""

    LIBRARIAN = "librarian"
    ARCHITECT = "architect"
    DESIGNER = "designer"
    VERIFIER = "verifier"

    @property
    def skill_name(self) -> str:
        return f"analog.roles.{self.value}"


@dataclass(frozen=True)
class RoleResult:
    """Output of one role invocation."""

    role: Role
    summary: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    next_role: Role | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RoleExecutor(Protocol):
    """Pluggable backend that turns a role + prompt into a ``RoleResult``.

    Implementations:
      - ``DryRunExecutor`` (in ``harness``) — prints the rendered prompt
        and emits a synthetic result; used by tests and the demo.
      - Real LLM backends (ADK / Claude Code CLI) live behind this
        protocol; they are wired in by the caller.
    """

    def execute(
        self,
        role: Role,
        prompt: str,
        context: dict[str, Any],
    ) -> RoleResult:
        ...
