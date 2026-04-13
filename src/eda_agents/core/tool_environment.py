"""Tool environment abstraction for EDA tool invocation.

Provides a thin layer between stage runners and the OS so that the
same runner code can target local tools, Nix shells, or (future)
Docker containers without conditional logic at each call site.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class ToolEnvironment(ABC):
    """Abstract interface for locating and running EDA tools."""

    @abstractmethod
    def which(self, tool: str) -> Path | None:
        """Find *tool* on this environment's PATH.

        Returns the resolved path or ``None`` if the tool is not
        available.
        """
        ...

    @abstractmethod
    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: int = 1800,
    ) -> subprocess.CompletedProcess[str]:
        """Execute *cmd* and return the completed process.

        Parameters
        ----------
        cmd : list[str]
            Command and arguments.
        cwd : Path or str, optional
            Working directory.
        env : dict, optional
            Full environment dict.  If ``None``, inherits the current
            process environment (with any environment-level overrides
            the concrete class applies).
        timeout_s : int
            Maximum wall-clock seconds before the process is killed.

        Raises
        ------
        FileNotFoundError
            If the executable is not found.
        subprocess.TimeoutExpired
            If *timeout_s* is exceeded.
        """
        ...


class LocalToolEnvironment(ToolEnvironment):
    """Runs tools directly on the host via ``subprocess``."""

    def which(self, tool: str) -> Path | None:
        result = shutil.which(tool)
        return Path(result) if result else None

    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: int = 1800,
    ) -> subprocess.CompletedProcess[str]:
        logger.debug("LocalToolEnvironment: %s", " ".join(cmd))
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            env=env,
            timeout=timeout_s,
        )


class DockerToolEnvironment(ToolEnvironment):
    """Placeholder for IIC-OSIC-TOOLS Docker invocation (Phase 7)."""

    def which(self, tool: str) -> Path | None:
        raise NotImplementedError(
            "DockerToolEnvironment is deferred to Phase 7. "
            "Use LocalToolEnvironment for now."
        )

    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: int = 1800,
    ) -> subprocess.CompletedProcess[str]:
        raise NotImplementedError(
            "DockerToolEnvironment is deferred to Phase 7. "
            "Use LocalToolEnvironment for now."
        )
