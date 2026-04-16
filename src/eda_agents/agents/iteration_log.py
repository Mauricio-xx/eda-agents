"""YAML-backed iteration log for the analog-role DAG.

The log persists handoffs between Librarian / Architect / Designer /
Verifier so re-running the harness can pick up from the last valid
state and so post-mortems can see how many iterations each block
needed before convergence.

Contract:

  - ``max_iterations`` guards the designer <-> verifier loop. When
    exceeded, ``append`` raises ``EscalationError`` so the harness
    can escalate (bounce back to the architect or surface to the
    user) instead of silently spinning forever.
  - The log is a list of ``IterationEntry`` instances, each sharing
    the same ``block`` and ``session_id``.
  - YAML round-trip is deterministic enough for diffing: the list
    is serialised in append order, timestamps are ISO-8601 UTC, and
    extra fields live under ``metadata`` so the schema is stable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class EscalationError(RuntimeError):
    """Raised when the designer / verifier loop exceeds ``max_iterations``."""


class IterationEntry(BaseModel):
    """A single handoff between two roles."""

    model_config = ConfigDict(extra="forbid")

    iteration: int = Field(ge=0)
    from_role: str
    to_role: str
    status: str = Field(
        default="handoff",
        description="handoff | accepted | rejected | escalated",
    )
    summary: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class IterationLog(BaseModel):
    """Bounded append-only iteration log.

    The log doubles as:

      - an audit trail (YAML-serialisable),
      - an iteration counter for the designer / verifier loop,
      - and the hook point for post-session lessons-learned.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    block: str
    max_iterations: int = Field(default=3, ge=1, le=16)
    entries: list[IterationEntry] = Field(default_factory=list)

    def append(self, entry: IterationEntry) -> IterationEntry:
        """Append ``entry`` to the log, enforcing the iteration cap."""
        if entry.iteration > self.max_iterations:
            raise EscalationError(
                f"iteration {entry.iteration} exceeds cap of "
                f"{self.max_iterations} for block '{self.block}'"
            )
        self.entries.append(entry)
        return entry

    def record(
        self,
        from_role: str,
        to_role: str,
        status: str = "handoff",
        summary: str = "",
        iteration: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IterationEntry:
        """Convenience wrapper that numbers entries monotonically."""
        next_iter = iteration if iteration is not None else (
            self.entries[-1].iteration + 1 if self.entries else 1
        )
        entry = IterationEntry(
            iteration=next_iter,
            from_role=from_role,
            to_role=to_role,
            status=status,
            summary=summary,
            metadata=dict(metadata or {}),
        )
        return self.append(entry)

    def latest(self) -> IterationEntry | None:
        return self.entries[-1] if self.entries else None

    def current_iteration(self) -> int:
        return self.entries[-1].iteration if self.entries else 0

    def escalate(self, summary: str = "iteration cap reached") -> IterationEntry:
        """Record an ``escalated`` entry without re-raising.

        The caller is expected to have observed ``EscalationError`` or
        to have reached a decision point that warrants human review.
        """
        entry = IterationEntry(
            iteration=self.current_iteration(),
            from_role="harness",
            to_role="user",
            status="escalated",
            summary=summary,
        )
        self.entries.append(entry)
        return entry

    # -- Persistence ------------------------------------------------

    def to_yaml(self) -> str:
        payload = self.model_dump(mode="json")
        return yaml.safe_dump(payload, sort_keys=False)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_yaml())
        return p

    @classmethod
    def load(cls, path: str | Path) -> "IterationLog":
        data = yaml.safe_load(Path(path).read_text()) or {}
        return cls.model_validate(data)
