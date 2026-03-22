"""Base protocol and data structures for EDA artifact importers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class ImportItem:
    """A single item extracted from an EDA artifact.

    Represents one knowledge entry to be written to the context store.
    """

    type: str  # "knowledge"
    key: str  # e.g. "librelane-config-inverter"
    content: str  # markdown body
    source: str  # human-readable file path


@runtime_checkable
class EdaImporter(Protocol):
    """Protocol for EDA artifact parsers.

    Unlike AdapterProtocol (bidirectional, models AI coding tools),
    EdaImporter is import-only: it reads EDA-specific files and
    produces structured knowledge entries. No export, no MCP config.
    """

    name: str

    def can_parse(self, path: Path) -> bool:
        """Check if this importer can handle the given path."""
        ...

    def parse(self, path: Path) -> list[ImportItem]:
        """Parse the artifact at path and return knowledge items."""
        ...

    def describe(self) -> str:
        """Human-readable description of what this importer handles."""
        ...
