"""RTL file snapshot manager for rollback in RTL-aware autoresearch.

Manages save/restore of RTL source files and (optionally) the LibreLane
config file so that discarded proposals can be cleanly rolled back.

Directory layout::

    {work_dir}/rtl_snapshots/
        original/       # pristine state, written once at init
        best/           # updated on each keep decision
        config_best/    # config snapshot (hybrid strategy only)
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class RtlSnapshotManager:
    """Manages RTL file snapshots for rollback during autoresearch.

    Parameters
    ----------
    work_dir : Path
        Autoresearch working directory (holds ``rtl_snapshots/``).
    project_dir : Path
        Root of the design project (RTL files live here).
    """

    def __init__(self, work_dir: Path, project_dir: Path):
        self._snap_dir = work_dir / "rtl_snapshots"
        self._original_dir = self._snap_dir / "original"
        self._best_dir = self._snap_dir / "best"
        self._config_best_dir = self._snap_dir / "config_best"
        self._project_dir = project_dir

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def init_from_originals(
        self,
        rtl_sources: list[Path],
        config_path: Path | None = None,
    ) -> None:
        """Snapshot the original (unmodified) RTL as the initial 'best'.

        Called once at the start of an autoresearch run. If snapshots
        already exist (resume scenario), this is a no-op.
        """
        if self._best_dir.is_dir() and any(self._best_dir.iterdir()):
            logger.info("RTL snapshots already exist, resuming")
            return

        self._original_dir.mkdir(parents=True, exist_ok=True)
        self._best_dir.mkdir(parents=True, exist_ok=True)

        for src in rtl_sources:
            if not src.is_file():
                logger.warning("RTL source not found: %s", src)
                continue
            rel = self._rel_path(src)
            for target_dir in (self._original_dir, self._best_dir):
                dest = target_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

        if config_path and config_path.is_file():
            self._config_best_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(config_path, self._config_best_dir / config_path.name)

        logger.info(
            "RTL snapshots initialized: %d files", len(rtl_sources)
        )

    # ------------------------------------------------------------------
    # Restore / Update
    # ------------------------------------------------------------------

    def restore_best(
        self,
        rtl_sources: list[Path],
        config_path: Path | None = None,
    ) -> None:
        """Restore RTL files (and optionally config) from the best snapshot."""
        for src in rtl_sources:
            rel = self._rel_path(src)
            snap = self._best_dir / rel
            if snap.is_file():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snap, src)

        if config_path and (self._config_best_dir / config_path.name).is_file():
            shutil.copy2(self._config_best_dir / config_path.name, config_path)

    def update_best(
        self,
        rtl_sources: list[Path],
        config_path: Path | None = None,
    ) -> None:
        """Save current RTL (and optionally config) as the new best."""
        for src in rtl_sources:
            if not src.is_file():
                continue
            rel = self._rel_path(src)
            dest = self._best_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

        if config_path and config_path.is_file():
            self._config_best_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(config_path, self._config_best_dir / config_path.name)

    # ------------------------------------------------------------------
    # Apply changes
    # ------------------------------------------------------------------

    def apply_rtl_changes(
        self,
        rtl_changes: dict[str, str],
    ) -> list[Path]:
        """Write proposed RTL content to disk.

        Parameters
        ----------
        rtl_changes : dict
            Mapping of relative file paths (relative to project_dir)
            to full file content strings.

        Returns
        -------
        list[Path]
            Absolute paths of files that were written.
        """
        written: list[Path] = []
        for rel_path, content in rtl_changes.items():
            dest = self._project_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            written.append(dest)
            logger.debug("Wrote RTL: %s (%d chars)", dest, len(content))
        return written

    # ------------------------------------------------------------------
    # Diff / Hash
    # ------------------------------------------------------------------

    def diff_summary(self, rtl_sources: list[Path]) -> str:
        """Generate a human-readable diff of current vs best RTL.

        Returns a compact summary suitable for program.md updates.
        """
        parts: list[str] = []
        for src in rtl_sources:
            rel = self._rel_path(src)
            snap = self._best_dir / rel
            if not src.is_file() or not snap.is_file():
                continue
            current_lines = src.read_text().splitlines(keepends=True)
            best_lines = snap.read_text().splitlines(keepends=True)
            diff = list(difflib.unified_diff(
                best_lines, current_lines,
                fromfile=f"best/{rel}", tofile=f"current/{rel}",
                n=2,
            ))
            if diff:
                # Truncate long diffs
                if len(diff) > 30:
                    diff = diff[:30] + [f"... ({len(diff) - 30} more lines)\n"]
                parts.append("".join(diff))
        return "\n".join(parts) if parts else "(no changes)"

    def content_hash(self, rtl_sources: list[Path]) -> str:
        """Compute a hash of current RTL content for dedup purposes."""
        h = hashlib.md5()
        for src in sorted(rtl_sources):
            if src.is_file():
                h.update(src.read_bytes())
        return h.hexdigest()[:12]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rel_path(self, src: Path) -> Path:
        """Get path relative to project_dir, handling edge cases."""
        try:
            return src.resolve().relative_to(self._project_dir.resolve())
        except ValueError:
            # File is outside project_dir -- use filename only
            return Path(src.name)
